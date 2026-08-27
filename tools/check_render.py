#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 渲染门禁：检出 GitHub 上会失效的加粗（残留字面 `**`）与意外删除线（单波浪号配对）。

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


def render_issues(md_text: str) -> tuple[int, int]:
    """返回 (失效加粗数, 意外删除线数)。

    删除线：GFM 的 strikethrough 扩展接受**单个**波浪号做定界——正文里两个
    范围写法（top-50~100 … top-5~10）会配对成删除线，把中间整段划掉。
    本书从不使用删除线，渲染产物出现 <del> 即为事故；范围一律用 – 连接。
    """
    html = cmarkgfm.github_flavored_markdown_to_html(md_text)
    html = _PRE.sub("", html)
    html = _CODE.sub("", html)
    return html.count("**"), html.count("<del>")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    bad = []
    checked = 0
    for md in sorted(root.rglob("*.md")):
        if "/.git/" in str(md) or "/__pycache__/" in str(md):
            continue
        checked += 1
        bold, strike = render_issues(md.read_text(encoding="utf-8"))
        if bold or strike:
            bad.append((md.relative_to(root), bold, strike))
    if bad:
        print(f"✗ 渲染检查失败（{len(bad)} 个文件 / 共 {checked}）："
              f"残留字面 ** =失效加粗；<del> =波浪号配对成意外删除线")
        for f, bold, strike in bad:
            parts = []
            if bold:
                parts.append(f"失效加粗 {bold} 处")
            if strike:
                parts.append(f"意外删除线 {strike} 处（范围写法用 – 不用 ~）")
            print(f"  - {f}: " + "；".join(parts))
        return 1
    print(f"✓ 渲染检查通过：{checked} 个 Markdown 无失效加粗、无意外删除线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
