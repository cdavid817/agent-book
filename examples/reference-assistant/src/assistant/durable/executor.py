# -*- coding: utf-8 -*-
"""Durable Execution / Agent SRE 最小原语（第 12 章 P1 · 13.2）。

核心问题：Agent 的长任务会崩、会超时、会遇到供应商抖动。若**每一层都自作主张重试**，
同一个副作用会被执行多次、成本被放大。本模块给出四块可测的可靠性原语：

  1. 重试归属（retry ownership）——每层只重试**自己那层**的故障，不越权重试下层故障；
  2. 幂等键（idempotency key）——有副作用的操作执行一次、重放返回同一结果；
  3. 租约 + 心跳 + 回收（lease/heartbeat/reclaim）——worker 崩溃后任务能被安全接管；
  4. 退避 + 死信（backoff/dead-letter）——超过尝试上限进死信队列，不无限重试。

只用标准库；时间以**显式传入的 now**驱动（可测、可复现，不调用 time.time）。
Saga 补偿的语义见 [C-20]。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ---- 1) 重试归属：每层故障只由其所属层重试，防多层同时重试放大副作用 ----

# region book:ch12-retry-ownership
class FailureLayer(Enum):
    TRANSPORT = "transport"        # HTTP 传输故障
    TOOL = "tool"                  # Tool 瞬时故障
    AGENT_STEP = "agent_step"      # Agent Step 失败
    WORKER = "worker"              # Worker 崩溃
    TASK = "task"                  # 整个任务失败


# 故障层 → 唯一重试责任方（计划 13.2 的归属表）
RETRY_OWNER: dict[FailureLayer, str] = {
    FailureLayer.TRANSPORT: "transport_sdk",
    FailureLayer.TOOL: "tool_adapter",
    FailureLayer.AGENT_STEP: "agent_runtime",
    FailureLayer.WORKER: "durable_executor",
    FailureLayer.TASK: "workflow_operator",
}


def owns_retry(actor: str, layer: FailureLayer) -> bool:
    """actor 是否**应当**重试 layer 层的故障。只有归属方返回 True——
    其他层必须原样上抛，由归属层处理，避免叠加重试。"""
    return RETRY_OWNER[layer] == actor
# endregion book:ch12-retry-ownership


# ---- 2) 幂等键：有副作用的操作执行一次、重放命中缓存 ----

class IdempotencyStore:
    """execute-once：相同 key 只真正执行一次，后续重试/重放返回首次结果。
    生产中换成带 TTL 的持久存储（DB/Redis），此处教学用内存。"""

    def __init__(self) -> None:
        self._done: dict[str, Any] = {}

    def run_once(self, key: str, op: Callable[[], Any]) -> Any:
        if key in self._done:
            return self._done[key]         # 重放：不再触发副作用
        result = op()
        self._done[key] = result
        return result

    def seen(self, key: str) -> bool:
        return key in self._done


# ---- 3) 租约 + 心跳 + 回收 ----

class TaskState(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    DEAD = "dead"                  # 进死信


@dataclass
class DurableTask:
    task_id: str
    payload: dict = field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    attempts: int = 0
    max_attempts: int = 5
    owner: str | None = None
    lease_until: float = 0.0       # 单位与传入的 now 一致

    def lease(self, worker: str, now: float, ttl: float) -> None:
        self.state = TaskState.LEASED
        self.owner = worker
        self.lease_until = now + ttl
        self.attempts += 1

    def heartbeat(self, worker: str, now: float, ttl: float) -> bool:
        """续租；仅持租者可续。返回是否成功（防止已被回收后误续）。"""
        if self.state == TaskState.LEASED and self.owner == worker \
                and now <= self.lease_until:
            self.lease_until = now + ttl
            return True
        return False

    def lease_expired(self, now: float) -> bool:
        return self.state == TaskState.LEASED and now > self.lease_until


class DurableQueue:
    """最小 durable 队列：租约领取、心跳续租、崩溃回收、退避、死信。"""

    def __init__(self) -> None:
        self.tasks: dict[str, DurableTask] = {}

    def submit(self, task: DurableTask) -> None:
        self.tasks[task.task_id] = task

    def acquire(self, worker: str, now: float, ttl: float) -> DurableTask | None:
        """领取一个可执行任务：PENDING 或**租约已过期**（原 worker 崩溃）的任务。"""
        for t in self.tasks.values():
            if t.state == TaskState.PENDING or t.lease_expired(now):
                if t.lease_expired(now):        # 崩溃回收：进入死信前先判尝试上限
                    if t.attempts >= t.max_attempts:
                        t.state = TaskState.DEAD
                        continue
                t.lease(worker, now, ttl)
                return t
        return None

    def complete(self, task_id: str) -> None:
        self.tasks[task_id].state = TaskState.DONE

    def fail(self, task_id: str) -> None:
        """一次尝试失败：未达上限回到 PENDING 等重试，达上限进死信。"""
        t = self.tasks[task_id]
        if t.attempts >= t.max_attempts:
            t.state = TaskState.DEAD
        else:
            t.state = TaskState.PENDING
            t.owner = None
            t.lease_until = 0.0

    def dead_letters(self) -> list[DurableTask]:
        return [t for t in self.tasks.values() if t.state == TaskState.DEAD]


# ---- 4) 退避：确定性指数退避（生产另加抖动，此处可测所以不加随机） ----

def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """第 attempt 次重试的退避秒数：base * 2^(attempt-1)，封顶 cap。attempt 从 1 起。"""
    if attempt < 1:
        return 0.0
    return min(cap, base * (2 ** (attempt - 1)))
