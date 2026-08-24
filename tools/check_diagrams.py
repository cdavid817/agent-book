#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图源门禁：D2 架构图必须已渲染为 SVG 并被正文引用（图表规范.md 的纪律）。

GitHub 不内联渲染 D2，只显示已提交的 SVG。故每个 diagrams/d2/<name>.d2 必须：
  1. 存在对应 assets/<name>.svg；
  2. 该 SVG 被至少一个 Markdown 以 ![](.../assets/<name>.svg) 引用；
  3. SVG 内不含 <foreignObject>（含则 GitHub 上不渲染）。
反向：assets/ 下的每个 SVG 也应被引用（检出孤儿产物）。

用法：python3 tools/check_diagrams.py [仓库根]   退出码 0=一致；1=缺失/孤儿/不可渲染。
"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    d2_dir = root / "diagrams" / "d2"
    assets = root / "assets"
    mds = [m for m in root.rglob("*.md")
           if "/.git/" not in str(m) and "/__pycache__/" not in str(m)]
    all_md_text = "\n".join(m.read_text(encoding="utf-8") for m in mds)

    errors = []
    d2_srcs = sorted(d2_dir.glob("*.d2")) if d2_dir.is_dir() else []
    svgs = sorted(assets.glob("*.svg")) if assets.is_dir() else []
    referenced_svgs = set()

    for d2 in d2_srcs:
        svg = assets / (d2.stem + ".svg")
        if not svg.exists():
            errors.append(f"[缺 SVG] {d2.relative_to(root)} 无对应 assets/{d2.stem}.svg（未渲染）")
            continue
        if f"{d2.stem}.svg" not in all_md_text:
            errors.append(f"[未引用] assets/{d2.stem}.svg 未被任何 Markdown 以 ![]() 引用")
        else:
            referenced_svgs.add(svg.name)
        if "<foreignObject" in svg.read_text(encoding="utf-8", errors="ignore"):
            errors.append(f"[不可渲染] assets/{d2.stem}.svg 含 <foreignObject>，GitHub 上不显示")

    for svg in svgs:
        if f"{svg.name}" not in all_md_text and svg.name not in referenced_svgs:
            errors.append(f"[孤儿] assets/{svg.name} 未被任何 Markdown 引用")

    if errors:
        print(f"✗ 图源检查失败（{len(errors)} 项）：")
        for e in errors:
            print("  -", e)
        return 1
    print(f"✓ 图源检查通过：{len(d2_srcs)} 个 D2 源均已渲染、被引用且可在 GitHub 渲染")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
