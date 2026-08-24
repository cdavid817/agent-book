# -*- coding: utf-8 -*-
"""事件流 → OTel Span/指标 桥接器（对应第 14 章）。

只从第 12 章事件契约取字段：turn_end 的 token 从 payload["usage"] 嵌套取，
tool_call/tool_result 用同一个 call_id 配对。多会话并发时按 session_id 分槽，
Span 与 Token 不串线。
"""
from __future__ import annotations

from opentelemetry import metrics, trace

from ..contracts.event import AgentEvent


class OtelBridge:
    """订阅 EventBus 全量事件，配对成四层 Span 并打指标。"""

    def __init__(self) -> None:
        self.tracer = trace.get_tracer("assistant")
        meter = metrics.get_meter("assistant")
        self.tokens = meter.create_counter(
            "assistant.tokens.used", description="token 消耗, 按类别分列")
        self.tool_calls = meter.create_counter(
            "assistant.tool.calls", description="工具调用数, 按工具与最终结果分列")
        self.turns_hist = meter.create_histogram(
            "assistant.session.turns", description="已结束会话的轮次分布")
        # 并发会话按 session_id 分槽，避免串线
        self._sessions: dict[str, object] = {}
        self._turns: dict[str, object] = {}
        self._tools: dict[str, dict[str, object]] = {}

    def on_event(self, ev: AgentEvent) -> None:      # EventBus 消费者接口
        p, t, sid = ev.payload, ev.type, ev.session_id
        if t == "session_start":
            self._sessions[sid] = self.tracer.start_span("session", attributes={
                "session.id": sid, "task.type": p.get("task_type", "unknown")})
            self._tools[sid] = {}
        elif t == "turn_start":
            ctx = trace.set_span_in_context(self._sessions[sid])
            self._turns[sid] = self.tracer.start_span(
                f"turn-{p['turn']}", context=ctx, attributes={"turn": p["turn"]})
        elif t == "turn_end":
            usage = p["usage"]                       # 契约：usage 嵌套（第 12 章）
            turn = self._turns[sid]
            for k in ("input_tokens", "output_tokens"):
                if k in usage:                       # gen_ai 语义约定的属性名
                    turn.set_attribute(f"gen_ai.usage.{k}", usage[k])
                    self.tokens.add(usage[k], {"category": k.split("_")[0]})
            turn.set_attribute("stop_reason", p.get("stop_reason", ""))
            turn.end()
        elif t == "tool_call":
            ctx = trace.set_span_in_context(self._turns[sid])
            self._tools[sid][p["call_id"]] = self.tracer.start_span(
                f"tool:{p['name']}", context=ctx, attributes={
                    "tool.name": p["name"], "tool.effect": p.get("effect", ""),
                    "trace.pointer": p.get("pointer", "")})
        elif t == "tool_result":
            span = self._tools[sid].pop(p["call_id"])
            outcome = "error" if p.get("is_error") else "ok"
            span.set_attribute("tool.outcome", outcome)
            span.set_attribute("tool.retries", p.get("retries", 0))
            span.end()
            self.tool_calls.add(1, {"tool": p["name"], "outcome": outcome})
        elif t == "session_end":
            session = self._sessions[sid]
            session.set_attribute("session.status", p.get("status", ""))
            self.turns_hist.record(p.get("turns", 0),
                                   {"task.type": p.get("task_type", "unknown")})
            session.end()
