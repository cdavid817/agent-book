# 《企业级 Agent 从入门到专家（2026版）》章节大纲

> 主线逻辑：能力递进为主线、专题深潜为支线
> 全书七篇 · 27 章 · 附录 A–P 十六本工具书 · 附贯穿实战项目

---

## 第一篇 认知与基础（入门）

**第 1 章 Agent 是什么**
- 从 Chatbot 到 Agent 的演进（含历史注脚：AutoGPT / BabyAGI 第一波自主 Agent 尝试及其失败教训——无限循环、无验证、上下文爆炸）
- Agentic Loop 的本质
- 基础架构五要素与全书地图：感知 Perception（→ 第 5、9 章）、记忆 Memory（→ 第 10 章）、规划 Planning（→ 第 4 章）、行动 Action（→ 第 7–9 章）、反思 Reflection（→ 第 4 章）
- LLM 能力边界：为什么需要 Agent 补齐（工具、记忆、规划）

**第 2 章 第一个 Agent**
- 手写最小 ReAct Loop（不依赖框架）
- 建立正确的 Agent 心智模型
- 贯穿项目启动：项目骨架搭建

---

## 第二篇 单 Agent 核心机制（原理）

### A. 核心循环（心智模型层）

**第 3 章 Agentic Loop 解剖**
- ReAct 范式：Reason → Act → Observe
- Loop 终止条件设计：max_turns、任务完成判定、预算熔断（反面案例：第 1 章 AutoGPT 的无限循环教训）
- 循环结构选型：单轮 vs 多轮、内循环与外循环（全章四节 = 附录 B.3 Loop Engineering 方法论主体）
- 流式输出与中断恢复

**第 4 章 规划与推理**
- 隐式规划（CoT）vs 显式规划（Plan-then-Execute、TODO List 驱动）vs 解耦规划（ReWOO：一次规划 + 变量串联 + 免观察执行），三者在 token 成本 / 并行度 / 纠错能力上的权衡
- 搜索式推理：ToT（Tree of Thoughts）、多路径采样与投票（self-consistency）
- 反思机制：Self-Refine / Reflexion、批评-重试循环、与 Eval 反馈的结合
- 任务分解策略与重规划触发时机
- Extended Thinking / 推理预算的权衡

### B. 上下文工程（输入层）

**第 5 章 Context Window 管理**
- 上下文构成：System Prompt、历史、工具结果、注入内容的优先级
- 压缩与 Compaction：阈值触发、摘要策略、信息损耗控制
- 上下文污染与中毒：注入攻击的防御

**第 6 章 提示工程、Skills 与 Hooks**
- System Prompt 分层设计：身份、约束、工作流
- Skills 机制：渐进式披露、按需加载、与 Prompt 的边界
- CLAUDE.md / AGENTS.md 项目级上下文文件的设计模式
- Hooks 机制：生命周期钩子点（PreToolUse / PostToolUse / SessionStart / Stop / Notification）
- 三种定制手段选型：Prompt（概率性引导）vs Skills（按需知识）vs Hooks（确定性拦截）
- 典型用例：自动格式化、危险命令拦截、审计日志注入

### C. 能力扩展（行动层）

**第 7 章 工具调用**
- 演进脉络：Prompt 拼 JSON → 原生 Function Calling API → Tool Use，原生支持为何更可靠（参见附录 A.6）
- Function Calling 底层机制：Schema 设计、并行调用、错误重试
- 多轮工具调用协议：tool_use / tool_result 消息配对规则、id 关联与常见出错模式
- 工具设计原则：粒度、幂等性、返回值对 LLM 的友好度
- 工具结果的上下文成本：大结果截断、分页返回策略（与第 5 章交叉）
- 动态工具集：tool search、按需注册

**第 8 章 MCP 协议**
- 协议架构：Host / Client / Server、传输层（stdio / SSE / Streamable HTTP）
- Resources、Tools、Prompts 三原语
- MCP vs 原生工具的取舍、安全边界

**第 9 章 代码执行与环境交互**
- 沙箱执行：容器、权限降级
- 文件系统作为工作区：Coding Agent 的收敛模式
- LSP 集成：让 Agent 拥有 IDE 级代码理解

### D. 记忆与检索（状态层）

**第 10 章 记忆系统**
- 分类：工作记忆 / 情景记忆 / 语义记忆 / 程序记忆
- 两层架构实战：会话内压缩 + SQLite 持久化
- 记忆写入策略：何时记、记什么、遗忘机制

