#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 渲染门禁：检出 GitHub 上会失效的加粗（残留字面 `**`）。

GFM 的 flanking 规则遇到相邻全角标点时 `**加粗**` 可能不生效，在页面上露出字面
`**`。本检查用 cmarkgfm（GitHub 同款渲染器）把每个 .md 渲染为 HTML，剔除代码块后
若仍出现 `**` 即判失败。

用法：python3 tools/check_render.py [仓库根]   退出码 0=全部干净；1=存在失效加粗。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import cmarkgfm
except ImportError:
    print("需要 cmarkgfm：pip install cmarkgfm", file=sys.stderr)
    raise SystemExit(2)

_PRE = re.compile(r"<pre[^>]*>.*?</pre>", re.S)
_CODE = re.compile(r"<code>.*?</code>", re.S)


def stray_bold(md_text: str) -> int:
    html = cmarkgfm.github_flavored_markdown_to_html(md_text)
    html = _PRE.sub("", html)
    html = _CODE.sub("", html)
    return html.count("**")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    bad = []
    checked = 0
    for md in sorted(root.rglob("*.md")):
        if "/.git/" in str(md) or "/__pycache__/" in str(md):
            continue
        checked += 1
        n = stray_bold(md.read_text(encoding="utf-8"))
        if n:
            bad.append((md.relative_to(root), n))
    if bad:
        print(f"✗ 渲染检查失败（{len(bad)} 个文件 / 共 {checked}）：残留字面 ** =失效加粗")
        for f, n in bad:
            print(f"  - {f}: {n} 处")
        return 1
    print(f"✓ 渲染检查通过：{checked} 个 Markdown 无失效加粗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
