# -*- coding: utf-8 -*-
"""Token 用量契约（对应第 14/16 章）。

统一层只做规范化，不丢弃供应商原始分类；未知新增字段不得导致旧消费者崩溃。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# 规范化后的已知分类。供应商可能提供更多字段，一律保留在 raw 中。
KNOWN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """一次 LLM 响应的 token 用量。保留原始分类，供成本台账与 OTel 共用同一份数据。"""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "TokenUsage":
        """从供应商 usage dict 构造。未知字段保留在 raw，不报错（前向兼容）。"""
        if "input_tokens" not in m or "output_tokens" not in m:
            raise ValueError("usage 必须含 input_tokens 与 output_tokens")
        return cls(
            input_tokens=int(m["input_tokens"]),
            output_tokens=int(m["output_tokens"]),
            cached_input_tokens=int(m.get("cached_input_tokens", 0)),
            cache_creation_input_tokens=int(m.get("cache_creation_input_tokens", 0)),
            reasoning_tokens=int(m.get("reasoning_tokens", 0)),
            raw=dict(m),
        )

    def get(self, key: str, default: int = 0) -> int:
        """按分类取值，优先规范化字段，回落到原始 raw。"""
        if key in KNOWN_FIELDS:
            return int(getattr(self, key))
        return int(self.raw.get(key, default))