**第 11 章 RAG 与检索**
- Agentic RAG vs 传统 RAG：检索从"前置管线"变成"工具之一"
- 数据入库管线：文档解析与结构化（以 Docling / MinerU / Unstructured 为例）、切分策略
- 混合检索、重排、向量库选型（以 Milvus / Qdrant / Chroma / FAISS 为例）
- RAG 变体：GraphRAG、Multimodal RAG 的适用场景
- Grep / 文件系统检索为何在 Coding 场景反超向量检索

---

## 第三篇 单 Agent 生产工程化（质量层）

**第 12 章 运行时架构**
- Harness 视角：为什么同一模型在不同产品中表现迥异——脚手架质量的决定性作用
- 集成方式对比：PTY 子进程 / SDK / HTTP API（控制力、开销、升级成本）
- 进程生命周期：冷启动、健康检查、优雅退出
- Checkpoint 与状态恢复：shadow git repo 方案
- Agent 事件模型：生命周期事件流设计（事件类型、载荷 Schema、顺序保证）
- 事件消费者：GUI 渲染、可观测性采集、外部系统联动
- 事件与 Span 的关系：事件是第 14 章 OTel Span 的原料

**第 13 章 安全与权限**
- PDP/PEP 架构：策略决策与执行分离（PEP 的执行点之一是第 6 章的 PreToolUse hook）
- 权限分级：自动放行 / 询问 / 拒绝
- Human-in-the-loop 的 UX 设计
- Git Worktree 隔离：变更爆炸半径控制

**第 14 章 可观测性**
- Span 层级设计：Session → Agent → Tool/MCP → Process Exec
- 关键指标：Token 消耗、工具成功率、循环轮次分布
- 指标定义方法论：口径（分子/分母）、聚合维度（per-session / per-turn / per-tool）、SLI/SLO 推导；效果 / 效率 / 行为健康三类分类框架
- 与 OTel 生态对接、trace 回放调试

**第 15 章 评测（Eval）**
- 为什么 Agent Eval 比 LLM Eval 难：非确定性、多步、环境依赖
- Scorer 设计：规则打分 / LLM-as-Judge / 结果验证
- 评测工具生态：Promptfoo / DeepEval / Inspect / RAGAS 定位对比；通用 Agent 基准：WebArena、OSWorld
- 评测指标与监控指标的贯通：Eval 基线定义线上告警阈值、线上 bad case 回流评测集（与第 14 章交叉）
- 沙箱化评测环境（worktree 方案）与回归基线

**第 16 章 成本与性能**
- Token 经济学：Prompt Caching、模型路由（大小模型分工）
- 延迟优化：并行工具调用、投机执行
- 失败经济学：重试预算、降级策略

---

## 第四篇 多 Agent（进阶）

**第 17 章 多 Agent 拓扑分类学**
- Supervisor / Pipeline / Parallel / Hierarchical / Reviewer / Debate / Arena / Swarm
- 各拓扑的适用场景与代价模型

**第 18 章 编排、通信与图执行**
- 任务分解与分派：DELEGATOR / WORKER / GUARD 框架
- Agent 间通信（A2A）
- 上下文交接（Context Handoff）：规范化域模型、Importer/Exporter、损耗报告
- 图执行范式：节点 / 边 / 共享状态、条件路由、循环与回边
- 显式图 vs 隐式循环：声明式编排（LangGraph 式）vs Agentic Loop 自主决策的适用边界
- 图的持久化：断点恢复、人工介入节点（interrupt）

**第 19 章 可行性权衡与工程约束**
- 什么场景该用什么拓扑
- 不可行拓扑的判定：以 PTY 冷启动开销否决 Swarm 为例
- 多 Agent 的可观测性与调试难题

---

## 第五篇 企业落地（差异化）

**第 20 章 部署与选型**
- 私有化部署与模型选型
- 数据安全、合规、审计

**第 21 章 与存量系统集成**
- 消息队列、大数据平台（Spark / Kafka / Flink / HBase / ES）、内部工具链
- 网关、鉴权与流量治理

**第 22 章 组织与流程**
- Spec 驱动开发：OpenSpec 工作流（propose → apply → archive）
- 团队协作模式：人如何与 Agent 分工
- 企业级成本治理：Token 预算、缓存、路由策略落地

---

