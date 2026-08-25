# -*- coding: utf-8 -*-
"""Agent Span 语义 / 传播 / 采集分级合同测试（第 14 章 P1 · 13.8）。"""
from __future__ import annotations

from assistant.obs.spans import (
    SpanName, CaptureLevel, apply_capture, redact_text,
    format_traceparent, parse_traceparent, CONTENT_KEYS,
)


def test_span_taxonomy_complete_and_dotted():
    names = {s.value for s in SpanName}
    # 覆盖整条生命周期的 11 个 span 名
    assert names == {
        "task.run", "agent.turn", "model.call", "tool.call", "retrieval.query",
        "memory.read", "memory.write", "policy.evaluate", "approval.wait",
        "handoff", "artifact.write",
    }


def test_traceparent_roundtrip():
    tp = format_traceparent("0af7651916cd43dd8448eb211c80319c",
                            "b7ad6b7169203331", sampled=True)
    got = parse_traceparent({"traceparent": tp})
    assert got == {"trace_id": "0af7651916cd43dd8448eb211c80319c",
                   "span_id": "b7ad6b7169203331", "sampled": True}


def test_traceparent_invalid_and_missing():
    assert parse_traceparent({}) is None
    assert parse_traceparent({"traceparent": "garbage"}) is None
    assert parse_traceparent({"traceparent": "00-tooshort-x-01"}) is None


def test_redact_masks_email_and_long_numbers():
    r = redact_text("联系 bob@acme.com 退款订单 1234567 金额 50")
    assert "bob@acme.com" not in r and "<email>" in r
    assert "1234567" not in r and "<num>" in r
    assert "50" in r                                    # 短数字不误伤


def test_capture_metadata_only_drops_content():
    attrs = {"gen_ai.usage.input_tokens": 100, "tool.name": "search",
             "tool.arguments": "user=bob@acme.com", "gen_ai.input.messages": "hi"}
    out = apply_capture(CaptureLevel.METADATA_ONLY, attrs)
    assert out == {"gen_ai.usage.input_tokens": 100, "tool.name": "search"}
    for k in CONTENT_KEYS:
        assert k not in out


def test_capture_redacted_keeps_but_masks_content():
    attrs = {"tool.name": "refund", "tool.arguments": "card 4111111111111111"}
    out = apply_capture(CaptureLevel.REDACTED, attrs)
    assert out["tool.name"] == "refund"                 # 元数据原样
    assert "4111111111111111" not in out["tool.arguments"]
    assert "<num>" in out["tool.arguments"]


def test_capture_full_keeps_everything():
    attrs = {"tool.arguments": "user=bob@acme.com", "tool.name": "x"}
    out = apply_capture(CaptureLevel.FULL, attrs)
    assert out == attrs


def test_default_is_strictest():
    # metadata_only 是最严级别：默认就该用它
    assert CaptureLevel.METADATA_ONLY.value == "metadata_only"
    attrs = {"prompt": "secret", "model.call.duration_ms": 12}
    assert "prompt" not in apply_capture(CaptureLevel.METADATA_ONLY, attrs)
