# -*- coding: utf-8 -*-
"""事件契约：贯穿项目唯一的事件外壳与载荷校验（对应第 12 章）。

设计纪律（第 12 章 2.5 节）：
- 事件外壳固定为 session_id / seq / ts / type / payload / schema_version；
- 生产者在 emit 前校验必填字段，消费者不再猜测字段名；
- turn_end 的 token 必位于 payload["usage"]，且 model 必填；
- tool_call / tool_result 通过同一个 call_id 关联；
- session_end.status 使用有限枚举（run.SessionStatus）；
- 可选字段前向兼容；破坏性变更提升 schema_version。

第 14 章（OTel）与第 16 章（成本台账）只从本契约取字段，是同一事件的两个投影。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from .run import SessionStatus

EventType = Literal[
    "session_start",
    "turn_start",
    "turn_end",
    "tool_call",
    "tool_result",
    "session_end",
]

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


class ContractError(ValueError):
    """生产者侧契约违规——必须在 emit 处失败，不得让消费者静默兜底。"""


# region book:ch12-event-envelope
@dataclass(frozen=True, slots=True)
class AgentEvent:
    """版本化的运行时事实。第 2 章的 AgentEvent 以此为准。"""

    session_id: str
    seq: int
    ts: datetime
    type: EventType
    payload: Mapping[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            # 不支持的 Schema 版本 fail-fast，不静默解释（第 12 章契约）
            raise ContractError(
                f"unsupported schema_version={self.schema_version}; "
                f"supported={sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        _validate_payload(self.type, self.payload)
# endregion book:ch12-event-envelope


def _validate_payload(etype: str, p: Mapping[str, Any]) -> None:
    """按事件类型校验必填载荷字段。可选字段一律放行（前向兼容）。"""
    if etype == "turn_end":
        if "usage" not in p:
            raise ContractError("turn_end.payload 必含 usage")
        if not isinstance(p["usage"], Mapping):
            raise ContractError("turn_end.payload.usage 必须是映射（token 分类）")
        for k in ("input_tokens", "output_tokens"):
            if k not in p["usage"]:
                raise ContractError(f"turn_end.payload.usage 必含 {k}")
        if "model" not in p:
            raise ContractError("turn_end.payload 必含 model")
    elif etype == "tool_call":
        for k in ("call_id", "name"):
            if k not in p:
                raise ContractError(f"tool_call.payload 必含 {k}")
    elif etype == "tool_result":
        if "call_id" not in p:
            raise ContractError("tool_result.payload 必含 call_id")
    elif etype == "session_end":
        status = p.get("status")
        if not (isinstance(status, str) and SessionStatus.is_valid(status)):
            raise ContractError(
                f"session_end.payload.status 非法: {status!r}; "
                f"合法值 {sorted(s.value for s in SessionStatus)}"
            )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- 生产者工厂：在构造处即校验，杜绝无效事件进入总线 ----

def make_turn_end(
    *, session_id: str, seq: int, turn: int, model: str,
    usage: Mapping[str, Any], stop_reason: str = "", ts: datetime | None = None,
    **extra: Any,
) -> AgentEvent:
    payload = {"turn": turn, "model": model, "usage": dict(usage),
               "stop_reason": stop_reason, **extra}
    return AgentEvent(session_id, seq, ts or _now(), "turn_end", payload)


def make_tool_call(
    *, session_id: str, seq: int, call_id: str, name: str,
    effect: str = "", pointer: str = "", ts: datetime | None = None, **extra: Any,
) -> AgentEvent:
    payload = {"call_id": call_id, "name": name, "effect": effect,
               "pointer": pointer, **extra}
    return AgentEvent(session_id, seq, ts or _now(), "tool_call", payload)


def make_tool_result(
    *, session_id: str, seq: int, call_id: str, name: str,
    is_error: bool = False, retries: int = 0, pointer: str = "",
    ts: datetime | None = None, **extra: Any,
) -> AgentEvent:
    payload = {"call_id": call_id, "name": name, "is_error": is_error,
               "retries": retries, "pointer": pointer, **extra}
    return AgentEvent(session_id, seq, ts or _now(), "tool_result", payload)


def make_session_end(
    *, session_id: str, seq: int, status: str, turns: int = 0,
    task_type: str = "unknown", ts: datetime | None = None, **extra: Any,
) -> AgentEvent:
    payload = {"status": status, "turns": turns, "task_type": task_type, **extra}
    return AgentEvent(session_id, seq, ts or _now(), "session_end", payload)