## 第六篇 Coding Agent（三章，只讲领域特有层，机制回指前文）

**第 23 章 代码库理解与编辑**
- 为什么 Coding 是 Agent 技术的试验田：机制先在此成熟再外溢（篇立意）
- 代码库理解三层：Repo Map / Grep / LSP 分工、大仓分块、探索外包给子 Agent（上下文卫生）
- 项目知识文件内容学：构建命令 / 架构导航 / 风格禁区 / 陷阱清单，三条维护纪律
- 编辑策略对比：whole-file 重写 vs diff/patch vs str_replace 精确替换，各自的失败模式
- 遗留仓考古三步：地图先行、行为快照测试、禁区标注；就近风格纪律

**第 24 章 验证闭环与质量工程**
- 验证闭环：编译 / 测试 / lint 作为天然 reward signal、test-driven repair loop、UI 视觉验证
- 测试基建三适配：分层执行、依赖图增量选测、flaky 隔离（反馈周期是修复环的心率）
- 领域评测：SWE-bench 系的工作原理与局限（对照第 15 章通用 Eval）
- 生成代码供应链卫生：依赖幻觉与 slopsquatting、license 审查、扫描入门禁

**第 25 章 协作形态与交付工程**
- 人机协作四档谱系：内联 / 会话式 / 异步委托 / 并行队列，档位与风险分级联动
- Coding 场景的多 Agent 分工：Reviewer 模式、并行 worktree 开发
- 冲突治理四件套：文件亲和分派、合并预检、冲突回会话、定期 rebase；下游容量核算
- 综合验收：机制总图串联第 3–19 章机制在单一领域中的组合方式

---

## 第七篇 专家视野（前沿与收尾）

**第 26 章 前沿方向**
- Agent 自进化
- Agent 后训练：何时后训练 vs 继续做 Prompt/Context 工程、SFT / LoRA / DPO / GRPO 速览、Tool-use 轨迹数据合成与回流
- Agent OS / 基础设施趋势

**第 27 章 完整实战收尾**
- 贯穿项目的完整交付
- 从 0 到生产级的复盘

---

## 附录 A：LLM 基础 Wiki

> 词条式组织，不按章节叙事；正文遇到相关概念时以"参见附录 A.x"引用。
> 定位：为无 LLM 背景的转型工程师兜底，只讲"对 Agent 使用者意味着什么"，不展开训练与数学细节。

**A.1 模型机制**
- Transformer 直觉理解：注意力在做什么
- KV Cache：为什么长对话越来越贵、Prompt Caching 的原理基础
- 上下文窗口的物理含义与限制来源

**A.2 推理参数**
- temperature / top_p / top_k
- max_tokens、停止序列
- 采样确定性与 Agent 场景下的参数选择

**A.3 Token 体系**
- Tokenization 原理与常见分词器
- 计费逻辑：输入/输出/缓存 token 的价格差异
- 中文 token 效率问题

**A.4 训练谱系**
- 预训练 / SFT / RLHF / RL 的分工
- 对使用者的意义：为什么模型会"讨好"、指令遵循从何而来
- 模型版本迭代对 Agent 行为的影响

**A.5 能力与缺陷**
- 幻觉成因与缓解思路
- 长上下文衰减（lost in the middle）
- 指令遵循的边界、越狱与注入的模型侧根源

**A.6 结构化输出**
- JSON Mode、Constrained Decoding
- Schema 约束与 Function Calling 的关系

**A.7 模型选型速查**
- 能力 / 成本 / 延迟三角
- 开源 vs 闭源、私有化部署可行性速查表

**正文引用点（示例）**
- 第 1 章 → A.1、A.5（LLM 能力边界）
- 第 5 章 → A.1、A.3（Context Window 管理）
- 第 7 章 → A.6（工具调用 Schema）
- 第 16 章 → A.2、A.3、A.7（成本与性能）
- 第 20 章 → A.7（部署与选型）

---

## 附录 B：Agent Engineering 方法论速查

> 定位：方法论速查 + 正文导航。每个词条 = 一句话定义 + 核心原则 + 正文章节映射，不承载正文内容。

**B.1 Prompt Engineering（提示工程）**
- 定义：通过自然语言指令引导模型行为的概率性手段
- 核心原则：分层（身份/约束/工作流）、明确性优于长度、示例驱动、正负例配合
- 正文映射：第 6 章（主体）、第 4 章（规划提示）、附录 A.5（指令遵循边界）

