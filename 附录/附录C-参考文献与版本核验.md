# 附录 C：参考文献与版本核验

> 定位：本附录是全书的**事实来源账本**。它不替代正文解释，而是让论文结论、协议语义、模型价格与工具版本都能被读者复查。凡带有量化结果、版本号、"当前/已废弃/主流"等时效性表述的正文，均应在首次出现处标 `[C-01]`，并在此处登记来源与核验日期。

---

## C.1 使用规则

1. **原始来源优先**：论文结论链接论文或正式 proceedings；协议链接规范；价格与产品能力链接供应商官方文档。二手文章只能帮助理解，不能作为事实唯一来源。
2. **事实与观点分开**：实验数字、规范要求、产品状态必须有来源；工程判断可署名为“本书建议”或“团队经验”，不得伪装成行业定律。
3. **时效性信息必须带日期**：模型名、价格、MCP/OTel 语义约定、工具生态状态，均写明“核验于 YYYY-MM-DD”；过期后保留历史记录，但正文改为最新结论或“以官方页面为准”。
4. **引用应贴近断言**：首次出现量化结论时就标注，例如“GSM8K +17.9pp [C-01]”，不要只在章末堆链接。
5. **发布门禁**：每次发行前执行一次 C.3 的核验清单；链接失效、版本漂移或无来源数字不得进入发布稿。

## C.2 核心来源索引

| 编号 | 支撑的正文主题 | 原始来源 | 核验日期 |
|---|---|---|---|
| C-01 | 第 4 章 Self-Consistency、GSM8K 增益 | Wang et al., *Self-Consistency Improves Chain of Thought Reasoning in Language Models*, ICLR 2023：https://openreview.net/pdf?id=1PL1NIMMrw | 2026-08-23 |
| C-02 | 第 4 章 Tree of Thoughts、Game of 24 结果 | Yao et al., *Tree of Thoughts: Deliberate Problem Solving with Large Language Models*, NeurIPS 2023：https://proceedings.neurips.cc/paper_files/paper/2023/file/271db9922b8d1f4dd7aaef84ed5ac703-Paper-Conference.pdf | 2026-08-23 |
| C-03 | 第 8 章 MCP 传输层与 HTTP+SSE 兼容状态 | Model Context Protocol, *Transports*：https://modelcontextprotocol.io/specification/2025-06-18/basic/transports | 2026-08-23 |
| C-04 | 第 14 章 GenAI trace/metric 属性 | OpenTelemetry, *GenAI semantic conventions*：https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ | 2026-08-23 |
| C-05 | 第 15、23 章 Agent/Coding Agent 公共基准 | SWE-bench：https://www.swebench.com/；WebArena：https://webarena.dev/；OSWorld：https://os-world.github.io/ | 2026-08-23 |

> **阅读提示**：C-01/C-02 中的数字仅在各自论文的模型、提示和评测设置下成立，不能外推为生产收益承诺；C-03/C-04 为会演进的规范，正文实现必须 pin 具体版本。

## C.3 出版前版本核验清单

| 类别 | 核验项 | 处理规则 |
|---|---|---|
| 协议/SDK | MCP 版本、传输层弃用状态、OTel 语义约定与 SDK 版本 | 在正文和代码注释中同时更新；不兼容时给迁移说明。 |
| 模型/价格 | 模型标识、上下文窗口、输入/输出/缓存价格、地区可用性 | 价格表只作为“示例配置”；真实数值附供应商链接与日期。 |
| 工具生态 | 框架、Server、评测工具是否仍维护 | 标注版本范围；不可复现的工具改为历史案例。 |
| 论文/基准 | 链接、数据集版本、污染/许可说明 | 数字注明实验设置；链接失效时换正式归档。 |
| 法规/合规 | 适用法域、生效日期、内部政策版本 | 仅给工程检查点，不替代法务意见。 |

## C.4 章节映射

- 第 4 章：C-01、C-02
- 第 8 章：C-03
- 第 14 章：C-04
- 第 15、23 章：C-05
- 第 16、20、24 章：按发布时的供应商/标准官方页面增补时效性条目

---

**维护责任**：每个时效性条目应有 owner；正文变更与本附录索引在同一 PR/提案内评审。引用账本本身也进入第 15 章的文档回归：检查链接可访问、核验日期未超过发行策略允许的期限。
