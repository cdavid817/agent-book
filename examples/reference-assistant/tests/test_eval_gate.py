# -*- coding: utf-8 -*-
"""统计门禁合同测试（第 15 章 P1 · 13.6）。

验证：Wilson 区间正确；小样本噪声不误 BLOCK；大样本真实回归会 BLOCK；
确定性用例任一失败即 BLOCK；分层汇总正确。
"""
from __future__ import annotations

import pytest

from assistant.eval.gate import (
    wilson_interval, two_proportion_significant,
    CaseResult, classify_case, gate,
)


def test_wilson_known_value():
    lo, hi = wilson_interval(8, 10)          # statsmodels: ~ (0.490, 0.950)
    assert 0.47 < lo < 0.51
    assert 0.93 < hi < 0.97
    assert lo < 0.8 < hi                     # 点估计落在区间内


def test_wilson_clamped_and_degenerate():
    lo, hi = wilson_interval(10, 10)         # 全过：上界 clamp 到 1.0
    assert hi == pytest.approx(1.0) and lo < 1.0
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_two_proportion_small_sample_not_significant():
    # 3/3 → 2/3：k=3 的抽样噪声，不显著
    assert two_proportion_significant(3, 3, 2, 3) is False


def test_two_proportion_large_sample_significant():
    # 920/1000 → 850/1000：真实回归，显著
    assert two_proportion_significant(920, 1000, 850, 1000) is True


def test_probabilistic_small_sample_noise_does_not_block():
    # 基线 3/3，现 2/3 —— 不应 BLOCK（正是原逻辑过敏的场景）
    r = CaseResult("noisy", "probabilistic", passes=2, n=3,
                   baseline_passes=3, baseline_n=3)
    v, why = classify_case(r)
    assert v == "PASS", why


def test_probabilistic_real_regression_blocks():
    r = CaseResult("real", "probabilistic", passes=850, n=1000,
                   baseline_passes=920, baseline_n=1000)
    v, why = classify_case(r)
    assert v == "BLOCK", why


def test_probabilistic_confidently_below_threshold_blocks():
    # 现 40/100，阈值 0.9：即便 CI 上界也远低于阈值 → BLOCK
    r = CaseResult("belowbar", "probabilistic", passes=40, n=100, threshold=0.9)
    assert classify_case(r)[0] == "BLOCK"


def test_probabilistic_small_sample_below_threshold_not_block():
    # 现 2/3（阈值 0.9）：CI 上界仍高，样本太小不足以确信不达标 → 不 BLOCK
    r = CaseResult("tiny", "probabilistic", passes=2, n=3, threshold=0.9)
    assert classify_case(r)[0] != "BLOCK"


def test_deterministic_any_failure_blocks_regardless_of_stats():
    r = CaseResult("perm", "deterministic", passes=99, n=100)
    assert classify_case(r)[0] == "BLOCK"     # 确定性用例不谈置信区间
    assert classify_case(CaseResult("perm2", "deterministic", 100, 100))[0] == "PASS"


def test_style_never_blocks_only_warns():
    r = CaseResult("tone", "style", passes=10, n=100, threshold=0.9)
    assert classify_case(r)[0] == "WARN"


def test_gate_layering():
    results = [
        CaseResult("d1", "deterministic", 100, 100),                 # PASS
        CaseResult("p1", "probabilistic", 2, 3, baseline_passes=3, baseline_n=3),  # PASS(噪声)
        CaseResult("s1", "style", 10, 100, threshold=0.9),           # WARN
    ]
    out = gate(results)
    assert out["verdict"] == "WARN"           # 无 BLOCK，有 WARN
    assert out["blocks"] == []

    results.append(CaseResult("d2", "deterministic", 99, 100))       # BLOCK
    out2 = gate(results)
    assert out2["verdict"] == "BLOCK"
    assert any(cid == "d2" for cid, _ in out2["blocks"])