**B.2 Context Engineering（上下文工程）**
- 定义：管理进入模型窗口的信息的构成、优先级与生命周期
- 核心原则：窗口是稀缺资源、按需加载（渐进式披露）、压缩有损需可控、防注入
- 正文映射：第 5 章（主体）、第 6 章（CLAUDE.md / Skills）、第 10 章（记忆）、附录 A.1/A.3

**B.3 Loop Engineering（循环工程）**
- 定义：设计 Agent 的迭代执行结构——何时继续、何时终止、何时升级
- 核心原则：终止条件先于能力设计、预算熔断、内外循环分离、失败可恢复
- 正文映射：第 3 章（主体）、第 15 章（Eval 对循环的度量）、第 16 章（失败经济学）

**B.4 Graph Engineering（图工程）**
- 定义：将多步工作流显式建模为节点与边的执行图，以获得确定性与可恢复性
- 核心原则：显式状态、条件路由、回边有界、断点可恢复、人工介入节点
- 正文映射：第 18 章（主体）、第 17 章（拓扑 = 图的组织形态）、第 12 章（事件与状态持久化）

**B.5 Spec-Driven Development（规格驱动开发）**
- 定义：以结构化规格文档为单一事实源，驱动人与 Agent 协作开发的过程方法论
- 类别注记：B.1–B.4 是运行时工程（Agent 如何执行），SDD 是开发过程方法论（人与 Agent 如何协作）
- 核心原则：先 Spec 后代码、变更即提案（propose → apply → archive）、Spec 是给 Agent 的高质量上下文、可审计的决策记录
- 正文映射：第 22 章（主体：OpenSpec 工作流）、第 6 章（Spec 文件作为项目级上下文）、B.2（Spec 与 Context Engineering 的交叉）

**B.6 Runtime / Harness Engineering（运行时 / 脚手架工程）**
- 定义：模型之外的一切执行脚手架——循环驱动、上下文组装、工具执行、状态管理、事件与拦截的总和
- 核心原则：harness 质量决定同一模型的表现上限、事件驱动、状态可恢复、确定性拦截层（Hooks/PEP）
- 正文映射：第 12 章（主体）、第 3 章（循环驱动）、第 5–9 章（上下文与工具执行）、第 13 章（拦截层）
- 与 B.1–B.4 的关系：四个运行时方法论的物理载体

**六者关系速览**
- Prompt 引导单次生成 → Context 决定模型看见什么 → Loop 决定执行结构 → Graph 把多个 Loop 组织成工作流
- 控制力递增：概率性（Prompt）→ 半确定性（Context/Loop）→ 确定性（Graph、Hooks）
- SDD 在更外层：定义"人给 Agent 什么任务、如何验收"，前四者定义"Agent 如何完成任务"
- Harness 是载体：B.1–B.4 是方法论，Harness 是它们共同的运行载体；比较不同产品即是比较 harness

---

## 附录 C：参考文献与版本核验

> 定位：事实来源账本与出版前核验清单。论文数字、协议/SDK 版本、模型价格和工具生态状态均有原始来源、核验日期与责任规则。

- 原始来源优先，事实与工程观点分开
- 时效性信息必须带核验日期；发行前重检链接、版本与价格
- 正文首次出现量化/协议断言时就近标注 `[C.n]`
- 首批索引：Self-Consistency、ToT、MCP Transports、OTel GenAI、SWE-bench/WebArena/OSWorld

---

## 附：写作约定

**每章固定模板**
1. 场景引入
2. 原理
3. 动手实现（贯穿项目增量）
4. 生产级考量
5. 常见坑
6. 面试高频问题

**贯穿项目**
- 每章代码示例累加到同一项目，最终交付完整系统
- 候选：VaneHub AI 简化版

---

## 附录 G：OpenTelemetry 详解

> 定位：OTel 本身的机制兜底——第 14 章讲"Agent 怎么用"，本附录讲"OTel 是什么"。

