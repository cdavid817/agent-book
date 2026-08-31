#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录单一来源门禁（Task Group 4 · §9.4）。

以 book.yml 为唯一结构来源，校验它与真实文件、README、outline 一致，
并对每章施加固定结构约束。任何漂移 → 退出码 1。

检查项：
  - book.yml 中所有 path 存在；
  - 磁盘上的正式章节全部登记在 book.yml（无孤儿章节、无缺失）；
  - 章节号唯一且从 1 连续；
  - 附录 id/number 唯一；
  - 每章含固定六段（## 1..6.）；
  - 每章图号从 1 连续、每图有图注；
  - README 引用了每章与每附录的路径（目录不漂移）；
  - outline 覆盖每个章节号。

用法：python3 tools/check_book.py [仓库根]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

CHAP_RE = re.compile(r"第\s*(\d+)\s*章")
SIX_SECTIONS = ("1", "2", "3", "4", "5", "6")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors: list[str] = []

    book = yaml.safe_load((root / "book.yml").read_text(encoding="utf-8"))
    parts = book.get("parts", [])
    appendices = book.get("appendices", [])

    # 收集 book.yml 中的章节
    yaml_chapters = []
    for part in parts:
        if not (root / part["path"]).is_dir():
            errors.append(f"[路径缺失] part {part['id']} 目录不存在: {part['path']}")
        for ch in part.get("chapters", []):
            yaml_chapters.append((ch, part))

    # 1) 所有 path 存在
    for ch, _ in yaml_chapters:
        if not (root / ch["path"]).is_file():
            errors.append(f"[路径缺失] {ch['id']} 文件不存在: {ch['path']}")
    for ap in appendices:
        if not (root / ap["path"]).is_file():
            errors.append(f"[路径缺失] {ap['id']} 文件不存在: {ap['path']}")
    for se in (book.get("series") or []):
        if not (root / se["path"]).is_file():
            errors.append(f"[路径缺失] {se['id']} 文件不存在: {se['path']}")

    # 2) 磁盘章节 == book.yml 章节（无孤儿/无缺失）
    disk_ch = {int(CHAP_RE.search(p.name).group(1)): p
               for p in root.rglob("*.md")
               if CHAP_RE.search(p.name) and "/.git/" not in str(p)}
    yaml_numbers = [ch["number"] for ch, _ in yaml_chapters]
    disk_numbers = set(disk_ch)
    missing = disk_numbers - set(yaml_numbers)
    orphan = set(yaml_numbers) - disk_numbers
    for n in sorted(missing):
        errors.append(f"[孤儿章节] 第{n}章在磁盘但未登记于 book.yml")
    for n in sorted(orphan):
        errors.append(f"[虚构章节] book.yml 登记第{n}章但磁盘无对应文件")

    # 3) 章节号唯一且连续 1..N
    if len(yaml_numbers) != len(set(yaml_numbers)):
        errors.append(f"[章号重复] {sorted(yaml_numbers)}")
    if yaml_numbers and sorted(yaml_numbers) != list(range(1, len(yaml_numbers) + 1)):
        errors.append(f"[章号不连续] 期望 1..{len(yaml_numbers)}，实为 {sorted(yaml_numbers)}")

    # 4) 附录 id/number 唯一
    ap_ids = [a["id"] for a in appendices]
    ap_letters = [a.get("number") for a in appendices]
    if len(ap_ids) != len(set(ap_ids)):
        errors.append(f"[附录 id 重复] {ap_ids}")
    if len(ap_letters) != len(set(ap_letters)):
        errors.append(f"[附录 number 重复] {ap_letters}")

    # 5)+6) 每章六段结构 + 图号连续 + 图注存在
    for ch, _ in yaml_chapters:
        fp = root / ch["path"]
        if not fp.is_file():
            continue
        text = fp.read_text(encoding="utf-8")
        present = [n for n in SIX_SECTIONS if re.search(rf"^##\s*{n}\.", text, re.M)]
        if len(present) != 6:
            errors.append(f"[结构缺段] {ch['id']} 六段仅见 {present}")
        caps = [int(x) for x in re.findall(r"图\s*(\d+)\s*(?:（[^）]*）)?\s*[:：]", text)]
        uniq = sorted(set(caps))
        if uniq and uniq != list(range(1, len(uniq) + 1)):
            errors.append(f"[图号不连续] {ch['id']} 图注号 {uniq}")

    # 7) README 引用每章/每附录路径
    readme = (root / "README.md").read_text(encoding="utf-8")
    for ch, _ in yaml_chapters:
        if ch["path"] not in readme:
            errors.append(f"[README 漂移] 缺少 {ch['id']} 的链接: {ch['path']}")
    for ap in appendices:
        if ap["path"] not in readme:
            errors.append(f"[README 漂移] 缺少 {ap['id']} 的链接: {ap['path']}")

    # 8) outline 覆盖每个章节号
    outline_p = root / "agent-book-outline.md"
    if outline_p.is_file():
        outline = outline_p.read_text(encoding="utf-8")
        outline_nums = set(int(x) for x in CHAP_RE.findall(outline))
        for n in yaml_numbers:
            if n not in outline_nums:
                errors.append(f"[outline 漂移] 缺少第{n}章")

    if errors:
        print(f"✗ 目录一致性检查失败（{len(errors)} 项）：")
        for e in errors:
            print("  -", e)
        return 1
    print(f"✓ 目录一致性检查通过：{len(yaml_numbers)} 章 + {len(appendices)} 附录，"
          f"book.yml / 磁盘 / README / outline 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
