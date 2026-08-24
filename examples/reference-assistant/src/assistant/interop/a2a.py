# -*- coding: utf-8 -*-
"""A2A 协议互操作原语（第 18 章 P1 · 13.1）。

A2A（Agent2Agent，Linux Foundation）标准化**跨信任边界的 Agent↔Agent 调用**：
对方以 Agent Card 声明能力，任务以有生命周期的 Task 承载，结果放 Artifact。
它与 MCP（Agent→工具，第 8 章）、内部图（进程内编排，第 18 章）是三个不同边界。

本模块给出可测的三块：Task 状态机（含合法迁移与取消规则）、Agent Card 最小校验、
A2A Task ↔ 本书内部 Run 状态映射。协议细节以官方规范 [C-21] 为准。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..contracts.run import SessionStatus


# ---- Task 状态机（规范的 9 个 TaskState） ----

class TaskState(str, Enum):
    SUBMITTED = "TASK_STATE_SUBMITTED"          # 运行
    WORKING = "TASK_STATE_WORKING"              # 运行
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"  # 中断（等输入）
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"    # 中断（等鉴权）
    COMPLETED = "TASK_STATE_COMPLETED"          # 终态
    FAILED = "TASK_STATE_FAILED"                # 终态
    CANCELED = "TASK_STATE_CANCELED"            # 终态
    REJECTED = "TASK_STATE_REJECTED"            # 终态
    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"      # 未定


RUNNING = frozenset({TaskState.SUBMITTED, TaskState.WORKING})
PAUSED = frozenset({TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED})
TERMINAL = frozenset({TaskState.COMPLETED, TaskState.FAILED,
                      TaskState.CANCELED, TaskState.REJECTED})

# region book:ch18-a2a-state-machine
# 合法迁移：运行→中断/终态，中断→恢复运行/终态；终态是吸收态（不可再迁移）。
_TRANSITIONS: dict[TaskState, frozenset] = {
    TaskState.SUBMITTED: frozenset({TaskState.WORKING, TaskState.REJECTED,
                                    TaskState.CANCELED}),
    TaskState.WORKING: frozenset({TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED,
                                  TaskState.COMPLETED, TaskState.FAILED,
                                  TaskState.CANCELED}),
    TaskState.INPUT_REQUIRED: frozenset({TaskState.WORKING, TaskState.CANCELED,
                                         TaskState.FAILED}),
    TaskState.AUTH_REQUIRED: frozenset({TaskState.WORKING, TaskState.CANCELED,
                                        TaskState.FAILED}),
}


def can_transition(src: TaskState, dst: TaskState) -> bool:
    return dst in _TRANSITIONS.get(src, frozenset())


def can_cancel(state: TaskState) -> bool:
    """终态不可取消（规范：返回 TaskNotCancelableError）。"""
    return state not in TERMINAL


def can_accept_message(state: TaskState) -> bool:
    """终态 Task 不再接受消息（规范明文）。"""
    return state not in TERMINAL
# endregion book:ch18-a2a-state-machine


class TaskNotCancelableError(Exception):
    pass


# ---- Agent Card 最小校验 ----

REQUIRED_CARD_FIELDS = ("name", "url", "version", "capabilities", "skills")


def validate_agent_card(card: dict) -> list[str]:
    """返回缺失/非法字段列表（空表示合法）。发布在 /.well-known/agent-card.json。"""
    errors = []
    for k in REQUIRED_CARD_FIELDS:
        if k not in card:
            errors.append(f"缺字段 {k}")
    if "skills" in card and not isinstance(card["skills"], list):
        errors.append("skills 必须是数组")
    caps = card.get("capabilities")
    if caps is not None and not isinstance(caps, dict):
        errors.append("capabilities 必须是对象")
    return errors


# ---- A2A Task ↔ 内部 Run 映射 ----

# A2A 终态 → 本书内部会话状态（contracts.run.SessionStatus）
_STATUS_MAP = {
    TaskState.COMPLETED: SessionStatus.SUCCESS,
    TaskState.FAILED: SessionStatus.FAILED,
    TaskState.REJECTED: SessionStatus.FAILED,
    TaskState.CANCELED: SessionStatus.ABORTED,
}


def a2a_terminal_to_session_status(state: TaskState) -> SessionStatus:
    """把 A2A 远端 Task 的终态映射为内部 Run 的会话状态，便于统一审计与计费。"""
    if state not in TERMINAL:
        raise ValueError(f"{state} 非终态，尚不能映射为最终会话状态")
    return _STATUS_MAP[state]


@dataclass
class RemoteTask:
    """本地对一个远端 A2A Task 的镜像。task_id 由**服务端生成**（规范：客户端不得自造）。"""
    task_id: str
    state: TaskState = TaskState.SUBMITTED
    artifacts: list[dict] = field(default_factory=list)
    trace_context: dict[str, str] = field(default_factory=dict)  # traceparent 等，跨边界透传

    def advance(self, dst: TaskState) -> None:
        if not can_transition(self.state, dst):
            raise ValueError(f"非法迁移 {self.state.name} → {dst.name}")
        self.state = dst

    def cancel(self) -> None:
        if not can_cancel(self.state):
            raise TaskNotCancelableError(f"{self.state.name} 是终态，不可取消")
        self.state = TaskState.CANCELED
