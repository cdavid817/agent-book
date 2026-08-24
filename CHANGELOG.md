# 变更记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本遵循书稿发行里程碑。

## [Unreleased]

### 新增
- 贯穿项目 `examples/reference-assistant/`：统一事件契约包（`contracts/`）+ 合同/消费者测试（TG1）。
- 章节代码片段同步机制 `tools/check_snippets.py` + region/snippet 标记（TG3，首片接入第 12 章）。
- 目录单一来源 `book.yml` + `tools/check_book.py`（TG4）。
- 结构化来源账本 `references/sources.yaml` + `tools/check_sources.py`（TG5）。
- 统一验证入口 `Makefile` + GitHub Actions `verify.yml`（7 门禁）+ 锁定依赖 `requirements-ci.txt`（TG6）。
- 仓库机器基线 `reviews/baseline-2026-08-24.md` + 审计脚本（TG0）。
- 治理文件：许可证（代码 MIT / 内容 CC BY 4.0）、CONTRIBUTING、CODE_OF_CONDUCT、SECURITY、ROADMAP、CITATION、Issue/PR 模板（TG7）。

### 变更
- 第 8 章 MCP 升级到 2026-07-28 无状态协议（移除 initialize 握手、逐请求 `_meta`、`server/discover`、Tasks/MRTR），依据官方 changelog 核验（TG2）。
- 统一第 12/14/16 章事件契约：`turn_end.payload.usage` 嵌套、`tool_call/tool_result` 用 `call_id`，`session_end.status` 取有限枚举。
- 审读报告迁入 `reviews/review-2026-08-23.md`（原样保留历史）。

### 修复
- 第 14 章 OtelBridge 按契约读取 `usage` 嵌套与 `call_id`（此前读扁平字段会丢 token 指标）。
