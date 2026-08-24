# -*- coding: utf-8 -*-
"""A2A 协议互操作合同测试（第 18 章 P1 · 13.1）。"""
from __future__ import annotations

import pytest

from assistant.interop.a2a import (
    TaskState, RUNNING, PAUSED, TERMINAL,
    can_transition, can_cancel, can_accept_message,
    validate_agent_card, a2a_terminal_to_session_status,
    RemoteTask, TaskNotCancelableError,
)
from assistant.contracts.run import SessionStatus


def test_state_groups_cover_all_states():
    all_states = set(TaskState) - {TaskState.UNSPECIFIED}
    assert RUNNING | PAUSED | TERMINAL == all_states
    assert not (RUNNING & TERMINAL) and not (PAUSED & TERMINAL)


def test_valid_transitions():
    assert can_transition(TaskState.SUBMITTED, TaskState.WORKING)
    assert can_transition(TaskState.WORKING, TaskState.INPUT_REQUIRED)
    assert can_transition(TaskState.INPUT_REQUIRED, TaskState.WORKING)   # 恢复
    assert can_transition(TaskState.WORKING, TaskState.COMPLETED)


def test_invalid_transitions_and_terminal_absorbing():
    assert not can_transition(TaskState.SUBMITTED, TaskState.COMPLETED)  # 必先 WORKING
    for t in TERMINAL:
        for dst in TaskState:
            assert not can_transition(t, dst)                           # 终态吸收


def test_cancellation_rules():
    assert can_cancel(TaskState.WORKING)
    assert can_cancel(TaskState.INPUT_REQUIRED)
    for t in TERMINAL:
        assert can_cancel(t) is False                                   # 终态不可取消


def test_terminal_rejects_messages():
    assert can_accept_message(TaskState.WORKING)
    for t in TERMINAL:
        assert can_accept_message(t) is False


def test_agent_card_validation():
    ok = {"name": "research", "url": "https://a/x", "version": "1.0",
          "capabilities": {"streaming": True}, "skills": []}
    assert validate_agent_card(ok) == []
    bad = {"name": "x", "skills": {}}
    errs = validate_agent_card(bad)
    assert any("url" in e for e in errs) and any("skills" in e for e in errs)


def test_terminal_to_session_status_mapping():
    assert a2a_terminal_to_session_status(TaskState.COMPLETED) == SessionStatus.SUCCESS
    assert a2a_terminal_to_session_status(TaskState.FAILED) == SessionStatus.FAILED
    assert a2a_terminal_to_session_status(TaskState.REJECTED) == SessionStatus.FAILED
    assert a2a_terminal_to_session_status(TaskState.CANCELED) == SessionStatus.ABORTED
    with pytest.raises(ValueError):
        a2a_terminal_to_session_status(TaskState.WORKING)               # 非终态不能映射


def test_remote_task_lifecycle_and_trace_propagation():
    t = RemoteTask("srv-gen-123", trace_context={"traceparent": "00-abc-def-01"})
    t.advance(TaskState.WORKING)
    t.advance(TaskState.INPUT_REQUIRED)
    t.advance(TaskState.WORKING)
    t.advance(TaskState.COMPLETED)
    assert t.state == TaskState.COMPLETED
    assert t.trace_context["traceparent"] == "00-abc-def-01"           # 跨边界透传保持
    with pytest.raises(ValueError):
        t.advance(TaskState.WORKING)                                    # 终态不能再迁移


def test_remote_task_cancel_and_not_cancelable():
    t = RemoteTask("srv-1")
    t.advance(TaskState.WORKING)
    t.cancel()
    assert t.state == TaskState.CANCELED
    with pytest.raises(TaskNotCancelableError):
        t.cancel()                                                     # 已终态
