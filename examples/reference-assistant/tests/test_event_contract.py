# -*- coding: utf-8 -*-
"""事件契约合同测试（对应第 12 章契约、第 15 章 BLOCK 门禁）。

覆盖计划 TG1 §6.7 的九条：正向、反向（缺 usage/model/call_id）、孤儿结果、
双消费者一致、前向兼容、Schema 版本 fail-fast、并发不串线、序号逆序/重复检测。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from assistant.contracts import (
    AgentEvent, ContractError, TokenUsage, SessionStatus,
    make_turn_end, make_tool_call, make_tool_result, make_session_end,
    pair_tool_events, iter_orphan_call_ids,
)
from assistant.runtime.events import EventBus, SequenceError

TS = datetime(2026, 8, 24, tzinfo=timezone.utc)


# 1) 缺少 usage 时生产者失败
def test_turn_end_missing_usage_fails():
    with pytest.raises(ContractError, match="usage"):
        AgentEvent("s", 0, TS, "turn_end", {"model": "m", "turn": 1})


# 2) 缺少 model 时生产者失败
def test_turn_end_missing_model_fails():
    with pytest.raises(ContractError, match="model"):
        AgentEvent("s", 0, TS, "turn_end",
                   {"usage": {"input_tokens": 1, "output_tokens": 1}, "turn": 1})


# 3) tool_call 缺少 call_id 时生产者失败
def test_tool_call_missing_call_id_fails():
    with pytest.raises(ContractError, match="call_id"):
        AgentEvent("s", 0, TS, "tool_call", {"name": "search"})


# 4) tool_result 的 call_id 找不到对应调用 → 孤儿事件被检出
def test_orphan_tool_result_detected():
    events = [
        make_tool_result(session_id="s", seq=0, call_id="ghost", name="x"),
    ]
    pairs, orphans = pair_tool_events(events)
    assert pairs == []
    assert len(orphans) == 1 and orphans[0].payload["call_id"] == "ghost"


def test_hanging_tool_call_detected():
    events = [make_tool_call(session_id="s", seq=0, call_id="c1", name="x")]
    assert list(iter_orphan_call_ids(events)) == ["c1"]


# 5) turn_end 同时被 OTel 与成本台账正确消费（见 test_consumers.py 完整版）
def test_turn_end_consumable_by_ledger():
    from assistant.cost.ledger import CostLedger
    ev = make_turn_end(session_id="s", seq=0, turn=1, model="test-model",
                       usage={"input_tokens": 1_000_000, "output_tokens": 0})
    led = CostLedger()
    led.on_event(ev)
    assert led.by_session["s"] == pytest.approx(1.0)   # 1M input * $1.0


# 6) 未知可选字段不破坏旧消费者（前向兼容）
def test_unknown_optional_field_is_forward_compatible():
    ev = make_turn_end(session_id="s", seq=0, turn=1, model="test-model",
                       usage={"input_tokens": 1, "output_tokens": 1},
                       future_field="ignored")           # 新增字段
    assert ev.payload["future_field"] == "ignored"       # 不报错，旧消费者忽略即可
    # usage 里新增分类也不崩
    u = TokenUsage.from_mapping({"input_tokens": 1, "output_tokens": 1,
                                 "brand_new_tokens": 42})
    assert u.get("brand_new_tokens") == 42


# 7) Schema 版本不支持时 fail-fast，不静默解释
def test_unsupported_schema_version_fails_fast():
    with pytest.raises(ContractError, match="schema_version"):
        AgentEvent("s", 0, TS, "turn_start", {"turn": 1}, schema_version=999)


# 8) 多会话并发时 Span 与 Token 不串线（OTel 分槽）——完整版见 test_consumers.py
def test_multi_session_ledger_isolated():
    from assistant.cost.ledger import CostLedger
    led = CostLedger()
    led.on_event(make_turn_end(session_id="A", seq=0, turn=1, model="test-model",
                               usage={"input_tokens": 1_000_000, "output_tokens": 0}))
    led.on_event(make_turn_end(session_id="B", seq=0, turn=1, model="test-model",
                               usage={"input_tokens": 2_000_000, "output_tokens": 0}))
    assert led.by_session["A"] == pytest.approx(1.0)
    assert led.by_session["B"] == pytest.approx(2.0)     # 不串线


# 9) 事件序号逆序或重复时有检测
def test_sequence_duplicate_detected():
    bus = EventBus("s")
    bus.publish(AgentEvent("s", 0, TS, "turn_start", {"turn": 1}))
    with pytest.raises(SequenceError, match="重复"):
        bus.publish(AgentEvent("s", 0, TS, "turn_start", {"turn": 1}))


def test_sequence_out_of_order_detected():
    bus = EventBus("s")
    bus.publish(AgentEvent("s", 5, TS, "turn_start", {"turn": 1}))
    with pytest.raises(SequenceError, match="逆序"):
        bus.publish(AgentEvent("s", 2, TS, "turn_start", {"turn": 2}))


# 额外：session_end 非法状态被拒
def test_session_end_invalid_status_fails():
    with pytest.raises(ContractError, match="status"):
        AgentEvent("s", 0, TS, "session_end", {"status": "definitely_not_valid"})


def test_session_end_valid_status_ok():
    ev = make_session_end(session_id="s", seq=0, status=SessionStatus.OK.value,
                          turns=3)
    assert ev.payload["status"] == "ok"
