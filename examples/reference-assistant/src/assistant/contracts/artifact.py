# -*- coding: utf-8 -*-
"""执行产物契约（对应第 12 章轨迹存储与第 23 章 Coding Agent）。

Artifact 是可持久化、可审阅的执行产物（patch、报告、导出文件）。事件流里只带
指针（pointer），大对象存轨迹存储，避免事件流自身成为带宽/存储黑洞（第 12 章）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    kind: str                      # 如 "patch" / "report" / "export"
    pointer: str                   # 轨迹存储中的引用（URI/键），不内联大对象
    meta: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
