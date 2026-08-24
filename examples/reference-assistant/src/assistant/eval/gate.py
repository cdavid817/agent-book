# -*- coding: utf-8 -*-
"""统计可靠的评测门禁（第 15 章 P1 · 13.6）。

修正“通过率低于基线即 BLOCK”过于敏感的问题：k=3 时 3/3→2/3 只是抽样噪声，
不应阻断合入。改为分层门禁 + 置信区间：
  - deterministic（安全/权限/Schema）：任一失败立即 BLOCK；
  - probabilistic（功能评测）：仅当通过率**置信显著**低于阈值或基线时才 BLOCK；
  - style（主观质量）：只告警，不阻断。

只用标准库（math）；一手来源见 [C-15]（Wilson 1927）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

GateKind = Literal["deterministic", "probabilistic", "style"]
Verdict = Literal["BLOCK", "WARN", "PASS"]

# 常用 z 值：95% → 1.96，99% → 2.576
Z95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score 区间（比正态近似在小样本/极端比例下更稳）。返回 (lo, hi)，clamp 到 [0,1]。"""
    if n <= 0:
        return (0.0, 1.0)
    phat = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def two_proportion_significant(x1: int, n1: int, x2: int, n2: int,
                               z: float = Z95) -> bool:
    """两比例合并 z 检验：基线(x1/n1) 与当前(x2/n2) 差异是否统计显著（双尾）。"""
    if n1 == 0 or n2 == 0:
        return False
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return p1 != p2
    return abs(p1 - p2) / se > z


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    kind: GateKind
    passes: int
    n: int
    threshold: float | None = None       # 该用例要求的最低通过率
    baseline_passes: int | None = None   # 基线成功次数（用于回归显著性）
    baseline_n: int | None = None

    @property
    def rate(self) -> float:
        return self.passes / self.n if self.n else 0.0


# region book:ch15-eval-gate
def classify_case(r: CaseResult, z: float = Z95) -> tuple[Verdict, str]:
    """对单个用例给出裁决与理由。"""
    if r.kind == "deterministic":
        # 确定性用例：任一失败即阻断，不谈概率
        if r.passes < r.n:
            return "BLOCK", f"确定性用例失败 {r.passes}/{r.n}"
        return "PASS", "确定性用例全过"

    lo, hi = wilson_interval(r.passes, r.n, z)

    if r.kind == "style":
        if r.threshold is not None and hi < r.threshold:
            return "WARN", f"主观质量置信偏低（CI 上界 {hi:.2f} < 阈值 {r.threshold})"
        return "PASS", "主观质量未见显著问题"

    # probabilistic：两条阻断条件，均要求“置信显著”
    if r.threshold is not None and hi < r.threshold:
        # 即便乐观估计（CI 上界）也低于阈值 → 确信不达标
        return "BLOCK", f"置信低于阈值：CI 上界 {hi:.2f} < {r.threshold}"
    if (r.baseline_passes is not None and r.baseline_n is not None
            and r.rate < r.baseline_passes / r.baseline_n
            and two_proportion_significant(r.baseline_passes, r.baseline_n,
                                           r.passes, r.n, z)):
        return "BLOCK", (f"相对基线显著回归："
                         f"{r.baseline_passes}/{r.baseline_n} → {r.passes}/{r.n}")
    return "PASS", f"未达显著回归（CI [{lo:.2f}, {hi:.2f}]）"
# endregion book:ch15-eval-gate


def gate(results: list[CaseResult], z: float = Z95) -> dict:
    """分层汇总：任一 BLOCK → 整体 BLOCK；否则有 WARN → WARN；否则 PASS。"""
    rows = [(r.case_id, *classify_case(r, z)) for r in results]
    verdicts = {v for _, v, _ in rows}
    overall: Verdict = "BLOCK" if "BLOCK" in verdicts else (
        "WARN" if "WARN" in verdicts else "PASS")
    return {
        "verdict": overall,
        "blocks": [(cid, why) for cid, v, why in rows if v == "BLOCK"],
        "warns": [(cid, why) for cid, v, why in rows if v == "WARN"],
        "rows": rows,
    }