- G.1 是什么：观测标准化的收敛点（供应商中立、埋点写一次后端随便换）
- G.2 数据模型：Resource/Scope/Record 三层、四信号职责边界、Span 字段面与 Attribute/Event/Link 选用、Metrics 六仪表与时间性、Exemplar 排障动线
- G.3 API/SDK 分离与信号流水线：Provider/Sampler/Processor/Exporter、OTLP
- G.4 上下文传播：traceparent 四段结构、tracestate、baggage、Propagator 与 Agent 场景的载体清单
- G.5 Collector：receiver→processor→exporter 流水线（图 G-5）、五类组件、部署四档、Head/Tail 采样对比、含持久化队列的配置骨架、端到端信号流（图 G-6）
- G.6 GenAI 与多 Agent 系统的落地方式：完整执行链（图 G-7）、Agent 平台 Span 设计表、核心指标十域、内容采集三级、Telemetry Adapter、桌面应用六条纪律
- G.7 语义约定与稳定性：通用域 stable、GenAI 域 Development、Schema URL、OpenInference 并行生态
- G.8 落地顺序七步与常见误区速查（遥测非事务系统、TraceId 非业务主键、适配层隔离语义漂移）
- G.9 本书的 OTel 用件对照表（读完回正文的导航）

## 附录 H：DeepEval 实战指南

> 定位：DeepEval 完整实战教程（全文收录）——第 15 章讲评测方法论，本附录讲"用 DeepEval 怎么评"（API 快照 4.2.0）。

- H.1–H.5 入门：是什么、为何需要专门评估框架、整体工作模型（Golden→LLMTestCase→Metric、三粒度）、安装与密钥、五分钟第一个评估
- H.6–H.9 基础件：核心数据模型（LLMTestCase/ToolCall/Golden/Dataset、context 与 retrieval_context 之辨）、三种执行方式、Metric 通用配置、指标选择决策
- H.10–H.11 定制指标：G-Eval（criteria/evaluation_steps）、DAG Metric
- H.12–H.15 三条实战线：RAG（五指标+组合分数四情况+组件级）、Agent（轨迹/工具正确性/确定性规则）、Tracing（@observe/Span 指标）、多轮对话（ConversationalTestCase/Simulator）
- H.16–H.19 数据与扩展：数据集与 Golden 管理（六层分层）、Synthesizer 合成数据、自定义 Judge（DeepEvalBaseLLM）、自定义 Metric（BaseMetric）
- H.20–H.25 工程化：并发/缓存/错误处理、CLI 参数、CI/CD 质量门禁（GitHub Actions+分层门禁）、阈值校准与 A/B 实验、成本/性能/数据安全、推荐项目结构
- H.26–H.27 收尾：常见问题排查、落地检查清单
- H.28–H.30 扩展：DeepEval 内置评估器全景、与 SWE-bench 等 Benchmark 的关系、参考资料

## 附录 I：评测与观测平台详解与选型

> 定位：DeepEval/Ragas/Arize Phoenix/Langfuse/MLflow 完整调研报告（全文收录，信息基准 2026-08-27）——第 15 章是选型地图，本附录是逐项地形志。

- I.1–I.2 结论先行与质量闭环定位：指标/测试执行层 vs 数据/可观测/工程平台层
- I.3–I.7 逐项详解：DeepEval（测试框架）、Ragas（RAG 指标与合成数据）、Arize Phoenix（可观测与实验工作台）、Langfuse（工程运营平台）、MLflow（统一 ML/LLM 平台）——各自的定位、数据模型、能力、优劣与适用场景
- I.8–I.10 横向对比（能力/许可证/核心差异）、典型场景选型、推荐组合方案（轻量/生产级/企业统一平台）
- I.11–I.12 通用落地架构建议（推荐架构、DDD/Ports & Adapters、Span 类型、四工具职责）与统一评测模型设计（Case/Run/Result）
- I.13–I.16 分阶段实施路线、风险与治理原则、最终建议、官方资料

## 附录 J：Pi 源码架构全景解析

> 定位：开源 Coding Agent「Pi」（earendil-works/pi）整仓源码解析（全文收录，锁定 main@853a80d）——第六篇讲通用机制，本附录是一个真实实现的完整解剖。

