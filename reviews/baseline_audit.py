#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task Group 0 · 可重复仓库基线审计。
用法: python3 reviews/baseline_audit.py > reviews/baseline-2026-08-24.md
所有核心统计由本脚本从仓库内容直接生成，不做手工估算。
"""
import os, re, subprocess, sys, collections, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
BASELINE_DATE = "2026-08-24"

def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

def md_files():
    out = []
    for dp, dn, fn in os.walk(ROOT):
        if "/.git" in dp:
            continue
        for f in fn:
            if f.endswith(".md"):
                out.append(os.path.relpath(os.path.join(dp, f), ROOT))
    return sorted(out)

CHAP_RE = re.compile(r"第(\d+)章")
APP_RE = re.compile(r"附录([A-F])")

MDS = md_files()
CHAPTERS = sorted([m for m in MDS if CHAP_RE.search(os.path.basename(m))],
                  key=lambda m: int(CHAP_RE.search(os.path.basename(m)).group(1)))
APPENDICES = sorted([m for m in MDS if os.path.basename(m).startswith("附录附录")
                     or re.match(r"附录[A-F]", os.path.basename(m))])
# appendix files live under 附录/ named 附录X-...
APPENDICES = sorted([m for m in MDS if m.startswith("附录/") and APP_RE.match(os.path.basename(m))])

# ---------- fenced code blocks ----------
FENCE_RE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")
def fences(path):
    """yield (lang, list_of_lines)"""
    inb = False; lang = None; buf = []
    for line in open(path, encoding="utf-8"):
        m = FENCE_RE.match(line.rstrip("\n"))
        if m:
            if not inb:
                inb = True; lang = m.group(1) or "(none)"; buf = []
            else:
                yield lang, buf; inb = False
        elif inb:
            buf.append(line.rstrip("\n"))

lang_counts = collections.Counter()
per_chapter_py = collections.Counter()
exec_candidates = 0
pseudocode_candidates = 0
PLACEHOLDER = re.compile(r"^\s*(\.\.\.|#\s*\.\.\.|# \.\.\.|pass\s*#.*省略)")
PSEUDO_MARK = re.compile(r"伪代码|示意|<[^>]*占位|<\.\.\.>|\bTODO\b|# 省略")
def is_exec_python(buf):
    code = [l for l in buf if l.strip() and not l.strip().startswith("#")]
    if len(code) < 3:
        return False
    txt = "\n".join(buf)
    if PSEUDO_MARK.search(txt):
        return False
    if any(PLACEHOLDER.match(l) for l in buf):
        return False
    if not re.search(r"\b(def|class|import|return|=|await|async)\b", txt):
        return False
    return True

for m in MDS:
    ch = CHAP_RE.search(os.path.basename(m))
    for lang, buf in fences(m):
        lang_counts[lang.lower()] += 1
        if lang.lower() in ("python", "py"):
            if ch:
                per_chapter_py[int(ch.group(1))] += 1
            if is_exec_python(buf):
                exec_candidates += 1
            else:
                pseudocode_candidates += 1
total_fences = sum(lang_counts.values())

# ---------- duplicate contract definitions ----------
CONTRACT_NAMES = ["AgentEvent", "usage", "call_id", "session_id", "Run", "Task", "Turn", "Step"]
def def_sites(name):
    """where the identifier is *defined* as a type or dataclass field/binding."""
    pats = [
        re.compile(r"^\s*class\s+%s\b" % re.escape(name)),
        re.compile(r"^\s*@dataclass"),   # context only
        re.compile(r"^\s*def\s+%s\b" % re.escape(name)),
        re.compile(r"^\s*%s\s*[:=]" % re.escape(name)),   # field / binding
    ]
    sites = []
    for m in CHAPTERS + APPENDICES:
        cur_ch = CHAP_RE.search(os.path.basename(m))
        for lang, buf in fences(m):
            if lang.lower() not in ("python", "py"):
                continue
            for i, l in enumerate(buf):
                if re.match(r"^\s*class\s+%s\b" % re.escape(name), l) or \
                   re.match(r"^\s*def\s+%s\b" % re.escape(name), l):
                    sites.append((os.path.basename(m), l.strip()[:70]))
    return sites

contract_report = {}
for n in CONTRACT_NAMES:
    contract_report[n] = def_sites(n)

# ---------- protocol / version strings ----------
VERSION_PATS = {
    "MCP 协议版本": re.compile(r"20\d\d-\d\d-\d\d"),
    "A2A": re.compile(r"\bA2A\b"),
    "OpenTelemetry/OTel": re.compile(r"OpenTelemetry|OTel|gen_ai\."),
    "Python 版本": re.compile(r"Python\s*[≥>=]{0,2}\s*3\.\d+|python3\.\d+"),
    "模型名": re.compile(r"GPT-\d|Claude|Gemini|Llama|DeepSeek|o\d(?:-mini)?\b"),
    "SDK/版本号": re.compile(r"\bv?\d+\.\d+\.\d+\b"),
}
version_hits = collections.Counter()
version_examples = collections.defaultdict(set)
for m in MDS:
    txt = open(m, encoding="utf-8").read()
    for label, pat in VERSION_PATS.items():
        for mm in pat.findall(txt):
            version_hits[label] += 1
            if len(version_examples[label]) < 8:
                version_examples[label].add(mm if isinstance(mm, str) else str(mm))

# ---------- high-risk phrases ----------
RISK_PATS = ["TODO", "TBD", "待确认", "latest", "当前主流", "最新版", "目前最"]
risk_hits = collections.Counter()
risk_locations = collections.defaultdict(list)
for m in MDS:
    for i, line in enumerate(open(m, encoding="utf-8"), 1):
        for p in RISK_PATS:
            if p in line:
                risk_hits[p] += 1
                if len(risk_locations[p]) < 6:
                    risk_locations[p].append(f"{os.path.basename(m)}:{i}")
total_risk = sum(risk_hits.values())

# ---------- time-sensitive unverified assertions ----------
TIME_MARK = re.compile(r"20\d\d 年|\$\d|美元|价格|定价|/1M|版本|最新|当前|截至|核验")
VERIFIED = re.compile(r"核验于|核验|截至\s*20\d\d|as of|参见附录\s*C|\[C-\d")
unverified = 0
unverified_examples = []
for m in MDS:
    if m.startswith("附录/附录C"):
        continue
    for i, line in enumerate(open(m, encoding="utf-8"), 1):
        if re.search(r"\$\d|/1M|定价|价格为|当前主流|最新版本", line) and not VERIFIED.search(line):
            unverified += 1
            if len(unverified_examples) < 10:
                unverified_examples.append(f"{os.path.basename(m)}:{i}  {line.strip()[:60]}")

# ---------- internal cross-reference validity ----------
MAX_CH = 25
VALID_APP = set("ABCDEF")
broken_refs = []
# chapter number -> max figure caption number in that chapter
def maxfig_of(path):
    caps = [int(x) for x in re.findall(r"图\s*(\d+)[:：]",
                                       open(path, encoding="utf-8").read())]
    return max(caps) if caps else 0
CH_MAXFIG = {int(CHAP_RE.search(os.path.basename(m)).group(1)): maxfig_of(m)
             for m in CHAPTERS}
for m in MDS:
    base = os.path.basename(m)
    lines = open(m, encoding="utf-8").read()
    my_maxfig = maxfig_of(m)
    # chapter refs
    for mm in re.finditer(r"第\s*(\d+)\s*章", lines):
        n = int(mm.group(1))
        if n < 1 or n > MAX_CH:
            broken_refs.append((base, f"第{n}章 (超出 1..{MAX_CH})"))
    # appendix refs
    for mm in re.finditer(r"附录\s*([A-Z])", lines):
        if mm.group(1) not in VALID_APP:
            broken_refs.append((base, f"附录{mm.group(1)} (不存在)"))
    # cross-chapter figure refs: 第 N 章图 M  -> check chapter N has >= M figures
    for mm in re.finditer(r"第\s*(\d+)\s*章图\s*(\d+)", lines):
        n, fn = int(mm.group(1)), int(mm.group(2))
        tgt = CH_MAXFIG.get(n, 0)
        if 1 <= n <= MAX_CH and fn > tgt:
            broken_refs.append((base, f"第{n}章图 {fn} (该章最多 图 {tgt})"))
    # same-file figure refs: 图 M NOT preceded by 章 -> check against this file's max
    if my_maxfig:
        for mm in re.finditer(r"图\s*(\d+)", lines):
            # skip if this match is part of a cross-chapter "章图 M"
            start = mm.start()
            if start >= 1 and lines[start-1] == "章":
                continue
            fn = int(mm.group(1))
            if fn > my_maxfig and fn <= 30:
                broken_refs.append((base, f"图 {fn} (本文最多 图 {my_maxfig})"))
# dedup
broken_refs = sorted(set(broken_refs))

# ---------- external links ----------
ext_links = []
for m in MDS:
    ext_links += re.findall(r"https?://[^\s)\]<>`\"，。）]+", open(m, encoding="utf-8").read())
ext_links_unique = sorted(set(ext_links))

# ---------- README / outline / 审读报告 range drift ----------
def chapters_mentioned(path):
    if not os.path.exists(path):
        return set()
    return set(int(x) for x in CHAP_RE.findall(open(path, encoding="utf-8").read()))
actual_ch = set(int(CHAP_RE.search(os.path.basename(m)).group(1)) for m in CHAPTERS)
readme_ch = chapters_mentioned("README.md")
outline_ch = chapters_mentioned("agent-book-outline.md")
report_ch = chapters_mentioned("reviews/review-2026-08-23.md")

# ---------- existing tests / CI / examples ----------
has_ci = os.path.isdir(".github/workflows")
has_make = os.path.exists("Makefile")
test_files = [p for p in glob.glob("**/test_*.py", recursive=True) if "/.git" not in p]
example_dirs = [d for d in ("examples", "reference-assistant") if os.path.isdir(d)]

# ---------- diagram / asset / script enumeration ----------
d2_srcs = sorted(glob.glob("diagrams/d2/*.d2"))
excalidraw = sorted(glob.glob("diagrams/excalidraw/*"))
svgs = sorted(glob.glob("assets/*.svg"))
scripts = sorted(glob.glob("diagrams/*.sh") + glob.glob("reviews/*.py"))

# =========================== EMIT REPORT ===========================
HEAD = sh("git rev-parse HEAD")
BRANCH = sh("git rev-parse --abbrev-ref HEAD")
STATUS = sh("git status --short")

o = print
o(f"# 仓库基线报告 · {BASELINE_DATE}")
o()
o("> Task Group 0 产物。核心统计由 `reviews/baseline_audit.py` 从仓库内容直接生成，非手工估算。")
o("> 重跑：`python3 reviews/baseline_audit.py > reviews/baseline-2026-08-24.md`")
o()
o("## 0. 机器可读摘要")
o()
o("```text")
o(f"HEAD SHA               {HEAD}")
o(f"分支                    {BRANCH}")
o(f"基线日期                {BASELINE_DATE}")
o(f"Markdown 文件总数        {len(MDS)}")
o(f"章节数                  {len(CHAPTERS)}")
o(f"附录数                  {len(APPENDICES)}")
o(f"代码围栏总数             {total_fences}")
o(f"可执行代码块候选数        {exec_candidates}")
o(f"伪代码候选数             {pseudocode_candidates}")
o(f"失效内部引用数           {len(broken_refs)}")
o(f"外链总数(去重)           {len(ext_links_unique)}")
o(f"失效外链数               未联网核验(需独立网络 pass)")
o(f"未核验时效性断言数        {unverified}")
o(f"高风险表述命中数          {total_risk}")
dup_count = sum(1 for n in CONTRACT_NAMES if len({s[0] for s in contract_report[n]}) > 1)
o(f"重复契约定义数(类/函数)   {dup_count}")
o(f"现有测试入口             {'无' if not test_files else ', '.join(test_files)}")
o(f"现有 CI 状态             {'无 .github/workflows' if not has_ci else '有'}; Makefile: {'有' if has_make else '无'}")
o("```")
o()

o("## 1. 仓库状态 (0.1)")
o()
o("```text")
o(f"HEAD    {HEAD}")
o(f"分支     {BRANCH}")
o("工作树:")
o(STATUS if STATUS else "  (clean)")
o("```")
o()

o("## 2. 文件枚举 (0.2)")
o()
o(f"- Markdown 文件：{len(MDS)}")
o(f"- 章节（第 N 章）：{len(CHAPTERS)} → {sorted(actual_ch)}")
o(f"- 附录：{len(APPENDICES)} → {[os.path.basename(a) for a in APPENDICES]}")
o(f"- D2 图源：{len(d2_srcs)} → {[os.path.basename(x) for x in d2_srcs]}")
o(f"- Excalidraw 说明：{len(excalidraw)} → {[os.path.basename(x) for x in excalidraw]}")
o(f"- 已渲染 SVG：{len(svgs)} → {[os.path.basename(x) for x in svgs]}")
o(f"- 脚本：{len(scripts)} → {[os.path.relpath(x) for x in scripts]}")
o(f"- 顶层文档：README.md, agent-book-outline.md, 图表规范.md, reviews/review-2026-08-23.md")
o()

o("## 3. 代码围栏统计 (0.3)")
o()
o("| 语言 | 数量 |")
o("|---|---|")
for lang, c in lang_counts.most_common():
    o(f"| {lang} | {c} |")
o()
o("Python 围栏按章分布：")
o()
o("| 章 | python 围栏数 |")
o("|---|---|")
for ch in sorted(per_chapter_py):
    o(f"| 第{ch}章 | {per_chapter_py[ch]} |")
o()
o(f"分类口径（启发式）：可执行候选 {exec_candidates}（有 import/def/class、≥3 行有效代码、无 `...` 占位/伪代码标记）；伪代码候选 {pseudocode_candidates}。")
o()

o("## 4. 契约定义查重 (0.4)")
o()
o("对 `AgentEvent / usage / call_id / session_id / Run / Task / Turn / Step` 检索 python 围栏中的**类/函数定义点**：")
o()
o("| 标识符 | 定义点数 | 位置 |")
o("|---|---|---|")
for n in CONTRACT_NAMES:
    sites = contract_report[n]
    loc = "; ".join(f"{f} `{c}`" for f, c in sites) if sites else "—（无显式类/函数定义）"
    o(f"| `{n}` | {len(sites)} | {loc} |")
o()
o("> 注：`usage`/`call_id`/`session_id` 多为事件载荷**字段**而非类定义，其一致性已由第 12 章契约约束（第 14/16 章消费者已对齐，见提交 b143835）。此处只统计类/函数级重复定义。")
o()

o("## 5. 协议与版本字符串 (0.5)")
o()
o("| 类别 | 命中数 | 样例 |")
o("|---|---|---|")
for label in VERSION_PATS:
    ex = ", ".join(sorted(version_examples[label])[:6])
    o(f"| {label} | {version_hits[label]} | {ex} |")
o()

o("## 6. 高风险表述 (0.6)")
o()
o(f"命中总数：{total_risk}")
o()
o("| 表述 | 命中 | 样例位置 |")
o("|---|---|---|")
for p in RISK_PATS:
    if risk_hits[p]:
        o(f"| {p} | {risk_hits[p]} | {', '.join(risk_locations[p])} |")
o()
o("> 人工研判（本轮）：`TODO` 命中绝大多数是正文术语（第 4 章规划谱系\"TODO List 驱动 / TodoWrite\"），非占位残留；`latest` 均为\"不追 latest / 锁版本\"的正向纪律语境。**真正待办的是 6 处 `待确认`**——其中附录 D 的 OhMyOpencode / openclaw / Hermes Agent 三条明确标注\"发行前核实或删除\"，是发布前必须清账的条目。")
o()

o("## 7. 范围一致性 (0.7)")
o()
o("```text")
o(f"实际章节        {sorted(actual_ch)}")
o(f"README 提及      {sorted(readme_ch)}")
o(f"outline 提及     {sorted(outline_ch)}")
o(f"审读报告 提及    {sorted(report_ch)}")
o(f"README 缺漏      {sorted(actual_ch - readme_ch)}")
o(f"outline 缺漏     {sorted(actual_ch - outline_ch)}")
o("```")
o()

o("## 8. 内部交叉引用 (0.8)")
o()
o(f"失效内部引用数：{len(broken_refs)}")
if broken_refs:
    o()
    o("| 文件 | 失效引用 |")
    o("|---|---|")
    for f, r in broken_refs:
        o(f"| {f} | {r} |")
o()
o("> 章号 1..25、附录 A..F、以及\"图 N 引用不超过本文图号上限\"三项规则的启发式检查。")
o()

o("## 9. 外链 (0.8 续)")
o()
o(f"去重外链总数：{len(ext_links_unique)}（失效数需独立联网 pass，本基线未联网核验）")
o()

o("## 10. 隐藏测试 / CI / 示例工程 (0.9)")
o()
o(f"- 独立测试入口：{'无' if not test_files else test_files}")
o(f"- `.github/workflows`：{'无' if not has_ci else '有'}")
o(f"- `Makefile`：{'无' if not has_make else '有'}")
o(f"- 示例工程目录（examples/reference-assistant）：{'无' if not example_dirs else example_dirs}")
o("- 章节 python 代码此前经临时提取到 scratchpad venv 测试（真实 OTel SDK / cmarkgfm / pytest），仓库内尚无常驻测试入口——即 TG3/TG6 的待建项。")
o()

o("## 11. 未核验时效性断言 (样例)")
o()
o(f"启发式命中：{unverified}")
if unverified_examples:
    o("```text")
    for e in unverified_examples:
        o(e)
    o("```")
o()

o("## 12. 结论与后续 Task Group 引用")
o()
o("- 本报告为 TG1–TG7 的共同基线。P0-01（MCP 2026-07-28）与 P0-02（事件契约）已在提交 `b143835`、`3416f64` 修复，基线中的对应条目已反映其后状态。")
o("- 待建：TG3 examples/reference-assistant、TG4 book.yml、TG5 references/sources.yaml、TG6 Makefile+CI、TG7 治理文件。")
o(f"- 重跑本报告即可刷新全部统计（幂等）。")
