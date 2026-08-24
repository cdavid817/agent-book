# -*- coding: utf-8 -*-
"""自治级别 → 审批闸门（第 13 章 P1 · 13.4）。

把"人机交互"落成一条可判定的规则：给定**自治级别**（L0–L4）、动作的**风险/可逆性**、
以及是否**落在策略范围内**，产出该动作应当"直接执行 / 逐步确认 / 仅建议 / 拒绝 / 升级人工"。
这是第 13 章 PDP（allow/ask/deny）在"人在环"维度的扩展，也是第 1 章自治光谱的工程化。

设计要点：**读无副作用，任何级别都放行**；副作用动作按级别递进授权，但**不可逆与高危永远
不因级别高而豁免**——L4 也要对不可逆动作确认、对高危或越界升级人工（渐进式授权 + 高危升级）。
"""
from __future__ import annotations

from enum import Enum, IntEnum


class AutonomyLevel(IntEnum):
    L0_ANSWER_ONLY = 0      # 仅回答，不代为行动
    L1_SUGGEST = 1          # 提出动作建议，由人执行
    L2_CONFIRM_EACH = 2     # 逐步确认后执行
    L3_AUTO_IN_POLICY = 3   # 策略范围内自动执行
    L4_BACKGROUND = 4       # 后台长期自治，异常升级


class ActionRisk(Enum):
    READ_ONLY = "read_only"              # 无副作用
    REVERSIBLE_WRITE = "reversible"      # 可撤销的写（有 Undo）
    IRREVERSIBLE = "irreversible"        # 不可逆（退款、发邮件、删除）
    HIGH_RISK = "high_risk"              # 高危（生产变更、资金、权限授予）


class Decision(Enum):
    EXECUTE = "execute"            # 直接执行
    CONFIRM = "confirm"            # 执行前需人工逐项确认（第 13 章 ask）
    SUGGEST_ONLY = "suggest_only"  # 只给建议，由人执行
    REFUSE = "refuse"             # 拒绝代为行动
    ESCALATE = "escalate"         # 升级人工接管 / 坐席 handoff


# region book:ch13-autonomy-gate
def decide(level: AutonomyLevel, risk: ActionRisk, within_policy: bool) -> Decision:
    """自治级别 × 风险 × 是否在策略内 → 审批裁决。"""
    # 读无副作用：任何级别都直接放行（这是 Agent 能回答/工作的前提）
    if risk is ActionRisk.READ_ONLY:
        return Decision.EXECUTE

    # 以下均为有副作用的动作
    if level is AutonomyLevel.L0_ANSWER_ONLY:
        return Decision.REFUSE
    if level is AutonomyLevel.L1_SUGGEST:
        return Decision.SUGGEST_ONLY
    if level is AutonomyLevel.L2_CONFIRM_EACH:
        return Decision.CONFIRM

    if level is AutonomyLevel.L3_AUTO_IN_POLICY:
        # 仅在策略内、且可逆时自动执行；不可逆/高危/越界一律回落到人工确认
        if within_policy and risk is ActionRisk.REVERSIBLE_WRITE:
            return Decision.EXECUTE
        return Decision.CONFIRM

    # L4：后台自治，但高危或越界要升级，不可逆仍需确认
    if not within_policy or risk is ActionRisk.HIGH_RISK:
        return Decision.ESCALATE
    if risk is ActionRisk.IRREVERSIBLE:
        return Decision.CONFIRM
    return Decision.EXECUTE
# endregion book:ch13-autonomy-gate


# 人在环的交互能力清单（UX 契约，供前端与审计对齐；见第 13 章 2.3/2.7）
HITL_AFFORDANCES = (
    "plan_preview",        # 计划预览
    "action_preview",      # 动作预览（含 Diff）
    "evidence_panel",      # 证据面板 + 来源引用
    "uncertainty",         # 不确定性展示
    "undo",                # 撤销（仅对可逆动作有效）
    "pause_resume",        # 暂停 / 恢复
    "cancel",              # 取消
    "plan_edit",           # 计划修正
    "takeover",            # 人工接管 / 坐席 handoff
)