- J.1–J.4 分析基线与全局：阅读方法与结论可信度、项目定位与设计哲学、Monorepo 分层与控制权分配、构建与可复现性
- J.5–J.11 pi-ai 模型层：统一领域对象、无副作用入口、Provider 工厂与兼容矩阵、认证与凭据、协议适配、流式事件、错误分类与重试
- J.12–J.18 pi-agent-core：Agent Loop 双层循环与单轮状态机、工具调用（校验/钩子/并行/终止）、Steering 与 Follow-up、Agent 类与宿主可替换点、Harness、通用会话与后端、搜索扫描
- J.19–J.30 pi-coding-agent：启动与多模式路由、AgentSession 协调器、Runtime 与 Services、JSONL 会话树（分支/Fork/Clone）、上下文压缩与保留尾部、设置与迁移、项目信任与两阶段加载、上下文文件与 Skill、扩展系统、模型注册表、内置文件工具、Bash 与进程取消
- J.31–J.36 界面与远程：TUI 差分渲染与布局输入、交互模式与命令体系、Print/JSON/RPC/SDK 模式、严格 CBOR 协议、Client/Server 与会话租约
- J.37–J.44 基础设施与评估：Telemetry 旁路、SQLite 后端与 FTS、测试与质量门禁、安全模型与提示注入、性能与容量规划、部署与跨平台、可扩展方案（MCP/子 Agent/权限层）、架构权衡与演进路线
- J.45–J.50 附带工具书：文件级源码导读卡片、端到端运行链路、数据模型与事件词典、二次开发蓝图、排障决策树、术语表

## 附录 K：Claude Code 源码架构深度解析

> 定位：Claude Code 还原源码整仓解析（全文收录，社区 source map 还原仓库 pengchengneo/Claude-Code、非官方，审阅基线 b78dd22）——证据按 [源码确认]/[结构推断]/[设计解读]/[改进建议]/[还原风险] 五级标注。

- 重要说明与阅读导航：还原仓库的可信边界、静态审阅方法、三条阅读路径
- 篇一（K.1–K.9）基线与全景：证据方法、还原机制、技术栈、设计原则、分层架构、目录地图、Bootstrap、CLI 多模式、REPL 与 Ink
- 篇二（K.10–K.24）会话内核：QueryEngine、query() 异步生成器与 Agent Loop、消息模型与事件流、工具系统契约、权限与沙箱、Provider 适配
- 篇三（K.25–K.34）上下文与恢复：压缩、记忆、Transcript、会话恢复
- 篇四（K.35–K.52）扩展编排：Task/Agent/Coordinator 多 Agent、MCP、Plugin、Skill、Bridge 远程、重构路线图
- 篇五/篇六（K.53–K.55）参考手册：50 个 Tool 条目（含证据等级）、87 个命令子目录与注册体系
- 篇七（K.56–K.63）源码导航与维护：模块地图、服务层地图、术语表、验收清单、架构决策与权衡、风险登记表、证据索引、总结

## 附录 L：Codex 源码架构深度解析

> 定位：OpenAI Codex（openai/codex，Rust Workspace）整仓源码解析（全文收录，快照锁定 main@0ae94fd）——源码事实/设计解释/演进推断三级证据；与附录 J/K 三套 Coding Agent 运行时互为对照。

- 篇一（L.1–L.4）全局认识：项目定位（本地 Agent 运行时）、Thread/Turn/Item 三名词、共享内核与宿主适配器、Rust 迁移动因、工作区全景与总体数据流
- 篇二（L.5–L.12）入口与会话控制：MultitoolCli、TUI、Exec、App Server、Protocol、ThreadManager、Session、Agent Loop
- 篇三（L.13–L.22）模型与工具执行：ModelClient、Prompt 与压缩、ToolRouter/Registry/Orchestrator、Shell、Patch、Code Mode
- 篇四（L.23–L.28）安全纵深：PermissionProfile、审批策略、Guardian、跨平台 Sandbox、Network Proxy、威胁模型
- 篇五（L.29–L.35）扩展与多 Agent：MCP、Skill、Plugin、Hook、Extension API、AgentControl 与角色协作
- 篇六（L.36–L.41）持久化与一致性：ThreadStore、JSONL rollout、SQLite 派生索引、恢复/分叉/归档与故障矩阵
- 篇七（L.42–L.49）配置认证与观测：Config、Profile、Auth、Model Provider、Realtime、OTEL、Analytics
- 篇八（L.50–L.58）测试与工程治理：测试金字塔、CI、静态检查、资源生命周期、发布与贡献模式
- 篇九（L.59）逐 crate 源码导读：覆盖固定提交全部 workspace member
- 篇十（L.60）端到端场景与故障推演：29 条控制流/安全流/数据流联合场景
- 篇十一（L.61–L.66）收尾：结论、阅读路线、二次开发原则、关键源码链接、术语表、质量门禁

## 附录 M：主流 Coding Agent 系统全景

