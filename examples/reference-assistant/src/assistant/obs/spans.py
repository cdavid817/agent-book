# -*- coding: utf-8 -*-
"""Agent Span 语义、跨边界传播与内容采集分级（第 14 章 P1 · 13.8）。

§10.6 定了"内容默认不采、区分规范/自定义属性"；本模块把观测再推进三步：
  1. 统一的 **Agent Span 名字表**——覆盖整条 Agent 生命周期（不止 turn/tool），
     让 A2A/durable/记忆/审批等环节都有标准 span 名，跨章一致；
  2. **跨边界 Trace 传播**——W3C traceparent 的注入/提取，把远端（MCP/A2A/队列/
     子 Agent/浏览器执行器/人工审批/异步 resume）的 span 挂回同一棵树；
  3. **内容采集分级**——metadata_only / redacted / full，默认最严，脱敏可机器执行。

规范属性以 [C-04] 为准；只用标准库。
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any


# ---- 1) Agent Span 名字表（统一命名，跨章一致） ----

class SpanName(str, Enum):
    TASK_RUN = "task.run"              # 一次 Task 的整体执行
    AGENT_TURN = "agent.turn"         # 一轮
    MODEL_CALL = "model.call"         # 一次 LLM 调用
    TOOL_CALL = "tool.call"           # 一次工具调用
    RETRIEVAL_QUERY = "retrieval.query"  # 检索（第 11 章）
    MEMORY_READ = "memory.read"       # 记忆读（第 10 章）
    MEMORY_WRITE = "memory.write"     # 记忆写
    POLICY_EVALUATE = "policy.evaluate"  # PDP 裁决（第 13 章）
    APPROVAL_WAIT = "approval.wait"   # 等待人工确认（第 13 章 HITL）
    HANDOFF = "handoff"               # 交接 / A2A 委派（第 18 章）
    ARTIFACT_WRITE = "artifact.write"  # 产物落盘（第 18 章 Artifact）


# ---- 2) 跨边界传播：W3C traceparent ----

_TP_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    """组装 W3C traceparent。trace_id 32 hex、span_id 16 hex。"""
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


def parse_traceparent(carrier: dict[str, str]) -> dict[str, Any] | None:
    """从跨边界载体（HTTP 头 / A2A _meta / 队列消息属性）提取父上下文；非法返回 None。"""
    tp = carrier.get("traceparent")
    if not tp:
        return None
    m = _TP_RE.match(tp.strip())
    if not m:
        return None
    _, trace_id, span_id, flags = m.groups()
    return {"trace_id": trace_id, "span_id": span_id,
            "sampled": flags == "01"}


# ---- 3) 内容采集分级 ----

class CaptureLevel(str, Enum):
    METADATA_ONLY = "metadata_only"   # 默认：只留元数据，内容字段整体丢弃
    REDACTED = "redacted"             # 内容保留但脱敏（邮箱/长数字等）
    FULL = "full"                     # 全量（仅在合规许可的隔离环境）


# 可能含敏感内容的属性键（其余视为元数据）
CONTENT_KEYS = frozenset({
    "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions",
    "tool.arguments", "tool.result", "prompt", "completion",
})

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONGNUM = re.compile(r"\d{6,}")


def redact_text(text: str) -> str:
    """粗粒度脱敏：邮箱与 6 位以上连续数字（卡号/工单号/账号）打码。"""
    text = _EMAIL.sub("<email>", text)
    text = _LONGNUM.sub("<num>", text)
    return text


# region book:ch14-capture-level
def apply_capture(level: CaptureLevel, attrs: dict[str, Any]) -> dict[str, Any]:
    """按采集级别过滤/脱敏 Span 属性。默认 metadata_only：内容字段一律不落。"""
    if level is CaptureLevel.FULL:
        return dict(attrs)
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if k not in CONTENT_KEYS:
            out[k] = v                                   # 元数据总是保留
        elif level is CaptureLevel.REDACTED:
            out[k] = redact_text(v) if isinstance(v, str) else v
        # METADATA_ONLY：内容字段直接丢弃
    return out
# endregion book:ch14-capture-level
