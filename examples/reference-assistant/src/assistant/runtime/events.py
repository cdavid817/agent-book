# -*- coding: utf-8 -*-
"""事件总线：一次发射、多方消费（对应第 12 章）。

生产者通过 emit 发射契约事件；总线维护单调递增的 seq，并检测逆序/重复。
消费者（第 14 章 OtelBridge、第 16 章 CostLedger）订阅同一总线。
"""
from __future__ import annotations

from typing import Callable

from ..contracts.event import AgentEvent, ContractError

Consumer = Callable[[AgentEvent], None]


class SequenceError(ContractError):
    """事件序号逆序或重复——可能是发射逻辑 bug 或轨迹被篡改。"""


class EventBus:
    """最小事件总线。每会话一个实例；seq 从 0 单调递增。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._seq = -1
        self._consumers: list[Consumer] = []
        self._seen_seq: set[int] = set()

    def subscribe(self, consumer: Consumer) -> None:
        self._consumers.append(consumer)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def publish(self, event: AgentEvent) -> AgentEvent:
        """发布一个已构造好的契约事件（构造时已校验载荷）。"""
        if event.session_id != self.session_id:
            raise ContractError(
                f"事件 session_id={event.session_id} 与总线 {self.session_id} 不符")
        if event.seq in self._seen_seq:
            raise SequenceError(f"重复 seq={event.seq}")
        if event.seq <= self._seq - 1 and self._seen_seq:
            # 允许等于 next_seq 分配值；严格逆序（小于已见最大）视为错误
            if event.seq < max(self._seen_seq):
                raise SequenceError(
                    f"逆序 seq={event.seq} < 已见最大 {max(self._seen_seq)}")
        self._seen_seq.add(event.seq)
        self._seq = max(self._seq, event.seq)
        for c in self._consumers:
            c(event)
        return event
