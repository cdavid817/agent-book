# 《企业级 Agent 从入门到专家》统一验证入口（Task Group 6）。
# 干净克隆后：make setup && make verify
#
# 已实现的是当前仓库真实存在、可通过的门禁。依赖 TG5（sources.yaml）
# 的目标显式标注 pending，不伪造通过。

PY ?= python3
REF := examples/reference-assistant

.PHONY: setup verify test test-contract \
        check-render check-book check-crossrefs check-snippets check-diagrams check-mermaid \
        check-sources check-sources-strict check-links lint typecheck docs-build baseline help

help:
	@echo "make setup           安装锁定依赖并以可编辑方式装入贯穿项目"
	@echo "make verify          运行全部真实门禁（渲染/交叉引用/图源/片段/测试）"
	@echo "make test            运行贯穿项目 pytest（合同 + 消费者）"
	@echo "make check-render    Markdown 渲染门禁（无失效加粗）"
	@echo "make check-book      目录一致性：book.yml=磁盘=README=outline"
	@echo "make check-crossrefs 章/附录/图号内部引用有效"
	@echo "make check-snippets  章节代码片段与源码零漂移"
	@echo "make check-diagrams  D2 图源已渲染、被引用且可在 GitHub 显示"
	@echo "make check-mermaid   Mermaid 块可被 mermaid.js 解析（需 node; 首次先 npm ci）"
	@echo "make check-sources   来源账本一致、协议版本一致（CI 非严格）"
	@echo "make check-links     外链存活检查（定时/发行前跑，非 PR 门禁）"
	@echo "make check-sources-strict  发行前：待确认/过期升级为失败"
	@echo "make baseline        重新生成仓库基线报告（信息性）"

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-ci.txt
	$(PY) -m pip install -e "$(REF)"

# ---- 真实门禁（构成 verify）----
check-render:
	$(PY) tools/check_render.py

check-book:
	$(PY) tools/check_book.py

check-crossrefs:
	$(PY) tools/check_crossrefs.py

check-snippets:
	$(PY) $(REF)/tools/check_snippets.py

check-diagrams:
	$(PY) tools/check_diagrams.py

check-mermaid:
	node tools/check_mermaid.mjs

test-contract test:
	$(PY) -m pytest -q $(REF)

verify: check-render check-book check-crossrefs check-snippets check-diagrams check-sources test
	@echo "✓ verify 全部通过"

# ---- 信息性 / 依赖后续 Task Group（不阻断，不伪造通过）----
baseline:
	$(PY) reviews/baseline_audit.py > reviews/baseline-2026-08-24.md
	@echo "基线报告已刷新：reviews/baseline-2026-08-24.md"

check-sources:
	$(PY) tools/check_sources.py

check-sources-strict:
	$(PY) tools/check_sources.py --strict

check-links:
	$(PY) tools/check_links.py

lint typecheck docs-build:
	@echo "$@: pending（lint/typecheck/docs-build 随 TG6 后续与 TG7 接入）"
