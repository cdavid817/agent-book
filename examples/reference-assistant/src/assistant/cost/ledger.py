# -*- coding: utf-8 -*-
"""per-session 成本台账（对应第 16 章）。

与第 14 章 OtelBridge 消费同一 turn_end 事件：从 payload["usage"] 取分类 token、
从 payload["model"] 取模型，按单价表计价。不预先把 usage 压成聚合 tokens，
以便分别计费输入/输出/缓存读/缓存写。
"""
from __future__ import annotations

from collections import defaultdict

from ..contracts.event import AgentEvent
from ..contracts.usage import TokenUsage

# 单价表（$/1M token）：input/output/缓存读/缓存写。价格会变——生产从配置加载。
# 此处示例价，核验口径见附录 C（时效性信息带日期）。
PRICES = {
    "test-model": {"input": 1.0, "output": 3.0, "cache_read": 0.1, "cache_write": 1.25},
}

# usage 分类 → 计价类目
_CATEGORY = {
    "input_tokens": "input",
    "cached_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
    "output_tokens": "output",
}


class CostLedger:
    """按会话累计成本，供经营分析与预算制定。"""

    def __init__(self) -> None:
        self.by_session: dict[str, float] = defaultdict(float)
        self.by_category: dict[str, float] = defaultdict(float)

    def add(self, session_id: str, model: str, usage: TokenUsage | dict) -> float:
        if not isinstance(usage, TokenUsage):
            usage = TokenUsage.from_mapping(usage)
        price = PRICES.get(model)
        if price is None:
            raise KeyError(f"未知模型 {model}，无法计价（先把单价加入配置）")
        cost = 0.0
        for cat_key, price_key in _CATEGORY.items():
            n = usage.get(cat_key, 0)
            unit = price.get(price_key, 0.0)
            c = n / 1_000_000 * unit
            cost += c
            self.by_category[price_key] += c
        self.by_session[session_id] += cost
        return cost

    def on_event(self, ev: AgentEvent) -> None:      # EventBus 消费者接口
        if ev.type == "turn_end":
            # 契约：usage 嵌套 + model 必填（第 12 章）；缺字段在生产者侧已 fail
            self.add(ev.session_id, ev.payload["model"], ev.payload["usage"])
