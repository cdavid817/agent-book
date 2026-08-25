# -*- coding: utf-8 -*-
"""Browser / Computer Use 安全动作循环合同测试（第 9 章 P1 · 13.3）。"""
from __future__ import annotations

from assistant.browser.loop import (
    Perception, StepOutcome, Observation, Action, run_step,
)


def _mk(observe, *, high_risk=False, allow=True, verify_ok=True,
        drift_after_ground=False):
    """构造一组注入回调；executed/rolled 记录副作用是否发生。"""
    state = {"executed": False, "rolled": False, "page": "v1"}

    def obs():
        return Observation(page_version=state["page"], elements={"btn": 1})

    def ground(o):
        a = Action(kind="click", target="btn", grounded_on=o.page_version,
                   high_risk=high_risk)
        if drift_after_ground:
            state["page"] = "v2"          # 定位后页面漂移
        return a

    def policy_allows(a):
        return allow

    def execute(a):
        state["executed"] = True

    def verify(a):
        return verify_ok

    def rollback(a):
        state["rolled"] = True

    outcome = run_step(observe=obs, ground=ground, policy_allows=policy_allows,
                       execute=execute, verify=verify, rollback=rollback)
    return outcome, state


def test_happy_path_commits():
    outcome, st = _mk(None)
    assert outcome is StepOutcome.COMMITTED
    assert st["executed"] and not st["rolled"]


def test_high_risk_blocked_by_policy():
    outcome, st = _mk(None, high_risk=True, allow=False)
    assert outcome is StepOutcome.BLOCKED
    assert not st["executed"]              # 拒绝后绝不执行


def test_stale_page_aborts_before_execute():
    # 定位后页面漂移 → 过期守卫拦下，不按旧坐标点
    outcome, st = _mk(None, drift_after_ground=True)
    assert outcome is StepOutcome.ABORTED_STALE
    assert not st["executed"]             # 关键：坐标漂移时没有误点


def test_verify_failure_triggers_rollback():
    outcome, st = _mk(None, verify_ok=False)
    assert outcome is StepOutcome.ROLLED_BACK
    assert st["executed"] and st["rolled"]  # 执行了但验证不过，回滚


def test_perception_modes_available():
    assert {p.value for p in Perception} == {"dom", "accessibility_tree", "screenshot"}
