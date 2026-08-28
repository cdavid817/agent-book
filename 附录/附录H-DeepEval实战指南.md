# 附录 H：DeepEval 实战指南

> 定位：**DeepEval 的上手工具书**。正文第 15 章讲评测方法论（五层指标体系、回流闭环、框架与平台选型），本附录讲"DeepEval 具体怎么用"——工作模型、数据模型、指标配置、RAG/Agent/多轮三条实战线、自定义扩展与 CI/CD 工程化，供选定 DeepEval 后照着落地。API 快照为 **DeepEval 4.2.0**（2026-08 验证），升级前先读 Release Notes（入口见 [C-31]）；RAG 指标的方法论口径见第 11 章 2.8，评测体系全貌见第 15 章。

---

## H.1 工作模型：从 Golden 到质量门禁

DeepEval 的心智模型是"**把 LLM 评测写成 pytest 单测**"。四个核心对象串成一条流水线：

```text
Golden（题目：input + 期望，不含实际输出）
    ↓ 喂给你的真实系统运行
LLMTestCase（试卷：input + actual_output + 各类上下文）
    ↓ 交给一组 Metric 打分
Metric（阅卷：score ∈ [0,1] + reason，score ≥ threshold 才通过）
    ↓ 汇入
Quality Gate（deepeval test run 的返回码 → CI 阻断发布）
```

评测有**三个粒度**，对应第 15 章"评什么"的三层：

| 粒度 | 判断什么 | 载体 |
|---|---|---|
| 端到端（End-to-End） | 最终输出对不对 | `LLMTestCase` + 结果指标 |
| 轨迹（Trajectory） | 执行过程走没走对（调了哪些工具、顺序） | `tools_called` / Trace + 轨迹指标 |
| 组件级（Component-Level） | 哪个环节坏了（Retriever/Tool/LLM/子 Agent） | `@observe` Trace 上的 Span 指标 |

**先端到端、后组件级**是标准演进顺序：端到端告诉你"坏了"，组件级告诉你"坏在哪"——与第 14 章 Trace 的排障动线同构。

## H.2 数据模型速查

单轮用例 `LLMTestCase` 的字段面（不同指标要求的字段不同，缺了会报错或跳过）：

| 字段 | 含义 | 谁需要 |
|---|---|---|
| `input` | 用户输入 | 几乎所有指标 |
| `actual_output` | 系统实际输出 | 几乎所有指标 |
| `expected_output` | 人工期望答案（Ground Truth） | Correctness 类、Contextual Recall |
| `context` | **理想**的事实依据（静态 Ground Truth） | Hallucination |
| `retrieval_context` | 检索器**实际**返回的内容 | RAG 五指标（Faithfulness 等） |
| `tools_called` / `expected_tools` | 实际/期望的工具调用（`ToolCall` 列表） | Tool Correctness |
| `token_cost` / `completion_time` | 成本与延迟 | 资源回归门禁 |

最易混的一对：`context` 是"**应该**依据什么"（人工整理、静态），`retrieval_context` 是"**实际**检索到什么"（系统产出、随检索器变化）。RAG 评测用后者——评的就是检索器本身；混用会让 Faithfulness 失去归因意义。

`Golden` 是"没跑之前的用例"（`input` + `expected_output` + 可选 `context`），`EvaluationDataset` 装一组 Golden，支持 JSONL 存取（`save_as`）与版本化——数据集进 Git、带版本或 Hash，是复现性的底座。

## H.3 三种执行方式

| 方式 | 形态 | 适用 |
|---|---|---|
| `assert_test(test_case, metrics)` | 写在 pytest 测试函数里，`deepeval test run tests/evals` 执行 | CI 门禁（返回码即闸门）|
| `evaluate(test_cases, metrics, hyperparameters=...)` | 脚本/Notebook 里直接调 | 实验对比、批量跑分 |
| `dataset.evals_iterator(metrics=...)` | 迭代 Golden、边跑系统边评 | 数据集驱动的回归 |