> 定位：Coding Agent 赛道全景调研（全文收录，信息基准 2026-08-30）——附录 D 管全品类速览与定位法，本附录深潜这一个赛道；名单会过期，四层分类与九维度框架不过期。

- M.1–M.3 框架：核心结论（四层市场格局）、易混产品辨析、标准系统架构（对第 12 章六大件）
- M.4–M.10 七类盘点：商业系统、开源系统与 Harness、国内系统、云端异步、多 Agent 工厂、代码审查 Agent、AI 应用构建平台
- M.11 能力九维度：Context Engine / Harness / ACI / 执行环境 / 验证 / 记忆与技能 / 多 Agent 编排 / 安全治理 / 可观测与评估
- M.12–M.13 标准与评测：MCP、ACP、AGENTS.md；评测体系（对第 15/24 章）
- M.14–M.17 趋势、场景选型、统一多 Agent 平台参考架构、最终判断

## 附录 N：主流 Agent 可观测系统全景

> 定位：Agent 可观测赛道全景调研（全文收录，信息基准 2026-08）——第 14 章讲方法、附录 G 讲 OTel 机制、附录 I 深评五家平台，本附录是赛道地图；名单会过期，六类问题框架与成熟度模型不过期。

- N.1–N.2 框架：整体技术栈、核心数据模型与对象边界（对第 14 章四层 Span）
- N.3 标准与埋点生态：OTel GenAI SemConv、OpenInference、OpenLLMetry、Agent Spec Tracing
- N.4–N.8 五路平台盘点：开源自托管（Langfuse/Phoenix/MLflow/OpenLIT/AgentOps/Helicone/TruLens）、商业专用（LangSmith/Weave/Braintrust/Arize AX）、传统 APM（Datadog/New Relic/Dynatrace/Splunk/通用后端）、云厂商原生、框架原生
- N.9–N.10 选型与指标体系（对第 14 章三类指标框架）
- N.11–N.14 生产参考架构（对附录 G.5）、成熟度模型、未来方向、总结

## 附录 O：主流 Agent 评估系统全景

> 定位：Agent 评估赛道全景调研·评估指标增强版（全文收录，v1.2，信息基准 2026-08-30）——第 15 章讲方法论、附录 H 上手 DeepEval、附录 I 深评五家平台，本附录是赛道地图；名单会过期，四类产品框架与指标分层口径不过期。

- O.1–O.3 框架：核心结论、四类产品（持续评估平台/代码级框架/云厂商/Benchmark）、评估总体架构
- O.4 评估对象与指标清单（增强版主体）：十八类核心维度；结果/轨迹/工具/记忆/安全六大评估域；指标分层与统一口径；业务价值、意图约束、计划轨迹、工具与 MCP、RAG、记忆、多轮体验、可靠性、性能成本、安全 Guardrail、可观测审计逐域指标；分类型专项指标；评估器与 Judge 质量指标
- O.5–O.8 盘点：持续评估平台、代码级评估框架、云厂商评估系统、主流 Agent Benchmark
- O.9–O.12 体系：Benchmark/框架/平台关系、多 Agent 系统评估、通用平台评估架构、工程闭环
- O.13–O.16 落地：指标设计与发布门禁（对第 15 章）、选型建议、发展趋势、总结

## 附录 P：主流 Agent Memory 系统全景

> 定位：Agent 记忆赛道全景调研（全文收录，信息基准 2026-08）——第 10 章讲记忆机制与本书实现，本附录是赛道地图；名单会过期，认知分类与数据模型口径不过期。

- P.1–P.3 框架：核心结论、定义与边界（Memory ≠ Context ≠ RAG ≠ Checkpoint，对附录 E.1）、认知分类（对第 10 章 CoALA 四分类）
- P.4–P.5 架构与数据模型：写入/检索链路、生命周期、MemoryRecord、三时间字段、作用域、更新操作
- P.6–P.9 盘点：独立系统十二家（Mem0/Zep/Graphiti/Letta/Hindsight/LangMem/Cognee/Supermemory/Backboard/Memobase/Redis/MemOS）、框架原生、云厂商、Coding Agent 文件型记忆（对第 6/23 章）
- P.10–P.11 研究与评测：研究型演进路线、Benchmark 与排行榜解读（对附录 O）
- P.12–P.15 落地：企业参考架构（对第 10 章远程记忆数据库）、选型方法、未来方向、总结
