#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""来源与时效性门禁（Task Group 5 · §10.5）。

references/sources.yaml 是附录 C 的机器可检查版本。本检查校验它与正文一致、
日期合法、协议版本与代码常量一致，并对时效性/待确认给出可机器判定的信号。

硬失败（退出码 1，无论是否 --strict）：
  - 正文引用的 [C-id] 在 sources.yaml 不存在；
  - 附录 C 表中的 [C-id] 未登记进 sources.yaml（反之亦然，orphan）；
  - verified_at / expires_at 非 YYYY-MM-DD；
  - claim_scope 指向不存在的章/附录 id；
  - 协议版本锚点（C-03.version）与第 8 章 PROTOCOL_VERSION 常量不一致。

软信号（默认告警，--strict 下升级为失败；§10.5 “告警或失败”）：
  - 已过期或 30 天内到期的时效性来源；
  - status 非 verified/stable（如 partial：含待确认子项）；
  - 附录 C/D 中的 待确认 / TBD / TODO source 标记。

用法：python3 tools/check_sources.py [--strict] [仓库根]
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CID_RE = re.compile(r"\[(C-\d+)\]")
VALID_SCOPE = {f"ch{n:02d}" for n in range(1, 26)} | \
              {f"appendix-{c}" for c in "abcdef"}


def valid_date(s: str) -> bool:
    if not (isinstance(s, str) and DATE_RE.match(s)):
        return False
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--strict"]
    strict = "--strict" in argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]

    data = yaml.safe_load((root / "references" / "sources.yaml").read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    by_id = {s["id"]: s for s in sources}
    yaml_ids = set(by_id)

    hard: list[str] = []
    warn: list[str] = []

    mds = [m for m in root.rglob("*.md")
           if "/.git/" not in str(m) and "/__pycache__/" not in str(m)]
    appendix_c = root / "附录" / "附录C-参考文献与版本核验.md"

    # 1) 正文 [C-id] 都在 yaml
    cited = set()
    for m in mds:
        for cid in CID_RE.findall(m.read_text(encoding="utf-8")):
            cited.add(cid)
    for cid in sorted(cited):
        if cid not in yaml_ids:
            hard.append(f"[缺来源] 正文引用 {cid} 但 sources.yaml 未登记")

    # 2) 附录 C 表 id ↔ yaml 一致（orphan 双向）
    c_ids = set(CID_RE.findall(appendix_c.read_text(encoding="utf-8"))) if appendix_c.exists() else set()
    # 附录 C 表格里的 id 形如 "| C-01 |"（无方括号），补充抓取
    if appendix_c.exists():
        c_ids |= set(re.findall(r"^\|\s*(C-\d+)\s*\|", appendix_c.read_text(encoding="utf-8"), re.M))
    for cid in sorted(c_ids):
        if cid not in yaml_ids:
            hard.append(f"[附录 C 漂移] 附录 C 有 {cid} 但 sources.yaml 无")
    for cid in sorted(yaml_ids):
        if cid not in c_ids and cid not in cited:
            warn.append(f"[孤儿来源] {cid} 既不在附录 C 表也未被正文引用")

    # 3) 日期格式 + claim_scope 合法 + 过期
    today = dt.date.today()
    for s in sources:
        sid = s["id"]
        if not valid_date(s.get("verified_at", "")):
            hard.append(f"[日期非法] {sid}.verified_at={s.get('verified_at')!r}")
        exp = s.get("expires_at")
        if exp is not None:
            if not valid_date(exp):
                hard.append(f"[日期非法] {sid}.expires_at={exp!r}")
            else:
                d = dt.date.fromisoformat(exp)
                if d < today:
                    warn.append(f"[已过期] {sid} 于 {exp} 过期，需重新核验（§10.4 快照内容）")
                elif (d - today).days <= 30:
                    warn.append(f"[临期] {sid} 将于 {exp} 到期（≤30 天）")
        for sc in s.get("claim_scope", []):
            if sc not in VALID_SCOPE:
                hard.append(f"[scope 非法] {sid}.claim_scope 含未知目标 {sc!r}")
        st = s.get("status")
        if st not in ("verified", "stable"):
            warn.append(f"[状态待清] {sid}.status={st}（如 partial：含待确认子项，发行前处理）")

    # 4) 协议版本锚点：C-03.version == 第 8 章 PROTOCOL_VERSION 常量
    for anchor in data.get("version_anchors", []):
        src = by_id.get(anchor["source_id"])
        f = root / anchor["file"]
        if not (src and f.exists()):
            hard.append(f"[锚点缺失] {anchor}")
            continue
        m = re.search(rf'{re.escape(anchor["constant"])}\s*=\s*"([^"]+)"',
                      f.read_text(encoding="utf-8"))
        if not m:
            hard.append(f"[锚点缺失] 未在 {anchor['file']} 找到 {anchor['constant']}")
        elif m.group(1) != src.get("version"):
            hard.append(f"[版本不一致] {anchor['constant']}={m.group(1)} "
                        f"≠ {src['id']}.version={src.get('version')}")

    # 5) 待确认 / TBD / TODO source（附录 C、D）
    for name in ("附录C-参考文献与版本核验.md", "附录D-常见Agent产品与框架速览.md"):
        p = root / "附录" / name
        if not p.exists():
            continue
        # 只匹配真实的“未决状态标记”，不误伤对治理规则的元描述（如 banner 里的「待确认」）
        marker = re.compile(r"核验状态[:：]\s*待确认|形态待确认|待确认——|TODO source|\bTBD\b")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if marker.search(line):
                warn.append(f"[待确认] {name}:{i} 发行前必须核实或删除")

    # ---- 汇总 ----
    if hard:
        print(f"✗ 来源检查硬失败（{len(hard)} 项）：")
        for e in hard:
            print("  -", e)
    if warn:
        print(f"{'✗' if strict else '⚠'} 来源软信号（{len(warn)} 项，{'strict 视为失败' if strict else '发行前处理'}）：")
        for w in warn:
            print("  -", w)
    if not hard and not warn:
        print(f"✓ 来源检查通过：{len(sources)} 条来源，正文/附录 C/代码常量一致，无过期或待确认")
        return 0
    if hard or (strict and warn):
        return 1
    print(f"✓ 来源硬校验通过：{len(sources)} 条来源一致；另有 {len(warn)} 条软信号待发行前处理")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