`evaluate()` 与 `@deepeval.log_hyperparameters` 都能记录超参数（模型版本、prompt 版本、temperature、top_k）——把每次运行变成可对比的实验，这是 A/B 结论可信的前提（H.9）。

## H.4 指标：配置、G-Eval 与 DAG

**通用配置**（所有内置指标共享）：

- `threshold`：通过线，默认 0.5——**不要凭感觉设**，用 H.9 的校准流程定；
- `strict_mode=True`：二值化（满分才过），适合硬约束；
- `include_reason`：输出打分理由，诊断必备（稳定后可选择性关闭省成本）；
- `flaky`（重试）与 `verbose_mode`（打印 Judge 中间过程）辅助排障。

4.2.0 起所有指标统一**分数越高越好**——从旧版本升级需重跑校准集、复查阈值方向（H.10）。

**G-Eval**：用自然语言写评分标准即成指标，是业务定制的第一选择。两种写法：`criteria`（一句话标准，由框架自动展开评分步骤）或 `evaluation_steps`（显式列步骤，更可控、波动更小）。原则：**一个 G-Eval 只评一个维度**——把"正确、简洁、礼貌"塞进一条 criteria，分数波动大且无法归因。

```python
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCaseParams

correctness = GEval(
    name="Business Correctness",
    evaluation_steps=[
        "检查 actual_output 的事实是否与 expected_output 一致",
        "遗漏关键条件或做出额外承诺都应扣分",
        "措辞与句式差异不扣分",
    ],
    evaluation_params=[
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    threshold=0.7,
)
```

**DAG Metric**：把评分写成决策树（先判断格式→再按分支评内容），每个节点是确定性判断或小的 LLM 判断。比单条 G-Eval 更可控、可解释，适合"有明确规则骨架 + 少量语义判断"的场景。

**选择决策**：有人工参考答案→ Reference-based（Correctness G-Eval）；没有→ Referenceless（Answer Relevancy、Faithfulness）；硬约束（必含字段、禁用词、JSON 合法）→ 确定性规则/自定义 Metric，**不要浪费 Judge 调用**。

## H.5 RAG 评测实战

五个内置指标与必要字段（方法论口径与组合诊断详见第 11 章 2.8——那边讲"指标组合说明什么"，这里讲"怎么跑出来"）：

| 指标 | 评谁 | 必要字段 |
|---|---|---|
| Answer Relevancy | 生成器：答得切不切题 | `input` + `actual_output` |
| Faithfulness | 生成器：有没有超出检索内容编造 | + `retrieval_context` |
| Contextual Relevancy | 检索器：召回的内容和问题相关吗 | `input` + `retrieval_context` |
| Contextual Recall | 检索器：该召回的召回全了吗 | + `expected_output` |
| Contextual Precision | 检索器：相关内容排得靠前吗 | + `expected_output` |

**分开评 Retriever 与 Generator** 是 RAG 评测的第一纪律：Faithfulness 高 + Contextual Recall 低 = 检索没召回、生成器只是"忠实于残缺的上下文"——单看端到端分数会把检索问题误诊为生成问题。

组件级归因用 `@observe` 把指标挂到具体组件上：

```python
from deepeval.tracing import observe, update_current_span

@observe(metrics=[contextual_relevancy])
def retriever(query: str) -> list[str]:
    chunks = search(query)
    update_current_span(test_case=LLMTestCase(input=query, retrieval_context=chunks))
    return chunks

@observe(metrics=[answer_relevancy, faithfulness])
def generator(query: str, chunks: list[str]) -> str: ...
```

跑一次真实调用，Retriever 和 Generator 各自拿到自己的分——第 11 章"组合诊断表"里的每个象限从此有了自动化的取数来源。

## H.6 Agent 与多轮对话

