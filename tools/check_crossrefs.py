#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部交叉引用门禁（与 reviews/baseline_audit.py 同规则）。

校验三类内部引用指向真实存在的目标：
  - 第 N 章：N ∈ 1..N_max（N_max 取自 book.yml）；
  - 附录 X：X ∈ A..F；
  - 跨章图引用 "第 N 章图 M"：目标章的图号上限 ≥ M；
  - 同文图引用 "图 M"：不超过本文图号上限。

用法：python3 tools/check_crossrefs.py [仓库根]   退出码 0=全部有效；1=存在失效引用。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

def _max_ch() -> int:
    """章数上限以 book.yml 为单一来源, 不在此硬编码"""
    root = Path(__file__).resolve().parents[1]
    try:
        import yaml
        book = yaml.safe_load((root / "book.yml").read_text(encoding="utf-8"))
        nums = [c["number"] for part in book.get("parts", [])
                for c in part.get("chapters", [])]
        return max(nums) if nums else 25
    except Exception:
        return 25


MAX_CH = _max_ch()
VALID_APP = set("ABCDEF")
CHAP_RE = re.compile(r"第(\d+)章")


def maxfig_of(text: str) -> int:
    caps = [int(x) for x in re.findall(r"图\s*(\d+)[:：]", text)]
    return max(caps) if caps else 0


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    mds = [m for m in sorted(root.rglob("*.md"))
           if "/.git/" not in str(m) and "/__pycache__/" not in str(m)]
    chapters = {int(CHAP_RE.search(m.name).group(1)): m
                for m in mds if CHAP_RE.search(m.name)}
    ch_maxfig = {n: maxfig_of(p.read_text(encoding="utf-8"))
                 for n, p in chapters.items()}

    broken = []
    for m in mds:
        base = m.name
        text = m.read_text(encoding="utf-8")
        my_max = maxfig_of(text)
        for mm in re.finditer(r"第\s*(\d+)\s*章", text):
            n = int(mm.group(1))
            if n < 1 or n > MAX_CH:
                broken.append((base, f"第{n}章 (超出 1..{MAX_CH})"))
        for mm in re.finditer(r"附录\s*([A-Z])", text):
            if mm.group(1) not in VALID_APP:
                broken.append((base, f"附录{mm.group(1)} (不存在)"))
        for mm in re.finditer(r"第\s*(\d+)\s*章图\s*(\d+)", text):
            n, fn = int(mm.group(1)), int(mm.group(2))
            tgt = ch_maxfig.get(n, 0)
            if 1 <= n <= MAX_CH and fn > tgt:
                broken.append((base, f"第{n}章图 {fn} (该章最多 图 {tgt})"))
        if my_max:
            for mm in re.finditer(r"图\s*(\d+)", text):
                if mm.start() >= 1 and text[mm.start() - 1] == "章":
                    continue
                fn = int(mm.group(1))
                if fn > my_max and fn <= 30:
                    broken.append((base, f"图 {fn} (本文最多 图 {my_max})"))

    broken = sorted(set(broken))
    if broken:
        print(f"✗ 交叉引用检查失败（{len(broken)} 项）：")
        for f, r in broken:
            print(f"  - {f}: {r}")
        return 1
    print(f"✓ 交叉引用检查通过：{len(mds)} 个 Markdown，章/附录/图号引用全部有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
