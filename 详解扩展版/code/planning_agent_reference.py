from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Protocol


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int = 1
    initial_backoff_s: float = 0.2
    backoff_multiplier: float = 2.0


@dataclass(slots=True)
class Step:
    id: str
    title: str
    tool: str
    args: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    completion: dict[str, Any] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_s: float = 30.0
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    output: dict[str, Any] | None = None
    error: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Plan:
    id: str
    goal: str
    steps: list[Step]
    constraints: list[str] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    replan_count: int = 0
    max_replans: int = 2
    cancelled: bool = False

    def step_map(self) -> dict[str, Step]:
        return {step.id: step for step in self.steps}


@dataclass(slots=True)
class ToolResult:
    ok: bool
    value: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    retryable: bool = True


@dataclass(slots=True)
class Verification:
    passed: bool
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PlanPatch:
    base_version: int
    reason: str
    operations: list[dict[str, Any]]


@dataclass(slots=True)
class Event:
    at: float
    type: str
    plan_id: str
    step_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class VersionConflict(RuntimeError):
    pass


class InvalidPlan(ValueError):
    pass


ToolCallable = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class Verifier(Protocol):
    async def verify(
        self,
        *,
        plan: Plan,
        step: Step,
        result: ToolResult,
    ) -> Verification: ...


class Replanner(Protocol):
    async def replan(self, *, plan: Plan, failed_steps: list[Step]) -> PlanPatch | None: ...


class EventSink(Protocol):
    async def emit(self, event: Event) -> None: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._lock = asyncio.Lock()

    async def emit(self, event: Event) -> None:
        async with self._lock:
            self.events.append(copy.deepcopy(event))


class InMemoryPlanStore:
    """示例存储：真实系统可替换为 SQLite/PostgreSQL + 事务。"""

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self._lock = asyncio.Lock()

    async def create(self, plan: Plan) -> None:
        async with self._lock:
            if plan.id in self._plans:
                raise ValueError(f"plan already exists: {plan.id}")
            self._plans[plan.id] = copy.deepcopy(plan)

    async def snapshot(self, plan_id: str) -> Plan:
        async with self._lock:
            try:
                return copy.deepcopy(self._plans[plan_id])
            except KeyError as exc:
                raise KeyError(f"unknown plan: {plan_id}") from exc

    async def mutate(
        self,
        plan_id: str,
        expected_version: int,
        fn: Callable[[Plan], None],
    ) -> Plan:
        async with self._lock:
            current = self._plans[plan_id]
            if current.version != expected_version:
                raise VersionConflict(
                    f"expected v{expected_version}, actual v{current.version}"
                )
            candidate = copy.deepcopy(current)
            fn(candidate)
            candidate.version += 1
            candidate.updated_at = time.time()
            self._plans[plan_id] = candidate
            return copy.deepcopy(candidate)


async def mutate_with_retry(
    store: InMemoryPlanStore,
    plan_id: str,
    fn: Callable[[Plan], None],
    *,
    retries: int = 20,
) -> Plan:
    for _ in range(retries):
        snapshot = await store.snapshot(plan_id)
        try:
            return await store.mutate(plan_id, snapshot.version, fn)
        except VersionConflict:
            await asyncio.sleep(0)
    raise VersionConflict(f"too many concurrent updates for plan {plan_id}")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolCallable] = {}

    def register(self, name: str, tool: ToolCallable) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> ToolCallable:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def names(self) -> set[str]:
        return set(self._tools)