**Agent 评测**在结果之外加轨迹：Task Completion（任务完成没有）、Tool Correctness（该调的工具调了吗——可配置严格度：只看工具名 / 加参数 / 加顺序）、Argument Correctness、Step/Plan 类指标。用例里给 `tools_called` 与 `expected_tools`（`ToolCall(name=..., input_parameters=...)`），或直接在 `@observe` Trace 上评。实践组合：**确定性断言管硬约束**（禁止调用危险工具、必须先查权限）+ **语义指标管质量**（任务完成度）——与第 15 章"Guard 管下限、Eval 管上限"同构。

**多轮对话**用 `ConversationalTestCase`（`turns=[Turn(role, content), ...]` + `scenario` / `expected_outcome` / `chatbot_role`），配套指标如 Role Adherence（人设守没守住）、Conversation Completeness。`ConversationSimulator` 能按 Persona 自动生成多轮对话压测你的回调（`model_callback`）——把"人肉聊十轮找问题"变成批量回归。

## H.7 自定义：Judge 与 Metric

**自定义 Judge**（用国产模型、本地模型或企业网关做裁判）：继承 `DeepEvalBaseLLM`，实现 `load_model` / `generate` / `a_generate` / `get_model_name`，然后传给任意指标的 `model=` 参数。注意 `a_generate` 必须真异步，否则并发评测会假死（H.10）。数据安全考量见 H.9——Judge 在哪跑，数据就流向哪。

**自定义 Metric**（确定性规则进评测体系）：继承 `BaseMetric`，`measure()` 里设 `self.score` / `self.success` / `self.reason`，实现 `is_successful()`：

```python
from deepeval.metrics import BaseMetric

class RequiredTermsMetric(BaseMetric):
    def __init__(self, required_terms: list[str], threshold: float = 1.0):
        self.required_terms, self.threshold = required_terms, threshold

    def measure(self, test_case) -> float:
        hits = [t for t in self.required_terms if t in test_case.actual_output]
        self.score = len(hits) / len(self.required_terms)
        self.success = self.score >= self.threshold
        self.reason = f"命中 {hits}，缺失 {set(self.required_terms) - set(hits)}"
        return self.score

    def is_successful(self) -> bool:
        return self.success
```

零 Judge 调用、零波动——**先跑便宜的确定性规则，失败就不再调昂贵 Judge**，是成本优化的第一杠杆（H.9）。

## H.8 数据集分层与 Synthesizer

数据集按用途分层（对应 CI/CD 分层门禁，H.9）：

```text
smoke（10～30 条，PR 必跑）→ regression（历史线上失败回流）
→ edge_cases（边界/无答案）→ adversarial（对抗与安全）
→ production_samples（线上抽样）→ human_gold（人工精标，校准用）
```

Golden 设计原则：期望里写**必须正确的事实、必须覆盖的条件、不能做的额外承诺**，不规定精确措辞——否则评的是复述而不是质量。历史线上失败**必须**回流进 regression（第 15 章回流闭环的落点）。

`Synthesizer` 四个入口批量造 Golden：`generate_goldens_from_docs`（从文档，RAG 冷启动首选）、`from_contexts`、`from_goldens`（扩增）、`from_scratch`。合成数据解决"冷启动没题库"，但高风险样本必须人工审核后才能进门禁。

## H.9 工程化：CLI、CI/CD、校准、成本与安全

**CLI 速查**（`deepeval test run tests/evals` 之上）：`-n 4` 多进程并行、`-c` 缓存、`-r 3` 重复跑（测波动）、`-i` 忽略执行错误、`-s` 缺字段跳过、`-d failing` 只看失败、`-id "rag-pr-184"` 运行标识（带上 PR/Commit 号）、`-m "smoke and not slow"` 按 pytest marker 过滤。`deepeval diagnose` 查配置、`deepeval inspect` 看 Trace。

**分层门禁**（成本与速度的平衡）：

| 触发 | 跑什么 | 预算 |
|---|---|---|
| PR | smoke 10～30 条 + 确定性规则 | 5～10 分钟内 |
| main | regression 100～500 条、完整 Trace 指标 | 半小时级 |
| Nightly | 大型数据集、重复跑、Judge 一致性、成本/延迟趋势 | 不限 |
| Release | 高风险全量 + 对抗集 + 人工抽检 | 人工参与 |

