# -*- coding: utf-8 -*-
"""自治级别 → 审批闸门合同测试（第 13 章 P1 · 13.4）。"""
from __future__ import annotations

from assistant.hitl.autonomy import (
    AutonomyLevel as L, ActionRisk as R, Decision as D, decide, HITL_AFFORDANCES,
)


def test_read_only_always_executes():
    for lvl in L:
        assert decide(lvl, R.READ_ONLY, within_policy=False) is D.EXECUTE


def test_l0_refuses_side_effects():
    assert decide(L.L0_ANSWER_ONLY, R.REVERSIBLE_WRITE, True) is D.REFUSE
    assert decide(L.L0_ANSWER_ONLY, R.HIGH_RISK, True) is D.REFUSE


def test_l1_only_suggests():
    assert decide(L.L1_SUGGEST, R.REVERSIBLE_WRITE, True) is D.SUGGEST_ONLY
    assert decide(L.L1_SUGGEST, R.IRREVERSIBLE, True) is D.SUGGEST_ONLY


def test_l2_confirms_every_side_effect():
    assert decide(L.L2_CONFIRM_EACH, R.REVERSIBLE_WRITE, True) is D.CONFIRM
    assert decide(L.L2_CONFIRM_EACH, R.HIGH_RISK, True) is D.CONFIRM


def test_l3_auto_only_reversible_in_policy():
    assert decide(L.L3_AUTO_IN_POLICY, R.REVERSIBLE_WRITE, within_policy=True) is D.EXECUTE
    # 不可逆 / 高危 / 越界都回落到人工确认
    assert decide(L.L3_AUTO_IN_POLICY, R.IRREVERSIBLE, True) is D.CONFIRM
    assert decide(L.L3_AUTO_IN_POLICY, R.HIGH_RISK, True) is D.CONFIRM
    assert decide(L.L3_AUTO_IN_POLICY, R.REVERSIBLE_WRITE, within_policy=False) is D.CONFIRM


def test_l4_background_with_escalation():
    assert decide(L.L4_BACKGROUND, R.REVERSIBLE_WRITE, within_policy=True) is D.EXECUTE
    assert decide(L.L4_BACKGROUND, R.IRREVERSIBLE, True) is D.CONFIRM     # 不可逆仍确认
    assert decide(L.L4_BACKGROUND, R.HIGH_RISK, True) is D.ESCALATE       # 高危升级
    assert decide(L.L4_BACKGROUND, R.REVERSIBLE_WRITE, within_policy=False) is D.ESCALATE  # 越界升级


def test_irreversible_never_auto_executes_at_any_level():
    # 关键安全不变式：不可逆动作在任何级别都不会被 EXECUTE（渐进式授权的底线）
    for lvl in L:
        assert decide(lvl, R.IRREVERSIBLE, within_policy=True) is not D.EXECUTE


def test_higher_level_never_more_restrictive_for_reversible_in_policy():
    # 单调性抽查：可逆且在策略内时，级别越高越不需要人工介入
    order = {D.REFUSE: 0, D.SUGGEST_ONLY: 1, D.CONFIRM: 2, D.ESCALATE: 2, D.EXECUTE: 3}
    seq = [order[decide(lvl, R.REVERSIBLE_WRITE, True)] for lvl in L]
    assert seq == sorted(seq)


def test_affordances_present():
    for a in ("plan_preview", "action_preview", "undo", "cancel", "takeover"):
        assert a in HITL_AFFORDANCES
