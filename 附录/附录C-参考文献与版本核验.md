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
| C-03 | 第 8 章 MCP 2026-07-28 无状态协议（移除 initialize 握手/Mcp-Session-Id）、传输层与废弃状态 | Model Context Protocol, *2026-07-28 Specification — Key Changes*：https://modelcontextprotocol.io/specification/2026-07-28/changelog （传输层见 .../basic/transports/streamable-http） | 2026-08-24 |
| C-04 | 第 14 章 GenAI trace/metric 属性；语义约定稳定级（Development）与消息内容采集默认关闭 | OpenTelemetry, *GenAI semantic conventions*（已迁至专门仓库；内容采集 opt-in，默认关闭）：https://opentelemetry.io/docs/specs/semconv/gen-ai/ | 2026-08-24 |
| C-05 | 第 15、23 章 Agent/Coding Agent 公共基准 | SWE-bench：https://www.swebench.com/；WebArena：https://webarena.dev/；OSWorld：https://os-world.github.io/ | 2026-08-23 |
| C-08 | 第 5 章 / 附录 A.5 lost-in-the-middle、中部召回跌幅 | Liu et al., *Lost in the Middle: How Language Models Use Long Contexts*, TACL 2024（arXiv 2023）：https://arxiv.org/abs/2307.03172 | 2026-08-23 |
| C-09 | 附录 F ReAct 范式原图 | Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models*, ICLR 2023：https://arxiv.org/abs/2210.03629 | 2026-08-23 |
| C-10 | 附录 F 思维链（CoT）原图 | Wei et al., *Chain-of-Thought Prompting Elicits Reasoning in Large Language Models*, NeurIPS 2022：https://arxiv.org/abs/2201.11903 | 2026-08-23 |
| C-11 | 附录 F Reflexion 原图 | Shinn et al., *Reflexion: Language Agents with Verbal Reinforcement Learning*, NeurIPS 2023：https://arxiv.org/abs/2303.11366 | 2026-08-23 |
| C-12 | 附录 F ReWOO 原图 | Xu et al., *ReWOO: Decoupling Reasoning from Observations for Efficient Augmented Language Models*：https://arxiv.org/abs/2305.18323 | 2026-08-23 |
| C-13 | 附录 F Transformer/注意力原图 | Vaswani et al., *Attention Is All You Need*, NeurIPS 2017：https://arxiv.org/abs/1706.03762 | 2026-08-23 |
| C-14 | 附录 F CoALA 记忆分类原图 | Sumers et al., *Cognitive Architectures for Language Agents*, TMLR 2024：https://arxiv.org/abs/2309.02427 | 2026-08-23 |
| C-15 | 第 15 章 通过率置信区间（Wilson score）与分层门禁 | Wilson, E. B., *Probable Inference, the Law of Succession, and Statistical Inference*, JASA 1927：https://doi.org/10.1080/01621459.1927.10502953 | 2026-08-24 |
| C-16 | 第 13 章 Agentic 风险清单与威胁模型 | OWASP GenAI Security Project, *Top 10 for Agentic Applications*（2025-12）与 *Agentic AI Threats & Mitigations*（2025-02）：https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/ | 2026-08-24 |
| C-17 | 第 13 章 AI 风险治理框架 | NIST, *AI Risk Management Framework 1.0*（AI 100-1）：https://doi.org/10.6028/NIST.AI.100-1 | 2026-08-24 |
| C-18 | 第 13 章 生成式 AI 专项动作 | NIST, *Generative AI Profile*（AI 600-1）：https://doi.org/10.6028/NIST.AI.600-1 | 2026-08-24 |
| C-19 | 第 13 章 攻击者战术词典（红队） | MITRE, *ATLAS*：https://atlas.mitre.org/ | 2026-08-24 |
| C-20 | 第 12 章 Saga 补偿（长事务/跨进程恰好一次） | Garcia-Molina & Salem, *Sagas*, ACM SIGMOD 1987：https://doi.org/10.1145/38713.38742 | 2026-08-24 |
| C-21 | 第 18 章 A2A 协议（Agent Card / Task 生命周期 / 跨组织互操作） | A2A Project (Linux Foundation), *Agent2Agent Protocol Specification*：https://a2a-protocol.org/latest/specification/ | 2026-08-24 |
| C-06 | 附录 D 全部产品/框架的名称、归属与成熟度状态 | 各产品供应商官方页面与开源仓库（发行前逐项复核，名单半衰期 6–12 个月） | 2026-08-23 |
| C-07 | 附录 D.7/D.8/D.9/D.10 社区与新兴项目（opencode / OhMyOpencode / Superpowers / codex / openclaw / DeepSeek Harness / Hermes Agent / Vibe Coding / D.10 分类速览全部条目 等） | 各项目开源仓库/官方页面；**标注"待确认"者发行前必须核实真实形态与存续，无法确证即删除**——不进发布稿 | 2026-08-23 |

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
- 第 5 章 / 附录 A.5：C-08
- 附录 F（经典图版索引）：C-09（ReAct）、C-10（CoT）、C-11（Reflexion）、C-12（ReWOO）、C-13（Transformer）、C-14（CoALA）；另复用 C-01（Self-Consistency）、C-02（ToT）、C-08（lost-in-the-middle）、C-03（MCP）、C-05（SWE-bench）
- 第 15、23 章：C-05；第 15 章另加 C-15（评测统计门禁）
- 第 13 章：C-16（OWASP Agentic）、C-17（NIST AI RMF）、C-18（NIST GenAI Profile）、C-19（MITRE ATLAS）
- 第 12 章：C-20（Saga 补偿）
- 第 18 章：C-21（A2A 协议）
- 附录 D：C-06（D.1–D.6）、C-07（D.7/D.8/D.9/D.10 社区与新兴项目及分类速览，含"待确认"条目）
- 第 16、20、24 章：按发布时的供应商/标准官方页面增补时效性条目

---

**维护责任**：每个时效性条目应有 owner；正文变更与本附录索引在同一 PR/提案内评审。引用账本本身也进入第 15 章的文档回归：检查链接可访问、核验日期未超过发行策略允许的期限。
