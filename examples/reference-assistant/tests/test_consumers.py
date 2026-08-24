# -*- coding: utf-8 -*-
"""OTel 桥接与成本台账消费同一事件流的端到端测试（第 14/16 章 × 第 12 章契约）。

验证计划 TG1 §6.9：第 12 章的示例事件可直接被第 14、16 章代码消费；
turn_end 同时被 OTel 与台账正确消费；多会话并发 Span/Token 不串线。
"""
from __future__ import annotations

import pytest

from assistant.contracts import (
    make_tool_call, make_tool_result, make_turn_end, make_session_end,
    AgentEvent,
)
from assistant.runtime.events import EventBus
from assistant.cost.ledger import CostLedger


_SHARED = {}   # 全局 OTel provider 每进程只能设一次，故在此缓存共享导出器


def _otel():
    """返回 (全新 OtelBridge, 共享 InMemory 导出器)。导出器每次调用前清空，
    保证测试隔离——OTel 的全局 TracerProvider 不允许被重复覆盖。"""
    from assistant.obs.otel_bridge import OtelBridge
    if "exp" not in _SHARED:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        exp = InMemorySpanExporter()
        tp = TracerProvider()
        tp.add_span_processor(SimpleSpanProcessor(exp))
        trace.set_tracer_provider(tp)
        metrics.set_meter_provider(MeterProvider(metric_readers=[InMemoryMetricReader()]))
        _SHARED["exp"] = exp
    _SHARED["exp"].clear()
    return OtelBridge(), _SHARED["exp"]


def test_single_turn_consumed_by_both():
    otel = pytest.importorskip("opentelemetry")
    bridge, exp = _otel()
    ledger = CostLedger()
    bus = EventBus("s1")
    bus.subscribe(bridge.on_event)
    bus.subscribe(ledger.on_event)

    def ev(factory=None, **k):
        return k
    bus.publish(AgentEvent("s1", bus.next_seq(), _ts(), "session_start", {"task_type": "qa"}))
    bus.publish(AgentEvent("s1", bus.next_seq(), _ts(), "turn_start", {"turn": 1}))
    bus.publish(make_tool_call(session_id="s1", seq=bus.next_seq(), call_id="c1",
                               name="search", effect="read", pointer="p"))
    bus.publish(make_tool_result(session_id="s1", seq=bus.next_seq(), call_id="c1",
                                 name="search", is_error=False))
    bus.publish(make_turn_end(session_id="s1", seq=bus.next_seq(), turn=1,
                              model="test-model",
                              usage={"input_tokens": 100, "output_tokens": 20}))
    bus.publish(make_session_end(session_id="s1", seq=bus.next_seq(), status="success",
                                 turns=1, task_type="qa"))

    from opentelemetry import trace
    trace.get_tracer_provider().force_flush()
    spans = {s.name: s for s in exp.get_finished_spans()}
    assert "turn-1" in spans
    assert spans["turn-1"].attributes["gen_ai.usage.input_tokens"] == 100
    assert spans["turn-1"].attributes["gen_ai.usage.output_tokens"] == 20
    tool = [s for n, s in spans.items() if n.startswith("tool:")][0]
    assert tool.attributes["tool.outcome"] == "ok"
    # 同一 turn_end 也进了台账
    assert ledger.by_session["s1"] == pytest.approx(100/1e6*1.0 + 20/1e6*3.0)


def test_concurrent_sessions_do_not_cross():
    pytest.importorskip("opentelemetry")
    bridge, exp = _otel()
    busA, busB = EventBus("A"), EventBus("B")
    for b in (busA, busB):
        b.subscribe(bridge.on_event)
    # 交错发射两会话
    busA.publish(AgentEvent("A", busA.next_seq(), _ts(), "session_start", {"task_type": "qa"}))
    busB.publish(AgentEvent("B", busB.next_seq(), _ts(), "session_start", {"task_type": "ops"}))
    busA.publish(AgentEvent("A", busA.next_seq(), _ts(), "turn_start", {"turn": 1}))
    busB.publish(AgentEvent("B", busB.next_seq(), _ts(), "turn_start", {"turn": 1}))
    busB.publish(make_turn_end(session_id="B", seq=busB.next_seq(), turn=1,
                               model="test-model",
                               usage={"input_tokens": 7, "output_tokens": 0}))
    busA.publish(make_turn_end(session_id="A", seq=busA.next_seq(), turn=1,
                               model="test-model",
                               usage={"input_tokens": 3, "output_tokens": 0}))
    from opentelemetry import trace
    trace.get_tracer_provider().force_flush()
    turns = {s.attributes.get("gen_ai.usage.input_tokens")
             for s in exp.get_finished_spans() if s.name == "turn-1"}
    # 两个 turn-1 各自 3 与 7，未串线
    assert turns == {3, 7}


def _ts():
    from datetime import datetime, timezone
    return datetime(2026, 8, 24, tzinfo=timezone.utc)
