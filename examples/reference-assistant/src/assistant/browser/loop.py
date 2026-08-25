# -*- coding: utf-8 -*-
"""Browser / Computer Use 的安全动作循环（第 9 章 P1 · 13.3）。

GUI 环境比工具调用凶险：页面会变、坐标会漂、动作可能点错、页面本身可能是注入面。
把执行拆成七段可审计的循环，每段拒绝盲动：

  Observe → Ground → Propose → Policy Check → Execute → Verify → Commit / Rollback

本模块沉淀其中与具体浏览器无关的**控制逻辑**（可测），两条最救命的守卫：
  1. **过期守卫**——动作在页面版本 vN 上定位（ground），执行前若页面已变成 vN+1，
     绝不按旧坐标点下去（防坐标漂移 / 页面状态过期），退回重新观察；
  2. **动作后验证 + 回滚**——执行后断言预期结果，不符即回滚，不把"点了"当"成了"。
高风险动作（上传/下载/提交表单/支付）先过策略闸（接第 13 章 PDP / 自治级别）。

感知模式与真实执行由调用方注入；本模块不绑定任何浏览器。计算机操作类基准见 [C-05]。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class Perception(Enum):
    DOM = "dom"                    # 结构化、稳定、可选择器定位
    AXTREE = "accessibility_tree"  # 语义角色，跨渲染稳，适合定位
    SCREENSHOT = "screenshot"      # 像素，最通用但坐标最易漂


class StepOutcome(Enum):
    COMMITTED = "committed"          # 执行并验证通过
    BLOCKED = "blocked"              # 策略拒绝 / 需审批
    ABORTED_STALE = "aborted_stale"  # 页面在定位后变化，拒绝按旧坐标动作
    ROLLED_BACK = "rolled_back"      # 验证失败，回滚


@dataclass(frozen=True, slots=True)
class Observation:
    page_version: str              # DOM/截图的指纹，页面一变即变
    elements: dict                 # 定位所需的元素信息（选择器/角色/坐标）


@dataclass(frozen=True, slots=True)
class Action:
    kind: str                      # click / type / upload / download / submit ...
    target: str                    # 定位到的元素引用
    grounded_on: str               # 定位所依据的 page_version
    high_risk: bool = False


# region book:ch09-browser-loop
def run_step(
    *,
    observe: Callable[[], Observation],
    ground: Callable[[Observation], Action],
    policy_allows: Callable[[Action], bool],
    execute: Callable[[Action], None],
    verify: Callable[[Action], bool],
    rollback: Callable[[Action], None] | None = None,
) -> StepOutcome:
    """跑一轮 Observe→Ground→PolicyCheck→(过期守卫)→Execute→Verify→Commit/Rollback。"""
    obs = observe()                                   # 1 Observe
    action = ground(obs)                              # 2 Ground（绑定 page_version）
    if not policy_allows(action):                     # 3+4 Propose + Policy Check
        return StepOutcome.BLOCKED
    # 过期守卫：执行前重新观察，页面若已漂移则拒绝按旧坐标动作
    if observe().page_version != action.grounded_on:
        return StepOutcome.ABORTED_STALE
    execute(action)                                   # 5 Execute
    if not verify(action):                            # 6 Verify（动作后验证）
        if rollback is not None:
            rollback(action)                          # 7 Rollback
        return StepOutcome.ROLLED_BACK
    return StepOutcome.COMMITTED                       # 7 Commit
# endregion book:ch09-browser-loop
