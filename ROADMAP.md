# 路线图

以《优化与补全实施方案》为纲。P0 基础设施（Task Group 0–7）已基本就位，以下为剩余与后续。

## 已完成（P0）

- TG0 仓库机器基线　- TG1 统一事件契约（真实工程 + 测试）　- TG2 MCP 2026 无状态重写
- TG3 片段同步机制（首片接入）　- TG4 book.yml 单一目录来源　- TG5 来源与时效治理
- TG6 统一验证入口 + CI（7 门禁）　- TG7 开源与发行治理

## 进行中 / 剩余子项

- **TG3 增量**：按依赖顺序把更多 `executable` 章节代码抽入 `examples/reference-assistant/` 并加 snippet 标记（contracts → loop → tools/retry → context → hooks/security → MCP → memory/RAG → obs/cost → eval → orchestration → coding-repair）。
- **TG4 §9.5**：一份覆盖 25 章 + 附录 A–F + README/outline/图表规范/book.yml/示例 README 的完整编辑性复核报告。

## 后续（P1 内容补全）

P1 主题在 P0 完成后以独立 PR 实施，每项须含真实源码 + 测试 + 一手来源。详见实施方案第 13 节。

**已完成**：13.6 统计可靠的评测门禁（Wilson 区间 + 分层门禁，gate.py + 测试 + 第 15 章 2.6）。

**待推进**：13.1 MCP/A2A 互操作、13.2 Durable Execution/Agent SRE、13.3 多模态/Browser/Computer Use、13.4 人机交互与自治级别、13.5 安全标准映射、13.7 扩展供应链、13.8 观测增强。

## 发行前门禁

- `make verify`（CI 每 PR）　- `make check-sources-strict`（`待确认`/过期升级为失败）
- 解决 `references/sources.yaml` 中所有 `partial` 状态与附录 D 的 `待确认` 产品条目（核实或删除）。
