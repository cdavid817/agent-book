# -*- coding: utf-8 -*-
"""工具调用/结果的 call_id 配对契约（对应第 7、12、14 章）。

tool_call 与 tool_result 通过同一个 call_id 关联。消费者据此配对成一段 Tool Span
（第 14 章）或审计记录。孤儿结果（找不到对应调用）必须可被检出，而非静默配对。
"""
from __future__ import annotations

from typing import Iterable, Iterator

from .event import AgentEvent


def pair_tool_events(events: Iterable[AgentEvent]) -> tuple[list[tuple[AgentEvent, AgentEvent]], list[AgentEvent]]:
    """把事件流里的 tool_call / tool_result 按 call_id 配对。

    返回 (已配对列表, 孤儿 tool_result 列表)。孤儿指没有先行 tool_call 的结果。
    """
    open_calls: dict[str, AgentEvent] = {}
    pairs: list[tuple[AgentEvent, AgentEvent]] = []
    orphans: list[AgentEvent] = []
    for ev in events:
        if ev.type == "tool_call":
            open_calls[ev.payload["call_id"]] = ev
        elif ev.type == "tool_result":
            cid = ev.payload["call_id"]
            call = open_calls.pop(cid, None)
            if call is None:
                orphans.append(ev)     # 孤儿结果：暴露给指标，不静默吞掉
            else:
                pairs.append((call, ev))
    return pairs, orphans


def iter_orphan_call_ids(events: Iterable[AgentEvent]) -> Iterator[str]:
    """未收到 result 的 tool_call 的 call_id（悬挂调用）。"""
    _, _ = None, None
    open_calls: set[str] = set()
    for ev in events:
        if ev.type == "tool_call":
            open_calls.add(ev.payload["call_id"])
        elif ev.type == "tool_result":
            open_calls.discard(ev.payload["call_id"])
    yield from open_calls