**阈值校准**：收集代表性样本（明显过/明显败/边界/高风险/轻微措辞问题）→ 双人标注 → 跑候选 Judge 对比人工标签 → 分析误报漏报 → 定阈值 → 独立验证集复验 → 进 CI。评 Judge 本身看与人工的一致率、F1、**严重错误漏报率**（高风险场景优先优化这个，不是整体准确率）与重复方差。**失败了不许降阈值**——看 reason、定位到 Span、修系统、同数据集同阈值重跑；只有人工校准证明阈值不合理才调，并记录原因。

**成本模型**：`用例数 × 指标数 × 每指标 Judge 调用次数 × 重复次数`——500 条 × 4 指标 × 2 次调用 × 3 重复 ≈ 1.2 万次模型调用。优化按序：PR 只跑 smoke → 确定性规则前置短路 → 缓存 → 只对关键 Span 上组件指标 → 大回归集放 Nightly → 低风险样本用便宜 Judge、失败样本才用强 Judge 复核。

**数据安全**：LLM-as-a-Judge 会把输入、输出、检索上下文、工具参数、对话历史发给 Judge 的模型提供方——上线前脱敏 PII、删密钥、敏感业务走企业网关或本地 Judge（H.7），并为评测日志设保留期限。**复现性**至少记录：DeepEval 版本、Judge/Generator 模型版本、Prompt 版本、Metric 配置与阈值、数据集 Hash、采样参数、Commit SHA。

## H.10 排查速查

| 症状 | 大概率原因与动作 |
|---|---|
| 评测卡 0% / 极慢 | Judge 额度/限流；降并发（`AsyncConfig(max_concurrent=3, throttle_value=1)`）、跑 `deepeval diagnose`；自定义 Judge 的 `a_generate` 是否真异步 |
| 分数波动大 | Judge 温度/滚动别名模型；criteria 太主观或混维度→拆分/换 `evaluation_steps`/换 DAG；边界样本→`-r 3` 看方差 |
| Faithfulness 高但答案错 | 它只看与 `retrieval_context` 是否一致——检索内容本身错/过期。补 Contextual Recall/Precision 与带参考的 Correctness |
| Answer Relevancy 高但编造 | 它只看切题不看事实。必须与 Faithfulness 组合，有参考再加 Correctness |
| Contextual Relevancy 低但答案对 | 召回大量噪声、生成器侥幸忽略，或模型靠自身知识——当前能过、成本与稳定性差，优化 Retriever |
| 升级后分数方向异常 | 4.2.0 统一"越高越好"；重跑校准集、复查所有阈值与自定义 Metric 的 `is_successful()` |

最后两条边界认知：DeepEval **不替代**传统测试（单测/集成/权限/负载照写），完整体系是"确定性测试 + LLM 语义评估 + 人工评审 + 生产监控"四层；本地跑完全可行，云平台（Confident AI）只在团队共享报告、长期趋势、集中数据集管理时才必要——自托管路线的替代是第 15 章的"框架产分、平台收分"（DeepEval 算分回写 Langfuse/Phoenix 等平台）。

**落地顺序**（照抄可用）：10～30 条 smoke Golden → 2～3 个系统专用指标 + 1 个业务 G-Eval → 接 `deepeval test run` 进 CI → RAG/Agent 加 `@observe` 组件级 → 最后建阈值校准、回归分层与线上失败回流。

---

> **使用提示**：与其他附录的分工——A 讲模型机制、B 讲方法论、C 记来源、D 列产品、E 辨异同、F 索引图版、G 详解 OTel、**H 上手 DeepEval**。第 15 章是"评什么、为什么评"，本附录是"用 DeepEval 怎么评"；第 11 章 2.8 的 RAG 指标组合诊断在 H.5 有自动化取数方案。API 快照为 4.2.0，动手前对照 [C-31] 核验当前版本。