class PlanValidator:
    def __init__(self, available_tools: set[str]) -> None:
        self.available_tools = available_tools

    def validate(self, plan: Plan) -> None:
        if not plan.goal.strip():
            raise InvalidPlan("goal must not be empty")
        if not plan.steps:
            raise InvalidPlan("plan must contain at least one step")

        ids = [step.id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise InvalidPlan("step ids must be unique")

        known = set(ids)
        for step in plan.steps:
            if step.tool not in self.available_tools:
                raise InvalidPlan(f"step {step.id}: unknown tool {step.tool}")
            if step.id in step.depends_on:
                raise InvalidPlan(f"step {step.id}: self dependency")
            unknown_deps = set(step.depends_on) - known
            if unknown_deps:
                raise InvalidPlan(
                    f"step {step.id}: unknown dependencies {sorted(unknown_deps)}"
                )
            if step.timeout_s <= 0:
                raise InvalidPlan(f"step {step.id}: timeout must be positive")
            if step.retry.max_retries < 0:
                raise InvalidPlan(f"step {step.id}: max_retries must be >= 0")

        self._assert_acyclic(plan)

    @staticmethod
    def _assert_acyclic(plan: Plan) -> None:
        indegree = {step.id: len(step.depends_on) for step in plan.steps}
        children: dict[str, list[str]] = {step.id: [] for step in plan.steps}
        for step in plan.steps:
            for dep in step.depends_on:
                children[dep].append(step.id)

        queue = [step_id for step_id, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            node = queue.pop()
            visited += 1
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        if visited != len(plan.steps):
            cycle_nodes = sorted(k for k, degree in indegree.items() if degree > 0)
            raise InvalidPlan(f"dependency cycle detected: {cycle_nodes}")


_REF = re.compile(r"^\$\{steps\.([^.}]+)\.output(?:\.([^}]+))?\}$")


def _read_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise KeyError(f"cannot resolve path component {part!r}")
    return current


def resolve_args(value: Any, plan: Plan) -> Any:
    """递归解析 ${steps.<id>.output.<path>} 引用。"""
    if isinstance(value, str):
        match = _REF.match(value)
        if not match:
            return value
        step_id, path = match.groups()
        step = plan.step_map()[step_id]
        if step.status != StepStatus.SUCCEEDED or step.output is None:
            raise RuntimeError(f"referenced output not ready: {step_id}")
        return copy.deepcopy(_read_path(step.output, path))
    if isinstance(value, list):
        return [resolve_args(item, plan) for item in value]
    if isinstance(value, dict):
        return {key: resolve_args(item, plan) for key, item in value.items()}
    return value


class ContractVerifier:
    """确定性优先的示例验证器。completion 支持 required_keys 和 equals。"""

    async def verify(
        self,
        *,
        plan: Plan,
        step: Step,
        result: ToolResult,
    ) -> Verification:
        if not result.ok:
            return Verification(False, result.error or "tool returned ok=false")

        required = step.completion.get("required_keys", [])
        missing = [key for key in required if key not in result.value]
        if missing:
            return Verification(False, f"missing required output keys: {missing}")

        expected = step.completion.get("equals", {})
        mismatched = {
            key: {"expected": wanted, "actual": result.value.get(key)}
            for key, wanted in expected.items()
            if result.value.get(key) != wanted
        }
        if mismatched:
            return Verification(False, f"value mismatch: {mismatched}")

        return Verification(
            True,
            "completion contract satisfied",
            evidence=result.evidence,
        )


class FallbackReplanner:
    """演示增量重规划：失败步骤声明 fallback_tool 时，仅替换该节点。"""

    async def replan(self, *, plan: Plan, failed_steps: list[Step]) -> PlanPatch | None:
        operations: list[dict[str, Any]] = []
        for step in failed_steps:
            fallback = step.metadata.get("fallback_tool")
            if fallback and not step.metadata.get("fallback_applied"):
                operations.append(
                    {
                        "op": "replace_tool",
                        "step_id": step.id,
                        "tool": fallback,
                        "set_metadata": {"fallback_applied": True},
                    }
                )
                operations.append(
                    {
                        "op": "reset_step",
                        "step_id": step.id,
                    }
                )

        if not operations:
            return None
        return PlanPatch(
            base_version=plan.version,
            reason="replace failed tools with declared fallbacks",
            operations=operations,
        )


def apply_patch_in_place(plan: Plan, patch: PlanPatch) -> None:
    if plan.version != patch.base_version:
        raise VersionConflict(
            f"patch based on v{patch.base_version}, current plan is v{plan.version}"
        )

    by_id = plan.step_map()
    for operation in patch.operations:
        op = operation["op"]
        step_id = operation.get("step_id")
        if op == "replace_tool":
            step = by_id[step_id]
            step.tool = operation["tool"]
            step.metadata.update(operation.get("set_metadata", {}))
        elif op == "reset_step":
            step = by_id[step_id]
            step.status = StepStatus.PENDING
            step.error = None
            step.output = None
            step.evidence.clear()
            step.attempts = 0
        elif op == "skip_step":
            step = by_id[step_id]
            step.status = StepStatus.SKIPPED
            step.error = operation.get("reason")
        elif op == "add_step":
            raw = operation["step"]
            new_step = Step(**raw)
            if new_step.id in by_id:
                raise InvalidPlan(f"patch adds duplicate step: {new_step.id}")
            plan.steps.append(new_step)
            by_id[new_step.id] = new_step
        else:
            raise InvalidPlan(f"unknown patch operation: {op}")

    plan.replan_count += 1


class DagScheduler:
    def __init__(
        self,
        *,
        store: InMemoryPlanStore,
        tools: ToolRegistry,
        verifier: Verifier,
        replanner: Replanner,
        events: EventSink,
        max_parallel: int = 4,
        total_timeout_s: float = 180.0,
    ) -> None:
        if max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        self.store = store
        self.tools = tools
        self.verifier = verifier
        self.replanner = replanner
        self.events = events
        self.max_parallel = max_parallel
        self.total_timeout_s = total_timeout_s
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def run(self, plan_id: str) -> Plan:
        initial = await self.store.snapshot(plan_id)
        PlanValidator(self.tools.names()).validate(initial)
        await self._set_plan_status(plan_id, PlanStatus.RUNNING)
        await self._emit("plan_started", plan_id)

        try:
            async with asyncio.timeout(self.total_timeout_s):
                while True:
                    plan = await self.store.snapshot(plan_id)
                    if plan.cancelled:
                        await self._cancel_pending(plan_id)
                        await self._set_plan_status(plan_id, PlanStatus.CANCELLED)
                        await self._emit("plan_cancelled", plan_id)
                        return await self.store.snapshot(plan_id)

                    if self._is_complete(plan):
                        await self._set_plan_status(plan_id, PlanStatus.COMPLETED)
                        await self._emit("plan_completed", plan_id)
                        return await self.store.snapshot(plan_id)

                    failed = [s for s in plan.steps if s.status == StepStatus.FAILED]
                    if failed:
                        changed = await self._try_replan(plan, failed)
                        if changed:
                            continue
                        await self._set_plan_status(plan_id, PlanStatus.FAILED)
                        await self._emit(
                            "plan_failed",
                            plan_id,
                            data={"failed_steps": [step.id for step in failed]},
                        )
                        return await self.store.snapshot(plan_id)

                    ready = self._ready_steps(plan)
                    if not ready:
                        await self._set_plan_status(plan_id, PlanStatus.FAILED)
                        await self._emit(
                            "plan_stalled",
                            plan_id,
                            data={"reason": "no ready steps and plan is not complete"},
                        )
                        return await self.store.snapshot(plan_id)

                    await asyncio.gather(
                        *(self._run_step(plan_id, step.id) for step in ready)
                    )
        except TimeoutError:
            await self._cancel_pending(plan_id)
            await self._set_plan_status(plan_id, PlanStatus.FAILED)
            await self._emit(
                "plan_timed_out",
                plan_id,
                data={"timeout_s": self.total_timeout_s},
            )
            return await self.store.snapshot(plan_id)

    @staticmethod
    def _is_complete(plan: Plan) -> bool:
        terminal_success = {StepStatus.SUCCEEDED, StepStatus.SKIPPED}
        return all(step.status in terminal_success for step in plan.steps)

    @staticmethod
    def _ready_steps(plan: Plan) -> list[Step]:
        steps = plan.step_map()
        ready: list[Step] = []
        for step in plan.steps:
            if step.status != StepStatus.PENDING:
                continue
            if step.requires_approval and not step.metadata.get("approved", False):
                continue
            if all(
                steps[dep].status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED}
                for dep in step.depends_on
            ):
                ready.append(step)
        return ready

    async def _run_step(self, plan_id: str, step_id: str) -> None:
        async with self._semaphore:
            claimed = await self._claim_step(plan_id, step_id)
            if not claimed:
                return

            snapshot = await self.store.snapshot(plan_id)
            step = snapshot.step_map()[step_id]
            await self._emit(
                "step_started",
                plan_id,
                step_id,
                {"tool": step.tool, "attempt": step.attempts},
            )

            try:
                args = resolve_args(step.args, snapshot)
                args.setdefault(
                    "_idempotency_key",
                    f"{plan_id}:{step_id}:{step.attempts}",
                )
                tool = self.tools.get(step.tool)
                async with asyncio.timeout(step.timeout_s):
                    result = await tool(args)
            except TimeoutError:
                result = ToolResult(ok=False, error="step timeout", retryable=True)
            except Exception as exc:  # noqa: BLE001 - tool boundary must normalize errors
                result = ToolResult(
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                    retryable=True,
                )

            fresh = await self.store.snapshot(plan_id)
            current_step = fresh.step_map()[step_id]
            verification = await self.verifier.verify(
                plan=fresh,
                step=current_step,
                result=result,
            )

            if verification.passed:
                await self._mark_succeeded(
                    plan_id,
                    step_id,
                    result.value,
                    result.evidence + verification.evidence,
                )
                await self._emit(
                    "step_succeeded",
                    plan_id,
                    step_id,
                    {"verification": verification.reason},
                )
                return

            await self._handle_failure(
                plan_id=plan_id,
                step_id=step_id,
                error=verification.reason,
                retryable=result.retryable,
            )

    async def _claim_step(self, plan_id: str, step_id: str) -> bool:
        claimed = False

        def mutate(plan: Plan) -> None:
            nonlocal claimed
            step = plan.step_map()[step_id]
            if step.status != StepStatus.PENDING:
                return
            step.status = StepStatus.RUNNING
            step.attempts += 1
            step.error = None
            claimed = True

        await mutate_with_retry(self.store, plan_id, mutate)
        return claimed

    async def _mark_succeeded(
        self,
        plan_id: str,
        step_id: str,
        output: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> None:
        def mutate(plan: Plan) -> None:
            step = plan.step_map()[step_id]
            step.status = StepStatus.SUCCEEDED
            step.output = copy.deepcopy(output)
            step.evidence = copy.deepcopy(evidence)
            step.error = None

        await mutate_with_retry(self.store, plan_id, mutate)

    async def _handle_failure(
        self,
        *,
        plan_id: str,
        step_id: str,
        error: str,
        retryable: bool,
    ) -> None:
        snapshot = await self.store.snapshot(plan_id)
        step = snapshot.step_map()[step_id]
        may_retry = retryable and step.attempts <= step.retry.max_retries

        if may_retry:
            backoff = step.retry.initial_backoff_s * (
                step.retry.backoff_multiplier ** max(0, step.attempts - 1)
            )
            await self._emit(
                "step_retry_scheduled",
                plan_id,
                step_id,
                {"error": error, "backoff_s": backoff},
            )
            await asyncio.sleep(backoff)

        def mutate(plan: Plan) -> None:
            target = plan.step_map()[step_id]
            target.error = error
            target.status = StepStatus.PENDING if may_retry else StepStatus.FAILED

        await mutate_with_retry(self.store, plan_id, mutate)
        if not may_retry:
            await self._emit("step_failed", plan_id, step_id, {"error": error})

    async def _try_replan(self, plan: Plan, failed: list[Step]) -> bool:
        if plan.replan_count >= plan.max_replans:
            return False
        patch = await self.replanner.replan(plan=plan, failed_steps=failed)
        if patch is None:
            return False

        def mutate(candidate: Plan) -> None:
            apply_patch_in_place(candidate, patch)
            PlanValidator(self.tools.names()).validate(candidate)

        try:
            await self.store.mutate(plan.id, plan.version, mutate)
        except VersionConflict:
            return True  # 其他执行器已更新；重新读取后继续判断。

        await self._emit(
            "plan_replanned",
            plan.id,
            data={"reason": patch.reason, "operations": patch.operations},
        )
        return True

    async def _cancel_pending(self, plan_id: str) -> None:
        def mutate(plan: Plan) -> None:
            for step in plan.steps:
                if step.status in {StepStatus.PENDING, StepStatus.RUNNING}:
                    step.status = StepStatus.CANCELLED

        await mutate_with_retry(self.store, plan_id, mutate)

    async def _set_plan_status(self, plan_id: str, status: PlanStatus) -> None:
        def mutate(plan: Plan) -> None:
            plan.status = status

        await mutate_with_retry(self.store, plan_id, mutate)

    async def _emit(
        self,
        event_type: str,
        plan_id: str,
        step_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self.events.emit(
            Event(
                at=time.time(),
                type=event_type,
                plan_id=plan_id,
                step_id=step_id,
                data=data or {},
            )
        )


# -------------------------- 示例工具与运行入口 --------------------------


async def fetch_source(args: dict[str, Any]) -> ToolResult:
    await asyncio.sleep(0.03)
    source = args["source"]
    if source == "primary" and not args.get("allow_primary", False):
        return ToolResult(ok=False, error="primary source unavailable", retryable=False)
    return ToolResult(
        ok=True,
        value={"source": source, "records": [1, 2, 3]},
        evidence=[{"type": "tool_receipt", "source": source}],
    )


async def fetch_mirror(args: dict[str, Any]) -> ToolResult:
    await asyncio.sleep(0.02)
    return ToolResult(
        ok=True,
        value={"source": "mirror", "records": [1, 2, 3]},
        evidence=[{"type": "tool_receipt", "source": "mirror"}],
    )


async def analyze_records(args: dict[str, Any]) -> ToolResult:
    records = args["records"]
    return ToolResult(
        ok=True,
        value={"count": len(records), "sum": sum(records)},
        evidence=[{"type": "calculation", "input_count": len(records)}],
    )


async def render_report(args: dict[str, Any]) -> ToolResult:
    report = {
        "title": args["title"],
        "summary": f"count={args['count']}, sum={args['sum']}",
    }
    return ToolResult(ok=True, value={"report": report})


def build_demo_plan() -> Plan:
    return Plan(
        id=str(uuid.uuid4()),
        goal="获取记录、完成分析并生成报告",
        constraints=["工具调用必须可重试", "失败时只替换失效节点"],
        steps=[
            Step(
                id="fetch",
                title="获取源数据",
                tool="fetch_source",
                args={"source": "primary", "allow_primary": False},
                completion={"required_keys": ["records"]},
                retry=RetryPolicy(max_retries=0),
                metadata={"fallback_tool": "fetch_mirror"},
            ),
            Step(
                id="analyze",
                title="分析记录",
                tool="analyze_records",
                args={"records": "${steps.fetch.output.records}"},
                depends_on=["fetch"],
                completion={"required_keys": ["count", "sum"]},
            ),
            Step(
                id="report",
                title="生成报告",
                tool="render_report",
                args={
                    "title": "分析报告",
                    "count": "${steps.analyze.output.count}",
                    "sum": "${steps.analyze.output.sum}",
                },
                depends_on=["analyze"],
                completion={"required_keys": ["report"]},
            ),
        ],
    )


async def main() -> None:
    store = InMemoryPlanStore()
    tools = ToolRegistry()
    tools.register("fetch_source", fetch_source)
    tools.register("fetch_mirror", fetch_mirror)
    tools.register("analyze_records", analyze_records)
    tools.register("render_report", render_report)

    events = InMemoryEventSink()
    plan = build_demo_plan()
    await store.create(plan)

    scheduler = DagScheduler(
        store=store,
        tools=tools,
        verifier=ContractVerifier(),
        replanner=FallbackReplanner(),
        events=events,
        max_parallel=3,
    )
    final = await scheduler.run(plan.id)

    print(f"plan status: {final.status.value}, version: {final.version}")
    print(json.dumps(final.step_map()["report"].output, ensure_ascii=False, indent=2))
    print("events:", [event.type for event in events.events])


if __name__ == "__main__":
    asyncio.run(main())
