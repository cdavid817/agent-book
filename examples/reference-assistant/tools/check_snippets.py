#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片段同步检查器（Task Group 3 · 8.4）。

校验 Markdown 里嵌入的源码片段与真实源文件保持一致——检查式同步，
绝不改写文件（CI 里不产生未提交修改）。

Markdown 标记（放在 ```lang 代码块正上方）：
    <!-- snippet: <repo相对路径>#<anchor> [mode=executable] [verified_by=<路径>] -->

源码标记（region 唯一）：
    # region book:<anchor>
    ...受控片段...
    # endregion book:<anchor>

校验项（8.4）：目标文件存在；region 唯一且存在；Markdown 片段与源码逐字符一致；
片段未越界；同一 (文件,anchor) 不被冲突方式重复嵌入；源码删除 → 失败退出。

用法：python3 examples/reference-assistant/tools/check_snippets.py [仓库根]
退出码 0=全部一致；1=存在漂移/缺失。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SNIPPET_RE = re.compile(
    r"<!--\s*snippet:\s*(?P<path>[^\s#]+)#(?P<anchor>[^\s]+)"
    r"(?P<attrs>[^>]*)-->\s*\n```[A-Za-z0-9_+-]*\n(?P<body>.*?)\n```",
    re.S,
)


def extract_region(src_text: str, anchor: str) -> list[str] | None:
    """返回 region 内的行（不含标记行）；region 不存在返回 None；非唯一抛错。"""
    begin = f"# region book:{anchor}"
    end = f"# endregion book:{anchor}"
    starts = [i for i, l in enumerate(src_text.splitlines()) if l.strip() == begin]
    ends = [i for i, l in enumerate(src_text.splitlines()) if l.strip() == end]
    if len(starts) == 0 or len(ends) == 0:
        return None
    if len(starts) > 1 or len(ends) > 1:
        raise ValueError(f"region '{anchor}' 非唯一（start={len(starts)} end={len(ends)}）")
    lines = src_text.splitlines()
    s, e = starts[0], ends[0]
    if e <= s:
        raise ValueError(f"region '{anchor}' 越界（endregion 在 region 之前）")
    return lines[s + 1:e]


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[3]
    md_files = sorted(root.rglob("*.md"))
    src_cache: dict[Path, str] = {}
    errors: list[str] = []
    checked = 0
    seen: set[tuple[str, str]] = set()

    for md in md_files:
        if "/.git/" in str(md):
            continue
        text = md.read_text(encoding="utf-8")
        for m in SNIPPET_RE.finditer(text):
            checked += 1
            rel, anchor, body = m.group("path"), m.group("anchor"), m.group("body")
            where = f"{md.relative_to(root)} → {rel}#{anchor}"
            key = (rel, anchor)
            if key in seen:
                errors.append(f"[重复嵌入] {where}：同一 region 被多处嵌入")
            seen.add(key)
            src_path = root / rel
            if not src_path.exists():
                errors.append(f"[源缺失] {where}：{rel} 不存在")
                continue
            src_text = src_cache.setdefault(src_path, src_path.read_text(encoding="utf-8"))
            try:
                region = extract_region(src_text, anchor)
            except ValueError as ex:
                errors.append(f"[region 错误] {where}：{ex}")
                continue
            if region is None:
                errors.append(f"[anchor 缺失] {where}：源码无 region '{anchor}'")
                continue
            if body.splitlines() != region:
                errors.append(
                    f"[漂移] {where}：Markdown 片段与源码 region 不一致\n"
                    f"       源码 {len(region)} 行 / Markdown {len(body.splitlines())} 行"
                )

    if errors:
        print(f"✗ 片段检查失败（{len(errors)} 项 / 共检查 {checked} 处）：\n")
        for e in errors:
            print("  -", e)
        return 1
    print(f"✓ 片段检查通过：{checked} 处嵌入与源码一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
