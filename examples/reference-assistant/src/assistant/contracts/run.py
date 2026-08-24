# -*- coding: utf-8 -*-
"""最小领域模型枚举与术语（对应第 12 章与附录 E）。

概念定义（与附录 E 术语表同步）：
  Task       用户或系统提交的业务目标
  Run        Task 的一次执行实例
  Attempt    Run 的一次执行尝试
  Session    交互与上下文容器
  Turn       一轮输入到输出
  Step       Run 内一个可记录的执行节点
  ToolCall   对工具的一次调用请求
  ToolResult ToolCall 的对应结果
  Artifact   可持久化和审阅的执行产物
  Checkpoint 用于恢复的状态快照
  Event      运行时发出的版本化事实
  Trace      事件在可观测系统中的因果投影
"""
from __future__ import annotations

from enum import Enum


class SessionStatus(str, Enum):
    """session_end.status 的有限枚举（与第 12 章事件表一致）。"""

    SUCCESS = "success"        # 正常完成
    ABORTED = "aborted"        # 预算/策略中断（参见第 3、13 章）
    FAILED = "failed"          # 异常失败

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in {s.value for s in cls}


VALID_SESSION_STATUSES = frozenset(s.value for s in SessionStatus)
