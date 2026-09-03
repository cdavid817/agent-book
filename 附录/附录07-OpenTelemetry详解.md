# 附录 7：OpenTelemetry 详解与 Agent 可观测性指南

> 定位：**OTel 机制兜底 + Agent 观测接入原理的完整指南**（全文收录，信息基准 2026-09，规范与文档入口见 [C-28]）。正文第 14 章讲"本书的 Agent 怎么用 OTel"（四层 Span、指标框架、采样策略），附录 13 盘点可观测赛道的平台产品，本附录讲两层原理：**OTel 本体**（7.2 的 24 个小节：数据模型、五信号、API/SDK、语义约定与 Schema 治理、OTLP、Collector 与部署形态、采样、排障）与 **Agent 观测机制**（四层观测模型、Agent 对象到遥测的映射、Python 零侵入探针原理、LangChain/LangGraph/OpenAI SDK/ChromaDB 的接入机制、Span 所有权与重复埋点治理、敏感数据治理、自研 Runtime 的原生观测设计）。一句话分工：第 14 章讲用法、13 讲平台、本附录讲机制与接入原理。

---

### 摘要

AI Agent 的运行过程通常同时包含工作流编排、Agent 调度、模型推理、工具调用、知识检索、记忆读写、权限判断、循环控制、人工审批、跨进程通信和外部服务访问。传统的 HTTP 监控只能看到网络请求，无法回答：

- 哪个 Agent 做出了决策；
- 为什么调用某个工具；
- 一次任务经历了多少次推理和重试；
- Token、成本和延迟消耗在哪个环节；
- 是规划、模型、工具、检索、网络还是权限系统导致失败；
- 多 Agent、MCP、CLI、向量数据库之间是否属于同一条调用链。

OpenTelemetry 提供统一的遥测标准，但它不会自动理解 Agent。完整的 Agent 可观测体系需要多个角色协同：

```text
Agent / 被观测框架
├── 遥测路径
│   └── Instrumentation → OTel API → OTel SDK → OTLP → Collector
│       └── Trace / Metric / Log / Eval 后端
└── 内容路径
    └── Artifact Gateway → Artifact Store / Catalog
```

两条路径通过 `artifact_id`、`version_id`、`trace_id` 和 `span_id` 互相关联，但拥有不同的数据生命周期和事实源。

同时，Semantic Conventions 作为统一数据字典，约束各种 Instrumentation 如何表达 Workflow、Agent、Model、Tool、Retriever 和 Memory 等语义；Trace Context 则负责把不同线程、进程和服务中的 Span 串联为同一条 Trace。

本文系统整理以下内容：

1. OpenTelemetry 的定位、分层架构与统一数据模型；
2. Trace、Metric、Log、Baggage、Profile 五类信号，基于 LogRecord 的 Events，以及非 OTel 信号的 Agent Artifact 领域模型；
3. Resource、InstrumentationScope、Span、Metric DataPoint 和 LogRecord；
4. API、SDK、Semantic Conventions、Schema、OTLP、Context Propagation 与 Collector；
5. Head／Tail Sampling、Metrics 聚合与基数、Logs 桥接、可靠性、安全和排障；
6. OpenTelemetry 如何与 Agent 结合并获得 Agent 内部逻辑数据；
7. Python 零代码探针如何实现；
8. 四层 Agent 观测模型；
9. LangChain、LangGraph、OpenAI、ChromaDB 等开源框架采用的接入机制；
10. 多层 Instrumentation 的 Span 所有权、重复埋点和安全治理；
11. 自研 Agent Runtime 应如何设计原生可观测能力；
12. GenAI Semantic Conventions 的 Span、Attribute、Metric、Event、Exception、Provider 与 MCP 完整规范；
13. OpenTelemetry EventRecord、EventName、Logs API／SDK、Span Event Bridge 与 Agent 领域事件治理；
14. Artifact 的身份、版本、Part、Manifest、存储、血缘、安全、框架映射及与 Span／Event／Metric／Eval 的协同。
15. Traceloop／OpenLLMetry `LangchainInstrumentor` 的 Callback 注入、LangGraph 专项 Patch、Context 传播、Span 去重、内容模式、指标和官方实现差异。
16. `opentelemetry-instrumentation-langchain` 发行包的模块结构、公共 API、Entry Point 自动加载、配置与生命周期、Attribute／Event 数据模型、Provider 识别、测试矩阵、生产配置、迁移和升级治理。

---

## 7.1 核心结论

### 1. OpenTelemetry 不会自动理解 Agent

OpenTelemetry 提供的是统一的遥测 API、SDK、数据模型、语义约定和传输协议。它并不知道：

- 什么是 Agent；
- 什么是规划；
- 什么是工具选择；
- 什么是长期记忆；
- 什么是 Agent Loop；
- 为什么进入某个图节点；
- 为什么触发权限拒绝或人工审批。

真正理解框架内部语义的是 **Instrumentation Library**。它通过 Callback、Processor、Event Bus、Monkey Patch、Wrapper、Decorator、Middleware、Interceptor 或原生埋点，把框架行为翻译为 OpenTelemetry Span、Metric 和 LogRecord；其中设置了 `EventName` 的 LogRecord 表示 Event。

### 2. “零侵入”通常是零业务代码修改，不是零注入

Python 中所谓零代码或零侵入接入，通常意味着：

- 不需要修改 Agent 业务代码；
- 需要安装对应 Instrumentation；
- 需要通过 `opentelemetry-instrument` 或初始化代码启动；
- 运行时会注册 Callback，或替换已知框架方法；
- 仍然存在版本兼容、性能、安全和重复 Span 风险。

它不是：

- 自动扫描全部 Python 函数；
- 自动解释任意自研 Agent 逻辑；
- 自动读取模型隐藏思维链；
- 对已运行进程进行任意动态附着；
- 不需要 SDK、Exporter 或 Collector 配置。

### 3. 四层观测共同构成完整视角

| 观测层 | 回答的核心问题 |
|---|---|
| Agent／框架语义层 | **Agent 做了什么** |
| SDK／客户端库层 | **调用了什么模型或基础能力** |
| 协议／传输层 | **请求如何传输，网络哪里慢** |
| 应用／领域逻辑层 | **为什么这么做** |

单独依赖任何一层都不完整。

### 4. OpenTelemetry“四件套”之外还必须有 Instrumentation

通常所说的四件套是：

1. OpenTelemetry API；
2. OpenTelemetry SDK；
3. Semantic Conventions；
4. OTLP。

但要把 LangChain、OpenAI、ChromaDB 接入 OpenTelemetry，还必须增加：

> **Instrumentation：框架与 OpenTelemetry 之间的翻译器。**

完整关系如下：

```text
LangChain / OpenAI / ChromaDB
        ↓
Instrumentation
        ↓
OpenTelemetry API
        ↓
OpenTelemetry SDK
        ↓
OTLP
        ↓
Collector
```

Semantic Conventions 不在线性数据流中，它约束 Instrumentation 应如何命名和表达数据。

### 5. OTLP 与 Trace Context 是两件事

- `traceparent`、`tracestate`、`baggage`：用于在业务请求中传播上下文；
- OTLP：用于把已经生成的遥测数据发送给 Collector。

两个服务都向同一个 Collector 发送 OTLP，并不会自动成为同一条 Trace。必须在业务调用中执行 Inject／Extract。

### 6. Artifact 是领域对象，不是 OpenTelemetry 新信号

Agent 生成的报告、代码补丁、图片、数据文件、测试证据和沙箱快照，应由独立的 Artifact Store 与 Catalog 管理。OpenTelemetry 负责记录这些对象如何被生成、验证、存储、消费和删除，但不应通过 OTLP 传输大体积正文，也不应把可能被采样的 Trace 当成 Artifact 的事实源。

推荐职责：

```text
Artifact Store
  → 保存真实内容

Artifact Catalog
  → 保存身份、版本、哈希、权限、状态和血缘

OpenTelemetry
  → 保存操作路径、耗时、错误、生命周期事件和聚合趋势
```

当前 GenAI Semantic Conventions 尚无稳定的 Artifact 语义，生产系统应使用明确标注的自定义命名空间和映射层，避免把自定义字段伪装成官方标准。

---

## 7.2 OpenTelemetry 详解：从数据模型到生产运行时

本章先脱离 Agent 场景，系统解释 OpenTelemetry 本体。后续各章再把这些机制映射到 LangChain、LangGraph、OpenAI、ChromaDB 和自研 Agent Runtime。

> **版本基线（2026-09-02）**  
> 本章依据当前官方规范和文档整理：OpenTelemetry Specification `1.60.0`、Semantic Conventions `1.44.0`、OTLP `1.11.0`。不同语言 SDK、自动探针和语义约定的成熟度可能不同，生产接入应以所用语言与组件的实际版本为准。

### 7.2.1 OpenTelemetry 是什么，不是什么

OpenTelemetry，简称 OTel，是一套厂商中立的可观测性标准、API、SDK、数据模型、语义约定、自动埋点生态和采集组件。它解决的是：

```text
如何产生遥测
如何统一描述遥测
如何传播调用上下文
如何处理和导出遥测
如何在不同语言、框架和后端之间保持可移植性
```

OpenTelemetry **不是**：

- Trace、Metric、Log 的长期存储数据库；
- 开箱即用的告警、Dashboard 或 APM UI；
- 自动判断业务正确性的评测系统；
- 只服务于微服务 HTTP 链路的追踪库；
- 某一个云厂商或监控厂商的私有协议。

典型职责边界如下：

```mermaid
flowchart LR
    A["应用与框架<br/>产生业务行为"] --> B["Instrumentation<br/>捕获并翻译行为"]
    B --> C["OTel API<br/>创建遥测"]
    C --> D["OTel SDK<br/>采样、聚合、处理"]
    D --> E["Exporter / OTLP<br/>导出"]
    E --> F["Collector<br/>接收、处理、路由"]
    F --> G["Backend<br/>存储、查询、告警、可视化"]

    H["Semantic Conventions<br/>统一命名与含义"] -.-> B
    I["Context Propagation<br/>串联跨边界因果关系"] -.-> A
```

OpenTelemetry 的核心价值不是“再造一个监控后端”，而是把遥测的生产端、传输端和消费端解耦。

### 7.2.2 OpenTelemetry 的整体分层

从工程实现看，可以分成八个协作层次：

| 层次 | 核心对象 | 主要职责 |
|---|---|---|
| 被观测系统 | 应用、框架、SDK、数据库、运行时 | 产生真实业务行为 |
| Instrumentation | Callback、Wrapper、Middleware、原生埋点 | 捕获调用并创建遥测 |
| API | Tracer、Meter、Logger、Context、Baggage | 提供稳定的编程接口 |
| SDK | Provider、Sampler、Processor、Reader、Exporter | 实现采样、聚合、批处理和导出 |
| Semantic Conventions | Span、Metric、Log、Resource 命名规范 | 统一数据语义 |
| Propagation | Propagator、Inject、Extract | 跨线程、进程、网络传播上下文 |
| OTLP | OTLP/gRPC、OTLP/HTTP | 标准化遥测传输 |
| Collector 与后端 | Receiver、Processor、Exporter、存储与 UI | 集中治理、存储、查询和告警 |

其中最容易混淆的是：

- **API** 是“怎么写遥测”；
- **SDK** 是“遥测如何真正运行”；
- **Semantic Conventions** 是“字段叫什么、表示什么”；
- **OTLP** 是“遥测怎样被运输”；
- **Trace Context** 是“业务调用如何被串联”；
- **Instrumentation** 是“怎样从框架内部拿到数据”。

### 7.2.3 信号与事件模型：Trace、Metric、Log、Baggage、Profile 与 EventRecord

OpenTelemetry 以信号组织客户端架构。主要概念体系包含 Trace、Metric、Log、Baggage 和 Profile。

**Event 不是独立的第六种传输信号。** OpenTelemetry Event 在数据模型上是一个设置了非空 `EventName` 的 `LogRecord`，通过 Logs API、Logs SDK 和 OTLP Logs Pipeline 处理。

| 信号或记录类型 | 回答的问题 | 数据形态 | 典型用途 |
|---|---|---|---|
| Trace | 一次请求经历了什么 | 具有父子关系的 Span 图 | 端到端故障定位、关键路径分析 |
| Metric | 整体趋势如何 | 时间序列和聚合数据点 | Dashboard、告警、容量规划、SLO |
| Log | 某个时刻记录了什么 | 带时间、严重度和正文的 LogRecord | 明细审计、诊断、运行记录 |
| EventRecord | 发生了哪类有意义的瞬时事件 | 设置了 `EventName` 的 LogRecord | 状态变化、生命周期、评估、异常 |
| Artifact（领域对象） | Agent 产生或消费了什么可交付内容 | 文件、对象、Manifest、Part 和版本 | 报告、代码、图片、证据、Checkpoint；**不是 OTel 信号** |
| Baggage | 哪些上下文应继续向下游传播 | 键值上下文 | 租户、实验组、业务关联信息传播 |
| Profile | 代码级资源消耗在哪里 | 时间采样或事件采样的栈数据 | CPU、内存、锁和热点函数分析 |

需要特别说明：

- **EventRecord 属于 Logs 数据模型**，不是独立 OTLP Signal，也没有独立 `/v1/events` 导出端点；
- **Artifact 是 Agent／应用领域对象，不属于 OpenTelemetry Signal**。其正文应进入 Artifact Store，OTel 只记录操作、生命周期、元数据和不透明引用；
- **Baggage 不是独立的存储型遥测记录**。它主要跟随 Context 传播，只有被复制到 Span、Metric、Log 或 Event 属性后，才会在后端中可查询；
- **Profile 是较新的信号方向**。语言 SDK、Collector 组件和后端支持程度并不完全一致，接入前应核对所用栈的能力；
- 各信号相互关联，但生命周期、采样和导出策略相互独立。

### 2.3.1 多信号关联

```mermaid
flowchart TD
    R["Resource<br/>service.name=agent-service"]
    R --> T["Trace<br/>一次请求的因果链"]
    R --> M["Metric<br/>延迟、错误率、吞吐"]
    R --> L["Log<br/>异常、审计、业务明细"]
    R --> P["Profile<br/>CPU、内存、调用栈"]

    T -->|"TraceId / SpanId"| L
    M -->|"Exemplar"| T
    P -->|"时间、服务、线程等维度"| T
```

一个理想的可观测后端应支持：

```text
Metric 告警
  → 通过 Exemplar 跳到具体 Trace
  → 从 Trace 跳到关联 Log
  → 再按同一 Resource 和时间窗口查看 Profile
```

### 7.2.4 统一数据包络：Resource 与 InstrumentationScope

OpenTelemetry 的 Trace、Metric、Log 都不是孤立记录。它们通常处于两层公共上下文之下：

```text
Resource
└── InstrumentationScope
    └── Span / Metric / LogRecord
```

### 2.4.1 Resource

Resource 描述“被观测的实体是谁、位于哪里”，常见属性包括：

```text
service.name
service.namespace
service.version
service.instance.id
deployment.environment.name
host.name
process.pid
container.id
k8s.pod.name
cloud.provider
cloud.region
```

Resource 应具备以下特征：

- 在一批遥测记录之间稳定共享；
- 描述服务、进程、容器、Pod、主机或设备；
- 不放请求级数据；
- 不放 Prompt、Trace ID、用户输入等高基数内容；
- `service.name` 应明确配置，避免不同服务被聚合到默认名称。

示例：

```text
Resource:
  service.name = coding-agent-runtime
  service.namespace = vane-ai
  service.version = 2.4.0
  service.instance.id = pod-7b4d8
  deployment.environment.name = production
```

### 2.4.2 InstrumentationScope

InstrumentationScope 标识“是哪一个逻辑软件单元产生了遥测”，由以下元组构成：

```text
name
version
schema_url
attributes
```

例如：

```text
Scope:
  name = opentelemetry.instrumentation.genai.openai
  version = 0.x.y
  schema_url = https://opentelemetry.io/schemas/...
```

Resource 和 InstrumentationScope 的区别：

| 对象 | 标识谁 | 生命周期 |
|---|---|---|
| Resource | 被观测服务或实体 | 通常与进程、容器或服务实例一致 |
| InstrumentationScope | 产生遥测的库或模块 | 与 Instrumentation／业务模块版本一致 |
| Span 属性 | 某一次操作 | 与请求或操作一致 |

同一进程中，LangChain Instrumentation、OpenAI Instrumentation 和业务手工埋点可以共享一个 Resource，但分别使用不同的 InstrumentationScope。

### 2.4.3 OTLP 中的层级结构

```text
ResourceSpans
└── ScopeSpans
    └── Span

ResourceMetrics
└── ScopeMetrics
    └── Metric
        └── DataPoint

ResourceLogs
└── ScopeLogs
    └── LogRecord
```

理解这三层结构，对排查 `service.name` 丢失、同名 Metric 冲突、Scope 版本不一致和语义迁移问题非常重要。

### 7.2.5 Trace 详解

### 2.5.1 Trace 与 Span

Trace 表示一个端到端操作的因果图；Span 表示其中一个具有开始时间和结束时间的操作单元。

每个 Span 至少包含：

```text
TraceId          归属哪条Trace
SpanId           当前Span的唯一标识
ParentSpanId     父Span标识，可为空
Name             低基数操作名
StartTime        开始时间
EndTime          结束时间
Kind             调用角色
Status           结果状态
Attributes       键值元数据
Events           带时间戳的离散事件
Links            与其他SpanContext的因果关联
Resource         被观测实体
InstrumentationScope 产生遥测的模块
```

### 2.5.2 Span 名称

Span 名称应描述稳定操作，不应直接嵌入高基数值。

推荐：

```text
GET /orders/{id}
execute_tool
invoke_agent reviewer
chat gpt-5
SELECT users
```

不推荐：

```text
GET /orders/982739129
run for user david@example.com
prompt: 帮我读取完整仓库...
```

动态值应放入属性，而不是 Span 名称，否则后端聚合、索引和采样规则会失控。

### 2.5.3 SpanKind

OpenTelemetry 定义五种 SpanKind：

| SpanKind | 含义 | 典型场景 |
|---|---|---|
| `INTERNAL` | 进程内部操作 | Agent 规划、函数执行、内存计算 |
| `CLIENT` | 发起远程请求 | HTTP Client、数据库客户端、远程模型调用 |
| `SERVER` | 处理远程请求 | HTTP Server、gRPC Server、MCP Server |
| `PRODUCER` | 发送异步消息或任务 | 发布消息、提交后台任务 |
| `CONSUMER` | 消费异步消息或任务 | 消费队列、执行异步任务 |

重要原则：一个 Span 不要同时承担多个角色。服务端处理请求时创建 `SERVER` Span；调用下游时应另建 `CLIENT` Span。

### 2.5.4 Span Status

Span 状态包含：

```text
UNSET
ERROR
OK
```

- `UNSET` 是默认状态，通常也表示没有显式错误；
- `ERROR` 表示操作失败；
- `OK` 表示应用明确判定成功，通常没有必要对每个成功 Span 主动设置。

推荐错误处理：

```text
记录异常事件
设置 error.type
必要时设置 status=ERROR
保留业务错误码和终止原因
```

仅记录异常堆栈并不等于完整表达失败；同样，HTTP 200 也不等于 Agent 或工具业务成功。

### 2.5.5 Attributes

Attributes 是用于过滤、聚合和诊断的键值数据。常见类型为：

```text
string
boolean
integer
floating point
上述类型的数组
```

设计原则：

- 在 Span 创建时尽量写入采样所需属性；
- 高频筛选字段使用标准 Semantic Conventions；
- 大文本、二进制和无限长度数组不要放入属性；
- 高基数属性可以放 Trace，但要控制索引策略；
- 敏感内容默认不采集。

### 2.5.6 Span Event

Span Event 是 Trace 数据模型的一部分，适合表示某个 Span 生命周期中的有意义时间点：

```text
retry.started
first_chunk.received
permission.denied
loop.detected
human_approval.requested
```

判断标准：

- 需要独立开始和结束时间：使用 Span；
- 只需要一个有时间戳的瞬时事实：使用 Event；
- 只是描述整个 Span 的元数据且时间点不重要：使用 Attribute。

必须区分：

```text
Span Event
  → 存在于Span内部
  → 随Trace导出
  → 没有独立Severity和Body

Log-based Event
  → 是带EventName的LogRecord
  → 通过Logs Pipeline导出
  → 可独立于Trace存在
  → 支持Severity和Body
```

两者的详细关系、迁移路径与 Event-to-Span-Event Bridge 见 [2.8 Events 详解](#28-events-详解基于-logs-数据模型的结构化事件)。
### 2.5.7 Span Link

父子关系适合严格的同步或嵌套因果链；Link 适合非树形关系：

- 一个批处理消费多个上游消息；
- 一个异步任务在原 Trace 结束后启动；
- 多个 Agent 结果汇聚到一个聚合 Agent；
- 新 Trace 需要关联旧 Trace，但不应直接作为其子 Span；
- 一个工作项被重新入队或重放。

示意：

```text
Trace A: 用户请求
└── producer enqueue_job

Trace B: 后台任务
└── consumer execute_job
    links = [Trace A / producer SpanContext]
```

### 2.5.8 Trace 不是严格的一棵本地调用栈

分布式 Trace 更准确地说是一个以父子关系为主、辅以 Links 的有向因果图。它可以跨越：

```text
线程
异步任务
进程
容器
服务
数据中心
消息队列
人工审批等待
```

这也是 Trace 与普通函数调用栈、日志缩进或单机 Profiler 的根本区别。

### 7.2.6 Metrics 详解

Metric 用于表达可聚合的数值趋势。它不是“定时打印一个数字”，而是由测量、属性、聚合、时间范围和 temporality 共同构成的数据流。

### 2.6.1 核心对象

```text
MeterProvider
└── Meter
    └── Instrument
        └── Measurement
            └── Aggregation
                └── Metric DataPoint
```

- `MeterProvider`：Meter 工厂和统一配置入口；
- `Meter`：按 InstrumentationScope 创建 Instrument；
- `Instrument`：记录测量；
- `MetricReader`：触发收集并决定导出节奏；
- `MetricExporter`：发送聚合后的数据。

### 2.6.2 Instrument 类型

| Instrument | 行为 | 典型用途 |
|---|---|---|
| Counter | 单调递增 | 请求数、Token 总量、错误数 |
| UpDownCounter | 可增可减 | 活跃任务数、队列占用、并发连接 |
| Histogram | 记录数值分布 | 延迟、响应大小、Token 分布、工具耗时 |
| Gauge | 记录某时刻值 | 温度、当前预算、瞬时利用率 |
| Observable Counter | 回调读取单调累计值 | 进程 CPU 时间、累计字节 |
| Observable UpDownCounter | 回调读取可增减累计值 | 当前队列长度、连接数 |
| Observable Gauge | 回调读取瞬时状态 | 内存、GPU 利用率、线程数 |

选择原则：

```text
只会增加的累计量          → Counter
会上升也会下降的状态量    → UpDownCounter
关注分位数和分布          → Histogram
只关心当前快照            → Gauge
值只能在采集时读取        → Observable Instrument
```

### 2.6.3 Aggregation

SDK 不会默认把每次 Measurement 都原样发送出去，而是在进程内聚合，例如：

```text
Sum
LastValue
ExplicitBucketHistogram
ExponentialHistogram
Drop
```

Histogram 通常比“平均值”更有诊断价值，因为平均值会掩盖长尾。Agent 场景应重点关注：

```text
p50 / p90 / p95 / p99 模型延迟
工具执行长尾
首 Token 延迟分布
每次任务的 Token 分布
每次工作流的 Agent 调用次数分布
```

### 2.6.4 Temporality

Metric 数据常见两种时间性：

| Temporality | 含义 | 示例 |
|---|---|---|
| Cumulative | 从固定起点累计到当前 | 进程启动以来共处理 1000 次请求 |
| Delta | 只表示上一个采集周期到当前的增量 | 最近 60 秒处理 80 次请求 |

SDK、Collector 和后端之间必须正确处理 Delta／Cumulative 转换，否则容易产生重置、重复累计或速率异常。

### 2.6.5 Views

View 是 Metrics SDK 的治理入口，可以：

- 修改输出 Metric 名称；
- 修改描述和单位；
- 选择聚合方式；
- 配置 Histogram Bucket；
- 删除高基数属性；
- 屏蔽不需要的 Instrument；
- 限制某类 Metric 的数据流数量。

例如，Instrumentation 记录：

```text
agent.tool.duration
attributes:
  tool.name
  tool.type
  tool.call.id
  user.id
```

生产 View 应保留：

```text
tool.name
tool.type
```

删除：

```text
tool.call.id
user.id
```

### 2.6.6 Cardinality

一个 Metric 在不同属性组合下会形成不同时间序列：

```text
metric_name + attribute_set = 一条时间序列
```

危险标签：

```text
trace_id
span_id
run_id
conversation_id
user_id
request_id
file_path
prompt_hash
完整URL
```

OpenTelemetry Metrics SDK 具备基数限制和 Overflow 数据点机制，但它不是放任高基数设计的理由。超过限制后，测量值虽然可被折叠到 Overflow 流中，维度分析能力仍会丢失。

### 2.6.7 Exemplars

Exemplar 在 Metric 数据点中保留一个具有代表性的原始测量，并可携带 TraceId／SpanId。

典型跳转：

```text
agent.workflow.duration p99 异常
    ↓ Exemplar
具体慢 Trace
    ↓
慢在模型、检索、工具还是审批
```

因此，Exemplar 是 Metrics 与 Traces 之间非常重要的桥梁。

### 7.2.7 Logs 详解

OpenTelemetry Logs 的重点不是要求所有应用抛弃现有日志库，而是：

- 把已有日志桥接到统一 LogRecord 数据模型；
- 自动关联 TraceId 和 SpanId；
- 附加 Resource 与 InstrumentationScope；
- 统一采集、转换和导出；
- 兼容 Syslog、Log4j、Python logging、容器日志等来源。

### 2.7.1 LogRecord 主要字段

```text
Timestamp
ObservedTimestamp
SeverityText
SeverityNumber
Body
Attributes
TraceId
SpanId
TraceFlags
Resource
InstrumentationScope
EventName（按实现和语义使用）
```

其中：

- `Timestamp`：事件实际发生时间；
- `ObservedTimestamp`：采集系统观察到该日志的时间；
- `Body`：正文，可为字符串或结构化值；
- `Attributes`：查询维度和结构化上下文；
- `TraceId`、`SpanId`：实现日志与 Trace 的直接关联。

### 2.7.2 三种日志接入方式

| 方式 | 机制 | 适用场景 |
|---|---|---|
| Logging Bridge／Appender | 从现有日志框架转换为 OTel LogRecord | Java、Python、.NET 等已有应用日志 |
| Collector 文件采集 | 读取文件、stdout、容器日志并解析 | 无法修改应用、基础设施日志 |
| 直接使用 Logs API | 应用或 Instrumentation 直接发 LogRecord | 新系统、结构化事件、框架适配器 |

### 2.7.3 普通 Log、Log-based Event 与 Span Event 的区别

| 维度 | 普通 Log | Log-based Event | Span Event |
|---|---|---|---|
| 数据模型 | LogRecord | `EventName` 非空的 LogRecord | Span 内部 Event |
| 是否必须有稳定类型名 | 否 | 是 | 是 |
| 是否必须依附 Span | 否 | 否 | 是 |
| 是否有独立日志生命周期 | 是 | 是 | 否 |
| Severity／Body | 支持 | 支持 | 无一等字段 |
| 是否适合高量明细 | 相对适合 | 取决于事件级别 | 不适合无限增长 |
| Trace 未采样时是否可保留 | 可以 | 可以 | 通常不可以 |
| 典型用途 | 诊断文本、运行明细 | 状态变化、评估、异常、审计型遥测 | 重试、首 Chunk、Span 内状态切换 |

不要把大量流式 Token、完整 stdout 或逐行文件内容全部作为 Span Event 或 Log-based Event；这会扩大数据量、增加内存和后端索引压力。此类内容更适合受控普通日志或对象存储引用。

OpenTelemetry 当前使用 `LogRecord.EventName`／OTLP `event_name` 标识 Log-based Event；旧的 `event.name` Attribute 已废弃。

### 7.2.8 Events 详解：基于 Logs 数据模型的结构化事件

> **当前模型要点**  
> OpenTelemetry Event 不是独立于 Logs 的第六种传输信号。它在数据模型上是一个设置了非空 `EventName` 的 `LogRecord`，语义约定文档也常称其为 `EventRecord`。Event 通过 Logs API、Logs SDK 和 OTLP Logs 管线处理与导出。
>
> **成熟度说明（2026-09-02）**：OTLP `LogRecord.event_name` 字段已经稳定；通用 Event Semantic Conventions 和 Event-to-Span-Event Bridge 仍处于 Development，不同语言 API／SDK 和后端的支持进度可能不同。

### 2.8.1 Event 的定义

OpenTelemetry Event 表示：

> **在一个有业务意义的时间点发生、具有稳定名称和明确结构、可被机器查询与聚合的事件记录。**

它适合描述：

```text
状态切换
生命周期节点
用户交互
策略判定
重试调度
权限决策
模型评估结果
异常发生
配置重载
Agent Handoff
上下文压缩完成
```

Event 的核心不是“有一条日志”，而是：

- 有稳定且低基数的事件类型；
- 有清晰的触发时机；
- 有规定的数据结构；
- 有明确的严重度和时间语义；
- 可以跨语言、框架和后端统一消费；
- 可以与 Trace、Resource 和 InstrumentationScope 关联。

因此可以把 OpenTelemetry Event 理解为：

```text
普通Log：
  主要服务于人类阅读，结构可能不稳定

OpenTelemetry Event：
  具有语义约定、稳定EventName和结构化字段的LogRecord
```

### 2.8.2 Event 不是独立信号

OpenTelemetry 客户端与 OTLP 中仍然使用 Logs 信号承载 Event：

```text
ResourceLogs
└── ScopeLogs
    └── LogRecord
        └── EventName != ""
```

因此不存在一条独立的：

```text
/v1/events
```

Event 通常通过以下入口导出：

```text
OTLP/gRPC LogsService.Export
OTLP/HTTP /v1/logs
```

事件与普通日志可以：

- 共用同一个 LoggerProvider；
- 共用同一个 Logs SDK；
- 共用 BatchLogRecordProcessor；
- 共用 OTLP Log Exporter；
- 进入同一个 Collector Logs Pipeline；
- 在 Collector 或后端按 `EventName` 分流。

### 2.8.3 EventRecord 与 LogRecord

语义约定中常使用 `EventRecord` 表达“事件记录”，但 OTLP 线上的实际消息仍然是 `LogRecord`。

一个 Event 具有以下主要字段：

| 字段 | 作用 | 说明 |
|---|---|---|
| `EventName` | 标识事件类型 | 非空时，该 LogRecord 被识别为 Event |
| `Timestamp` | 事件发生时间 | 应表示源系统实际发生时间 |
| `ObservedTimestamp` | 事件被观察或接收时间 | 通常由 SDK、接收器或 Collector 填充 |
| `SeverityNumber` | 机器可比较的严重度 | 例如 INFO=9、WARN=13、ERROR=17 |
| `SeverityText` | 原始文本严重度 | 例如 `INFO`、`WARNING` |
| `Body` | 面向人类的显示消息或事件正文 | 不应替代结构化 Attributes |
| `Attributes` | 事件的结构化字段 | 用于过滤、聚合、路由和关联 |
| `TraceId` | 关联 Trace | 可选 |
| `SpanId` | 关联具体 Span | 可选 |
| `TraceFlags` | Trace 标志 | 可选 |
| `Resource` | 事件来源实体 | 服务、进程、Pod、主机等 |
| `InstrumentationScope` | 事件产生者 | Instrumentation 或业务模块名称、版本 |
| `DroppedAttributesCount` | 被截断或丢弃的属性数量 | 用于判断数据完整性 |

需要注意：

```text
EventName = 事件类型
TraceId / SpanId = 事件与调用链的关系
Attributes = 本次事件的具体值
Body = 可选显示文本
```

不要把它们混为一谈。

### 2.8.4 EventName 是结构标识，不是事件实例 ID

同一个 `EventName` 下的事件应遵循相同的 Attributes 和 Body 结构。

例如：

```text
EventName = app.agent.permission.decision
```

所有该类型事件都应遵循统一结构：

```text
app.permission.action
app.permission.resource.type
app.permission.decision
app.permission.policy.name
app.permission.risk.level
```

事件实例的唯一标识不应放进 EventName：

```text
错误：
app.agent.permission.decision.request-9ab732
agent.retry.2389823
user.david.clicked.button

正确：
app.agent.permission.decision
app.agent.retry.scheduled
app.ui.button.clicked

动态值：
app.permission.request.id = request-9ab732
app.retry.attempt = 3
enduser.id_hash = ...
```

EventName 是“类名”，不是“对象 ID”。

### 2.8.5 何时应该定义 Event

适合使用 Event 的情况：

1. **瞬时发生**，没有独立持续时间；
2. 是一次有意义的检查点或状态变化；
3. 同一 Span 内可能发生零次、一次或多次；
4. 需要自己的时间戳；
5. 需要自己的严重度；
6. 需要独立保留、过滤、路由或审计；
7. 即使当前 Trace 未采样，也希望保留该记录；
8. 可以定义稳定、低基数的事件名称和字段结构。

典型示例：

```text
app.agent.retry.scheduled
app.agent.permission.decision
app.agent.context.compacted
app.agent.handoff.completed
app.agent.budget.exhausted
gen_ai.evaluation.result
gen_ai.client.operation.exception
```

### 2.8.6 何时不应该定义 Event

以下情况不应优先使用 Event。

#### 操作具有持续时间

应使用 Span：

```text
模型推理
工具执行
向量检索
Agent运行
工作流执行
人工审批等待
沙箱创建
```

#### 数据描述整个操作

应使用 Span Attribute：

```text
gen_ai.request.model
gen_ai.provider.name
gen_ai.agent.name
app.workflow.version
error.type
```

#### 只是无结构诊断文本

应使用普通 Log：

```text
"连接池当前状态异常，稍后重试"
"调试：缓存条目未命中"
```

#### 只需要总体趋势

应使用 Metric：

```text
Agent失败率
权限拒绝次数
重试次数
模型Token用量
评估通过率
```

#### 数据是业务事实源

应使用业务数据库、消息系统或审计存储。OpenTelemetry Event 是遥测，不应默认承担：

```text
订单账本
计费唯一依据
工作流状态机唯一状态
金融交易事实源
不可丢失的安全审计主记录
Event Sourcing事件库
```

对于不能丢失的业务事件，应先通过事务、Outbox 或可靠消息写入业务事实源，再将其镜像为 OTel Event。

### 2.8.7 Event、Span、Attribute、Log、Metric 的选择

| 数据类型 | 是否有持续时间 | 是否有独立时间戳 | 是否需要稳定名称 | 是否可独立于 Trace | 典型用途 |
|---|---:|---:|---:|---:|---|
| Span | 是 | 开始与结束 | 是 | 可以成为根 Span | 操作、调用、等待 |
| Span Attribute | 否 | 否 | 属性键稳定 | 否 | 描述整个 Span |
| Span Event | 否 | 是 | 是 | 否，必须属于 Span | Span 内部时间点 |
| Log-based Event | 否 | 是 | 是，使用 `EventName` | 是 | 状态变化、评估、异常、审计型遥测 |
| 普通 Log | 否 | 是 | 不一定 | 是 | 诊断文本、运行明细 |
| Metric | 聚合窗口 | 数据点时间 | 指标名稳定 | 是 | 趋势、告警、SLO |

快速判断：

```text
有持续时间？
  是 → Span

没有持续时间，但要单独时间戳、严重度或重复发生？
  是 → Event

只是描述整个操作？
  是 → Span Attribute

主要给人阅读且结构不稳定？
  是 → 普通 Log

主要看总量、比率、分位数？
  是 → Metric
```

### 2.8.8 EventName 命名规则

EventName 应满足：

- 使用小写、点分隔、领域化名称；
- 能唯一识别事件结构；
- 不包含动态值；
- 不包含用户输入；
- 不包含 TraceId、RunId、文件路径等实例数据；
- 尽量短且可读；
- 跨服务使用相同事件时，字段定义必须一致；
- 一旦发布，应按 Schema 演进规则治理，不应随意复用旧名称表达新结构。

推荐模式：

```text
<domain>.<component>.<occurrence>
```

例如：

```text
app.agent.retry.scheduled
app.agent.permission.decision
app.agent.context.compacted
app.agent.handoff.completed
gen_ai.evaluation.result
gen_ai.client.operation.exception
http.client.request.exception
device.app.lifecycle
```

不推荐：

```text
agent.retry.3
agent.error.timeout-user-123
permission.decision./home/david/file.txt
model.response.gpt-5.2026-09-02T12:00:00
```

### 2.8.9 `EventName`、`event.name` 与 `otel.event.name`

这三个概念必须区分。

| 名称 | 当前用途 | 状态 |
|---|---|---|
| `LogRecord.EventName`／OTLP `event_name` | 事件的权威类型字段 | 推荐使用 |
| `event.name` Attribute | 旧的事件名称属性 | 已废弃 |
| `otel.event.name` Attribute | 日志 Bridge 或非 OTLP Exporter 的兼容桥接字段 | 稳定，但不是权威线上的事件字段 |

正确做法：

```text
直接使用Logs API：
  设置 event_name / EventName

已有日志框架不支持EventName：
  可按语言和Bridge实现使用 otel.event.name
  由SDK、Bridge或Collector提升为EventName

非OTLP目标不支持EventName：
  Exporter可使用 otel.event.name 表达
```

错误做法：

```text
attributes["event.name"] = "app.agent.retry"
```

`event.name` 旧属性不应继续作为新实现的标准方案。

### 2.8.10 Timestamp 与 ObservedTimestamp

Event 必须有实际发生时间语义。

#### Timestamp

表示事件在源系统中实际发生的时间：

```text
权限引擎做出决定的时间
模型首个Chunk被应用收到的时间
Agent切换状态的时间
评估器产生评分的时间
```

#### ObservedTimestamp

表示事件被遥测系统观察到或接收到的时间：

```text
日志Bridge读取记录的时间
Collector接收到事件的时间
异步队列消费者处理事件的时间
```

两者差值可以揭示：

```text
异步日志队列堆积
Agent进程阻塞
Collector入口拥塞
时钟偏差
批量处理延迟
```

语义约定应规定 Timestamp 的来源，但不应硬编码 ObservedTimestamp；后者通常由 SDK、Bridge、Receiver 或 Collector 根据观察时刻填充。

### 2.8.11 Severity 设计

事件语义约定应尽量规定默认 `SeverityNumber`。严重度表示事件对系统的预期影响，而不只是“发生了什么”。

常见基准：

| 严重度 | Number | 适用情况 |
|---|---:|---|
| TRACE | 1 | 极细粒度诊断，不建议语义约定默认使用 |
| DEBUG | 5 | 正常但需要调试关注的内部事件 |
| INFO | 9 | 正常生命周期、状态切换、成功决策 |
| WARN | 13 | 可恢复异常、客户端调用失败、重试耗尽 |
| ERROR | 17 | 未被应用正常处理、服务端操作失败 |
| FATAL | 21 | 通常导致进程或服务终止 |

Agent 示例：

```text
app.agent.started                    INFO
app.agent.handoff.completed          INFO
app.agent.permission.denied          WARN 或 INFO，取决于是否预期
app.agent.retry.scheduled            WARN
gen_ai.client.operation.exception    WARN
app.agent.runtime.crashed            ERROR/FATAL
```

语义约定通常应指定 Severity Number，而不应规定固定 Severity Text。Severity Text 更适合保留来源日志框架的原始文本。

### 2.8.12 Attributes 与 Body

#### Attributes

Attributes 保存可查询、可过滤、可聚合的结构化字段。

例如：

```text
EventName = app.agent.retry.scheduled

Attributes:
  app.retry.attempt = 3
  app.retry.reason = rate_limit
  app.retry.backoff_ms = 2000
  gen_ai.provider.name = openai
  gen_ai.request.model = model-x
```

设计规则：

- 复用现有 Semantic Conventions；
- 事件结果为失败时使用低基数 `error.type`；
- 不默认复制父 Span 的全部属性；
- 只有事件脱离 Span 后仍必须解释时，才复制必要字段；
- 需要过滤和分组的值放 Attributes；
- 动态长文本不放低基数属性；
- 大型结构采用受控复杂属性或外部工件引用；
- 标记可能包含 PII、源代码、Prompt 或密钥的字段。

#### Body

Body 适合放面向人类的显示消息：

```text
"Agent permission request was denied by workspace policy"
```

不要把 Body 作为唯一机器语义来源：

```text
错误：
Body = "third retry due to rate limit after 2000ms"

正确：
Body = "Agent retry scheduled"
Attributes:
  app.retry.attempt = 3
  app.retry.reason = rate_limit
  app.retry.backoff_ms = 2000
```

### 2.8.13 Event 与 Trace Context 的关联

Event 可以在有或没有活动 Span 的情况下发出。

#### 有活动 Span

```text
invoke_agent reviewer
└── Event: app.agent.permission.decision
```

Event 应携带当前：

```text
TraceId
SpanId
TraceFlags
```

这样后端可以从事件跳到对应 Agent Span。

#### 没有活动 Span

以下事件仍然有效：

```text
service.configuration.reloaded
app.agent.scheduler.started
app.agent.memory.repair.completed
离线评估结果
全局未处理异常
```

它们可以只有：

```text
Resource
InstrumentationScope
EventName
Timestamp
Attributes
```

#### 异步场景

如果 Event 在异步线程、队列或延迟任务中发出，必须明确：

- 是继承原 Context；
- 还是创建新的 Trace；
- 还是作为无 Trace Event；
- 是否使用 Span Link 关联原操作。

不要依赖线程切换后“当前 Span 仍然存在”的偶然行为。

### 2.8.14 Event 的 API、SDK 与 OTLP 协同

完整链路：

```mermaid
sequenceDiagram
    participant Framework as Agent或框架
    participant Inst as Instrumentation
    participant API as Logs API
    participant SDK as Logs SDK
    participant Exp as OTLP Log Exporter
    participant Col as Collector
    participant Back as Backend

    Framework->>Inst: 生命周期事件或异常
    Inst->>API: Logger.emit(event_name=...)
    API->>SDK: 生成并提交LogRecord
    SDK->>SDK: 限制、过滤、处理、批量
    SDK->>Exp: 导出ReadableLogRecord
    Exp->>Col: OTLP Logs
    Col->>Col: 脱敏、过滤、路由、转换
    Col->>Back: 日志/事件存储
```

职责分工：

| 组件 | Events 职责 |
|---|---|
| Instrumentation | 决定何时发事件、提取字段 |
| Logs API | 提供 `Logger.emit` 和 `EventName` 参数 |
| Logs SDK | 限制、处理、批量和导出 |
| Semantic Conventions | 定义 EventName、触发时机、严重度和字段 |
| OTLP | 用 LogRecord 的 `event_name` 传输 |
| Collector | 脱敏、过滤、转换、路由和复制 |
| Backend | 存储、索引、查询、关联和告警 |

### 2.8.15 Logs API 如何发出 Event

当前 Logs API 的核心对象仍是：

```text
LoggerProvider
Logger
LogRecord
```

`Logger.emit` 可以接收：

```text
Timestamp
ObservedTimestamp
Context
SeverityNumber
SeverityText
Body
Attributes
EventName
Exception
```

只要 `EventName` 非空，该 LogRecord 就具有 Event 语义。

概念示例：

```python
logger.emit(
    event_name="app.agent.permission.decision",
    severity_number=SeverityNumber.INFO,
    body="Agent permission decision",
    attributes={
        "app.permission.action": "workspace.write",
        "app.permission.decision": "deny",
        "app.permission.policy.name": "workspace-boundary",
    },
)
```

Instrumentation 作者应通过 Logs API 发 Event，而不是绑定某个第三方日志库。这样可以避免：

- 第三方 Logger 依赖冲突；
- 用户没有配置对应 Appender；
- 事件结构被文本格式化器破坏；
- `EventName` 无法被稳定设置；
- 无法统一进入 OTel Logs SDK。

### 2.8.16 `Logger.Enabled` 与昂贵事件构造

Event 可能包含：

```text
完整消息历史
工具定义
评估解释
异常Stacktrace
RAG文档摘要
序列化后的状态快照
```

这些字段构造成本可能很高。Logs API 定义了 `Enabled` 能力，用于在创建昂贵数据前判断当前 Logger、Severity 和 EventName 是否可能被处理。

概念模式：

```python
if logger.enabled(
    severity_number=SeverityNumber.INFO,
    event_name="gen_ai.client.inference.operation.details",
):
    details = build_redacted_model_details()
    logger.emit(
        event_name="gen_ai.client.inference.operation.details",
        attributes=details,
    )
```

注意：

- `Enabled` 是性能优化，不是正确性前置条件；
- 即使没有先调用 `Enabled`，Processor 仍必须独立执行过滤；
- 返回值可能随动态配置变化，不应永久缓存；
- 不同语言 SDK 的实现进度可能不同；
- 如果语言 SDK 暂不支持，应使用配置开关在业务侧避免昂贵构造。

### 2.8.17 Logs SDK 中的 Event 处理

Event 与普通 Log 共用 Logs SDK：

```text
LoggerProvider
└── Logger
    └── LogRecordProcessor
        ├── Filter / Redaction Processor
        ├── Event-to-Span-Event Bridge
        ├── SimpleLogRecordProcessor
        └── BatchLogRecordProcessor
            └── LogRecordExporter
```

SDK 负责：

- 将当前 Context 写入 TraceId／SpanId；
- 应用 LogRecord 属性数量和长度限制；
- 统计 `DroppedAttributesCount`；
- 调用 Processor；
- 队列和批量处理；
- ForceFlush 与 Shutdown；
- 调用 OTLP 或其他 Log Exporter。

事件不应绕过统一 SDK 直接由每个框架单独向后端发送，否则会导致：

```text
Resource不一致
Trace关联丢失
脱敏策略分散
Exporter重复
重试策略冲突
无法统一Shutdown
```

### 2.8.18 Event-to-Span-Event Bridge

Logs SDK 规范定义了处于 Development 状态的 Event-to-Span-Event Bridge。它可以把 Log-based Event 同时附加为当前 Span 的 Span Event。

只有同时满足以下条件才应转换：

1. LogRecord 的 `EventName` 非空；
2. LogRecord 具有有效 TraceId 和 SpanId；
3. 解析出的 Context 中存在正在记录的当前 Span；
4. LogRecord 的 TraceId／SpanId 与当前 Span 完全一致。

映射规则：

| Log-based Event | Span Event |
|---|---|
| `EventName` | Event name |
| `Timestamp`，否则 `ObservedTimestamp` | Event timestamp |
| `Attributes` | Event attributes |
| `SeverityNumber` | 默认不会自然映射 |
| `SeverityText` | 默认不会自然映射 |
| `Body` | 默认不会自然映射 |

需要注意：

- Bridge 不应阻止原 LogRecord 继续走 Logs Pipeline；
- 同时导出 Event 和 Span Event 会形成双份记录；
- Backend 必须去重或明确两份数据的用途；
- 没有活动且正在记录的 Span 时，Bridge 不会创建 Span Event；
- 只依赖 Bridge 会使未采样 Trace 下的事件丢失；
- Body、Severity 等字段在 Span Event 中没有等价的一等字段。

### 2.8.19 Log-based Event 与 Span Event 的关系

这两种 Event 不是同一个数据模型。

| 维度 | Log-based Event | Span Event |
|---|---|---|
| 承载信号 | Logs | Traces |
| 数据结构 | LogRecord + EventName | Span 内部 Event |
| 是否可独立存在 | 可以 | 不可以 |
| 是否必须有活动 Span | 否 | 是 |
| Severity | 原生支持 | 无一等字段 |
| Body | 原生支持 | 无一等字段 |
| 导出时机 | 可在事件产生后进入日志批处理 | 通常随 Span 结束导出 |
| 未采样 Trace 下是否可保留 | 可以，取决于 Logs 管线 | 通常不可以 |
| 后端是否必须支持 Logs | 是 | 否，只需 Trace 后端 |
| 适合高价值独立事件 | 更适合 | 受 Span 生命周期限制 |
| 典型 API | `Logger.emit(event_name=...)` | `Span.add_event(...)` |

当前工程判断：

- 新的共享 Instrumentation 更适合使用 Log-based Event；
- 已有 Span Event 实现可继续兼容；
- 需要 Trace-only 后端时，可以启用 Bridge；
- 应避免无计划地同时双写；
- 业务应用可以根据语言 SDK、后端和版本选择迁移节奏。

### 2.8.20 Python 直接发 Event 示例

下面示例使用 Logs API／SDK，并通过 OTLP Logs 导出。具体包路径可能随 Python SDK 版本演进，生产项目应锁定版本。

```python
from time import time_ns

from opentelemetry._logs import (
    SeverityNumber,
    get_logger,
    set_logger_provider,
)
from opentelemetry.context import get_current
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter,
)
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


resource = Resource.create(
    {
        "service.name": "agent-runtime",
        "service.version": "1.0.0",
        "deployment.environment.name": "production",
    }
)

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(
        OTLPLogExporter(endpoint="http://otel-collector:4317")
    )
)
set_logger_provider(logger_provider)

event_logger = get_logger(
    "com.example.agent.events",
    "1.0.0",
)

event_logger.emit(
    timestamp=time_ns(),
    context=get_current(),
    event_name="app.agent.permission.decision",
    severity_number=SeverityNumber.INFO,
    body="Agent permission decision",
    attributes={
        "app.permission.action": "workspace.write",
        "app.permission.resource.type": "file",
        "app.permission.decision": "deny",
        "app.permission.policy.name": "workspace-boundary",
        "app.permission.risk.level": "high",
    },
)

logger_provider.force_flush()
```

如果当前 Context 中存在活动 Span，SDK 可以将 TraceId 和 SpanId 关联到事件；否则该 Event 仍可以作为独立 Logs 记录导出。

### 2.8.21 通过已有日志框架发 Event

已有日志框架通常原生支持：

```text
时间
级别
正文
结构化字段
Logger名称
```

但不一定支持 OpenTelemetry 的一等 `EventName`。

可选方案：

1. 使用支持 EventName 的 OTel Appender／Handler；
2. 在日志记录中使用 `otel.event.name` 兼容字段；
3. 由 Bridge、SDK Processor 或 Collector 将其提升到 `EventName`；
4. 对强语义 Event 直接调用 Logs API；
5. 普通诊断日志继续使用原日志框架。

注意：

- `otel.event.name` 是否会被自动提升，取决于语言、版本和 Bridge；
- 不支持提升时，它只会成为普通 Attribute；
- 新代码不应重新使用已废弃的 `event.name`；
- 关键 Event 应通过集成测试验证最终 OTLP 中确实出现 `event_name`。

### 2.8.22 Collector 中的 Event 治理

Collector 应把 Event 当作 Logs Pipeline 中的结构化记录处理。

常见策略：

```text
按EventName路由
按Severity过滤
删除或哈希敏感字段
截断超大Body
将高价值Event复制到审计存储
将评估Event发送到分析仓库
将异常Event发送到告警后端
控制不同租户的保留期
```

示例路由逻辑：

```text
gen_ai.evaluation.result
    → Eval Analytics

gen_ai.client.operation.exception
    → Logs Backend + Alerting

app.agent.permission.decision
    → Security Audit Backend

gen_ai.client.inference.operation.details
    → 仅受控高安全存储

普通无EventName日志
    → 通用日志平台
```

Collector 转换时必须保留：

```text
EventName
Timestamp
Severity
TraceId
SpanId
Resource
InstrumentationScope
```

如果目标系统不支持 EventName，可在导出边界映射为 `otel.event.name`，但不应在 OTel 内部主链路中把事件降级为无类型普通日志。

### 2.8.23 Agent 领域 Event 分类

Agent 系统可定义以下事件族。

| EventName | 触发时机 | 建议严重度 | 核心字段 |
|---|---|---:|---|
| `app.agent.lifecycle.started` | Agent 开始运行 | INFO | agent、run、workflow |
| `app.agent.lifecycle.completed` | Agent 正常完成 | INFO | outcome、iterations |
| `app.agent.lifecycle.cancelled` | 用户或系统取消 | INFO／DEBUG | cancel reason |
| `app.agent.loop.iteration.started` | 新一轮 Agent Loop 开始 | DEBUG | iteration |
| `app.agent.retry.scheduled` | 已决定下一次重试 | WARN | attempt、reason、backoff |
| `app.agent.loop.detected` | 检测到循环或无进展 | WARN | detector、window、signature |
| `app.agent.handoff.completed` | Agent 交接完成 | INFO | from、to、reason |
| `app.agent.permission.decision` | 权限引擎做出决定 | INFO／WARN | action、resource、decision、policy |
| `app.agent.guardrail.triggered` | Guardrail 命中 | WARN | guardrail、category、action |
| `app.agent.context.compacted` | 上下文压缩完成 | INFO | before tokens、after tokens、strategy |
| `app.agent.memory.promoted` | 信息晋升为长期记忆 | INFO | memory type、policy、evidence count |
| `app.agent.human_approval.requested` | 请求人工审批 | INFO | approval type、risk |
| `app.agent.human_approval.resolved` | 审批完成 | INFO／WARN | decision、actor type、wait duration |
| `app.agent.budget.exhausted` | Token、时间或轮次预算耗尽 | WARN | budget type、limit、actual |
| `app.agent.runtime.crashed` | Agent Runtime 异常终止 | ERROR／FATAL | error type、phase |

事件命名和字段应由平台级 Event Catalog 管理，避免每个 Agent 自行创造不兼容结构。

### 2.8.24 不要用 Event 替代应有的 Span

以下操作有明确持续时间，应建 Span，而不是只发开始和结束两个 Event：

```text
invoke_agent
invoke_workflow
chat
execute_tool
retrieval
search_memory
sandbox.create
human_approval.wait
test.run
```

不推荐：

```text
Event tool.started
Event tool.completed
```

推荐：

```text
Span execute_tool
├── Event retry.scheduled
├── Event permission.decision
└── Span Attributes:
    tool.name
    tool.type
    result
```

两个 Event 无法自然表达：

```text
持续时间
父子关系
关键路径
SpanKind
Status
Links
Head Sampling属性
```

### 2.8.25 GenAI 标准 Event 总览

当前 GenAI Semantic Conventions 主要定义以下 Event：

| EventName | 作用 | Requirement Level | 规范严重度／工程建议 |
|---|---|---|---|
| `gen_ai.client.inference.operation.details` | 保存推理请求、输入输出和参数详情 | Opt-In | 规范未指定；工程上通常使用 INFO |
| `gen_ai.evaluation.result` | 保存对 GenAI 输出的评估结果 | Recommended | 规范未指定；工程上通常使用 INFO |
| `gen_ai.client.operation.exception` | 保存 GenAI Client 操作异常 | Recommended | WARN，SeverityNumber 13（规范建议） |

这些 Event 均基于 LogRecord/EventName 模型，而不是新的独立 Events 信号。前两个事件的规范当前未指定默认 Severity；表中的 INFO 是工程建议。异常事件则明确建议使用 WARN（SeverityNumber 13）。

GenAI Events 整体仍处于 Development，语言实现覆盖不完全。生产系统应：

- 锁定 SDK 和 Instrumentation 版本；
- 做能力探测；
- 校验 EventName 是否进入 OTLP；
- 提供兼容 Mapper；
- 避免同时把同一语义写为多份 Event 和 Span Event；
- 对输入输出详情执行严格 Opt-In。

### 2.8.26 `gen_ai.client.inference.operation.details`

该 Event 用于将模型交互详情独立于 Trace 存储：

```text
EventName = gen_ai.client.inference.operation.details
```

它适合包含：

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.model
gen_ai.response.id
gen_ai.input.messages
gen_ai.output.messages
gen_ai.system_instructions
gen_ai.tool.definitions
gen_ai.request.*
gen_ai.usage.*
server.address
server.port
error.type
```

概念示例（以下 INFO 是工程默认值，不是该事件的规范强制值）：

```yaml
EventName: gen_ai.client.inference.operation.details
SeverityNumber: INFO
TraceId: 4bf92f3577b34da6a3ce929d0e0e4736
SpanId: 00f067aa0ba902b7
Attributes:
  gen_ai.operation.name: chat
  gen_ai.provider.name: openai
  gen_ai.request.model: model-x
  gen_ai.response.id: resp_123
  gen_ai.usage.input_tokens: 1024
  gen_ai.usage.output_tokens: 256
  gen_ai.input.messages:
    - role: user
      parts:
        - type: text
          content: "<redacted>"
```

该 Event 是 **Opt-In**，原因包括：

- 输入输出可能包含 PII；
- 可能包含源代码、密钥和业务数据；
- 工具定义和多模态内容体积很大；
- 每次模型调用都发详情会显著增加日志量；
- Trace 已经可能记录部分相同字段；
- 下游评测系统可能拥有不同保留期和权限模型。

更安全的生产方式：

```text
Event只保留：
  content_hash
  object_storage_ref
  redaction_policy_version
  content_type
  token_count

正文：
  加密存入受控工件存储
```

### 2.8.27 `gen_ai.evaluation.result`

评估结果 Event：

```text
EventName = gen_ai.evaluation.result
```

核心字段：

```text
gen_ai.evaluation.name
gen_ai.evaluation.score.value
gen_ai.evaluation.score.label
gen_ai.evaluation.explanation
gen_ai.response.id
error.type
```

关联规则：

1. 能获得被评估 GenAI Span 时，Event 应关联该 Span Context；
2. 无法获得 SpanId 时，应尽量提供 `gen_ai.response.id`；
3. 企业内部可补充 evaluator、dataset、rubric 和 run 版本；
4. `score.label` 应保持低基数；
5. Explanation 可能包含模型输出片段，需要脱敏和长度控制。

示例（以下 INFO 是工程默认值，不是该事件的规范强制值）：

```yaml
EventName: gen_ai.evaluation.result
SeverityNumber: INFO
Attributes:
  gen_ai.evaluation.name: answer_relevance
  gen_ai.evaluation.score.value: 0.92
  gen_ai.evaluation.score.label: pass
  gen_ai.evaluation.explanation: "主要事实已覆盖"
  gen_ai.response.id: resp_123
  app.eval.evaluator.version: 3.1.0
  app.eval.dataset.version: support-v12
  app.eval.rubric.id: relevance-rubric-v4
```

评估 Event 与 Metric 的关系：

```text
Event：
  保存每次评估的明细、解释和关联信息

Metric：
  聚合评估均值、通过率、分位数和趋势
```

不要把高基数 `response.id`、`trace_id` 作为 Metric 标签。

### 2.8.28 `gen_ai.client.operation.exception`

GenAI Client 异常 Event：

```text
EventName = gen_ai.client.operation.exception
SeverityNumber = WARN
```

核心字段：

```text
exception.type
exception.message
exception.stacktrace
```

建议：

- 一次逻辑 GenAI 操作最终失败时记录一次；
- SDK 内部重试的每个网络尝试由 HTTP／RPC 层观测；
- 不要在 Agent、LangChain、OpenAI SDK、HTTPX 四层重复记录同一个异常；
- Event 应关联发生异常的 GenAI Span；
- Span 同时设置 `error.type` 和 Status；
- Stacktrace、消息和 Provider 错误正文必须脱敏；
- 人为取消或正常控制流异常可使用 DEBUG 或不记录；
- 客户端可恢复异常通常使用 WARN；
- 服务端未处理异常通常使用 ERROR。

合理结构：

```text
chat model-x
├── HTTP attempt 1    429
├── HTTP attempt 2    timeout
└── Event gen_ai.client.operation.exception
    exception.type = ProviderUnavailable
```

### 2.8.29 流式响应中的 Event 设计

流式 LLM 调用可能产生数百到数万个 Chunk。不要默认为每个 Token 或 Chunk 发 Event：

```text
错误：
gen_ai.token.received × 20,000
```

这会导致：

- Logs 数量爆炸；
- Trace/Log 后端索引压力；
- 网络和序列化开销；
- 敏感内容泄露面扩大；
- 事件顺序和丢包处理复杂；
- Collector 队列占满。

推荐：

```text
首Chunk延迟
  → Metric：time_to_first_chunk
  → 必要时 Span Event 或低量Event

Chunk间隔
  → Metric：time_per_output_chunk

完整输出
  → inference.operation.details Event 或外部工件

流被取消
  → 状态属性或取消Event

流式消费异常
  → exception Event
```

只有在协议调试或受控测试环境中，才考虑逐 Chunk 结构化日志，并设置采样、上限和短保留期。

### 2.8.30 Event 的采样、过滤与保留

Log-based Event 不受 Trace Head Sampling 的直接控制，因此即使 Span 未采样，Event 仍可能被导出。这是优势，也会带来成本风险。

建议分级：

| Event 类型 | 采集策略 | 保留期 |
|---|---|---|
| 权限拒绝、Guardrail、Runtime Crash | 100% | 中长期 |
| GenAI Exception | 100% 或高比例 | 中期 |
| Eval Result | 依据评测计划 | 中期或分析仓库 |
| Handoff、Budget Exhausted | 高比例 | 中期 |
| Loop Iteration | 低比例或仅异常 Trace | 短期 |
| Inference Details | 明确 Opt-In、受控采样 | 极短或专用存储 |
| Debug 生命周期 | 开发环境或动态开启 | 极短 |

过滤维度应优先使用：

```text
EventName
SeverityNumber
service.name
deployment.environment.name
gen_ai.provider.name
gen_ai.operation.name
error.type
app.permission.decision
```

### 2.8.31 基数治理

EventName 必须保持低基数，但 Event Attributes 可以包含高基数关联 ID。后端索引策略应区分二者。

适合索引：

```text
EventName
SeverityNumber
service.name
agent.name
agent.version
workflow.name
gen_ai.provider.name
gen_ai.request.model
error.type
permission.decision
eval.name
eval.score.label
```

谨慎索引：

```text
trace_id
span_id
conversation_id
agent.run.id
response.id
tool.call.id
file.path
user.id_hash
artifact_ref
```

不应默认采集或索引：

```text
Prompt正文
Completion正文
Tool完整参数
完整文档
源代码
Stacktrace全文
任意用户输入
```

### 2.8.32 顺序、重复与幂等

OpenTelemetry Event 是遥测记录，不提供业务消息系统级别的：

```text
Exactly-once
全局顺序
事务提交
消费确认
业务幂等
永久持久化
```

在 Agent 多线程、多进程和 Collector 重试场景中，后端可能观察到：

```text
乱序
延迟
批次重排
重复
部分丢失
```

如果消费方需要重建状态，可增加自定义字段：

```text
app.event.id
app.event.sequence
app.agent.run.id
app.workflow.run.id
app.state.version
app.event.producer.timestamp
```

但这仍不能把 OTel Event 变成业务事实源。真正的状态重建应依赖业务存储。

### 2.8.33 Event Schema 与版本治理

每种 Event 都应维护 Event Catalog：

| 字段 | 示例 |
|---|---|
| EventName | `app.agent.permission.decision` |
| Owner | Agent Runtime Team |
| Status | Development／Stable／Deprecated |
| Trigger | 权限引擎完成一次决策 |
| Default Severity | INFO |
| Required Attributes | action、decision、policy |
| Optional Attributes | risk level、resource hash |
| Sensitive Fields | resource path、user identity |
| Cardinality | low／medium／high |
| Retention | 30 days |
| Schema Version | 2.1.0 |
| Compatibility Policy | additive only within major |
| Downstream Consumers | Security、Eval、APM |

演进原则：

- 新增可选字段通常可向后兼容；
- 修改字段语义应发布新 Schema；
- 修改字段类型通常属于破坏性变化；
- 不要复用已废弃 EventName 表达新事件；
- 通过 InstrumentationScope Version 或 Schema URL 标记版本；
- Collector Transform 只做迁移，不应长期隐藏生产者不一致；
- 对 EventName、字段集合和敏感数据做契约测试。

### 2.8.34 Events 与 CloudEvents、领域事件、Event Sourcing 的区别

| 概念 | 目标 | 可靠性定位 | 是否是 OTel 遥测 |
|---|---|---|---|
| OpenTelemetry Event | 结构化可观测事件 | Best-effort 遥测 | 是 |
| CloudEvents | 跨系统事件信封规范 | 取决于消息系统 | 否，但可被 OTel 观测 |
| 领域事件 | 表达业务领域事实 | 由业务架构决定 | 否，可镜像为 OTel Event |
| Event Sourcing Event | 重建业务状态的事实记录 | 必须持久、严格治理 | 否 |
| Span Event | Span 内部时间点 | 依赖 Trace 记录和导出 | 属于 Trace 信号 |

例如“订单已支付”应先成为可靠领域事件；OpenTelemetry 可以记录：

```text
EventName = commerce.payment.completed
```

用于观测，但不能把可丢失的遥测副本作为财务账本。

### 2.8.35 Event 查询与分析

后端常见查询：

```text
按EventName统计次数
按Severity和error.type分组
按Agent版本比较权限拒绝率
从Eval Result跳到被评估Trace
从Exception Event定位模型Span
按Conversation关联多次模型调用
按Run ID重建Agent关键状态变化
```

示意查询逻辑：

```sql
SELECT
    event_name,
    attributes['error.type'],
    COUNT(*)
FROM otel_logs
WHERE
    resource['service.name'] = 'agent-runtime'
    AND event_name IS NOT NULL
GROUP BY
    event_name,
    attributes['error.type'];
```

评估分析：

```sql
SELECT
    attributes['gen_ai.request.model'],
    AVG(attributes['gen_ai.evaluation.score.value'])
FROM otel_logs
WHERE event_name = 'gen_ai.evaluation.result'
GROUP BY attributes['gen_ai.request.model'];
```

具体 SQL 取决于后端，核心是 EventName 成为第一过滤维度。

### 2.8.36 Event 接入验收清单

#### 数据模型

- [ ] OTLP LogRecord 中存在非空 `event_name`；
- [ ] 未继续使用已废弃的 `event.name`；
- [ ] `otel.event.name` 只用于必要的 Bridge；
- [ ] EventName 不含动态 ID；
- [ ] 同 EventName 的字段结构一致；
- [ ] Timestamp 表示真实发生时间；
- [ ] SeverityNumber 符合事件影响；
- [ ] TraceId／SpanId 关联正确；
- [ ] Resource 与 InstrumentationScope 完整。

#### 性能和可靠性

- [ ] 事件构造有开关或 Enabled 优化；
- [ ] 大型结构有长度和数量限制；
- [ ] Batch Processor 队列容量经过压测；
- [ ] Collector 过载时的丢弃策略明确；
- [ ] 高价值事件有独立路由；
- [ ] 不把 Event 当作业务唯一事实源；
- [ ] 重复、乱序和丢失的消费策略明确。

#### 安全

- [ ] Prompt、Completion 默认关闭；
- [ ] Tool 参数和结果默认关闭；
- [ ] Stacktrace 和异常消息脱敏；
- [ ] 文件路径、用户标识和会话标识最小化；
- [ ] 租户隔离、保留期和访问审计已配置；
- [ ] `gen_ai.client.inference.operation.details` 明确 Opt-In。

#### Agent 语义

- [ ] 有持续时间的动作使用 Span；
- [ ] 生命周期时间点使用 Event；
- [ ] 整体属性使用 Span Attribute；
- [ ] 总体趋势使用 Metric；
- [ ] Eval 明细使用 `gen_ai.evaluation.result`；
- [ ] GenAI 异常使用统一异常 Event；
- [ ] 不为每个流式 Token 发 Event；
- [ ] Span Event 与 Log-based Event 双写规则明确。

### 7.2.9 Artifact 详解：Agent 可交付物、外部内容与遥测关联

### 2.9.1 先明确：Artifact 不是 OpenTelemetry 信号

在 Agent 系统中，**Artifact** 通常表示任务执行过程中产生、接收、转换或交付的持久化内容，例如报告、代码补丁、图片、表格、测试结果、检索证据、数据集切片、沙箱快照或模型生成文件。

它和 Trace、Metric、Log、Event 的关系是：

```text
Artifact
  → 是 Agent 或业务领域对象
  → 保存真正的内容、版本、结构和生命周期
  → 通常位于对象存储、文件系统、数据库或制品库

OpenTelemetry
  → 观测 Artifact 的创建、读取、转换、发布和删除过程
  → 记录 Artifact 的标识、类型、大小、哈希和存储引用
  → 不应替代 Artifact Store
  → 不应通过 OTLP 运输大体积 Artifact 正文
```

截至本文整理日期，OpenTelemetry GenAI Semantic Conventions 已经规范模型、Agent、Workflow、Tool、Retrieval、Memory、MCP、Metric 和 Event 等语义，但 **Artifact 仍主要处于提案和讨论阶段**。因此，现阶段不应把自定义字段伪装为稳定标准，也不应自行创建看似官方的 `gen_ai.artifact.*` 字段后宣称兼容规范。

推荐做法是：

```text
内部稳定领域模型：artifact.*
        ↓ SemConv Mapper
当前自定义遥测：app.artifact.*
        ↓ 未来标准稳定后
映射到正式的 gen_ai.artifact.* 或其他标准字段
```

> 本章中的 `app.artifact.*`、`app.artifact.*` Metric 和 `app.artifact.*` EventName 都是本文建议的**应用自定义命名空间**，不是当前 OpenTelemetry 官方稳定语义约定。

### 2.9.2 为什么 Agent 系统需要 Artifact

普通聊天系统主要处理 Message，而可执行 Agent 会产生大量超出消息正文承载能力的结果：

- Coding Agent 生成代码补丁、完整文件、测试日志和构建包；
- Data Agent 生成 CSV、Parquet、SQL 结果和可视化图片；
- Research Agent 生成报告、引用集合和证据快照；
- Office Agent 生成 Markdown、Word、PDF、PPT 和 Spreadsheet；
- Browser Agent 生成截图、下载文件和网页快照；
- RAG Agent 产生检索结果集、重排结果和引用证据；
- 多 Agent 系统在 Agent 之间交接计划、草稿、评审意见和最终产物；
- Eval 系统保存评分证据、Rubric、失败样本和优化报告；
- Durable Agent Runtime 保存 Checkpoint、沙箱快照和可恢复状态。

如果把这些内容全部塞入消息或 Span Attribute，会产生以下问题：

```text
上下文窗口膨胀
遥测体积失控
敏感数据扩散
Trace 查询性能下降
采样后内容不可恢复
版本与血缘关系丢失
无法按对象实施权限和保留策略
```

Artifact 的价值在于把“Agent 产生的内容”从“Agent 的对话和遥测”中解耦：

```text
Message 负责交互
State 负责运行状态
Memory 负责未来复用的知识
Artifact 负责可交付、可寻址、可验证的内容
Telemetry 负责解释它如何产生和被使用
```

### 2.9.3 Artifact 的工作定义

本文采用以下工程定义：

> **Artifact 是 Agent 任务中的可寻址、可持久化、可版本化、可验证，并能够作为输入、输出、证据、中间结果或检查点被后续系统消费的内容对象。**

一个对象不一定必须是最终交付物才能称为 Artifact。它可以承担以下角色：

| 角色 | 含义 | 示例 |
|---|---|---|
| `input` | 任务输入内容 | 用户上传文件、需求文档、数据集 |
| `output` | 面向用户或下游的最终结果 | 报告、代码补丁、图片、PPT |
| `intermediate` | 可复用的中间产物 | 查询结果、规划草稿、解析后的 AST |
| `evidence` | 支撑判断或评估的证据 | 测试报告、截图、引用片段、评分依据 |
| `checkpoint` | 用于恢复执行的状态快照 | Graph State、沙箱快照、执行游标 |
| `attachment` | 与消息或事件关联的附件 | Tool 原始返回文件、下载内容 |
| `bundle` | 由多个 Part 或文件组成的集合 | 代码变更包、评测包、交付包 |

Artifact 是否值得持久化，应根据其业务价值、复用需求、安全等级和重建成本判断。瞬时、低价值、可廉价重算的数据可以只留在内存中。

### 2.9.4 Artifact 与相邻概念的边界

| 概念 | 核心职责 | 是否保存真实内容 | 是否有持续时间 | 是否应作为事实源 |
|---|---|---:|---:|---:|
| Message | 用户、Agent 和 Tool 之间的交互载体 | 可以，但通常受上下文限制 | 否 | 会话事实源之一 |
| Artifact | 持久化可交付内容、证据和中间产物 | **是** | 否 | **内容和版本的事实源** |
| Span | 描述一个有开始和结束的操作 | 只应放小型元数据或引用 | 是 | 否，可能被采样 |
| Event | 描述一个有意义的瞬时事实 | 只应放小型元数据或引用 | 否 | 否，可能丢失或重复 |
| Log | 诊断、审计和运行明细 | 可有正文，但不适合大对象 | 否 | 通常不是业务唯一事实源 |
| Metric | 聚合趋势、SLO 和告警 | 否 | 时间序列 | 否 |
| Memory | 为未来推理复用而抽取的知识或偏好 | 通常保存摘要或结构化知识 | 否 | 记忆域的事实源 |
| Checkpoint | 为恢复执行保存的运行状态 | 是 | 否 | Runtime 恢复事实源 |
| Attachment | 消息或 UI 层的附件表达 | 可能内嵌，也可能仅引用 | 否 | 取决于实现 |
| Chroma Record | 检索索引中的向量、文档和元数据记录 | 可保存文本，但不宜承担通用 Artifact Store | 否 | 检索索引事实源 |

几个关键判断：

1. **Artifact 不是 Message。** Message 面向对话和模型上下文；Artifact 面向持久化和下游消费。
2. **Artifact 不是 Span。** Span 表示生成或处理 Artifact 的操作，而不是产物本身。
3. **Artifact 不是 Event。** Event 可以表达“Artifact 已发布”，但 Artifact 内容必须由独立存储保存。
4. **Artifact 不是 Memory。** Artifact 可以被 Memory 提取器消费，但原始文档和抽取后的长期知识是两个对象。
5. **Artifact 不是向量索引记录。** 向量库通常只保存可检索表示或副本，原始 Artifact 应有独立权威存储。

### 2.9.5 Artifact 的典型分类

#### 按内容形态分类

```text
text
  Markdown、纯文本、源代码、日志

document
  PDF、DOCX、PPTX、HTML

data
  JSON、CSV、Parquet、数据库导出

media
  PNG、JPEG、SVG、音频、视频

executable
  Script、二进制、容器镜像、WASM、安装包

patch
  Unified Diff、Git Commit、变更集

state
  Checkpoint、Snapshot、Serialized Graph State

bundle
  ZIP、目录树、Manifest + 多个 Parts
```

#### 按产生阶段分类

```text
input → intermediate → candidate → validated → published
```

#### 按业务用途分类

```text
用户交付物
机器消费结果
模型上下文附件
评估证据
审计证据
运行恢复快照
跨Agent交接包
```

内容形态、生命周期状态和业务角色应分开建模，不要用一个 `type` 字段同时表达三者。

### 2.9.6 Artifact 的生命周期

一个生产级 Artifact 通常经历：

```mermaid
stateDiagram-v2
    [*] --> Staging: 创建写入会话
    Staging --> Uploading: 写入内容或分片
    Uploading --> Validating: 完成上传
    Validating --> Quarantined: 安全或格式检查失败
    Validating --> Ready: 哈希、大小、Schema校验通过
    Ready --> Published: 对用户或下游可见
    Published --> Consumed: 被下载、检索或继续处理
    Published --> Superseded: 被新版本替代
    Ready --> Expired: 超过保留期限
    Published --> Expired: 超过保留期限
    Quarantined --> Deleted: 清理
    Superseded --> Deleted: 清理
    Expired --> Deleted: 清理
    Deleted --> [*]
```

推荐状态：

| 状态 | 含义 |
|---|---|
| `staging` | 元数据已创建，正文尚未完整写入 |
| `uploading` | 正在接收流式或分片内容 |
| `validating` | 正在进行格式、大小、哈希、安全和 Schema 校验 |
| `ready` | 内容完整，可被内部消费 |
| `published` | 已进入用户或外部系统可见范围 |
| `quarantined` | 发现恶意内容、格式异常或安全策略冲突 |
| `superseded` | 已有新版本取代当前版本 |
| `expired` | 逻辑过期，等待删除或归档 |
| `deleted` | 元数据保留最小墓碑，正文已删除 |

不要仅靠“对象已经上传成功”判断 Artifact 可用。对象存储成功、元数据事务成功、病毒扫描通过、格式验证通过和发布授权是不同阶段。

### 2.9.7 三平面模型：内容、元数据与遥测

Artifact 体系可以拆分为三个平面：

```mermaid
flowchart LR
    subgraph DataPlane["内容平面"]
        O["Object Store / File Store<br/>真实字节和分片"]
    end

    subgraph MetadataPlane["元数据平面"]
        C["Artifact Catalog<br/>标识、版本、哈希、ACL、血缘"]
        V["Vector / Search Index<br/>可检索派生表示"]
    end

    subgraph TelemetryPlane["观测平面"]
        T["Trace<br/>生成和消费路径"]
        E["Event / Log<br/>生命周期与审计"]
        M["Metric<br/>容量、时延、失败率"]
    end

    C --> O
    C --> V
    T -. artifact_id .-> C
    E -. artifact_id .-> C
    M -. type / status .-> C
```

职责边界：

- **内容平面** 保存 Artifact 正文和 Part；
- **元数据平面** 是 Artifact 身份、版本、权限、完整性和血缘的事实源；
- **遥测平面** 解释 Artifact 在何时、由谁、通过什么执行路径被创建、转换、读取和发布。

Telemetry 不应承担 Catalog 的角色，因为 Trace 和 Log 可能被采样、缩短保留期、异步延迟或部分丢失。

### 2.9.8 Artifact 身份模型

推荐至少区分以下标识：

| 标识 | 作用域 | 是否随版本变化 | 用途 |
|---|---|---:|---|
| `artifact_id` | 平台或租户全局 | 否 | 标识一个逻辑 Artifact |
| `version_id` | 某 Artifact 内 | 是 | 标识不可变版本 |
| `part_id` | 某版本内 | 是 | 标识多 Part 内容单元 |
| `provider_artifact_id` | 外部 Provider | 取决于 Provider | 保存 OpenAI File ID、A2A Artifact ID 等 |
| `content_hash` | 内容寻址域 | 内容变化时变化 | 完整性、去重和重现 |
| `idempotency_key` | 创建请求作用域 | 由调用方控制 | 防止重试重复创建 |
| `storage_ref` | 存储系统 | 可能随迁移变化 | 定位真实字节 |

推荐语义：

```text
artifact_id
  → 表示逻辑对象，例如“季度分析报告”

version_id
  → 表示不可变版本，例如第3次修订

content_hash
  → 表示该版本真实内容的摘要

storage_ref
  → 表示该版本当前存放位置
```

不要把以下对象混为一个 ID：

```text
Trace ID
Agent Run ID
Tool Call ID
Artifact ID
Provider File ID
Storage Object Key
```

它们的生命周期、作用域和重试语义不同。

### 2.9.9 Artifact Manifest

复杂 Artifact 应使用版本化 Manifest，而不是散落的数据库列和临时字段。

示例：

```json
{
  "schema_version": "1.0",
  "artifact_id": "art_01K1...",
  "version_id": "ver_01K1...",
  "logical_name": "architecture-review",
  "display_name": "架构评审报告.md",
  "role": "output",
  "type": "document",
  "media_type": "text/markdown",
  "status": "ready",
  "size_bytes": 184233,
  "content_hash": {
    "algorithm": "sha256",
    "value": "7cc0..."
  },
  "storage": {
    "scheme": "object-store",
    "ref": "obj://tenant-a/artifacts/art_01K1/ver_01K1/content",
    "etag": "e874..."
  },
  "producer": {
    "service_name": "agent-runtime",
    "workflow_run_id": "wf_123",
    "agent_run_id": "ar_456",
    "tool_call_id": "call_789",
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    "span_id": "00f067aa0ba902b7"
  },
  "lineage": {
    "input_artifact_ids": ["art_input_1"],
    "parent_version_id": "ver_previous",
    "supersedes_version_id": "ver_previous"
  },
  "security": {
    "classification": "internal",
    "tenant_id": "tenant-a",
    "retention_class": "project-180d",
    "encryption_key_ref": "kms://artifact-key"
  },
  "created_at": "2026-09-02T06:20:00Z",
  "finalized_at": "2026-09-02T06:20:03Z"
}
```

Manifest 本身也是需要版本和 Schema 治理的数据契约。建议包含：

```text
schema_version
identity
content description
storage description
integrity
producer
lineage
security
lifecycle timestamps
extensions
```

### 2.9.10 单体 Artifact、Multipart Artifact 与 Bundle

一个 Artifact 可以是单个对象，也可以由多个 Part 组成。

```text
Artifact
├── Part 1: report.md
├── Part 2: chart.png
├── Part 3: source-data.csv
└── Part 4: citations.json
```

Part 推荐字段：

```text
part_id
sequence
name
role
media_type
size_bytes
content_hash
storage_ref
encoding
schema_ref
```

常见组合：

| Artifact | Parts |
|---|---|
| 分析报告 | Markdown 正文、图表、引用 JSON、原始数据 |
| 代码变更 | Patch、修改后文件、测试报告、构建日志 |
| PPT | PPTX、封面图、图表数据、演讲稿 |
| 评估结果 | Score JSON、Rubric、证据截图、模型输出 |
| 沙箱快照 | 文件系统层、环境清单、依赖锁、执行日志 |

对于可独立下载和授权的 Part，建议每个 Part 使用独立存储对象；不要把所有内容强制内嵌到 Manifest。

### 2.9.11 内容寻址、哈希和完整性

Artifact 应至少记录安全哈希：

```text
sha256(content_bytes)
```

哈希用途：

- 验证传输和存储完整性；
- 检测重复内容；
- 判断缓存是否命中；
- 证明 Eval 使用了哪个版本；
- 支撑可重现执行；
- 防止同一 `artifact_id` 下内容被静默替换。

需要区分：

```text
Content Hash
  → 对规范化后的真实内容计算

ETag
  → 由对象存储定义，未必是内容哈希

Digital Signature
  → 证明发布者和内容完整性

Schema Validation
  → 证明结构符合预期
```

对于 Multipart Artifact，可以同时计算：

```text
每个 Part 的哈希
Manifest 的哈希
按稳定顺序组合后的根哈希
```

不要在校验前信任上传方声明的 MIME Type、文件扩展名或哈希。

### 2.9.12 不可变版本与更新语义

推荐采用：

```text
逻辑 Artifact 可变
Artifact Version 不可变
```

更新时：

1. 创建新的 `version_id`；
2. 写入新的内容和 Manifest；
3. 完成校验；
4. 原子更新“当前版本”指针；
5. 将旧版本标记为 `superseded`；
6. 发出生命周期 Event；
7. 保留或按策略删除旧版本。

不推荐直接覆盖旧对象，因为会破坏：

- Trace 重现；
- Eval 证据；
- 审计；
- 缓存一致性；
- 内容哈希；
- 下游引用。

### 2.9.13 流式 Artifact 与分片组装

长报告、代码包、媒体文件和远程 Agent 输出可能以流式分片产生。

推荐协议：

```text
begin
  artifact_id
  version_id
  expected_media_type
  optional expected_size

append
  part_id or chunk_id
  sequence
  offset
  bytes
  chunk_hash

finalize
  final_size
  final_hash
  part_count
```

接收端必须处理：

- 重试导致的重复 Chunk；
- Chunk 乱序；
- 缺失 Chunk；
- 同一序号内容冲突；
- 流中断；
- Finalize 重复调用；
- 写入完成但元数据事务失败；
- 元数据完成但对象存储不可读。

推荐规则：

```text
artifact_id + version_id + sequence
  → 构成幂等键

finalize
  → 只有大小、Part集合和最终哈希全部通过后才进入ready
```

A2A 的 `TaskArtifactUpdateEvent` 提供 `append` 和 `lastChunk` 语义，可用于跨 Agent 流式输出；平台内部仍应增加序号、哈希和幂等控制。

### 2.9.14 Artifact Store 的参考架构

```mermaid
flowchart TD
    A["Agent / Tool / Provider"] --> G["Artifact Gateway"]
    G --> P["Policy / DLP / Malware Scan"]
    G --> M["Metadata Catalog"]
    G --> O["Object Store"]
    M --> I["Search / Vector Index"]
    M --> L["Lineage Graph"]
    M --> Acl["ACL / Tenant / Retention"]

    G --> T["OpenTelemetry API"]
    T --> C["Collector"]
    C --> B["Trace / Log / Metric Backend"]

    U["UI / Downstream Agent"] --> R["Artifact Resolver"]
    R --> Acl
    R --> M
    R --> O
```

核心组件：

| 组件 | 职责 |
|---|---|
| Artifact Gateway | 统一创建、上传、Finalize、下载、删除接口 |
| Object Store | 保存正文、Part 和大体积内容 |
| Metadata Catalog | 保存身份、版本、状态、哈希、权限和引用 |
| Artifact Resolver | 根据不透明引用执行鉴权并解析实际存储位置 |
| Scanner／Validator | MIME、病毒、DLP、Schema、哈希和格式校验 |
| Lineage Store | 保存输入、输出、派生和替代关系 |
| Search／Vector Index | 为检索保存派生表示，不作为原文唯一存储 |
| Lifecycle Worker | 保留期、归档、删除、孤儿对象回收 |
| Telemetry Adapter | 发出 Span、Event、Log 和 Metric |

### 2.9.15 Artifact 与 OpenTelemetry 的协同原则

OpenTelemetry 应观测以下动作：

```text
生成 generate
写入 store
读取 fetch
转换 transform
验证 validate
发布 publish
消费 consume
归档 archive
删除 delete
```

但遥测中只保留：

```text
不透明Artifact ID
版本ID
角色和类型
MIME Type
大小
哈希摘要
状态
失败类型
安全分类
不敏感的存储方案
```

遥测中通常不保留：

```text
完整文件字节
完整Prompt或Completion
长期有效的下载URL
带凭证的对象存储URL
用户原始文件路径
大体积JSON
源代码全文
图片Base64
```

### 2.9.16 推荐的自定义 Artifact 属性

以下字段采用 `app.artifact.*` 命名空间，以便与当前官方语义约定区分。

#### 身份和版本

```text
app.artifact.id
app.artifact.version.id
app.artifact.part.id
app.artifact.provider.id
app.artifact.idempotency_key_hash
```

#### 业务语义

```text
app.artifact.name
app.artifact.role
app.artifact.type
app.artifact.media_type
app.artifact.status
app.artifact.schema.ref
app.artifact.part.count
```

#### 内容和完整性

```text
app.artifact.size_bytes
app.artifact.content_hash.algorithm
app.artifact.content_hash.value
app.artifact.encoding
app.artifact.compression
```

#### 存储

```text
app.artifact.storage.scheme
app.artifact.storage.ref
app.artifact.storage.region
```

#### 安全和保留

```text
app.artifact.sensitivity
app.artifact.retention.class
app.artifact.encryption.enabled
app.artifact.scan.result
```

#### 生产者和血缘

```text
app.artifact.producer.type
app.artifact.producer.name
app.artifact.parent.id
app.artifact.supersedes.version.id
```

属性使用建议：

| 字段 | Span／Log／Event | Metric Label |
|---|---:|---:|
| Artifact ID | 可以，按访问控制决定 | **不可以** |
| Version ID | 可以 | **不可以** |
| Type | 可以 | 可以，枚举受控 |
| Role | 可以 | 可以，枚举受控 |
| Media Type | 可以 | 谨慎，需归一化 |
| Status | 可以 | 可以，枚举受控 |
| Size | 可以 | 作为数值 Measurement，不做标签 |
| Hash | 可以，必要时截断 | **不可以** |
| Storage Ref | 谨慎，最好使用不透明引用 | **不可以** |
| Tenant／User ID | 谨慎 | 通常不可以 |

### 2.9.17 Artifact 操作 Span

当前没有稳定的官方 Artifact Span 语义，建议应用层采用低基数操作名：

```text
artifact generate
artifact store
artifact fetch
artifact transform
artifact validate
artifact publish
artifact delete
```

示例：

```text
invoke_agent report_writer
├── chat model-x
├── artifact generate
│   └── artifact validate
└── artifact store
```

Span 建议：

```text
Span name:
  artifact store

Span kind:
  INTERNAL  → 同进程内容处理
  CLIENT    → 调用远程Artifact服务
  SERVER    → Artifact服务接收请求

Attributes:
  app.artifact.id
  app.artifact.version.id
  app.artifact.role
  app.artifact.type
  app.artifact.media_type
  app.artifact.size_bytes
  app.artifact.status
  app.artifact.storage.scheme
  error.type
```

规则：

- Span 名称不能包含文件名、Artifact ID 或对象 Key；
- 一个 Span 表示一次操作，不表示 Artifact 本体；
- 流式写入可以使用一个逻辑 `artifact store` Span，底层分片 HTTP 请求作为子 Span；
- 自动重试应在逻辑 Span 下形成多个网络尝试；
- 失败时设置 `error.type`，异常栈按安全策略记录。

### 2.9.18 Artifact 生命周期 Event

推荐使用 Log-based Event 表达状态变化：

```text
app.artifact.lifecycle.created
app.artifact.lifecycle.upload.started
app.artifact.lifecycle.upload.completed
app.artifact.lifecycle.validated
app.artifact.lifecycle.quarantined
app.artifact.lifecycle.stored
app.artifact.lifecycle.published
app.artifact.lifecycle.consumed
app.artifact.lifecycle.superseded
app.artifact.lifecycle.expired
app.artifact.lifecycle.deleted
app.artifact.verification.failed
```

示例字段：

```text
EventName = app.artifact.lifecycle.stored
Body = Artifact stored
Attributes:
  app.artifact.id
  app.artifact.version.id
  app.artifact.role
  app.artifact.type
  app.artifact.media_type
  app.artifact.size_bytes
  app.artifact.content_hash.algorithm
  app.artifact.content_hash.value
  app.artifact.storage.scheme
  app.artifact.status = ready
```

Event 不是 Artifact Catalog 的替代品。生命周期事件可能重复、乱序或丢失，消费者应使用 `artifact_id + version_id + event_name + event_sequence` 做幂等，并以 Catalog 状态为最终依据。

### 2.9.19 Artifact Metrics

推荐自定义指标：

```text
app.artifact.operation.duration
app.artifact.operation.count
app.artifact.operation.errors
app.artifact.bytes
app.artifact.active
app.artifact.storage.bytes
app.artifact.verification.failures
app.artifact.quarantine.count
app.artifact.orphan.count
app.artifact.time_to_ready
app.artifact.time_to_publish
```

推荐低基数标签：

```text
operation
artifact_role
artifact_type
media_type_family
status
result
error_type
storage_scheme
service.name
```

禁止或谨慎使用：

```text
artifact_id
version_id
file_name
storage_ref
content_hash
trace_id
user_id
raw_media_type
```

例如 `media_type` 可以归一化为：

```text
text
image
audio
video
application
archive
unknown
```

而不是直接把任意 MIME 字符串作为 Metric 标签。

### 2.9.20 Artifact Log 与审计记录

普通 Log 适合记录运维诊断：

```text
上传重试
对象存储限流
扫描器不可用
Manifest解析失败
孤儿对象回收
下载鉴权失败
```

审计事件应至少覆盖：

```text
who
performed what action
on which artifact/version
under which tenant/project
when
through which policy decision
with what result
```

建议将安全审计与普通调试日志分开路由和保留。审计记录需要防篡改、稳定保留和受控访问，不能仅依赖可能被采样或短期保留的 Trace。

### 2.9.21 同一 Trace 内的 Artifact 关联

当 Artifact 在当前 Trace 中生成时：

```text
invoke_workflow create_report
├── retrieval evidence
├── chat model-x
└── artifact store
```

`artifact store` Span 记录 `app.artifact.id` 和 `app.artifact.version.id`。父 Agent Span可以记录最终输出 Artifact ID 列表，但必须限制数量和大小。

建议：

```text
Producer Span
  → 记录 Artifact ID、Version ID、Role、Type

Artifact Manifest
  → 记录 Producer Trace ID、Span ID
```

这样后端可以双向导航：

```text
Trace → Artifact
Artifact → Producer Trace
```

### 2.9.22 跨 Trace 的 Artifact 血缘

Artifact 经常在后续任务中再次作为输入：

```text
Trace A
  → 生成 Artifact X

Trace B
  → 读取 Artifact X
  → 生成 Artifact Y
```

推荐同时保存：

```text
Artifact Y Manifest:
  input_artifact_ids = [X]
  producer_trace_id = Trace B

Trace B Consumer Span:
  app.artifact.id = X
  app.artifact.role = input
```

如果能够获得 Artifact X 的生产者 SpanContext，可以在 Trace B 的消费 Span 上增加 **Span Link**，形成非父子因果关联。需要注意：

> Span Link 关联的是另一个 SpanContext，不是直接关联 Artifact。Artifact 的权威血缘仍应保存在 Catalog 或 Lineage Store 中。

不要强行把相隔数小时或数天的生产者和消费者建成一个超长 Trace。

### 2.9.23 Artifact Lineage 与 Provenance

Lineage 回答：

```text
这个Artifact由哪些输入派生？
经过哪些Agent、Tool、模型和转换？
哪个版本替代了哪个版本？
评估时使用的是哪份内容？
```

推荐关系：

```text
derived_from
transformed_from
merged_from
split_from
validated_by
published_from
supersedes
references
contains
```

Provenance 建议包含：

```text
producer service and version
workflow and agent version
model provider and response ID
prompt template version
tool name and version
input artifact versions
code revision
runtime environment
trace and span references
creation timestamp
```

不要把全部 Provenance 复制到每个 Span。Catalog 保存完整结构，Telemetry 只保存定位所需的最小引用。

### 2.9.24 Artifact 与 Eval 证据

Eval 结果经常依赖大体积证据：

- 模型输入和输出；
- 参考答案；
- Rubric；
- Tool 调用轨迹；
- 测试报告；
- UI 截图；
- 代码 Diff；
- 检索文档集合；
- 人工标注。

推荐模型：

```text
Eval Result
  score / label / explanation
  trace_id
  response_id
  evidence_artifact_ids[]
  rubric_artifact_id
  report_artifact_id
```

`gen_ai.evaluation.result` Event 中只保存小型评分字段和不透明证据引用。完整证据、截图、报告和 Rubric 保存在 Artifact Store。

这样可以避免：

```text
Event Body过大
Prompt和Completion散落到日志系统
评测证据随Trace采样消失
无法证明评分使用了哪个版本
```

### 2.9.25 Prompt、Completion 与 Artifact 化内容治理

对于需要保留的 Prompt、Completion、Tool Result 和 RAG 文档，推荐采用外部 Artifact 模式：

```mermaid
sequenceDiagram
    participant Agent as Agent Runtime
    participant Redact as Redaction / Policy
    participant Store as Artifact Store
    participant OTel as OTel Span / Event

    Agent->>Redact: 待保存内容
    Redact->>Redact: 脱敏、分类、截断策略判断
    Redact->>Store: 加密保存内容
    Store-->>Agent: artifact_id / version_id / hash
    Agent->>OTel: 只记录不透明引用和元数据
```

推荐属性：

```text
app.gen_ai.input.artifact.id
app.gen_ai.output.artifact.id
app.tool.result.artifact.id
app.retrieval.result.artifact.id
```

这些仍是应用自定义字段。

不要在遥测中记录：

```text
预签名URL
临时访问Token
KMS密钥标识的敏感细节
本地绝对路径
包含租户名的对象Key
```

遥测应保存不透明 ID，由 Artifact Resolver 在鉴权后返回短时访问地址。

### 2.9.26 Tool、CLI、Sandbox 与 Coding Agent Artifact

Coding Agent 的典型 Artifact：

```text
代码补丁
修改后文件
Git Commit
构建包
测试报告
Coverage报告
编译数据库
LSP诊断快照
终端输出归档
截图
沙箱文件系统快照
```

推荐链路：

```text
invoke_agent implementer
├── execute_tool apply_patch
│   └── artifact store          role=output type=patch
├── execute_tool run_tests
│   └── artifact store          role=evidence type=test-report
└── artifact publish            role=bundle type=delivery-package
```

CLI stdout／stderr 处理建议：

```text
小型摘要
  → Log Body或Span Event

完整输出
  → Artifact Store
  → Trace只记录artifact_id、bytes、truncated、exit_code
```

沙箱快照必须排除：

- API Key；
- SSH Key；
- Cookie；
- `.env`；
- 云凭证；
- 用户主目录敏感文件；
- 未授权工作区之外的内容。

### 2.9.27 LangChain／LangGraph 中的 Artifact

LangChain 的 `ToolMessage.artifact` 用于保存**不会发送给模型、但可以被程序化访问的补充数据**。典型用途包括：

```text
原始工具结果
文档ID和页码
调试信息
下游处理所需的结构化数据
```

这解决了“模型需要看到的简化内容”和“应用需要保留的完整结果”之间的分离：

```text
ToolMessage.content
  → 发送给模型
  → 应短小、可读、与推理相关

ToolMessage.artifact
  → 不发送给模型
  → 供应用、UI或下游Node使用
```

但 `ToolMessage.artifact` 本身不等于持久化 Artifact Store。大型或需要跨会话复用的内容应：

1. 写入 Artifact Store；
2. 在 `ToolMessage.artifact` 中只保存 `artifact_id`、类型和必要元数据；
3. 在 LangGraph State 中保存引用，而不是文件字节；
4. Checkpoint 只序列化引用和恢复信息；
5. 使用 OTel Span／Event 关联生产者和消费者。

示例：

```python
ToolMessage(
    content="测试完成：1,284项通过，2项失败。",
    tool_call_id="call_123",
    artifact={
        "artifact_id": "art_test_report",
        "version_id": "ver_1",
        "media_type": "application/json",
        "failed_count": 2,
    },
)
```

### 2.9.28 OpenAI 输出文件和图片的 Artifact 归一化

OpenAI 的不同 API 和 Tool 可能返回：

```text
Provider File ID
Container File Citation
文件名
图片Base64
图片URL
Response ID
```

平台可以把这些 Provider 原生对象归一化为内部 Artifact：

```text
provider = openai
provider_artifact_id = file_id
provider_container_id = container_id
producer_response_id = response_id
artifact_id = 平台全局ID
version_id = 平台版本ID
```

推荐流程：

1. 接收 Provider 返回的文件引用或内容；
2. 校验允许的类型和大小；
3. 下载或解码到受控缓冲区；
4. 执行病毒、DLP 和内容类型检查；
5. 保存到平台 Artifact Store；
6. 记录 Provider ID 作为外部引用；
7. OTel Span 中记录平台 Artifact ID 和 Provider 响应 ID；
8. 不把长期访问凭证或完整 Base64 放入遥测。

Provider File ID 不应直接作为平台全局 Artifact ID，因为它的作用域、生命周期、访问控制和删除语义由 Provider 决定。

### 2.9.29 A2A Artifact 的协议语义

Agent2Agent，简称 A2A，把 Artifact 定义为 Agent 针对 Task 生成的输出，并由一个或多个 Part 组成。Artifact 可表达文档、图片、文件引用和结构化数据等结果。

A2A Artifact 的关键字段包括：

```text
artifactId
name
optional description
parts
metadata
optional extensions
```

A2A 的 `TaskArtifactUpdateEvent` 用于流式传输或更新 Artifact，并提供：

```text
artifact
append
lastChunk
```

平台映射建议：

| A2A | 内部 Artifact 模型 |
|---|---|
| `taskId` | 生产任务引用 |
| `artifactId` | `provider_artifact_id` 或任务内局部 ID |
| `parts` | Artifact Parts |
| `metadata` | 经过允许列表过滤的扩展元数据 |
| `append` | 分片追加语义 |
| `lastChunk` | Finalize 信号 |

注意：A2A 的 `artifactId` 通常在任务上下文内识别，企业平台仍应分配租户级或全局内部 ID。跨任务引用、版本历史和长期血缘应由平台 Catalog 管理，而不能仅依赖单次 A2A Task 对象。

### 2.9.30 MCP 与 Artifact 的关系

MCP 核心协议当前提供：

```text
Text Content
Image Content
Audio Content
Resource Link
Embedded Resource
structuredContent
outputSchema
```

它没有要求所有工具输出都变成一个通用的 `Artifact` 实体。因此在 MCP 场景中，Artifact 通常是宿主平台或上层框架的归一化抽象：

```text
MCP Tool Result
  ├── 小型文本 → ToolMessage.content
  ├── structuredContent → 结构化Artifact或ToolMessage.artifact
  ├── Resource Link → 外部Artifact引用候选
  ├── Embedded Resource → 保存后转为Artifact
  ├── Image / Audio → 保存后转为媒体Artifact
  └── UI Resource → 可保存为交互式Artifact引用
```

LangChain MCP Adapter 在收到 `structuredContent` 时，会将其包装为 `MCPToolArtifact` 并放入 `ToolMessage.artifact`。这属于 LangChain 的适配层行为，不意味着 MCP 本身定义了统一 Artifact Store。

安全要求：

- 不自动信任 MCP Server 返回的 URI；
- 只允许批准的 URI Scheme 和 Host；
- 防止 `file://` 越权访问；
- 校验 MIME Type、大小和 Schema；
- Embedded Resource 先隔离扫描，再进入正式存储；
- Tool 输出的 Artifact 权限不得高于当前调用者权限。

### 2.9.31 ChromaDB 与 Artifact 的关系

ChromaDB 适合保存：

```text
embedding
可检索文本
metadata
URI或外部ID
```

对于大型文档、高清图片、视频、代码包等内容，推荐模式是：

```text
Artifact Store
  → 保存原始权威内容

Chroma
  → 保存Chunk、Embedding、Metadata和Artifact引用
```

推荐 Chroma Metadata：

```json
{
  "artifact_id": "art_123",
  "version_id": "ver_4",
  "part_id": "part_8",
  "chunk_id": "chunk_27",
  "media_type": "text/markdown",
  "content_hash": "sha256:...",
  "source_role": "evidence"
}
```

一致性规则：

1. Artifact 新版本发布后创建新索引记录；
2. 查询结果必须返回 `artifact_id + version_id + chunk_id`；
3. 下游引用时通过 Artifact Resolver 重新鉴权；
4. Artifact 删除或过期后异步清除向量索引；
5. 检索时发现版本失效应拒绝返回正文；
6. Chroma 索引可以重建，Artifact Store 和 Catalog 才是权威来源。

### 2.9.32 Artifact 安全模型

Artifact 的风险通常高于普通 Span Attribute，因为它保存完整内容。

需要控制：

```text
租户隔离
项目隔离
对象级ACL
加密传输
静态加密
密钥轮换
DLP
恶意文件扫描
内容类型嗅探
压缩炸弹检测
最大文件和解压后大小
路径穿越
SSRF
外部URI允许列表
下载审计
水印和版权策略
```

安全分类示例：

```text
public
internal
confidential
restricted
secret
```

Telemetry 中可以记录 `app.artifact.sensitivity`，但不要记录会泄露真实业务含义的详细分类备注。

### 2.9.33 访问引用和预签名 URL

推荐遥测记录：

```text
artifact://art_123/ver_4
```

或：

```text
app.artifact.id = art_123
app.artifact.version.id = ver_4
```

不推荐遥测记录：

```text
https://object-store/...?...signature=...
file:///Users/alice/secret/report.pdf
s3://customer-name-sensitive-bucket/path
```

Artifact Resolver 负责：

1. 根据调用者身份解析不透明 ID；
2. 检查租户、项目、角色和保留状态；
3. 生成短时、最小权限的访问地址；
4. 记录下载审计；
5. 在必要时执行在线扫描或水印。

预签名 URL 应短期有效，不应进入 Span、Log、Event、Baggage 或错误消息。

### 2.9.34 保留、归档、删除与法律保持

Artifact 生命周期策略应至少考虑：

```text
业务保留期
遥测保留期
Eval证据保留期
模型训练排除策略
用户删除请求
租户合同要求
Legal Hold
备份副本
向量索引副本
缓存和CDN副本
```

删除流程：

```text
标记删除请求
  → 检查Legal Hold
  → 撤销访问
  → 删除主对象
  → 删除Part和派生预览
  → 清除向量索引
  → 清除缓存
  → 更新Catalog墓碑
  → 发出删除Event
  → 保留最小审计记录
```

“删除 Catalog 记录”不等于“内容已经从所有副本删除”。应对对象存储、索引、缓存、备份和外部 Provider 分别跟踪删除状态。

### 2.9.35 双写一致性和可靠性

Artifact Store 通常涉及对象存储与元数据数据库双写。常见失败：

```text
对象写成功，Catalog事务失败 → 孤儿对象
Catalog成功，对象写失败 → 悬空记录
事件已发出，事务回滚 → 虚假状态
事务提交，事件未发出 → 下游不知道
```

推荐模式：

- Staging 状态；
- 不可变对象 Key；
- Finalize 事务；
- Transactional Outbox；
- 幂等消费者；
- 周期性对账；
- 孤儿对象 GC；
- 内容哈希校验；
- 失败补偿和修复队列。

状态事实源应是 Catalog。Event 用于通知和分析，不作为唯一状态依据。

### 2.9.36 Collector 与后端中的 Artifact 治理

Collector 应防止 Artifact 内容意外进入遥测：

```text
删除或哈希完整storage URL
删除查询参数和访问Token
限制Body和Attribute长度
过滤Base64和超大JSON
删除本地绝对路径
识别Prompt、Completion、Tool Result敏感字段
按Artifact敏感等级路由
```

建议：

```text
OTLP
  → 只传Artifact元数据和引用

Artifact Data API
  → 独立传输真实内容
```

不要把对象存储上传改造成 OTLP Log Export。OTLP 的目标是遥测，不是通用大文件传输协议。

### 2.9.37 Python 埋点示例

下面示例展示：生成内容、计算哈希、写入 Artifact Store、在 Span 中记录引用，并通过 Logs API 发出生命周期 Event。示例中的 `app.artifact.*` 均为自定义属性。

```python
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Protocol

from opentelemetry import trace
from opentelemetry._logs import get_logger
from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.context import get_current
from opentelemetry.trace import Status, StatusCode


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    version_id: str
    storage_ref: str


class ArtifactStore(Protocol):
    def put(
        self,
        *,
        content: bytes,
        media_type: str,
        content_hash: str,
        idempotency_key: str,
    ) -> StoredArtifact:
        """Persist bytes and return an opaque reference."""


tracer = trace.get_tracer("example.agent.artifacts", "1.0.0")
event_logger = get_logger("example.agent.artifacts", "1.0.0")


def persist_report(
    *,
    markdown: str,
    store: ArtifactStore,
    idempotency_key: str,
) -> StoredArtifact:
    content = markdown.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()

    with tracer.start_as_current_span(
        "artifact store",
        attributes={
            "app.artifact.role": "output",
            "app.artifact.type": "document",
            "app.artifact.media_type": "text/markdown",
            "app.artifact.size_bytes": len(content),
            "app.artifact.content_hash.algorithm": "sha256",
            "app.artifact.content_hash.value": digest,
        },
    ) as span:
        try:
            stored = store.put(
                content=content,
                media_type="text/markdown",
                content_hash=digest,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise

        # Trace仅保存可检索元数据和不透明引用，不保存正文或预签名URL。
        span.set_attribute("app.artifact.id", stored.artifact_id)
        span.set_attribute("app.artifact.version.id", stored.version_id)
        span.set_attribute("app.artifact.storage.scheme", "artifact")
        span.set_attribute("app.artifact.storage.ref", stored.storage_ref)
        span.set_attribute("app.artifact.status", "ready")

        event_logger.emit(
            timestamp=time.time_ns(),
            context=get_current(),
            event_name="app.artifact.lifecycle.stored",
            severity_number=SeverityNumber.INFO,
            body="Artifact stored",
            attributes={
                "app.artifact.id": stored.artifact_id,
                "app.artifact.version.id": stored.version_id,
                "app.artifact.role": "output",
                "app.artifact.type": "document",
                "app.artifact.media_type": "text/markdown",
                "app.artifact.size_bytes": len(content),
                "app.artifact.content_hash.algorithm": "sha256",
                "app.artifact.content_hash.value": digest,
                "app.artifact.storage.scheme": "artifact",
                "app.artifact.status": "ready",
            },
        )

        return stored
```

生产实现还应增加：

- `LoggerProvider`、`LogRecordProcessor` 和 OTLP Exporter 配置；
- MIME 嗅探；
- DLP 和恶意文件扫描；
- 最大大小限制；
- Transactional Outbox；
- 超时、重试和幂等；
- ACL、租户和保留策略；
- Metric 记录；
- Artifact Catalog 事务。

### 2.9.38 Artifact Span、Event、Metric 和 Catalog 的协同示例

```mermaid
sequenceDiagram
    participant A as Agent
    participant S as Artifact Service
    participant O as Object Store
    participant C as Artifact Catalog
    participant T as OTel SDK
    participant Q as Outbox/Event Worker

    A->>S: CreateArtifact(idempotency_key)
    S->>T: start artifact store Span
    S->>O: 写入不可变对象
    O-->>S: ETag
    S->>S: 计算并验证SHA-256
    S->>C: 事务写入Manifest + Outbox
    C-->>S: artifact_id/version_id
    S->>T: Span记录ID、大小、状态
    S-->>A: 不透明Artifact引用
    S->>T: end Span
    Q->>C: 读取Outbox
    Q->>T: emit app.artifact.lifecycle.stored
```

其中：

```text
Catalog
  → 业务状态事实源

Object Store
  → 内容事实源

Span
  → 本次存储操作的耗时和错误

Event
  → Artifact状态变化通知与分析

Metric
  → 全局吞吐、容量、延迟和失败率
```

### 2.9.39 Schema、命名和标准演进策略

由于 Artifact 的 GenAI OpenTelemetry 语义仍在演进，建议维护内部 Mapper：

```python
class ArtifactTelemetryMapper:
    def to_span_attributes(self, manifest): ...
    def to_event_attributes(self, manifest): ...
    def to_metric_labels(self, manifest): ...
```

并维护版本 Manifest：

```yaml
artifact_domain_schema: 1.2.0
telemetry_mapping_version: 1.0.0
otel_semconv_version: 1.44.0
genai_semconv_commit: pinned-commit-or-release
custom_namespace: app.artifact
```

未来官方语义稳定时：

1. 对比字段语义和作用域；
2. 不机械替换名称；
3. 为旧字段和新字段设置过渡期；
4. 更新 Dashboard、告警和查询；
5. 防止重复记录；
6. 通过 Schema／Collector Transform 做迁移；
7. 保留历史数据解释文档。

### 2.9.40 最小生产字段集

在不采集正文的情况下，建议至少记录：

#### Catalog

```text
artifact_id
version_id
role
type
media_type
status
size_bytes
content_hash
storage_ref
producer_trace_id
producer_span_id
created_at
security_classification
retention_class
```

#### Producer／Consumer Span

```text
app.artifact.id
app.artifact.version.id
app.artifact.role
app.artifact.type
app.artifact.media_type
app.artifact.size_bytes
app.artifact.status
error.type
```

#### Lifecycle Event

```text
EventName
artifact_id
version_id
status
operation result
trace_id/span_id
occurred timestamp
```

#### Metrics

```text
operation duration
operation count
error count
bytes
active count
verification failures
```

### 2.9.41 Artifact 接入验收清单

#### 概念和模型

- [ ] Artifact 被定义为领域对象，而不是 OTel 新信号；
- [ ] Message、Artifact、Memory、Checkpoint、Event 和 Span 边界清晰；
- [ ] 逻辑 Artifact 与不可变 Version 分离；
- [ ] Multipart 和 Bundle 有明确模型；
- [ ] Provider ID 与平台 ID 分离；
- [ ] Manifest 有版本和 Schema。

#### 存储和完整性

- [ ] 正文存入受控 Artifact Store，而不是 OTLP；
- [ ] Catalog 是身份、版本和状态事实源；
- [ ] 每个版本有大小和安全哈希；
- [ ] 流式 Chunk 有序号、幂等和 Finalize 校验；
- [ ] 对象存储与 Catalog 双写有修复机制；
- [ ] 有孤儿对象和悬空引用对账任务。

#### OpenTelemetry

- [ ] Span 表示 Artifact 操作，而不是 Artifact 本体；
- [ ] Event 表示生命周期时间点；
- [ ] Metric 只使用低基数维度；
- [ ] Trace 和 Artifact 能够双向导航；
- [ ] 跨 Trace 血缘保存在 Catalog；
- [ ] `app.artifact.*` 明确标记为自定义命名空间；
- [ ] 未伪造当前不存在的稳定 `gen_ai.artifact.*`；
- [ ] 内容字节、Base64 和预签名 URL 未进入遥测。

#### 安全

- [ ] 有租户和对象级 ACL；
- [ ] 有 DLP、恶意文件和 MIME 检查；
- [ ] 外部 URI 有 Scheme／Host 允许列表；
- [ ] 文件路径、凭证和签名 URL 被过滤；
- [ ] Artifact Resolver 每次访问重新鉴权；
- [ ] 下载和删除有审计；
- [ ] 保留、归档、Legal Hold 和彻底删除策略明确。

#### 框架集成

- [ ] LangChain `ToolMessage.artifact` 与持久化 Store 的边界明确；
- [ ] LangGraph State 和 Checkpoint 主要保存 Artifact 引用；
- [ ] OpenAI Provider File ID 已归一化且不作为全局 ID；
- [ ] A2A Artifact／Part／流式更新已正确映射；
- [ ] MCP Resource／structuredContent 经过校验后再持久化；
- [ ] Chroma 只承担检索索引，原文有权威存储；
- [ ] Eval 结果能够引用不可变证据 Artifact。

### 2.9.42 本章核心结论

```text
Artifact不是OpenTelemetry信号
Artifact是Agent领域的持久化可交付物和证据对象
OpenTelemetry观测Artifact的生命周期与操作
Artifact Store保存正文
Catalog保存身份、版本、权限、哈希和血缘
Trace保存生产和消费路径
Event保存生命周期事实
Metric保存总体趋势
Log保存诊断和审计明细
Eval通过Artifact引用保存证据
```

最重要的工程原则是：

> **不要把可交付内容塞进遥测系统，也不要把遥测数据当作可交付内容的事实源。让 Artifact 系统管理内容和生命周期，让 OpenTelemetry 管理操作、关联和运行证据。**

### 7.2.10 Context、SpanContext 与 Baggage

### 2.10.1 Context

Context 是当前逻辑执行链路携带的上下文容器。当前 Span、Baggage 和其他运行时值通常与 Context 关联。

同进程内，语言 SDK 可能使用：

```text
ThreadLocal
ContextVar
AsyncLocal
Coroutine Context
显式 context 参数
```

具体实现因语言而异，但目标相同：让子调用获取正确的当前 Span。

### 2.10.2 SpanContext

SpanContext 是可传播的 Trace 身份，核心字段包括：

```text
TraceId
SpanId
TraceFlags
TraceState
是否远程上下文
```

SpanContext 可以在未记录完整 Span 的情况下继续向下游传播，因此“本地没有导出某个 Span”不必然意味着下游无法继承 TraceId。

### 2.10.3 Baggage

Baggage 是随 Context 传播的键值集合，适合传递：

```text
tenant.id
experiment.group
request.origin
workflow.class
```

但 Baggage 存在显著风险：

- 可能被自动写入跨服务请求头；
- 下游收到的 Baggage 属于不可信输入；
- 可能泄露 PII、密钥或内部拓扑；
- 过大时会放大网络开销；
- 不应直接把全部 Baggage 自动复制到 Metric 标签。

建议：

```text
建立白名单
限制键和值长度
禁止密钥、Token、Prompt正文和个人敏感信息
跨信任域时清理
进入Metric前再次做低基数过滤
```

### 7.2.11 Context Propagation 与 W3C Trace Context

### 2.11.1 Inject 与 Extract

跨网络传播分为两步：

```text
客户端：Inject 当前Context到Carrier
服务端：Extract Carrier中的Context并创建子Span
```

Carrier 可以是：

```text
HTTP Header
gRPC Metadata
消息属性
MCP _meta
环境变量
自定义IPC字段
```

### 2.11.2 W3C Trace Context

默认推荐传播：

```text
traceparent
tracestate
```

`traceparent` 逻辑结构：

```text
version-trace_id-parent_id-trace_flags
```

例如：

```text
00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

含义：

```text
00                                版本
4bf92...4736                      TraceId
00f067aa0ba902b7                  上游SpanId
01                                TraceFlags，最低位表示sampled
```

### 2.11.3 传播协议与 OTLP 的区别

```text
业务请求：traceparent / tracestate / baggage
遥测导出：OTLP
```

它们运行在两条不同的数据路径上：

```mermaid
flowchart LR
    A["Service A"] -->|"业务请求 + traceparent"| B["Service B"]
    A -.->|"OTLP"| C["Collector"]
    B -.->|"OTLP"| C
```

没有业务侧 Context Propagation，即使 A 和 B 都向同一 Collector 导出，也会形成两条不相关的 Trace。

### 7.2.12 Sampling 详解

采样用于控制 Trace 数据量与成本。采样不应与 Metrics 聚合或日志级别过滤混为一谈。

### 2.12.1 Head Sampling

在 Trace 开始或 Span 创建时立即决定是否记录／导出。

优点：

- 开销低；
- 容易配置；
- 不需要缓存完整 Trace；
- 适合高吞吐入口保护。

局限：

- 无法预先知道后续是否报错；
- 无法根据整条 Trace 总耗时做决策；
- 可能丢掉低频但关键的异常链路。

常见策略：

```text
AlwaysOn
AlwaysOff
TraceIdRatioBased
ParentBased
Consistent Probability Sampling
```

### 2.12.2 Tail Sampling

在 Collector 收集到一条 Trace 的大部分或全部 Span 后再决定是否保留。

适合：

```text
错误Trace全部保留
总耗时超过阈值全部保留
包含特定Agent、租户或版本的Trace提高采样率
权限拒绝、预算耗尽、Loop检测全部保留
普通成功Trace按比例保留
```

代价：

- 需要缓存 Span；
- 需要等待 Trace 完成；
- Collector 变成有状态组件；
- 内存和延迟更高；
- 横向扩容时必须保证同一 Trace 路由到同一采样实例。

### 2.12.3 Head 与 Tail 组合

高流量系统可以先使用 Head Sampling 限制最上游流量，再对进入 Collector 的 Trace 使用 Tail Sampling 做精细决策。但要理解：

> Head Sampling 已经丢掉的 Trace，Tail Sampling 无法恢复。

### 2.12.4 采样对日志和指标的影响

- Trace 未采样，不代表 Metric 不记录；
- Log 是否导出取决于日志管线和过滤规则；
- 未采样 Trace 的日志可能仍携带 TraceId，但后端未必能找到对应 Span；
- Eval、审计和计费数据不能盲目依赖采样后的 Trace 作为唯一事实来源。

### 7.2.13 OpenTelemetry API 详解

API 是供 Instrumentation、框架和应用调用的稳定接口。

### 2.13.1 Trace API

核心对象：

```text
TracerProvider
Tracer
Span
SpanContext
Context
TextMapPropagator
```

典型逻辑：

```python
tracer = trace.get_tracer("my.agent.runtime", "1.0.0")

with tracer.start_as_current_span("invoke_agent") as span:
    span.set_attribute("agent.name", "reviewer")
    run_agent()
```

### 2.13.2 Metrics API

核心对象：

```text
MeterProvider
Meter
Counter
UpDownCounter
Histogram
Gauge
Observable Instruments
```

### 2.13.3 Logs API 与 Events

核心对象：

```text
LoggerProvider
Logger
LogRecord
```

Logs API 既可以被应用直接使用，也常由日志 Bridge／Appender 调用，以兼容已有日志生态。

当前 Event 通过 Logs API 发出：

```text
Logger.emit(
  event_name = "...",
  timestamp = ...,
  severity_number = ...,
  body = ...,
  attributes = ...,
  context = ...
)
```

只要 `event_name` 非空，该 LogRecord 就被识别为 Event。独立 Events API／SDK 的旧设计已被 Logs API／SDK 统一模型取代；不同语言的迁移和移除时间可能不同。

### 2.13.4 API 的 No-op 特性

库只依赖 API，而宿主应用没有安装或配置 SDK 时，API 应以低开销 No-op 运行。这保证公共库可以安全加入 OTel API 依赖，而不会强制用户选择某个 SDK 或后端。

### 7.2.14 OpenTelemetry SDK 详解

SDK 是 API 的实际实现。应用所有者通常在每个进程中统一配置一套 Provider。

### 2.14.1 Trace SDK 管线

```mermaid
flowchart LR
    A["start_span"] --> B["TracerProvider"]
    B --> C["Sampler"]
    C --> D["Recording Span"]
    D --> E["SpanProcessor"]
    E --> F["Batch Queue"]
    F --> G["SpanExporter"]
    G --> H["Collector / Backend"]
```

关键组件：

| 组件 | 职责 |
|---|---|
| TracerProvider | Tracer 工厂、全局 Trace SDK 配置 |
| Sampler | 创建 Span 时决定记录和采样 |
| IdGenerator | 生成 TraceId 和 SpanId |
| SpanLimits | 限制 Attributes、Events、Links 数量和长度 |
| SpanProcessor | 在 Span 开始／结束时处理 |
| SimpleSpanProcessor | 同步导出，适合开发调试 |
| BatchSpanProcessor | 队列、批量、后台导出，适合生产 |
| SpanExporter | 导出到 OTLP、控制台或其他协议 |

### 2.14.2 Metrics SDK 管线

```text
Instrumentation
    ↓ Measurement
Aggregator
    ↓
MetricReader
    ↓
MetricExporter
```

关键组件：

```text
MeterProvider
View
Aggregation
Cardinality Limit
MetricReader
Periodic Exporting Reader
MetricExporter
Exemplar Reservoir
```

### 2.14.3 Logs SDK 与 Events 管线

```text
Logger / Logging Bridge
    ↓
LogRecord(EventName可选)
    ↓
LogRecordProcessor
    ├── Filter / Redaction
    ├── Event-to-Span-Event Bridge
    └── BatchLogRecordProcessor
        ↓
LogExporter
```

Event 不需要单独的 Event SDK。它与普通 Log 共用 Logs SDK，区别由 `EventName` 是否非空决定。

### 2.14.4 Resource Detector

SDK 可以从环境和运行平台检测 Resource：

```text
环境变量
进程
主机
容器
Kubernetes
云平台
服务配置
```

自动检测结果与显式配置的合并规则必须确认，尤其是 `service.name`、环境和实例标识。

### 2.14.5 生命周期

生产应用应正确处理：

```text
force_flush
shutdown
进程退出
容器终止信号
Serverless冻结与恢复
崩溃时未发送队列
```

批处理提高吞吐，但意味着异常退出时可能丢失尚未导出的遥测。关键审计数据不应只依赖内存中的 Batch Queue。

### 7.2.15 OTLP 详解

OTLP 是 OpenTelemetry 原生协议，用于传输 Trace、Metric、Log 和逐步扩展的其他信号。

### 2.15.1 两种主流传输

| 方式 | 默认端口 | 编码与特点 |
|---|---:|---|
| OTLP/gRPC | `4317` | Protobuf + gRPC，双向能力和流控生态成熟 |
| OTLP/HTTP | `4318` | Protobuf over HTTP，网络兼容性较好 |

OTLP/HTTP 常见路径：

```text
/v1/traces
/v1/metrics
/v1/logs
/v1/profiles
```

### 2.15.2 成功、部分成功与失败

OTLP 支持：

```text
Full Success
Partial Success
Retryable Failure
Non-retryable Failure
```

部分成功表示接收端接受了一部分数据并拒绝另一部分。客户端不应简单把部分成功当成网络失败并重复发送整批数据，否则可能造成重复。

OTLP/HTTP 常见可重试响应包括：

```text
429
502
503
504
```

客户端通常应使用指数退避和随机抖动，并遵守 `Retry-After`。

### 2.15.3 OTLP 生产配置关注点

```text
TLS
mTLS
认证Header
压缩
批大小
请求超时
发送队列
并发连接
重试上限
持久化队列
最大消息体
代理与负载均衡
```

不要把 API Key、Bearer Token 或 Collector 凭证写入 Span 属性或普通日志。

### 2.15.4 OTLP 不保证端到端永久可靠

OTLP 规范定义传输行为，但完整可靠性还取决于：

```text
SDK Batch Queue
应用退出处理
网络
Collector Receiver
Collector Processor
Exporter Queue
持久存储
后端接收能力
```

因此，“SDK 返回成功”通常只代表当前导出边界成功，不代表数据已进入最终可查询的长期存储。

### 7.2.16 Collector 详解

OpenTelemetry Collector 是厂商中立的遥测接收、处理和导出进程。它通过 Pipeline 组织组件。

### 2.16.1 五类组件

| 组件 | 作用 | 是否位于 Pipeline |
|---|---|---:|
| Receiver | 接收或拉取遥测 | 是 |
| Processor | 转换、过滤、批处理、采样、限流 | 是 |
| Exporter | 向后端或下一级 Collector 发送 | 是 |
| Connector | 连接两个 Pipeline，同时充当 Exporter 和 Receiver | 是 |
| Extension | 健康检查、认证、存储、调试等辅助能力 | 否，挂载于 Service |

### 2.16.2 Pipeline

每条 Pipeline 对应一种信号：

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp]
```

只在配置文件中声明组件并不会自动启用；组件必须被引用到 Pipeline 或 `service.extensions`。

### 2.16.3 Connector

Connector 可在不同 Pipeline 之间转换、复制或路由数据，例如：

```text
Traces → Metrics
Traces → Service Graph
一条Pipeline → 多条Pipeline
条件路由
```

概念上：

```text
traces pipeline
    ↓ connector作为exporter
connector
    ↓ connector作为receiver
metrics pipeline
```

### 2.16.4 常用 Processor

```text
memory_limiter     限制内存压力
batch              批处理
resource           修改Resource
attributes         修改Span/Log属性
transform          使用OTTL转换数据
filter             丢弃不需要的数据
redaction          脱敏
tail_sampling      尾采样
probabilistic_sampler 概率采样
resourcedetection  检测环境资源
```

Processor 顺序会改变结果。例如通常先做 `memory_limiter`，再做过滤、转换、采样，最后 `batch`。

### 2.16.5 Collector 基础配置示例

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 1024
  resource:
    attributes:
      - key: telemetry.pipeline
        value: agent-observability
        action: upsert
  batch:
    timeout: 5s

exporters:
  otlp/backend:
    endpoint: observability-backend:4317
    tls:
      insecure: false
  debug:
    verbosity: basic

extensions:
  health_check:

service:
  extensions: [health_check]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [otlp/backend]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [otlp/backend]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [otlp/backend]
```

实际组件字段会随 Collector 版本和 Distribution 变化，应使用目标版本配置校验命令验证。

### 2.16.6 Collector Distribution

Collector 是可组合架构。不同 Distribution 包含的 Receiver、Processor、Exporter、Connector 和 Extension 不同。

生产建议：

- 检查所选 Distribution 的组件清单；
- 不要假设文档中的任意组件都已打包；
- 对供应链和攻击面敏感时，使用 OpenTelemetry Collector Builder 构建最小 Distribution；
- 固定镜像摘要和组件版本；
- 对配置做启动前校验和集成测试。

### 7.2.17 Collector 部署形态

### 2.17.1 直接导出

```text
Application SDK → Backend
```

适合本地开发和小规模验证。缺点是：

- 应用需要持有后端凭证；
- 难以统一脱敏、路由和采样；
- 更换后端需要修改大量应用配置；
- Backend 故障可能直接影响 SDK 队列。

### 2.17.2 Agent／本地 Collector

```text
Application SDK → Local Collector → Backend
```

本地 Collector 靠近工作负载，负责协议收敛、批处理、资源检测和第一层脱敏。

### 2.17.3 Gateway Collector

```text
Application / Local Collector → Gateway Collector → Backends
```

Gateway 适合：

```text
Tail Sampling
多租户路由
统一认证
多后端复制
中心化转换
成本治理
```

### 2.17.4 Agent-to-Gateway

```mermaid
flowchart LR
    A1["App 1"] --> L1["Local Collector"]
    A2["App 2"] --> L2["Local Collector"]
    A3["App 3"] --> L3["Local Collector"]

    L1 --> G["Gateway Collector Cluster"]
    L2 --> G
    L3 --> G

    G --> T["Trace Backend"]
    G --> M["Metric Backend"]
    G --> L["Log Backend"]
```

这是较常见的生产形态，但 Tail Sampling、Span-to-Metrics 等有状态组件需要按 TraceId 或服务名做一致性路由。

### 2.17.5 Kubernetes 部署选择

| 形态 | 典型部署 | 适用场景 |
|---|---|---|
| Sidecar | 每个 Pod 一个 Collector | 强隔离、特定协议、本地文件访问 |
| DaemonSet | 每个 Node 一个 Collector | 节点级日志、主机指标、本地接收 |
| Deployment | 中心 Gateway 集群 | Tail Sampling、路由、集中治理 |
| 混合 | DaemonSet／Sidecar + Deployment | 大中型生产环境 |

### 7.2.18 Instrumentation 模式详解

### 2.18.1 手工埋点

业务代码直接调用 OTel API：

```text
优点：语义最准确
缺点：需要修改代码和维护埋点
```

适合：

```text
业务工作流
权限决策
领域状态机
Agent Loop
预算与审批
自研协议
```

### 2.18.2 原生埋点

框架自身依赖 OTel API，并在源码内创建遥测：

```text
优点：框架作者最了解语义
缺点：需要框架长期维护兼容性
```

### 2.18.3 独立 Instrumentation Library

外部适配器通过 Callback、Wrapper、Middleware、Interceptor 或 Monkey Patch 观测目标库。

```text
被观测库 ≠ Instrumentation库
```

### 2.18.4 零代码／自动埋点

不修改应用业务源码，通过启动注入、字节码增强、Monkey Patch、CLR/JVM Profiler、编译期插件或 eBPF 等方式自动采集。

不同语言实现差异很大：

```text
Java       Java Agent + 字节码增强
.NET       CLR Profiling API / 自动探针
Python     启动注入 + sitecustomize + Monkey Patch
Node.js    Module Hook / Instrumentation
Go         手工/编译期/自动化能力按方案不同
原生环境   eBPF或代理方式
```

“零代码”只表示不修改业务源文件，不表示没有注入、没有配置、没有性能成本，也不表示能理解所有领域语义。

### 2.18.5 进程外和 eBPF 观测

eBPF、Service Mesh 和网络代理可在不进入应用进程的情况下观察系统调用或协议行为，适合补齐：

```text
网络连接
DNS
TCP
部分HTTP/gRPC
系统调用
内核调度
```

但通常无法恢复：

```text
Prompt语义
Agent规划
Tool参数
业务状态机
模型Token
框架Callback层级
```

因此它是协议和运行时观测补充，而不是 Agent 语义层替代品。

### 7.2.19 Semantic Conventions 与 Schema 版本治理

### 2.19.1 为什么需要语义约定

没有 Semantic Conventions 时，各 Instrumentation 可能产生：

```text
http.status
http.status_code
responseCode
status
```

统一后，后端可以使用同一查询、Dashboard 和告警规则跨语言工作。

Semantic Conventions 规定：

```text
Span名称
SpanKind
Attribute名称、类型、含义和值域
Metric名称、单位和Instrument类型
Event名称
Resource属性
错误表达
```

### 2.19.2 稳定性等级

不同语义组可能处于：

```text
development
alpha
beta
release_candidate
stable
```

不能因为 OpenTelemetry API 稳定，就推断所有 GenAI、数据库、消息或 Profile 语义都已稳定。

### 2.19.3 Schema URL

InstrumentationScope 或 Resource 可以携带 Schema URL，用于说明遥测符合哪个语义 Schema。Schema 机制旨在支持属性重命名和版本转换，使遥测生产者、Collector 和后端能够独立升级。

示意：

```text
旧数据：deployment.environment
新数据：deployment.environment.name

Schema Translator
    → 根据schema_url做兼容转换
```

### 2.19.4 稳定语义迁移

部分 Instrumentation 使用：

```text
OTEL_SEMCONV_STABILITY_OPT_IN
```

支持：

```text
只发新版稳定语义
新旧字段双发
继续发旧版语义
```

双发阶段要防止：

- Metric 重复；
- Dashboard 同时统计新旧字段；
- 成本翻倍；
- 属性冲突；
- 下游转换再次复制。

### 2.19.5 自定义命名空间

自研属性建议使用组织或产品前缀：

```text
app.agent.loop.iteration
vane.permission.decision
company.workflow.run.id
```

避免：

- 占用 `otel.*` 保留命名空间；
- 私自重新定义已有标准字段；
- 把实验字段直接写成未来可能冲突的 `gen_ai.*`；
- 在大量代码中散落字符串常量。

推荐增加内部 Telemetry Schema／Adapter 层统一管理。

### 7.2.20 配置模型与常用环境变量

OpenTelemetry 通常支持代码配置、环境变量、自动探针配置和 Collector 配置。常见环境变量包括：

```bash
OTEL_SERVICE_NAME=agent-service
OTEL_RESOURCE_ATTRIBUTES=service.namespace=vane-ai,deployment.environment.name=prod

OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-gateway.example.com
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer%20...

OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp

OTEL_PROPAGATORS=tracecontext,baggage
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1
```

配置注意事项：

- 各语言 SDK 对某些环境变量的支持程度可能不同；
- 信号级 Endpoint 可能覆盖通用 Endpoint；
- OTLP/gRPC 与 OTLP/HTTP 对 URL Path 的解释不同；
- Header 中的空格、逗号和特殊字符需要正确编码；
- 自动探针、框架和应用可能同时尝试设置全局 Provider；
- 凭证优先由 Secret 管理系统注入，不写入仓库。

### 7.2.21 性能、可靠性与安全治理

### 2.21.1 性能成本来自哪里

```text
创建Span和对象分配
读取和序列化属性
Context切换
流式Chunk观察
Metric属性组合聚合
日志正文复制
Batch Queue内存
压缩和网络发送
Collector转换与采样
后端索引
```

优化顺序：

1. 先减少无价值遥测；
2. 控制正文、Event 和 Attribute 数量；
3. 控制 Metric 基数；
4. 使用 Batch Processor；
5. 使用采样；
6. 调整 Collector 队列和并发；
7. 最后再做低层微优化。

### 2.21.2 遥测不得破坏业务

Instrumentation 和 SDK 的基本原则是：

```text
遥测错误不应成为业务错误
Exporter失败不应阻塞核心请求
异常属性类型不应使应用崩溃
Collector不可用时应有有界队列和丢弃策略
```

但这不代表无限缓存。生产系统必须明确：

```text
队列上限
内存上限
重试上限
磁盘上限
数据丢弃优先级
关键审计数据的独立持久化路径
```

### 2.21.3 敏感数据

重点风险字段：

```text
Prompt和Completion
Tool Arguments和Result
RAG文档正文
数据库Statement
HTTP Header
URL Query
文件路径
用户ID、邮箱、手机号
Access Token、Cookie、API Key
Baggage
异常堆栈中的业务数据
```

推荐多层防护：

```mermaid
flowchart LR
    A["业务数据"] --> B["Instrumentation白名单与截断"]
    B --> C["SDK Processor"]
    C --> D["Local Collector脱敏"]
    D --> E["Gateway二次过滤"]
    E --> F["分级存储与RBAC"]
```

### 2.21.4 信任边界

跨租户、跨网络区或跨公司边界时：

- 不信任外部 `traceparent`、`tracestate` 和 `baggage` 的附加内容；
- 验证格式并限制长度；
- 必要时重新建立 Trace 边界，只使用 Link 关联；
- 对 Baggage 做白名单清理；
- Collector Receiver 启用认证和 TLS；
- 防止租户伪造 Resource 或服务标识污染后端。

### 7.2.22 自可观测性与 Collector 运维

可观测系统自身也必须被观测。需要监控：

```text
SDK丢弃Span/Log数量
Batch Queue使用率
Exporter失败与重试
Collector接收速率
Processor拒绝数据
Exporter Queue大小与容量
永久发送失败
Collector内存和CPU
Tail Sampling等待Trace数量
持久队列磁盘使用
Backend摄取延迟
```

Collector 常用运维能力：

```text
health_check
zPages
pprof
内部Metrics
内部Logs
debug exporter
配置校验
```

容量判断不能只看 Collector CPU。若 Exporter Queue 持续接近容量，可能是后端或网络变慢，盲目增加 Collector 副本可能进一步压垮后端。

### 7.2.23 调试与排障方法

### 2.23.1 从最短链路验证

```text
应用 → Console／Debug Exporter
应用 → 单个Collector → Debug Exporter
应用 → Collector → 真实Backend
多服务Context传播
自动Instrumentation
采样和脱敏
```

不要一开始同时引入多级 Collector、Tail Sampling、多个后端和十几个 Processor。

### 2.23.2 排障检查表

| 现象 | 优先检查 |
|---|---|
| 完全没有遥测 | SDK 是否配置、Exporter 是否启用、Endpoint 是否可达 |
| 只有 HTTP Span | 框架／GenAI Instrumentation 是否安装和加载 |
| Trace 断裂 | Propagator、Inject／Extract、代理是否丢 Header |
| `service.name` 异常 | Resource 合并顺序和环境变量 |
| Span 很短或不结束 | 异步／流式 Wrapper 生命周期 |
| Token 翻倍 | 多层模型 Span 或双语义字段重复统计 |
| Metric 暴涨 | 高基数属性、View 和基数限制 |
| Collector 内存高 | Batch、Tail Sampling、队列、超大属性 |
| 数据偶发丢失 | SDK退出Flush、Exporter Queue、网络和后端限流 |
| 后端字段不一致 | Semantic Conventions 版本与 Schema URL |
| 日志无法跳Trace | LogRecord是否携带TraceId／SpanId，格式转换是否丢字段 |

### 2.23.3 Trace 结构契约测试

建议在 CI 中使用 In-Memory Exporter 或测试 Collector，断言：

```text
根Span类型和名称
父子关系
SpanKind
必要属性
错误状态
Event
敏感字段未出现
Metric标签不含高基数值
流式Span最终结束
取消与超时语义不同
```

这比仅检查“有没有 Span”更可靠。

### 7.2.24 OpenTelemetry 与常见可观测组件的关系

| 组件 | 与 OpenTelemetry 的关系 |
|---|---|
| Prometheus | 主要是 Metrics 抓取、存储和查询生态；Collector 可接收／导出 Prometheus 数据 |
| Jaeger | 分布式 Trace 后端与生态；可接收 OTel 导出的 Trace |
| Tempo | Trace 存储后端；通常通过 Collector／OTLP 接入 |
| Zipkin | Trace 数据模型、协议和后端生态；OTel 可兼容导出 |
| Loki | 日志存储和查询后端；可接收 Collector 转换后的日志 |
| Fluent Bit／Vector | 日志与事件采集处理组件；可与 Collector 协同或分工 |
| 商业 APM | 提供存储、查询、告警、UI 和分析；通常支持 OTLP |
| OpenTracing | 早期分布式追踪 API，已被 OTel 统一替代 |
| OpenCensus | 早期 Trace／Metric 项目，能力被 OTel 继承和整合 |
| eBPF 可观测工具 | 进程外协议和内核观测，可产生或补充 OTel 数据 |

OpenTelemetry 可以替换“每个后端一套 SDK”的接入方式，但不会自动替换所有存储、查询和告警系统。

### 7.2.25 设计一套高质量 OTel 埋点的基本原则

1. **先定义业务问题，再定义信号。** 不要为了“全量”给每个函数建 Span。
2. **Span 表示有持续时间的操作，Event 表示瞬时事实。**
3. **Metric 用于聚合趋势，不承载请求唯一标识。**
4. **Resource 标识实体，不承载请求级信息。**
5. **InstrumentationScope 标识遥测生产模块。**
6. **优先使用稳定 Semantic Conventions。**
7. **动态值进入 Attribute，不进入 Span／Metric 名称。**
8. **一个逻辑操作只有一个权威 Span。**
9. **远程边界使用正确 SpanKind，并传播 Context。**
10. **默认不采集敏感正文。**
11. **Provider 由宿主应用统一管理。**
12. **Collector 配置需要版本锁定、校验和容量测试。**
13. **采样不能作为计费、审计或 Eval 的唯一事实来源。**
14. **对遥测结构做契约测试。**
15. **监控可观测管线本身。**

### 7.2.26 从 OpenTelemetry 本体过渡到 Agent 可观测性

掌握上述机制后，Agent 接入可以理解为一组映射：

```text
Workflow / Agent / Tool / Retriever
    → Span与Event的边界设计

模型、Token、流式Chunk
    → SDK层Instrumentation

HTTP、gRPC、MCP、CLI
    → SpanKind与Context Propagation

Loop、预算、权限、审批
    → 应用领域Span／Event／Metric

LangChain、OpenAI、Chroma
    → 不同Hook机制翻译到同一OTel API

OTel SDK
    → 统一采样、聚合、批处理和导出

Collector
    → 脱敏、路由、Tail Sampling和多后端治理
```

后续章节将在这一基础上，详细说明 OpenTelemetry 如何进入 Agent 内部、Python 零侵入探针如何工作，以及各开源框架怎样与 API、SDK、Semantic Conventions、OTLP 和 Collector 协同。

### 本章主要官方参考

- OpenTelemetry Specification：<https://opentelemetry.io/docs/specs/otel/>
- OpenTelemetry Client Architecture：<https://opentelemetry.io/docs/specs/otel/overview/>
- Signals：<https://opentelemetry.io/docs/concepts/signals/>
- Traces：<https://opentelemetry.io/docs/concepts/signals/traces/>
- Metrics：<https://opentelemetry.io/docs/concepts/signals/metrics/>
- Logs：<https://opentelemetry.io/docs/concepts/signals/logs/>
- Baggage：<https://opentelemetry.io/docs/concepts/signals/baggage/>
- Profiles：<https://opentelemetry.io/docs/concepts/signals/profiles/>
- Resource Specification：<https://opentelemetry.io/docs/specs/otel/resource/>
- InstrumentationScope：<https://opentelemetry.io/docs/specs/otel/common/instrumentation-scope/>
- Trace API：<https://opentelemetry.io/docs/specs/otel/trace/api/>
- Trace SDK：<https://opentelemetry.io/docs/specs/otel/trace/sdk/>
- Metrics API：<https://opentelemetry.io/docs/specs/otel/metrics/api/>
- Metrics SDK：<https://opentelemetry.io/docs/specs/otel/metrics/sdk/>
- Logs Specification：<https://opentelemetry.io/docs/specs/otel/logs/>
- Logs Data Model：<https://opentelemetry.io/docs/specs/otel/logs/data-model/>
- Logs API：<https://opentelemetry.io/docs/specs/otel/logs/api/>
- Logs SDK：<https://opentelemetry.io/docs/specs/otel/logs/sdk/>
- General Event Semantic Conventions：<https://opentelemetry.io/docs/specs/semconv/general/events/>
- Event Attribute Registry：<https://opentelemetry.io/docs/specs/semconv/registry/attributes/event/>
- OTel Event Bridge Attribute：<https://opentelemetry.io/docs/specs/semconv/registry/attributes/otel/>
- Exception Events in Logs：<https://opentelemetry.io/docs/specs/semconv/exceptions/exceptions-logs/>
- OTLP LogRecord Proto：<https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/logs/v1/logs.proto>
- Span Event API Deprecation Plan OTEP：<https://github.com/open-telemetry/opentelemetry-specification/blob/main/oteps/4430-span-event-api-deprecation-plan.md>
- Context Propagators：<https://opentelemetry.io/docs/specs/otel/context/api-propagators/>
- Sampling：<https://opentelemetry.io/docs/concepts/sampling/>
- Semantic Conventions：<https://opentelemetry.io/docs/specs/semconv/>
- Telemetry Schemas：<https://opentelemetry.io/docs/specs/otel/schemas/>
- OTLP Specification：<https://opentelemetry.io/docs/specs/otlp/>
- Collector Architecture：<https://opentelemetry.io/docs/collector/architecture/>
- Collector Configuration：<https://opentelemetry.io/docs/collector/configuration/>
- Collector Components：<https://opentelemetry.io/docs/collector/components/>
- Collector Scaling：<https://opentelemetry.io/docs/collector/scaling/>

---

## 7.3 OpenTelemetry 与 Agent 的总体关系

### 7.3.1 一次 Agent 任务应建模为一条 Trace

推荐把一次用户可感知的 Agent 请求建模为一条完整 Trace：

```text
invoke_workflow coding_change
├── invoke_agent orchestrator
│   ├── plan orchestrator
│   │   └── chat model-a
│   ├── retrieval codebase
│   ├── search_memory project_memory
│   └── invoke_agent implementer
│       ├── chat model-b
│       ├── execute_tool read_file
│       ├── execute_tool apply_patch
│       └── execute_tool run_tests
└── invoke_agent reviewer
    ├── chat model-c
    └── execute_tool run_tests
```

这种结构能够回答：

- 整体任务耗时；
- 每个 Agent 的耗时；
- 每次模型调用的 Token 和延迟；
- 工具调用的成功率；
- 重试发生在哪一层；
- 哪个子 Agent 消耗最多；
- 是否出现循环、预算耗尽或人工接管；
- 检索、记忆、模型和工具之间的因果关系。

### 7.3.2 总体架构

```mermaid
flowchart LR
    subgraph Runtime["Agent 应用进程"]
        FW["LangChain / LangGraph / OpenAI / Chroma Client"]
        INS["Instrumentation<br/>Callback / Patch / Processor / Decorator"]
        API["OpenTelemetry API"]
        SDK["OpenTelemetry SDK"]
        EXP["OTLP Exporter"]
    end

    SEM["Semantic Conventions<br/>gen_ai.* / http.* / db.*"]
    COL["OpenTelemetry Collector"]
    BACK["Tempo / Jaeger / Prometheus / Loki / 商业平台"]

    FW -->|"产生框架行为"| INS
    INS -->|"创建 Span / Metric / Event"| API
    SEM -.->|"约束名称和属性"| INS
    API --> SDK
    SDK --> EXP
    EXP -->|"OTLP"| COL
    COL --> BACK
```

---

## 7.4 OpenTelemetry 体系中的关键角色

### 7.4.1 被观测框架

典型对象包括：

- LangChain；
- LangGraph；
- OpenAI Python SDK；
- OpenAI Agents SDK；
- ChromaDB；
- LlamaIndex；
- CrewAI；
- AutoGen；
- PydanticAI；
- Haystack；
- Semantic Kernel；
- LiteLLM；
- 自研 Agent Runtime。

这些框架负责业务执行，不负责统一遥测表达。

### 7.4.2 Instrumentation

Instrumentation 是连接框架与 OpenTelemetry 的适配层，主要职责是：

1. 找到稳定调用边界；
2. 捕获开始、结束、异常、流式 Chunk 等生命周期事件；
3. 提取模型、Token、工具、检索、Agent、Workflow 等字段；
4. 调用 OpenTelemetry API 创建遥测；
5. 按 Semantic Conventions 设置属性；
6. 恢复父子关系和上下文；
7. 处理同步、异步、流式、取消和重试。

Instrumentation 通常不负责：

- 选择后端；
- 长期存储；
- 中心化采样；
- Collector 路由；
- 可视化。

### 7.4.3 OpenTelemetry API

API 提供稳定的遥测编程接口：

```text
TracerProvider API
Tracer
Span
MeterProvider API
Meter
Counter
Histogram
Logger
Context
Propagator
```

Instrumentation 调用 API，例如：

```python
tracer = trace.get_tracer(
    "opentelemetry.instrumentation.genai.example"
)

with tracer.start_as_current_span(
    "invoke_agent researcher",
    attributes={
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "researcher",
    },
):
    run_agent()
```

API 本身不决定：

- 是否采样；
- 如何批量；
- 发往哪里；
- 使用什么协议；
- 如何持久化。

如果应用没有安装或配置 SDK，API 通常退化为低开销 No-op。

### 7.4.4 OpenTelemetry SDK

SDK 是 API 的实际运行实现，负责：

- `TracerProvider`、`MeterProvider`、`LoggerProvider`；
- Resource；
- Sampler；
- Span Processor；
- Metric Reader；
- Batch；
- Queue；
- Exporter；
- Flush；
- Shutdown；
- 聚合；
- 重试。

Trace 管线示意：

```mermaid
flowchart LR
    A["Instrumentation 调用 API"] --> B["TracerProvider"]
    B --> C["Sampler"]
    C --> D["SpanProcessor"]
    D --> E["BatchSpanProcessor"]
    E --> F["OTLPSpanExporter"]
    F --> G["Collector"]
```

### 7.4.5 GenAI Semantic Conventions 详解

> 本节基于 OpenTelemetry GenAI Semantic Conventions 官方专用仓库在 **2026-09-02** 可见的 `main` 分支内容整理。当前 GenAI Spans、Agent Spans、Metrics、Events、Exceptions、Provider-specific Conventions 与 MCP Conventions 均标记为 **Development**，并不等同于核心 OpenTelemetry Trace／Metric／Log API 的稳定级别。生产系统应锁定版本、建立内部兼容层并进行数据口径回归。
>
> 官方入口：<https://github.com/open-telemetry/semantic-conventions-genai>

### 4.5.1 GenAI Semantic Conventions 的定位

GenAI Semantic Conventions 是 OpenTelemetry 面向生成式 AI、Agent、RAG、Memory、Tool 和 MCP 场景定义的统一遥测语言。它解决的不是“如何采集数据”，而是“采集到的数据应如何表达”。

例如，不同框架可能使用完全不同的事件名称：

```text
LangChain:        on_tool_start
OpenAI Agents:    FunctionSpanData
CrewAI:           ToolExecutionStartedEvent
自研 Runtime:     tool.begin
```

经过 Instrumentation 翻译后，可以统一为：

```text
gen_ai.operation.name = execute_tool
gen_ai.tool.name = search_documents
gen_ai.tool.call.id = call_123
```

它与 OpenTelemetry 其他组件的关系如下：

```mermaid
flowchart LR
    A["GenAI 框架或 SDK<br/>LangGraph / OpenAI / Chroma / 自研 Agent"]
    B["Instrumentation<br/>Callback / Processor / Patch / Native OTel"]
    C["GenAI Semantic Conventions<br/>名称、属性、事件、指标与口径"]
    D["OpenTelemetry API<br/>Span / Event / Metric / Context"]
    E["OpenTelemetry SDK<br/>采样、处理、聚合、批量"]
    F["OTLP Exporter"]
    G["Collector"]
    H["可观测与评估后端"]

    A --> B
    C -.约束.-> B
    B --> D
    D --> E
    E --> F
    F --> G
    G --> H
```

各部分职责：

| 角色 | 职责 |
|---|---|
| 框架或 SDK | 产生真实的模型、Agent、工具、检索和记忆行为 |
| Instrumentation | 截获框架事件，提取数据，调用 OTel API |
| GenAI Semantic Conventions | 规定统一的 Span、Attribute、Event、Metric 名称和含义 |
| OTel API | 提供写入遥测的稳定接口 |
| OTel SDK | 负责采样、聚合、处理和导出 |
| OTLP | 负责遥测数据传输，不负责业务调用链上下文传播 |
| Trace Context | 通过 `traceparent`、`tracestate` 等把跨进程 Span 串联起来 |

因此，语义约定并不是探针，也不会执行 Hook。它是 Instrumentation、应用埋点和后端查询共同遵守的数据契约。

### 4.5.2 当前覆盖范围与成熟度

官方专用仓库当前覆盖以下范围：

| 规范领域 | 主要内容 | 当前状态 |
|---|---|---|
| Model Spans | 推理、Embedding、Retrieval、Fetch Response、Memory、Tool | Development |
| Agent Spans | Create Agent、Invoke Agent、Invoke Workflow、Plan | Development |
| Metrics | Client、Model Server、Workflow、Agent、Tool 指标 | Development |
| Events | 推理详情、评估结果 | Development |
| Exceptions | GenAI Client 异常事件 | Development |
| Provider-specific | OpenAI、Anthropic、Azure AI Inference、AWS Bedrock | Development |
| MCP | MCP Client／Server Span、上下文传播和指标 | Development |

核心 OpenTelemetry Semantic Conventions 网站中的旧 GenAI 页面已经标记为迁移，权威内容转移到独立的 `semantic-conventions-genai` 仓库。因此，不能只根据核心 SemConv 的旧页面判断最新字段。

需要特别注意：

1. **Development 表示字段、要求级别、Span 名称和数据结构仍可能变化。**
2. **不同语言 SDK 和不同 Instrumentation 的实现进度不一致。** 规范中存在字段，不代表当前 Python、Java、.NET、JavaScript 探针已经实现。
3. **Provider-specific 规范是在通用 `gen_ai.*` 之上的扩展。** 不应使用 Provider 字段替代可表达相同含义的通用字段。
4. **专用仓库的 Schema URL 仍在演进。** 不要默认 Collector 已经能够自动完成任意 GenAI 版本之间的 Schema 转换。

### 4.5.3 规范用词与要求级别

理解 OpenTelemetry 文档中的要求级别非常重要：

| 要求级别 | 含义 |
|---|---|
| Required | 实现该类遥测时必须提供 |
| Conditionally Required | 满足指定条件时必须提供 |
| Recommended | 推荐提供，除非存在明确限制 |
| Opt-In | 默认不采集，需用户显式开启 |
| Development | 规范仍在开发，可能发生不兼容变化 |

在 GenAI 场景中，大体遵循以下原则：

- 操作名称、Provider 等识别性字段通常属于 Required 或 Conditionally Required；
- Model、Response ID、Finish Reason、Token Usage 等在数据可获得时提供；
- Prompt、Completion、System Instructions、Tool Arguments、Tool Result、Retrieval Documents、Memory Records 通常属于 Opt-In；
- 属性应尽量使用低基数值，动态 ID 不应进入 Span 名或 Metric 标签；
- 对采样决策重要的字段应在 Span 创建时写入，而不是等 Span 结束后再补。

### 4.5.4 `gen_ai.operation.name`：统一操作词典

`gen_ai.operation.name` 是最核心的分类字段。若官方已有合适的预定义值，应优先使用预定义值；只有确实无法表达时才使用自定义值。

截至本文基线，可见的标准操作值如下：

| 类别 | 操作值 | 含义 |
|---|---|---|
| 模型推理 | `chat` | Chat Completion 类交互 |
| 模型推理 | `text_completion` | 传统文本补全 |
| 模型推理 | `generate_content` | 多模态内容生成，例如文本、图像、语音 |
| 向量化 | `embeddings` | 生成 Embedding |
| 响应读取 | `fetch_response` | 根据 ID 读取此前生成的响应，不重新推理 |
| 检索 | `retrieval` | 从向量库、搜索系统或知识源检索上下文 |
| Agent | `create_agent` | 在托管 Agent 服务上创建 Agent 资源 |
| Agent | `invoke_agent` | 调用 Agent |
| Workflow | `invoke_workflow` | 执行由多个 Agent 或 GenAI 操作构成的协调流程 |
| 规划 | `plan` | Agent 的规划或任务分解阶段 |
| 工具 | `execute_tool` | 执行函数、扩展、数据存储或其他工具 |
| Memory | `create_memory_store` | 创建或初始化记忆存储 |
| Memory | `delete_memory_store` | 删除或释放记忆存储 |
| Memory | `create_memory` | 创建记忆记录 |
| Memory | `update_memory` | 更新已有记忆记录 |
| Memory | `upsert_memory` | 创建或更新记忆记录 |
| Memory | `search_memory` | 搜索记忆记录 |
| Memory | `delete_memory` | 删除记忆记录 |

两个容易用错的点：

- `fetch_response` 表示获取已存在响应，不应把它当成一次新的推理，也不应为其记录推理 Token 使用量；
- `retrieval` 用于获得供 GenAI 使用的相关上下文，不等同于所有数据库查询。普通事务查询仍应优先使用 `db.*` 语义约定。

### 4.5.5 Span 的总体边界

GenAI Span 表示调用者视角下的一次**逻辑操作**：

```text
开始：调用方正式发起逻辑操作
结束：完整响应已收到，或操作因错误、取消而终止
```

如果 SDK 因瞬时错误自动重试，推荐用一个逻辑 Span 覆盖所有自动重试：

```text
chat model-x                         ← 一个逻辑 GenAI Span
├── HTTP attempt 1                   ← 429
├── retry Event                      ← backoff=1000ms
└── HTTP attempt 2                   ← 200
```

这样：

- GenAI Span 表示用户实际感知的一次模型操作；
- HTTP Span 表示每一次真实网络尝试；
- Retry 记录为 Event 或 Log；
- 不会把一次 SDK 自动重试误算为两次业务模型调用。

如果流式响应返回一个 Iterator／AsyncIterator，Span 不能在 SDK 的 `create()` 方法返回时结束，而应在以下任一条件发生时结束：

- Stream 正常消费完毕；
- Stream 被显式关闭；
- 消费过程中发生异常；
- 操作被取消；
- 探针确认流已不可继续。

### 4.5.6 Span 类型、名称与 SpanKind

| Span 类型 | `gen_ai.operation.name` | 推荐 Span 名 | 推荐 SpanKind | 典型创建者 |
|---|---|---|---|---|
| 推理 | `chat`／`generate_content`／`text_completion` | `{operation} {request.model}` | `CLIENT`；同进程模型可为 `INTERNAL` | 模型 SDK Instrumentation |
| Embedding | `embeddings` | `embeddings {request.model}` | `CLIENT` | Embedding SDK Instrumentation |
| Retrieval | `retrieval` | `retrieval {data_source.id}` | `CLIENT` | Retriever／RAG Instrumentation |
| Fetch Response | `fetch_response` | `fetch_response` | `CLIENT` | Provider SDK Instrumentation |
| Memory | Memory 操作值 | `{operation}` | 远程为 `CLIENT`，同进程可为 `INTERNAL` | Memory SDK／Runtime |
| Create Agent | `create_agent` | `create_agent {agent.name}` | `CLIENT` | 托管 Agent SDK |
| Invoke Agent | `invoke_agent` | `invoke_agent {agent.name}`；无名称时 `invoke_agent` | 远程 `CLIENT`，同进程 `INTERNAL` | Agent 框架 |
| Invoke Workflow | `invoke_workflow` | `invoke_workflow {workflow.name}` | `INTERNAL` | 图执行器／Orchestrator |
| Plan | `plan` | `plan {agent.name}`；无名称时 `plan` | `INTERNAL` | Agent Runtime |
| Execute Tool | `execute_tool` | `execute_tool {tool.name}` | `INTERNAL` | Tool Runtime／Agent 框架 |

Span 名应保持低基数。以下做法错误：

```text
chat req_01JY...
retrieval 用户输入的完整问题
execute_tool /home/user/project/src/a.py
fetch_response resp_123456789
```

这些动态值应放入 Attribute 或受控日志中，而不是 Span 名。

### 4.5.7 推理 Span

推理 Span 用于表示调用生成式模型或服务生成内容、返回 Tool Call 或生成多模态结果的逻辑操作。

最小推荐模型：

```text
Span name: chat gpt-x
Span kind: CLIENT

attributes:
  gen_ai.operation.name = chat
  gen_ai.provider.name = openai
  gen_ai.request.model = gpt-x
  server.address = api.example.com
```

常用字段可以分为五组。

#### 身份与调用边界

| 字段 | 说明 |
|---|---|
| `gen_ai.operation.name` | 操作类型，推理通常为 `chat`、`text_completion` 或 `generate_content` |
| `gen_ai.provider.name` | Instrumentation 视角下的 Provider |
| `gen_ai.conversation.id` | 框架或 Provider 已有的会话／线程 ID |
| `gen_ai.agent.name` | 该调用由哪个逻辑 Agent 发起，适用时记录 |
| `server.address` | 目标 GenAI 服务地址 |
| `server.port` | 目标端口；设置地址且端口可得时记录 |

#### Prompt 模板

| 字段 | 说明 |
|---|---|
| `gen_ai.prompt.name` | Prompt 模板的稳定名称 |
| `gen_ai.prompt.version` | SemVer、日期、环境标签或 Prompt 平台版本 ID |
| `gen_ai.prompt.variable.<name>` | 模板变量，例如 `gen_ai.prompt.variable.language=Chinese` |

Prompt 变量可能包含隐私和业务数据，不能因其是模板变量就默认公开采集。

#### 请求参数

| 字段 | 说明 |
|---|---|
| `gen_ai.request.model` | 请求时指定的模型名称 |
| `gen_ai.request.choice.count` | 请求候选结果数量，非默认值时尤其有价值 |
| `gen_ai.request.max_tokens` | 最大生成 Token 数 |
| `gen_ai.request.temperature` | Temperature |
| `gen_ai.request.top_p` | Top-P |
| `gen_ai.request.top_k` | 采样 Top-K，不是 OpenAI 的 `top_logprobs` |
| `gen_ai.request.frequency_penalty` | Frequency Penalty |
| `gen_ai.request.presence_penalty` | Presence Penalty |
| `gen_ai.request.seed` | 随机种子 |
| `gen_ai.request.stop_sequences` | 停止序列 |
| `gen_ai.request.stream` | 流式请求时设为 `true`；未设置通常表示非流式 |
| `gen_ai.request.reasoning.level` | Provider 原始推理强度／级别值 |
| `gen_ai.request.previous_response.id` | 基于上一响应继续交互时的响应 ID |
| `gen_ai.request.stream_cursor` | 从既有 Stream 位置继续消费时的游标 |
| `gen_ai.output.type` | 请求的输出模态，例如 `text`、`json`、`image`、`speech` |

`gen_ai.output.type` 表示**请求的输出模态**，不是实际传输格式。例如请求图像时，Provider 可能返回 URL，而该字段仍应为 `image`。

#### 响应字段

| 字段 | 说明 |
|---|---|
| `gen_ai.response.id` | Provider 返回的响应标识 |
| `gen_ai.response.model` | 实际生成结果的模型版本 |
| `gen_ai.response.finish_reasons` | 按 Choice／Candidate 顺序排列的终止原因数组 |
| `gen_ai.response.status` | queued、in_progress、completed 等响应生命周期状态 |
| `gen_ai.response.time_to_first_chunk` | 从发起请求到首个流式 Chunk 的秒数 |

`finish_reasons` 和 `response.status` 不应混为一谈：

- `finish_reasons` 解释每个生成候选为什么停止，例如 `stop`、`length`、`content_filter`；
- `response.status` 表示响应对象的生命周期，例如排队、处理中、完成或失败。

如果某个候选本应有 Finish Reason，但因失败、取消或 Stream 非正常结束未收到，推荐在对应位置使用 `error`，而不是直接省略数组元素。

#### 采样创建时字段

以下字段可能用于 Head Sampling 或早期路由，若要记录，应尽量在 Span 创建时提供：

```text
gen_ai.agent.name
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
server.address
server.port
```

如果在 Span 结束时才补充，基于这些属性的 Head Sampler 已经无法使用它们作决策。

### 4.5.8 Embedding Span

Embedding Span 表示调用模型生成向量表示：

```text
Span name: embeddings text-embedding-x
Span kind: CLIENT

gen_ai.operation.name = embeddings
gen_ai.provider.name = openai
gen_ai.request.model = text-embedding-x
gen_ai.embeddings.dimension.count = 1536
gen_ai.request.encoding_formats = ["float"]
```

关键点：

- `gen_ai.embeddings.dimension.count` 表示期望或生成的向量维度；
- `gen_ai.request.encoding_formats` 表示 float、base64、binary 等编码形式；
- 原始待嵌入文本属于敏感内容，不应默认作为普通 Span Attribute；
- 如果一次批量 Embedding 输入很多文本，应记录批量大小、Token 和耗时，而不是把每条文本都提升为 Metric 标签。

### 4.5.9 Retrieval Span

Retrieval Span 表示从向量库、搜索引擎、知识库或其他 Grounding Data Source 中获取相关上下文：

```text
Span name: retrieval product-knowledge
Span kind: CLIENT

gen_ai.operation.name = retrieval
gen_ai.data_source.id = product-knowledge
gen_ai.retrieval.top_k = 10
```

常用字段：

| 字段 | 说明 |
|---|---|
| `gen_ai.data_source.id` | GenAI 系统视角下的数据源稳定 ID |
| `gen_ai.retrieval.query.text` | 检索查询文本，敏感，通常 Opt-In |
| `gen_ai.retrieval.top_k` | 计划返回的最大结果数 |
| `gen_ai.retrieval.documents` | 检索文档、ID、Score 和可选内容，通常 Opt-In |

`gen_ai.data_source.id` 应描述 Agent／RAG 系统中的数据源，而不是简单复制底层数据库连接名。例如：

```text
推荐：product-knowledge
不推荐：postgresql://user:password@host/db
```

可以同时使用其他语义约定补充底层信息：

```text
gen_ai.data_source.id = product-knowledge
db.system.name = chroma
db.namespace = product_docs
server.address = chroma.internal
```

职责分层如下：

```text
retrieval product-knowledge           ← RAG语义
└── HTTP POST /query                  ← 网络语义
    └── Chroma Server internal spans  ← 数据库内部语义
```

### 4.5.10 Fetch Response Span

`fetch_response` 表示读取已经存在的响应对象，不重新执行推理：

```text
Span name: fetch_response
Span kind: CLIENT

gen_ai.operation.name = fetch_response
gen_ai.response.id = resp_123
```

设计要点：

- 响应 ID 是高基数字段，不进入 Span 名；
- 该操作不应报告推理 Token Usage；
- 若从指定位置恢复流，使用 `gen_ai.request.stream_cursor`；
- 如果 Provider 实际重新执行推理，就不应使用 `fetch_response`。

### 4.5.11 Memory Span

Memory 语义约定覆盖 Store 和 Record 两层操作：

```text
create_memory_store
delete_memory_store
create_memory
update_memory
upsert_memory
search_memory
delete_memory
```

常用字段：

| 字段 | 说明 |
|---|---|
| `gen_ai.memory.store.id` | 记忆存储的稳定标识 |
| `gen_ai.memory.record.id` | 单条记忆记录 ID |
| `gen_ai.memory.record.count` | 本次操作涉及或返回的记录数 |
| `gen_ai.memory.query.text` | 搜索记忆使用的查询文本，敏感，通常 Opt-In |
| `gen_ai.memory.records` | 记忆内容和元数据，敏感，通常 Opt-In |
| `gen_ai.provider.name` | 远程 Memory Provider 存在时记录 |

`record.count` 的语义随操作变化：

- `search_memory`：实际返回的记录数；
- `create_memory`：尝试创建的记录数；
- `update_memory`：尝试修改的记录数；
- `upsert_memory`：尝试创建或更新的记录数。

建议链路：

```text
invoke_agent assistant
├── search_memory
│   └── db.search
├── chat model-x
└── upsert_memory
    └── db.write
```

需要区分：

- `gen_ai.conversation.id`：会话关联；
- `gen_ai.memory.store.id`：长期或短期记忆容器；
- `gen_ai.memory.record.id`：具体记忆记录；
- Trace ID：一次执行链路 ID。

四者不能互相替代。

### 4.5.12 Agent Span

#### Create Agent

用于调用远程托管 Agent 服务创建持久化 Agent 资源：

```text
create_agent code-reviewer
```

通常使用：

```text
gen_ai.operation.name = create_agent
gen_ai.agent.name = code-reviewer
gen_ai.agent.version = 2.1.0
gen_ai.provider.name = aws.bedrock
```

`gen_ai.agent.id` 应记录 Provider 分配的稳定 Agent 资源 ID，例如 ARN、Assistant ID、Agent Registry ID。**不要把一次运行生成的内存对象 ID 或 Run UUID 写入该字段。** 一次运行的 ID 应使用应用自定义字段，例如：

```text
app.agent.run.id = run_01J...
```

#### Invoke Agent Client

适用于远程托管 Agent：

```text
Span name: invoke_agent support-agent
Span kind: CLIENT
```

例如：

- OpenAI 托管 Agent／Assistant；
- AWS Bedrock Agent；
- 独立部署的远程 Agent 服务。

#### Invoke Agent Internal

适用于同一进程中的 Agent 框架：

```text
Span name: invoke_agent researcher
Span kind: INTERNAL
```

例如 LangChain、CrewAI 等在应用进程内执行的 Agent。

常用 Agent 字段：

| 字段 | 说明 |
|---|---|
| `gen_ai.agent.id` | 托管 Agent 稳定资源 ID |
| `gen_ai.agent.name` | 低基数、可读的逻辑 Agent 名 |
| `gen_ai.agent.description` | Agent 描述；可能较长，应受控 |
| `gen_ai.agent.version` | Agent 配置或发布版本 |
| `gen_ai.request.model` | Agent 只允许一个固定模型时可记录；动态多模型 Agent 不应误填 |
| `gen_ai.conversation.id` | 已存在的会话／线程 ID |

### 4.5.13 Workflow Span

`invoke_workflow` 表示由多个 Agent 或多个 GenAI 操作构成的协调流程：

```text
invoke_workflow multi_agent_rag
├── invoke_agent researcher
├── invoke_agent writer
└── invoke_agent reviewer
```

应记录的场景：

- 应用显式定义的图执行；
- Orchestrator 协调多个 Agent；
- 用户可感知的组合式 GenAI 流程；
- 应用定义的嵌套子图。

不应记录的场景：

- 单独调用一个 Agent；
- Agent 内部实现为了委派而隐式启动的 Runner；
- 无法区分工作流和普通函数调用时；
- 仅为了让 Trace 看起来更“完整”而人为套壳。

`gen_ai.workflow.name` 应满足：

- 低基数；
- 对应用有业务含义；
- 在应用范围内可区分；
- 不使用通用类名作为默认值。

```text
推荐：customer_support_triage
推荐：multi_agent_code_review
不推荐：StateGraph
不推荐：RunnableSequence
不推荐：workflow_20260902_143055_1234
```

### 4.5.14 Plan Span

`plan` 表示 Agent 可明确识别的规划或任务分解阶段：

```text
invoke_agent implementer
├── plan implementer
│   └── chat planner-model
├── execute_tool edit_file
└── execute_tool run_tests
```

边界原则：

- 生成计划的 LLM 调用应是 `plan` 的子 Span；
- 根据计划执行的 Tool／Task 通常与 `plan` 互为兄弟 Span，同属于 `invoke_agent`；
- 只有框架能可靠判断某段逻辑确实是 Planning／Task Decomposition 时才创建；
- 普通模型推理、反思、自我批评不应仅凭猜测全部标记为 `plan`。

### 4.5.15 Execute Tool Span

Tool Span 推荐模型：

```text
Span name: execute_tool search_documents
Span kind: INTERNAL

gen_ai.operation.name = execute_tool
gen_ai.tool.name = search_documents
gen_ai.tool.type = function
gen_ai.tool.call.id = call_123
gen_ai.agent.name = researcher
```

字段：

| 字段 | 要点 |
|---|---|
| `gen_ai.tool.name` | 必须稳定、低基数，通常来自 Tool Registry |
| `gen_ai.tool.type` | 例如 `function`、`extension`、`datastore` |
| `gen_ai.tool.description` | 可用时记录，避免过长 |
| `gen_ai.tool.call.id` | 模型或框架生成的 Tool Call ID |
| `gen_ai.tool.call.arguments` | Opt-In，可能含凭证、路径、SQL、PII |
| `gen_ai.tool.call.result` | Opt-In，可能体积很大或含敏感结果 |
| `gen_ai.agent.name` | 适用时记录实际执行该工具的 Agent |

工具名不应使用动态参数：

```text
推荐：read_file
不推荐：read_file_/home/david/project/secrets.txt
```

对于应用自定义工具，若框架没有自动探针，应用应主动创建符合该语义约定的 Span。Tool 往往直接在业务代码中执行，不能只依赖 HTTP 探针。

### 4.5.16 Provider 字段与 Provider-specific 规范

`gen_ai.provider.name` 是 Provider 语义的判别字段。它应基于 Instrumentation 能够确认的最佳信息填写，可能表示代理平台或托管层，而不一定是真正的上游基础模型供应商。

例如：

```text
Agent → 企业AI网关 → Azure OpenAI → OpenAI模型
```

如果 SDK 只知道自己连接的是 Azure OpenAI，应该记录：

```text
gen_ai.provider.name = azure.ai.openai
```

而不是根据模型名称推测为 `openai`。

截至本文基线，可见的 well-known values 包括：

| 值 | Provider |
|---|---|
| `anthropic` | Anthropic |
| `aws.bedrock` | AWS Bedrock |
| `azure.ai.inference` | Azure AI Inference |
| `azure.ai.openai` | Azure OpenAI |
| `cohere` | Cohere |
| `deepseek` | DeepSeek |
| `gcp.gemini` | Gemini AI Studio／Generative Language Endpoint |
| `gcp.gen_ai` | 无法进一步识别时的 Google GenAI Endpoint |
| `gcp.vertex_ai` | Vertex AI |
| `groq` | Groq |
| `ibm.watsonx.ai` | IBM watsonx.ai |
| `mistral_ai` | Mistral AI |
| `moonshot_ai` | Moonshot AI |
| `openai` | OpenAI |
| `perplexity` | Perplexity |
| `x_ai` | xAI |

规则：

```text
通用语义：gen_ai.*
Provider专有：openai.* / aws.bedrock.* / anthropic.* / azure.*
```

优先级：

1. 能用通用 `gen_ai.*` 表达的，先写通用字段；
2. 通用字段无法表达的 Provider 特性，再写 Provider-specific 字段；
3. `gen_ai.provider.name` 必须与专有命名空间一致；
4. 不应同时把 AWS Bedrock 遥测标记为 `openai.*` 风格。

### 4.5.17 Conversation、Agent Run 与 Trace ID

这几个 ID 常被混用：

| ID | 生命周期 | 正确用途 |
|---|---|---|
| `trace_id` | 一次端到端执行 | 串联因果链 |
| `gen_ai.conversation.id` | 多轮会话／线程 | 跨多条 Trace 关联同一对话 |
| `gen_ai.agent.id` | 托管 Agent 资源生命周期 | 标识稳定 Agent 资源 |
| `app.agent.run.id` | 一次 Agent Run | 标识应用内部运行实例 |
| `gen_ai.response.id` | Provider 响应对象生命周期 | 查找或继续某个响应 |
| `gen_ai.tool.call.id` | 一次 Tool Call | 关联模型 Tool Call 与执行结果 |

`gen_ai.conversation.id` 只有在框架、Provider 或应用本身已经有真实会话 ID 时才记录。禁止用以下值伪造：

```text
随机UUID
Trace ID
Request ID
Prompt内容哈希
用户输入哈希
```

`gen_ai.conversation.compacted` 是一个正向事实字段。只有明确知道本次会话上下文发生过压缩时才设置为 `true`，不应在未知时默认写成 `false`。

### 4.5.18 Token Usage 的统一口径

核心字段：

```text
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
gen_ai.usage.reasoning.output_tokens
gen_ai.usage.cache_read.input_tokens
gen_ai.usage.cache_write.input_tokens
```

此外还可能有 text、audio、image 等模态细分字段。

#### 总量与子集关系

细分 Token 多数是总量的**子集**，不是可以直接相加的独立费用项：

```text
input_tokens
├── cache_read.input_tokens
├── cache_write.input_tokens
├── text.input_tokens
├── audio.input_tokens
└── image.input_tokens

output_tokens
├── reasoning.output_tokens
├── text.output_tokens
├── audio.output_tokens
└── image.output_tokens
```

因此下面的算法通常错误：

```text
总输入 = input_tokens + cache_read.input_tokens + text.input_tokens
```

因为会重复计算。

推荐规则：

1. `input_tokens` 包含所有输入 Token 类型，包括缓存命中的 Token；
2. `output_tokens` 表示总输出 Token；
3. Reasoning Token 是 Output Token 的子集；
4. Provider 同时给出“计费用量”和“模型实际消费量”时，推荐记录计费口径，以便和账单对齐；
5. Provider 未返回 Token 时，不应无条件使用不可靠的离线估算；若确需估算，应显式标记来源；
6. `fetch_response` 不报告 Token Usage；
7. 成本金额目前不应由不同 Instrumentation 各自使用不统一字段随意计算，建议在独立成本处理层根据 Token、模型、地区、租户和价格版本计算。

建议增加自定义来源字段：

```text
app.usage.source = provider | tokenizer_estimate | billing_export
app.pricing.version = 2026-09-01
```

### 4.5.19 结构化内容模型

GenAI 场景中的输入输出不是简单字符串，可能包含：

- 文本；
- 图像；
- 音频；
- Tool Call；
- Tool Result；
- Reasoning／Thinking 类型内容；
- 引用和检索文档；
- Provider 特有的 Content Part。

规范为这些内容定义结构化 JSON 模型。主要字段包括：

```text
gen_ai.system_instructions
gen_ai.input.messages
gen_ai.output.messages
gen_ai.tool.definitions
gen_ai.tool.call.arguments
gen_ai.tool.call.result
gen_ai.retrieval.documents
gen_ai.memory.records
```

简化示例：

```json
[
  {
    "role": "user",
    "parts": [
      {
        "type": "text",
        "content": "查询订单 1001 的状态"
      }
    ]
  },
  {
    "role": "assistant",
    "parts": [
      {
        "type": "tool_call",
        "id": "call_123",
        "name": "lookup_order",
        "arguments": {
          "order_id": "1001"
        }
      }
    ]
  }
]
```

记录规则：

- 写入 Event 时必须使用结构化形式；
- 写入 Span 时，如果语言 SDK 不支持复杂属性，可以序列化为 JSON String；支持结构化值时应优先使用结构化形式；
- 截断时应尽量保持 JSON 结构合法，不要在任意字节位置切断；
- Tool Definition 体积可能很大，默认不应包含所有可选 Schema 描述；
- 图片、音频和大文档更适合存外部对象存储，只在 Trace 中保存引用。

### 4.5.20 Artifact：当前规范状态与扩展边界

当前 GenAI Semantic Conventions 没有一组已经稳定发布、可直接依赖的 `gen_ai.artifact.*` 属性、Span、Event 或 Metric。上游 Agentic Systems 提案正在讨论 Task、Action、Agent、Team、Artifact 和 Memory 等概念及其关系，但提案不等于已经进入规范正文的稳定契约。

因此，应区分：

```text
已规范的GenAI内容
  gen_ai.input.messages
  gen_ai.output.messages
  gen_ai.tool.call.result
  gen_ai.retrieval.documents
  gen_ai.memory.records

Agent领域Artifact
  报告、文件、图片、补丁、数据集、证据、Checkpoint
  需要独立身份、版本、存储、权限和血缘
```

推荐扩展策略：

1. 内部先定义稳定、版本化的 Artifact Domain Model；
2. 当前遥测使用 `app.artifact.*`，明确声明为自定义命名空间；
3. 不自行杜撰并发布看似官方的 `gen_ai.artifact.*`；
4. Span 记录 Artifact 操作，Event 记录生命周期，Metric 记录趋势；
5. 大型内容进入 Artifact Store，Trace／Event 只保存不透明引用；
6. 通过 Mapper 隔离业务模型与正在演进的 GenAI SemConv；
7. 官方语义稳定后，再按语义而非仅按字段名迁移。

示例：

```text
app.artifact.id = art_123
app.artifact.version.id = ver_4
app.artifact.role = evidence
app.artifact.type = test-report
app.artifact.media_type = application/json
app.artifact.size_bytes = 48211
app.artifact.status = ready
```

Artifact 的完整模型、A2A／MCP／LangChain／OpenAI／Chroma 映射以及安全治理见 [2.9 Artifact 详解](#29-artifact-详解agent-可交付物外部内容与遥测关联)。

### 4.5.21 内容采集的三种模式

#### 模式一：默认不采集正文

生产默认建议：

```text
采集：模型、Provider、Token、延迟、状态、错误、工具名称
不采集：Prompt、Completion、System Prompt、工具参数、工具结果、文档正文
```

适合大多数生产环境。

#### 模式二：将内容记录到 Span 或 Event

适合：

- 开发／测试环境；
- 受控数据集；
- 低数据量调试；
- 具备严格权限隔离的评测环境。

风险：

- Trace 体积过大；
- Prompt 可能包含 PII、密钥、源代码和商业数据；
- Completion 可能重复敏感内容；
- Tail Sampling 之前，内容已经进入 Collector 内存；
- 不同后端对复杂属性支持不一致。

#### 模式三：正文外部存储，Trace 只留引用

推荐生产模式：

```text
Agent进程
├── 进程内脱敏
├── 加密上传到受控对象存储
└── Span记录 content_hash / artifact_ref / policy_version
```

示例：

```text
app.gen_ai.input.content_hash = sha256:...
app.gen_ai.input.storage_ref = artifact://tenant-a/trace-x/input
app.gen_ai.output.storage_ref = artifact://tenant-a/trace-x/output
app.redaction.policy.version = 3.2.0
```

部分 Instrumentation 可以暴露进程内 Upload Hook。该 Hook 可以独立于 Span 是否采样运行，由应用负责脱敏、上传并将引用附加到 Span。这样能避免把原文直接发送到通用可观测后端。

### 4.5.22 GenAI Events

GenAI Event 遵循 OpenTelemetry 通用 Event 模型：

```text
Event = LogRecord + 非空EventName
```

它通过 Logs API／SDK 和 OTLP Logs 管线导出，不是独立信号。新实现应设置 `EventName`／`event_name`，而不是继续写入已废弃的 `event.name` Attribute。

当前 GenAI Semantic Conventions 定义的主要事件包括：

| EventName | 用途 | Requirement Level |
|---|---|---|
| `gen_ai.client.inference.operation.details` | 模型请求、输入输出、参数和使用量详情 | Opt-In |
| `gen_ai.evaluation.result` | 模型或 Agent 输出的评估结果 | Recommended |
| `gen_ai.client.operation.exception` | GenAI Client 操作异常 | Recommended |

GenAI Events 仍处于 Development，并非所有语言 SDK 和 Instrumentation 都完整支持 `EventName`、复杂属性及 Event-to-Span 关联。生产系统应锁定版本并执行 OTLP 契约测试。

#### `gen_ai.client.inference.operation.details`

该 Event 用于把输入输出和请求详情独立于 Trace 存储：

```text
EventName = gen_ai.client.inference.operation.details
```

常见字段包括：

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.model
gen_ai.response.id
gen_ai.input.messages
gen_ai.output.messages
gen_ai.system_instructions
gen_ai.tool.definitions
gen_ai.request.*
gen_ai.usage.*
error.type
```

它是 **Opt-In**，适合：

- 需要独立保留模型交互详情；
- Trace 与正文使用不同生命周期；
- 评测系统消费模型输入输出；
- 后端支持按 EventName 隔离；
- 内容已经完成脱敏和访问控制。

它不能绕开隐私要求。使用该 Event 采集正文仍需：

- 显式开启；
- 进程内脱敏；
- 租户隔离；
- 保留期；
- 审计；
- 大字段限制；
- 必要时仅保存对象存储引用。

#### `gen_ai.evaluation.result`

用于记录一次评估结果：

```text
EventName = gen_ai.evaluation.result
gen_ai.evaluation.name = answer_relevance
gen_ai.evaluation.score.value = 0.92
gen_ai.evaluation.score.label = pass
gen_ai.evaluation.explanation = "回答覆盖了主要事实"
gen_ai.response.id = resp_123
```

能获得被评估 GenAI Span 时，Event 应关联该 Span Context；无法获得 SpanId 时，应尽量设置 `gen_ai.response.id`。

企业内部通常还需要补充：

```text
app.eval.evaluator.version
app.eval.dataset.id
app.eval.dataset.version
app.eval.rubric.id
app.eval.run.id
```

评估明细使用 Event，整体趋势使用 Metric：

```text
Event：
  每次评分、解释、response.id、trace关联

Metric：
  平均分、通过率、分位数、趋势
```

### 4.5.23 GenAI Exception Event

规范定义：

```text
EventName = gen_ai.client.operation.exception
SeverityNumber = WARN
```

推荐在 GenAI Client 逻辑操作出现异常时记录，典型包括：

- Provider API Error；
- Rate Limit；
- Timeout；
- Model Error；
- 连接失败；
- 流式消费异常；
- SDK 解析异常。

常用异常字段：

```text
exception.type
exception.message
exception.stacktrace
```

同时，Span 仍应遵循通用错误语义：

```text
span.status = ERROR
error.type = timeout | rate_limit | ProviderError | 429 | ...
```

建议一次逻辑模型操作最终失败时记录一次异常 Event；SDK 内部每次 HTTP／RPC 重试由底层协议 Span 记录，避免在 Agent、框架、模型 SDK 和 HTTP 层重复计数。

`error.type` 应是低基数、可聚合的错误类别；完整动态文本放入 `exception.message` 或受控日志。

异常消息与 Stacktrace 可能包含：

- Prompt 片段；
- URL Query；
- API Key 尾部；
- 文件路径；
- Tool 参数；
- SQL；
- 用户输入。

因此异常 Event 同样需要脱敏、截断和权限隔离。通用 Event 数据模型、Bridge 与治理详见 [2.8 Events 详解](#28-events-详解基于-logs-数据模型的结构化事件)。
### 4.5.24 GenAI Metrics

当前规范定义了 Client、Model Server、Workflow、Agent 和 Tool 五组指标。

#### Client Metrics

| 指标 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `gen_ai.client.token.usage` | Histogram | `{token}` | 输入和输出 Token 用量 |
| `gen_ai.client.operation.duration` | Histogram | `s` | GenAI Client 逻辑操作耗时 |
| `gen_ai.client.operation.time_to_first_chunk` | Histogram | `s` | 流式请求首 Chunk 时间 |
| `gen_ai.client.operation.time_per_output_chunk` | Histogram | `s` | 输出 Chunk 之间的间隔 |

`gen_ai.client.token.usage` 使用 `gen_ai.token.type=input|output` 区分方向。官方建议使用覆盖从小请求到超大上下文的指数型显式 Bucket，例如：

```text
1, 4, 16, 64, 256, 1024, 4096, 16384,
65536, 262144, 1048576, 4194304, 16777216, 67108864
```

该 Bucket 是建议值，可通过 View 根据企业模型上下文范围调整，但调整后应保持跨服务口径一致。

#### Model Server Metrics

| 指标 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `gen_ai.server.request.duration` | Histogram | `s` | 模型服务端请求总耗时 |
| `gen_ai.server.time_per_output_token` | Histogram | `s` | 服务端单输出 Token 时间 |
| `gen_ai.server.time_to_first_token` | Histogram | `s` | 服务端首 Token 时间 |

Client 的 First Chunk 与 Server 的 First Token 不完全相同：

```text
Client TTFC = 排队 + 网关 + 网络 + 服务端首Token + 传输
Server TTFT = 服务端接收请求后到首Token生成
```

两者结合才能区分网络／网关延迟与模型推理延迟。

#### Workflow Metrics

| 指标 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `gen_ai.invoke_workflow.duration` | Histogram | `s` | Workflow 端到端耗时 |

它与 `gen_ai.client.operation.duration` 可以同时存在：前者是整个工作流，后者是其中某一次模型或服务操作。

#### Agent Metrics

| 指标 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `gen_ai.invoke_agent.duration` | Histogram | `s` | 单次 Agent 调用耗时 |
| `gen_ai.invoke_agent.inference_calls` | Histogram | `{call}` | Agent 直接发起的推理调用数 |
| `gen_ai.invoke_agent.tool_calls` | Histogram | `{call}` | Agent 直接发起的 Client-side Tool 调用数 |

计数规则：

- 成功和失败的直接调用都计入；
- 父 Agent 不重复统计子 Agent 内部发起的调用；
- 每次调用只归属一个直接发起者；
- Provider 内置 Web Search、Code Interpreter 等服务器侧内置工具，不应被误算为应用 Client-side Tool Call，除非规范或 Provider Instrumentation 明确映射。

#### Tool Metrics

| 指标 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `gen_ai.execute_tool.duration` | Histogram | `s` | 工具执行耗时 |

如果同时产生 Tool Span，该指标的耗时边界应与 `execute_tool` Span 一致。

#### Metric 标签治理

适合做标签：

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.model
gen_ai.token.type
gen_ai.agent.name
gen_ai.workflow.name
gen_ai.tool.name
error.type
```

需要谨慎或禁止作为默认标签：

```text
trace_id
conversation.id
response.id
tool.call.id
agent.run.id
user.id
prompt正文
文件路径
检索Query
```

否则会产生高基数时间序列。

### 4.5.25 MCP Semantic Conventions

MCP 基于 JSON-RPC，但 OpenTelemetry 推荐使用 MCP 专用语义约定，而不是只使用通用 RPC 或 HTTP 语义。原因包括：

- 一个 Streamable HTTP 连接中可能承载多个 MCP 请求和通知；
- 单个 HTTP Span 无法表达消息级 Tool Call；
- MCP 有工具、资源、Prompt、通知等领域语义；
- stdio 场景没有普通 HTTP 上下文头。

#### 消息级上下文传播

MCP Context 推荐注入到请求 `params._meta`：

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "search-code",
    "arguments": {
      "query": "AgentRuntime"
    },
    "_meta": {
      "traceparent": "00-<trace-id>-<span-id>-01",
      "tracestate": "vendor=value",
      "baggage": "tenant.id=t1"
    }
  },
  "id": 1
}
```

服务端从 `_meta` 提取上下文并作为远程 Parent。`traceparent`、`tracestate`、`baggage` 在 `_meta` 中使用未加 DNS 前缀的标准键。

#### MCP 与 Tool Span 去重

可能出现：

```text
execute_tool search-code
└── mcp.client tools/call
    └── mcp.server tools/call
```

如果 MCP Instrumentation 能确认外层已有完整的 `execute_tool` Span，可以把 MCP 属性补充到现有 Span，而不是再创建同义 Tool Span。目标是保留不同边界，而不是重复表达同一个逻辑操作。

#### MCP Metrics

当前规范包含：

```text
mcp.client.operation.duration
mcp.server.operation.duration
```

现有文档还包含 Session Duration 等与有状态 MCP 生命周期相关的内容。

#### 2026-07-28 协议差异风险

截至 2026-09-02，OpenTelemetry GenAI 仓库中的 MCP 文档仍以带 `initialize` 和 `mcp.session.id` 的较早协议生命周期为主。官方仓库 Issue #437 指出，MCP `2026-07-28` 已移除协议级 Handshake／Session，并引入新的发现和通知机制。因此：

- 不应把当前 MCP Session 字段视为长期稳定口径；
- 接入新版 MCP Runtime 时应记录其协议版本；
- 应隔离 MCP 映射层，等待 SemConv 与新协议对齐；
- Session 指标和字段不得在新版协议上被机械套用。

建议增加：

```text
app.mcp.protocol.version = 2026-07-28
```

待正式语义约定提供标准字段后再迁移。

### 4.5.26 错误、取消、超时与重试

#### 错误

当操作失败时：

```text
Span status = ERROR
error.type = 低基数错误类别
exception event = 可选详细异常
```

推荐：

```text
error.type = rate_limit
error.type = timeout
error.type = authentication_error
error.type = OpenAIError
error.type = 429
```

不推荐：

```text
error.type = "Request failed for user abc at 14:03 with prompt ..."
```

#### 用户取消

用户主动取消不一定意味着系统错误：

```text
app.agent.termination_reason = user_cancelled
span event = app.agent.cancelled
```

只有框架或 Provider 把取消作为失败返回，或取消导致操作不满足业务契约时，才应设置 Error Status。

#### 超时

超时通常是错误：

```text
error.type = timeout
span.status = ERROR
```

还可补充：

```text
app.timeout.phase = model_wait | tool_execution | queue | stream
app.timeout.limit_ms = 30000
```

#### 自动重试

SDK 内部自动重试：一个逻辑 GenAI Span，多个 Transport Attempt Span。

Agent 策略层重新规划后再次调用：通常是两个独立 GenAI Span，因为它们是两个逻辑调用：

```text
invoke_agent
├── chat model-x                 ← 第一次逻辑调用
├── event plan.revised
└── chat model-x                 ← 第二次逻辑调用
```

### 4.5.27 Sampling、Cardinality 与属性时机

GenAI Trace 通常长、贵且包含敏感数据。采样设计应考虑：

- Agent／Workflow 名；
- Provider；
- Model；
- Error Type；
- 总耗时；
- Token 使用量；
- 工具失败；
- 循环次数；
- Eval 失败；
- 用户取消；
- 预算耗尽。

#### Head Sampling

适合基于 Span 创建时已知的低基数字段：

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.agent.name
gen_ai.workflow.name
server.address
```

#### Tail Sampling

适合基于完整 Trace 决策：

```text
任意Span出现error.type
总耗时超过阈值
Token超预算
Tool失败
Agent循环
Eval低分
人工接管
```

高基数属性的放置原则：

| 数据 | Span Attribute | Metric Label | Span Name |
|---|---:|---:|---:|
| Agent Name | 是 | 可控时是 | 可用于固定名称 |
| Conversation ID | 是 | 否 | 否 |
| Response ID | 是 | 否 | 否 |
| Tool Call ID | 是 | 否 | 否 |
| Prompt Name | 是 | 可控时是 | 一般否 |
| Prompt 正文 | 默认否 | 否 | 否 |
| Model Name | 是 | 是 | 是 |
| Error Type | 是 | 是 | 否 |
| 文件路径 | 受控时 | 否 | 否 |

### 4.5.28 与 LangChain、OpenAI、Chroma 的映射

#### LangChain／LangGraph

```text
on_chain_start / Graph.invoke
    → invoke_workflow 或 invoke_agent

on_chat_model_start
    → 框架级模型边界；若底层OpenAI探针拥有chat Span所有权，则避免重复

on_tool_start
    → execute_tool

on_retriever_start
    → retrieval
```

推荐所有权：

```text
LangGraph Instrumentation：invoke_workflow / invoke_agent / execute_tool / retrieval
OpenAI SDK Instrumentation：chat / embeddings
HTTPX Instrumentation：HTTP CLIENT
应用手工埋点：plan / permission / budget / loop / human approval
```

#### OpenAI Python SDK

```text
responses.create / chat.completions.create
    → chat 或 generate_content

embeddings.create
    → embeddings

responses.retrieve
    → fetch_response

stream wrapper
    → TTFC、Chunk间隔、完整响应、Token、Finish Reason
```

Provider-specific 字段只能补充 OpenAI 独有能力，通用模型和 Token 信息仍应写入 `gen_ai.*`。

#### OpenAI Agents SDK

```text
Runner / Trace
    → invoke_workflow

AgentSpanData
    → invoke_agent

FunctionSpanData
    → execute_tool

底层OpenAI模型调用
    → OpenAI SDK Instrumentation创建chat Span
```

#### ChromaDB

```text
LangChain Retriever
    → retrieval

Chroma Client HTTP
    → HTTP CLIENT

Chroma Server内建 trace_method
    → Chroma内部Span
```

完整链路：

```text
invoke_workflow rag_graph
└── invoke_agent researcher
    ├── retrieval product-knowledge
    │   └── HTTP POST /query
    │       └── Chroma Server internal query spans
    └── chat model-x
        └── HTTP POST /responses
```

### 4.5.29 推荐的统一内部数据模型

由于规范仍处于 Development，不建议业务代码到处硬编码 `gen_ai.*`。推荐建立内部领域模型：

```python
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class GenAIOperationContext:
    operation: str
    provider: str | None = None
    request_model: str | None = None
    response_model: str | None = None
    agent_name: str | None = None
    workflow_name: str | None = None
    conversation_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class GenAISemConvMapper:
    """唯一负责内部领域字段到当前 OTel GenAI SemConv 的映射。"""

    def to_span_attributes(self, ctx: GenAIOperationContext) -> dict[str, Any]:
        result: dict[str, Any] = {
            "gen_ai.operation.name": ctx.operation,
        }

        optional = {
            "gen_ai.provider.name": ctx.provider,
            "gen_ai.request.model": ctx.request_model,
            "gen_ai.response.model": ctx.response_model,
            "gen_ai.agent.name": ctx.agent_name,
            "gen_ai.workflow.name": ctx.workflow_name,
            "gen_ai.conversation.id": ctx.conversation_id,
        }

        result.update({key: value for key, value in optional.items() if value is not None})
        result.update(ctx.attributes)
        return result
```

优势：

- 升级 SemConv 时只修改 Mapper；
- 可同时兼容旧字段和新字段；
- 能统一 Python、Java、Rust、TypeScript 的命名；
- 可以集中完成脱敏、截断、白名单和基数检查；
- 可以对重复 Span 和重复 Token 统计做集中治理。

### 4.5.30 Python 手工 Span 示例

```python
from __future__ import annotations

from typing import Any
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

tracer = trace.get_tracer("com.example.agent-runtime", "2.1.0")


def call_model(client: Any, messages: list[dict[str, Any]]) -> Any:
    initial_attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "model-x",
        "gen_ai.agent.name": "researcher",
        "gen_ai.prompt.name": "research-answer",
        "gen_ai.prompt.version": "3.2.0",
        "gen_ai.request.stream": False,
    }

    with tracer.start_as_current_span(
        "chat model-x",
        kind=SpanKind.CLIENT,
        attributes=initial_attributes,
    ) as span:
        try:
            response = client.responses.create(
                model="model-x",
                input=messages,
            )

            if getattr(response, "id", None):
                span.set_attribute("gen_ai.response.id", response.id)

            if getattr(response, "model", None):
                span.set_attribute("gen_ai.response.model", response.model)

            usage = getattr(response, "usage", None)
            if usage is not None:
                if getattr(usage, "input_tokens", None) is not None:
                    span.set_attribute(
                        "gen_ai.usage.input_tokens",
                        usage.input_tokens,
                    )
                if getattr(usage, "output_tokens", None) is not None:
                    span.set_attribute(
                        "gen_ai.usage.output_tokens",
                        usage.output_tokens,
                    )

            return response

        except TimeoutError as exc:
            span.set_attribute("error.type", "timeout")
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise
```

工程注意：

- 自动 Instrumentation 已经覆盖 OpenAI SDK 时，不要再手工创建同义 `chat` Span；
- 可以在外层创建 `invoke_agent`，让底层 OpenAI Instrumentation 自动生成 `chat`；
- Prompt 正文未在示例中记录；
- 关键采样字段在创建 Span 时传入；
- Token 和 Response Model 只能在响应返回后补充。

### 4.5.31 生产版本治理

建议建立一个 SemConv Manifest：

```yaml
telemetry_contract:
  internal_version: 3.0.0
  baseline_date: 2026-09-02
  genai_repository_ref: <pinned-commit-or-release>
  instrumentation:
    langchain: <locked-version>
    openai: <locked-version>
    openai_agents: <locked-version>
  content_capture:
    default: disabled
    external_storage: enabled
  token_accounting:
    source_priority:
      - provider_billed
      - provider_consumed
      - tokenizer_estimate
  duplicate_span_policy:
    inference_owner: provider_sdk
    workflow_owner: agent_framework
    transport_owner: protocol_instrumentation
```

升级流程：

1. 固定当前官方仓库 Commit 或正式 Release；
2. 导出旧版与新版样例 Trace；
3. 比较 Span 名、Kind、父子关系和属性变化；
4. 比较 Token、Agent Call、Tool Call 和 Duration 指标口径；
5. 检查 Dashboard、Alert、Eval、Billing SQL 是否受影响；
6. 检查敏感字段是否新增或默认行为是否变化；
7. 灰度双写新旧字段；
8. 完成查询迁移后再停止旧字段；
9. 保留 SemConv 版本和 Instrumentation Scope 以便追溯。

### 4.5.32 最小生产字段集

对于不采集正文的生产环境，建议至少保留：

#### 模型调用

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.model
gen_ai.response.id
gen_ai.response.finish_reasons
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
gen_ai.agent.name
gen_ai.conversation.id          # 仅真实存在时
server.address
error.type                      # 失败时
```

#### Agent／Workflow

```text
gen_ai.operation.name
gen_ai.agent.name
gen_ai.agent.version
gen_ai.workflow.name
gen_ai.provider.name            # 远程托管时
gen_ai.conversation.id          # 真实存在时
error.type                      # 失败时
```

#### Tool

```text
gen_ai.operation.name = execute_tool
gen_ai.tool.name
gen_ai.tool.type
gen_ai.tool.call.id
gen_ai.agent.name
error.type                      # 失败时
```

#### Retrieval／Memory

```text
gen_ai.operation.name
gen_ai.data_source.id
gen_ai.retrieval.top_k
gen_ai.memory.store.id
gen_ai.memory.record.count
error.type
```

### 4.5.33 接入验收清单

#### Span 正确性

- [ ] 每次逻辑模型调用只有一个权威 GenAI Inference Span；
- [ ] 自动 Retry 位于同一个逻辑 Span 内，网络 Attempt 可单独展开；
- [ ] Stream 在真正结束、关闭、异常或取消时结束 Span；
- [ ] Workflow、Agent、Plan、Tool、Retrieval 父子关系符合真实因果链；
- [ ] Span 名不含 Response ID、Conversation ID、Tool Call ID 或用户输入；
- [ ] 远程调用使用 `CLIENT`，同进程 Agent／Tool 使用 `INTERNAL`；
- [ ] `fetch_response` 未记录推理 Token。

#### 属性正确性

- [ ] `gen_ai.provider.name` 与 Provider-specific 字段一致；
- [ ] `gen_ai.agent.id` 不是临时 Run ID；
- [ ] `gen_ai.conversation.id` 不是随机生成或 Trace ID；
- [ ] `gen_ai.request.top_k` 没有错误映射为 `top_logprobs`；
- [ ] `finish_reasons` 与 Choice 顺序一致；
- [ ] Token 总量和细分子集没有重复相加；
- [ ] 计费 Token 与账单口径一致；
- [ ] Sampling 所需属性在 Span 创建时已提供。

#### 内容安全

- [ ] Prompt、Completion、System Instructions 默认关闭；
- [ ] Tool Arguments／Result、Retrieval Documents、Memory Records 为 Opt-In；
- [ ] 进程内完成第一层脱敏；
- [ ] Collector 做二次过滤和 Redaction；
- [ ] 外部正文存储具备加密、租户隔离、保留期和审计；
- [ ] Exception Message 与 Stacktrace 经过敏感数据检查。

#### Metrics 正确性

- [ ] Token、Agent Call、Tool Call 没有因重复 Span 双计；
- [ ] 父 Agent 未重复统计子 Agent 内部调用；
- [ ] Metric 标签不包含会话 ID、响应 ID、Tool Call ID 和用户 ID；
- [ ] Client TTFC 与 Server TTFT 未混为同一指标；
- [ ] Tool Duration 与 Tool Span 边界一致。

#### 版本治理

- [ ] 锁定 GenAI SemConv 基线 Commit／Release；
- [ ] 锁定各语言 Instrumentation 版本；
- [ ] 内部 Mapper 隔离业务代码和 `gen_ai.*` 演进；
- [ ] Dashboard、Alert、Eval 和 Billing 查询有回归测试；
- [ ] MCP 协议版本与当前 MCP SemConv 兼容性经过检查。

### 4.5.34 本节官方资料

- GenAI Semantic Conventions 总览：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md>
- GenAI Model Spans：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md>
- GenAI Agent Spans：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md>
- GenAI Metrics：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md>
- GenAI Events：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md>
- GenAI Exceptions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-exceptions.md>
- GenAI Attribute Registry：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md>
- Provider-specific OpenAI：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/openai.md>
- Provider-specific Anthropic：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/anthropic.md>
- Provider-specific AWS Bedrock：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md>
- Provider-specific Azure AI Inference：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/azure-ai-inference.md>
- MCP Semantic Conventions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md>
- MCP 2026-07-28 对齐 Issue：<https://github.com/open-telemetry/semantic-conventions-genai/issues/437>

### 7.4.6 OTLP

OTLP 是 OpenTelemetry 原生遥测传输协议，负责：

- Trace、Metric、Log 的编码；
- Protobuf 数据结构；
- OTLP/gRPC；
- OTLP/HTTP；
- SDK 到 Collector；
- Collector 到 Collector；
- 部分成功、限流和重试语义。

OTLP 不理解 Agent、Tool、Token 或 Retriever，它只运输已经生成的遥测对象。

### 7.4.7 Context Propagation

Context Propagation 负责把调用关系串起来：

```text
同线程
异步任务
线程池
HTTP
gRPC
消息队列
MCP
子进程
CLI
```

典型标准字段：

```text
traceparent
tracestate
baggage
```

它不负责导出遥测。

### 7.4.8 Collector

Collector 位于 SDK 和后端之间，负责：

- 接收 OTLP；
- 批处理；
- 内存限制；
- 脱敏；
- 属性转换；
- 过滤；
- Tail Sampling；
- 多后端路由；
- 重试；
- 持久队列；
- 协议转换。

---

## 7.5 Agent 对象如何映射为 OpenTelemetry 遥测

### 7.5.1 Agent 概念与 Span 的映射

| Agent 概念 | OpenTelemetry 表达 | 推荐操作 |
|---|---|---|
| 一次用户任务／图工作流 | 根 Span | `invoke_workflow` |
| 单个 Agent 执行 | Agent Span | `invoke_agent` |
| 规划或任务分解 | Plan Span | `plan` |
| 模型推理 | GenAI Client Span | `chat`、`generate_content` |
| 向量检索 | Retrieval Span | `retrieval` |
| 记忆查询 | Memory Span | `search_memory` |
| 记忆写入 | Memory Span | `create_memory`、`update_memory`、`upsert_memory` |
| 工具调用 | Tool Span | `execute_tool` |
| MCP 调用 | MCP Client／Server Span | MCP 方法名 |
| Agent 交接 | Span、Event 或 Link | Handoff |
| 权限决策 | Span Event 或领域 Span | Permission |
| 一轮 Agent Loop | Event 或轻量内部 Span | Iteration |
| 评测结果 | Event、Log 或独立 Eval 数据 | `gen_ai.evaluation.result` |

### 7.5.2 推荐父子关系

```text
Trace
└── Workflow Span
    ├── Agent Span
    │   ├── Plan Span
    │   │   └── LLM Span
    │   ├── Retrieval Span
    │   ├── Memory Span
    │   ├── Tool Span
    │   │   ├── HTTP / Database Span
    │   │   └── MCP Client Span
    │   └── Sub-Agent Span
    └── Evaluation
```

### 7.5.3 推荐标准属性

模型调用：

```text
gen_ai.operation.name
gen_ai.provider.name
gen_ai.request.model
gen_ai.response.model
gen_ai.response.id
gen_ai.conversation.id
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
error.type
```

Agent：

```text
gen_ai.operation.name = invoke_agent
gen_ai.agent.name
gen_ai.agent.version
gen_ai.conversation.id
```

Tool：

```text
gen_ai.operation.name = execute_tool
gen_ai.tool.name
gen_ai.tool.type
gen_ai.tool.call.id
error.type
```

### 7.5.4 推荐应用自定义属性

标准语义约定无法覆盖所有领域逻辑，可使用应用命名空间：

```text
app.workflow.run.id
app.workflow.name

app.agent.run.id
app.agent.iteration
app.agent.parent.name

app.retry.count
app.retry.reason
app.retry.backoff_ms

app.budget.max_iterations
app.budget.max_tokens
app.budget.max_duration_ms
app.budget.termination_reason

app.permission.policy
app.permission.decision
app.permission.risk_level

app.sandbox.type
app.sandbox.id

app.handoff.from
app.handoff.to
app.handoff.reason

app.eval.name
app.eval.score
app.eval.result
```

### 7.5.5 两个容易误用的字段

### 不要把运行 ID 当成稳定 Agent ID

`gen_ai.agent.id` 更适合表达托管平台中的稳定 Agent 资源标识。一次执行产生的随机 UUID 应写入：

```text
app.agent.run.id
```

### 不要伪造 Conversation ID

只有系统本身确实有会话或线程标识时，才设置：

```text
gen_ai.conversation.id
```

不应使用：

- Trace ID；
- 随机 UUID；
- Prompt 哈希；
- 请求 ID；

作为虚假的 Conversation ID。

---

## 7.6 四层 Agent 观测模型

四层不是互斥选项，而是从不同位置观察同一条执行链。

```mermaid
flowchart TD
    A["应用领域层<br/>预算、权限、循环、审批、业务决策"]
    B["Agent 框架层<br/>Workflow、Agent、Node、Tool、Retriever"]
    C["SDK 客户端层<br/>OpenAI、Anthropic、向量库客户端"]
    D["协议传输层<br/>HTTP、gRPC、MCP、数据库协议"]
    E["外部服务"]

    A --> B
    B --> C
    C --> D
    D --> E
```

---

### 7.6.1 第一层：Agent／框架语义观测层

### 观察对象

这一层理解 Agent 运行语义：

```text
Workflow
Agent
Graph Node
Chain
Tool
Retriever
Handoff
Guardrail
Memory
Human-in-the-loop
任务状态
```

### 常见机制

- Callback；
- Trace Processor；
- Event Bus；
- Dispatcher；
- Listener；
- Middleware；
- Tracer Interface；
- 框架内部 Native OTel。

### 能获得的数据

- Agent 名称和版本；
- Workflow 名称；
- Graph Node；
- Agent 父子关系；
- Tool Name；
- Retriever Query；
- Agent Handoff；
- 任务成功、失败、取消；
- 运行 ID、父运行 ID。

### 局限

如果普通 Python 逻辑没有触发框架事件，框架层仍然看不到。

例如：

```python
def risk_node(state):
    score = calculate_risk(state)
    if score > 0.8:
        return {"route": "manual_review"}
    return {"route": "auto"}
```

框架可能只知道节点开始和结束，不知道：

- 风险分数；
- 命中的规则；
- 选择该分支的原因。

这些属于应用领域层。

### 典型框架

| 框架 | 机制 |
|---|---|
| OpenAI Agents SDK | Tracing Processor |
| LangChain／LangGraph | Callback Manager + Handler |
| LlamaIndex | Dispatcher + EventHandler + SpanHandler |
| CrewAI | Event Bus + Listener |
| Haystack | Tracer Interface + Connector |
| AutoGen | 框架核心 Native OTel |
| Semantic Kernel | ActivitySource / Native OTel |

---

### 7.6.2 第二层：SDK／客户端库观测层

### 观察对象

这一层关注具体模型或基础能力 SDK：

```text
OpenAI SDK
Anthropic SDK
Azure OpenAI
LiteLLM
Embedding SDK
Vector DB Client
Database Client
```

### 常见机制

- Monkey Patch；
- Method Wrapper；
- Proxy；
- Stream Wrapper；
- Decorator；
- SDK Callback；
- SDK 原生 Interceptor；
- Native OTel。

### 能获得的数据

- 模型名称；
- 请求参数；
- Prompt 消息结构；
- Tool definitions；
- Response ID；
- Response Model；
- Finish Reason；
- Token Usage；
- 流式 Chunk；
- 首 Chunk 延迟；
- SDK 异常；
- SDK 自动重试。

### 局限

SDK 通常不知道：

- 为什么调用模型；
- 调用属于规划、执行还是审核；
- 当前是哪一个 Agent；
- 为什么选择这个工具；
- 当前 Agent Loop 是第几轮。

### 典型组件

| 组件 | 机制 |
|---|---|
| OpenAI Python SDK | Monkey Patch + Wrapper + Stream Proxy |
| PydanticAI Model | InstrumentedModel |
| LiteLLM SDK | Input / Success / Failure Callback |
| ChromaDB 内部 API | `@trace_method` Decorator |

---

### 7.6.3 第三层：协议／传输观测层

### 观察对象

```text
HTTP
HTTPS
gRPC
WebSocket
数据库协议
消息队列
MCP Streamable HTTP
MCP stdio
进程间通信
```

### 常见机制

- ASGI／WSGI Middleware；
- HTTP Client Wrapper；
- gRPC Client／Server Interceptor；
- 数据库驱动 Instrumentation；
- Producer／Consumer Instrumentation；
- Service Mesh；
- eBPF；
- W3C Trace Context Inject／Extract。

### 能获得的数据

- HTTP method；
- URL 或 route；
- Status Code；
- 网络耗时；
- 请求响应大小；
- Server Address；
- DNS、连接、TLS；
- gRPC method；
- 网络重试；
- 网络异常。

### 局限

协议层看到：

```text
POST /v1/responses
POST /collections/query
POST /mcp
```

但不知道：

```text
这是规划还是审核
哪个 Agent 发起
工具语义是什么
这是长期记忆还是普通检索
```

### 典型组合

```text
chat gpt-*
└── HTTP POST /v1/responses
```

上层 Span 解释模型语义，HTTP Span 解释网络传输。

---

### 7.6.4 第四层：应用／领域逻辑观测层

### 观察对象

这一层负责企业和自研 Runtime 的专有逻辑：

```text
Loop 轮次
终止原因
预算
权限
审批
Agent 路由
状态迁移
记忆晋升
沙箱生命周期
质量门禁
测试结果
业务分支
```

### 常见机制

- Manual Span；
- Span Event；
- Structured Log；
- Custom Metric；
- Lifecycle Hook；
- Domain Event；
- Middleware；
- 自研 Tracing Adapter。

### 典型手工 Span

```python
with tracer.start_as_current_span(
    "permission_check",
    attributes={
        "app.permission.policy": "workspace_write",
        "app.permission.risk_level": "high",
    },
):
    decision = permission_engine.evaluate(request)
```

### 典型 Event

```python
span.add_event(
    "app.agent.retry",
    {
        "app.retry.attempt": 3,
        "app.retry.reason": "tool_timeout",
        "app.retry.backoff_ms": 1000,
    },
)
```

### 为什么不可缺少

前三层无法自动回答：

- 为什么选择 Reviewer Agent；
- 为什么进行了第四次循环；
- 为什么拒绝写文件；
- 为什么保存为长期记忆；
- 为什么触发 Compaction；
- 为什么预算耗尽；
- 为什么进入人工审批。

---

### 7.6.5 四层回答的问题对照

| 问题 | 主要观测层 |
|---|---|
| 哪个 Agent 失败 | 框架层 |
| Agent 调用了哪个工具 | 框架层 |
| 模型消耗多少 Token | SDK 层 |
| 首 Token 为什么慢 | SDK 层 + 协议层 |
| DNS、TLS 或网络哪里慢 | 协议层 |
| 为什么选择某个 Agent | 应用层 |
| 为什么进入第 N 轮循环 | 应用层 |
| 为什么拒绝文件写入 | 应用层 |
| Chroma 内部哪个操作慢 | Chroma 内部层 |
| HTTP 200 但任务为何失败 | 框架层或应用层 |
| Tool 在业务上是否达成目标 | 框架层 + 应用层 |

---

## 7.7 常见 Hook 与 Instrumentation 机制

“Hook”是一个统称，具体实现差异很大。

| 机制 | 原理 | 是否修改业务源码 | 稳定性 | 典型应用 |
|---|---|---:|---:|---|
| Callback | 框架主动调用观察者 | 通常不需要 | 高 | LangChain |
| Processor | 接收框架 Trace/Span 生命周期 | 通常只需注册 | 高 | OpenAI Agents |
| Event Bus | 发布领域事件，监听器订阅 | 通常只需初始化 | 高 | CrewAI |
| Dispatcher | 分派类型化 Event 和 Span | 需注册 Handler | 高 | LlamaIndex |
| Middleware | 包裹 Agent、模型、工具调用 | 需要配置 | 中高 | LangChain Middleware |
| Interceptor | 拦截协议请求 | 通常自动 | 中高 | gRPC |
| Monkey Patch | 运行时替换方法 | 不修改业务源码 | 中低 | OpenAI SDK |
| Wrapper／Proxy | 代理 SDK、模型或流 | 需初始化或自动注入 | 中高 | Streaming Wrapper |
| Decorator | 在定义处包裹函数 | 框架或业务源码需加 | 高 | ChromaDB |
| Native OTel | 框架内部直接调用 OTel API | 使用者只需配置 | 最高 | AutoGen 等 |
| Manual Span | 应用主动创建 Span | 需要修改业务代码 | 最高 | 自研领域逻辑 |

### 7.7.1 稳定性优先级

通常推荐：

```text
Native OpenTelemetry
    >
公开 Processor / Callback / Event Bus
    >
Middleware / Wrapper
    >
Monkey Patch
    >
仅通过协议推断
```

原因：

- Native OTel 最了解内部语义；
- Callback、Processor 是公开契约；
- Monkey Patch 依赖方法路径、签名和返回类型；
- 协议层很难恢复 Agent 语义。

### 7.7.2 为什么 Monkey Patch 仍然重要

Monkey Patch 的价值主要在于：

- 不修改业务代码；
- 可以通过统一启动命令自动加载；
- 适合大量第三方 SDK；
- 能覆盖同步、异步和流式接口；
- 能快速补齐没有原生 Hook 的客户端库。

它更适合稳定的 SDK 边界，不适合自研 Agent Runtime 的核心领域逻辑。

---

## 7.8 Python 零侵入探针的实现原理

Python 的自动埋点与 Java Agent 的字节码增强不同，主要依靠：

```text
启动注入
    +
sitecustomize
    +
Entry Point 自动发现
    +
Instrumentation
    +
Monkey Patch / Callback 注册
```

### 7.8.1 启动方式

典型命令：

```bash
opentelemetry-instrument python app.py
```

它不是在任意时间附着到已运行进程，而是在启动目标 Python 进程之前：

1. 读取 OTel 配置；
2. 调整 `PYTHONPATH`；
3. 注入自动埋点目录；
4. 使用 `exec` 启动目标应用。

### 7.8.2 `sitecustomize` 自动执行

Python 启动时会尝试加载 `sitecustomize.py`。OpenTelemetry 的自动注入目录中包含类似逻辑：

```python
from opentelemetry.instrumentation.auto_instrumentation import initialize

initialize()
```

因此 Instrumentation 可以在业务模块正式执行前完成加载。

### 7.8.3 Entry Point 自动发现

Instrumentation 包会声明 Entry Point，例如：

```toml
[project.entry-points.opentelemetry_instrumentor]
openai = "opentelemetry.instrumentation.genai.openai:OpenAIInstrumentor"
```

自动加载器会扫描：

```text
opentelemetry_instrumentor
```

并检查：

- Instrumentation 是否安装；
- 目标框架是否安装；
- 依赖版本是否兼容；
- 是否被禁用；
- 是否存在冲突。

通过后调用：

```text
instrument()
```

### 7.8.4 运行时注入

不同 Instrumentor 采用不同方法：

```text
OpenAI SDK
    → wrapt.wrap_function_wrapper
    → 包装 create / stream / embeddings

OpenAI Agents
    → add_trace_processor
    → 注册 TracingProcessor

LangChain / LangGraph
    → 包装 CallbackManager 构造器
    → 自动插入 OTel CallbackHandler

ChromaDB
    → 框架源码中的 @trace_method
```

### 7.8.5 父子 Span 如何自动形成

OpenTelemetry Python 通常基于 `ContextVar` 保存当前上下文。

同一同步或标准异步调用链中：

```text
当前 Span
    ↓
创建子 Span
    ↓
自动继承父 Context
```

跨线程池、子进程或网络边界时，需要显式传播。

### 7.8.6 流式调用为什么更复杂

普通方法可以在返回时结束 Span，但流式方法返回时数据尚未消费完成。

因此 Instrumentation 需要返回 Stream Proxy：

```text
原始 Stream
    ↓
OpenTelemetry Stream Wrapper
    ↓
业务代码消费
```

代理对象在消费过程中记录：

- 第一个 Chunk 时间；
- Chunk 间隔；
- 最终 Usage；
- Finish Reason；
- 取消；
- 异常；
- 正常关闭。

如果 Stream 未被完整消费或未关闭，可能出现：

- Span 长时间不结束；
- Token 数据不完整；
- 首 Chunk 有记录但最终 Usage 缺失。

---

## 7.9 LangChain 与 LangGraph 的接入机制

### 7.9.1 核心机制

LangChain 和 LangGraph 的主要观测机制是：

> **Callback Manager + Callback Handler**

运行过程中会产生：

```text
on_chain_start
on_chain_end
on_chain_error

on_chat_model_start
on_llm_new_token
on_llm_end
on_llm_error

on_tool_start
on_tool_end
on_tool_error

on_retriever_start
on_retriever_end
on_retriever_error
```

### 7.9.2 OpenTelemetry 如何自动插入 Handler

OpenTelemetry LangChain Instrumentation 采用混合方式：

1. Monkey Patch `BaseCallbackManager.__init__`；
2. 在 Callback Manager 创建时自动加入 `OpenTelemetryLangChainCallbackHandler`；
3. 实际运行数据通过框架 Callback 获得；
4. Handler 把事件转化为 OpenTelemetry Span。

```text
启动阶段
Monkey Patch CallbackManager.__init__
        ↓
自动插入 OTel Handler

运行阶段
LangGraph / LangChain Callback
        ↓
OTel Handler
        ↓
OpenTelemetry API
```

因此，LangGraph 不是单纯依赖 Monkey Patch：

> Monkey Patch 只负责自动接入，真正的语义数据来自框架 Callback。

### 7.9.3 事件映射

| LangChain／LangGraph Callback | OpenTelemetry 表达 |
|---|---|
| `on_chain_start` | `invoke_workflow` 或 `invoke_agent` |
| `on_chat_model_start` | 模型调用 Span |
| `on_tool_start` | `execute_tool` |
| `on_retriever_start` | `retrieval` |
| `*_end` | 补充结果并结束 Span |
| `*_error` | 记录异常并结束 Span |

### 7.9.4 父子关系恢复

Callback 通常携带：

```text
run_id
parent_run_id
```

Instrumentation 内部维护：

```text
run_id → Invocation / Span / Context
```

从而建立：

```text
Workflow
└── Agent
    ├── Model
    ├── Tool
    └── Retriever
```

### 7.9.5 LangGraph 自动观测的边界

自动 Callback 可以看到：

- Graph 或 Node 开始、结束；
- Node 内部模型调用；
- Tool 调用；
- Retriever 调用；
- 异常。

通常看不到：

- 普通 Python 函数的内部阶段；
- `if` 分支选择理由；
- 状态字段变化原因；
- 循环终止规则；
- 自定义风险计算；
- 权限命中规则。

这些需要：

- LangChain Middleware；
- 自定义 Callback；
- 手工 Span；
- 应用领域 Event。

### 7.9.6 Middleware 的作用

LangChain 可通过类似以下 Hook 扩展：

```text
before_agent
before_model
after_model
after_agent
wrap_model_call
wrap_tool_call
```

适合观测：

- 重试；
- 权限；
- Token 预算；
- 动态模型路由；
- Tool Guard；
- 状态变更；
- 领域指标。

---

### 7.9.7 Traceloop／OpenLLMetry `LangchainInstrumentor` 详解

本节专门解释 Traceloop 开源项目 OpenLLMetry 中的：

```python
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
```

它是 LangChain／LangGraph 生态中使用较多的一套第三方 OpenTelemetry Instrumentation。其核心价值是：

- 不修改 LangChain 业务代码；
- 自动向 LangChain Callback Manager 注入 Callback Handler；
- 把 Chain、LLM、Tool、Retriever 和 LangGraph 运行转换为 OTel Span；
- 记录模型、Token、输入输出、工具参数、错误和延迟等数据；
- 使用 OpenTelemetry API、SDK 和 OTLP，可输出到 Traceloop，也可输出到其他 OTel 后端。

不过，必须先明确：

> `opentelemetry-instrumentation-langchain` 是 Traceloop／OpenLLMetry 维护的 Instrumentation，不是 OpenTelemetry 官方 `opentelemetry-instrumentation-genai-langchain`。

两者类名、导入路径和部分语义映射不同。

### 9.7.1 两个容易混淆的 LangChain Instrumentor

截至 2026-09-03，Python 生态中至少存在下面两套名称高度相似的实现。

| 对比项 | Traceloop／OpenLLMetry | OpenTelemetry 官方 GenAI Instrumentation |
|---|---|---|
| PyPI 包 | `opentelemetry-instrumentation-langchain` | `opentelemetry-instrumentation-genai-langchain` |
| 导入路径 | `opentelemetry.instrumentation.langchain` | `opentelemetry.instrumentation.genai.langchain` |
| 类名 | `LangchainInstrumentor` | `LangChainInstrumentor` |
| 维护方 | Traceloop／OpenLLMetry | OpenTelemetry 项目 |
| 主要接入点 | Callback Manager + LangGraph 专项 Patch | Callback Manager + OTel GenAI Telemetry Handler |
| 兼容字段 | OTel GenAI 字段 + OpenLLMetry／Traceloop 字段 | 以 OpenTelemetry GenAI 语义约定为主 |
| 默认内容策略 | 默认采集内容，可通过环境变量关闭 | 默认不采集内容，需显式开启 |
| LangGraph 专项增强 | Pregel、Command、Agent Factory、Middleware Hook | 以官方实现当前支持范围为准 |

准确识别方式：

```python
# Traceloop / OpenLLMetry
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

# OpenTelemetry 官方 GenAI Instrumentation
from opentelemetry.instrumentation.genai.langchain import LangChainInstrumentor
```

不要仅凭类名中的大小写判断，生产环境应检查：

```bash
pip show opentelemetry-instrumentation-langchain
pip show opentelemetry-instrumentation-genai-langchain
pip freeze | grep -E 'langchain|openllmetry|traceloop|opentelemetry'
```

两套包都声明了名为 `langchain` 的 `opentelemetry_instrumentor` Entry Point。如果同时安装并使用 `opentelemetry-instrument` 自动加载，可能同时向 Callback Manager 注入不同 Handler，造成重复 Span、重复 Token 或父子关系混乱。因此通常应二选一。

### 9.7.2 OpenLLMetry、Traceloop SDK 与 Instrumentor 的关系

三个概念需要分开：

```text
OpenLLMetry
  → Traceloop 维护的开源 GenAI Instrumentation 集合

opentelemetry-instrumentation-langchain
  → OpenLLMetry 中负责 LangChain / LangGraph 的单独 Instrumentation 包

traceloop-sdk
  → 帮助用户统一初始化 OTel Provider、Exporter、Resource 和各类 Instrumentation 的便捷 SDK
```

完整关系：

```mermaid
flowchart LR
    APP["LangChain / LangGraph 应用"]
    LCI["OpenLLMetry LangchainInstrumentor"]
    API["OpenTelemetry API"]
    SDK["OpenTelemetry SDK"]
    EXP["OTLP Exporter"]
    COL["Collector 或 OTLP 后端"]
    TL["Traceloop SDK<br/>可选便捷初始化层"]

    APP --> LCI
    LCI --> API
    API --> SDK
    SDK --> EXP
    EXP --> COL
    TL -.配置 Provider 与 Instrumentor.-> LCI
    TL -.配置 SDK 与 Exporter.-> SDK
```

因此可以有两种主要使用方式。

#### 方式一：使用 Traceloop SDK

```python
from traceloop.sdk import Traceloop

Traceloop.init(app_name="rag-agent-service")
```

这种方式适合：

- 希望快速启用多个 LLM、向量数据库和框架探针；
- 不想自己组装 OTel SDK；
- 使用 Traceloop 或兼容 OTLP 的后端；
- 接受 Traceloop SDK 对 Resource、Exporter 和 Instrumentation 生命周期的统一管理。

#### 方式二：直接使用单独 Instrumentor

```python
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

LangchainInstrumentor().instrument()
```

这种方式适合：

- 企业已经有统一 OpenTelemetry SDK；
- 需要自行配置 Sampler、Processor、Exporter 和 Collector；
- 不希望引入完整 `traceloop-sdk`；
- 希望只启用 LangChain／LangGraph Instrumentation。

推荐企业平台优先采用第二种方式，把 Provider 和 Exporter 所有权保留在宿主应用中。

### 9.7.3 当前版本与依赖基线

截至 2026-09-03，PyPI 上 `opentelemetry-instrumentation-langchain` 的最新版本为 `0.62.3`，要求 Python `>=3.10,<4`。

发布包声明的关键依赖包括：

```text
opentelemetry-api
opentelemetry-instrumentation
opentelemetry-semantic-conventions
opentelemetry-semantic-conventions-ai
```

测试依赖基线覆盖：

```text
langchain >= 1.0, < 2
langgraph >= 1.0, < 2
langchain-openai >= 1.0, < 2
openai >= 1.52.2, < 3
```

这不意味着任意组合都完全兼容。它仍然依赖：

- LangChain Callback 接口的参数和生命周期；
- LangGraph 内部模块路径；
- `wrapt` 包装语义；
- OpenTelemetry Context 实现；
- GenAI Semantic Conventions 版本；
- 不同模型 Provider 返回的 `LLMResult` 和 Usage 结构。

因此生产环境应锁定整组依赖，而不是只锁一个 Instrumentor：

```text
Python
OpenTelemetry API / SDK / Instrumentation
opentelemetry-semantic-conventions-ai
LangChain
LangGraph
模型 Provider 适配包
OpenLLMetry LangChain Instrumentation
```

### 9.7.4 三种启动模式

#### 模式 A：Traceloop SDK 自动初始化

```bash
pip install traceloop-sdk langchain langgraph
```

```python
from traceloop.sdk import Traceloop

Traceloop.init(
    app_name="customer-support-agent",
    resource_attributes={
        "deployment.environment.name": "production",
        "service.version": "2.3.0",
    },
)
```

此时 Traceloop SDK 负责：

- 创建或使用 OTel Provider；
- 配置 Exporter；
- 发现并启用支持的 Instrumentation；
- 设置服务资源属性；
- 组织 Workflow、Task、Agent、Tool 等 OpenLLMetry 语义。

#### 模式 B：显式调用 Instrumentor

```bash
pip install \
  opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp \
  opentelemetry-instrumentation-langchain
```

```python
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

instrumentor = LangchainInstrumentor(
    use_attributes=True,
    disable_trace_context_propagation=False,
)

instrumentor.instrument(
    tracer_provider=tracer_provider,
    meter_provider=meter_provider,
    logger_provider=logger_provider,
)
```

需要注意：

- `use_attributes` 是构造函数参数；
- `tracer_provider`、`meter_provider`、`logger_provider` 是 `instrument()` 参数；
- 未传 Provider 时，将使用进程中的全局 Provider；
- 同一进程不要重复调用多个 Instrumentor 初始化路径。

#### 模式 C：`opentelemetry-instrument` 零代码启动

该包注册了：

```toml
[project.entry-points."opentelemetry_instrumentor"]
langchain = "opentelemetry.instrumentation.langchain:LangchainInstrumentor"
```

因此安装包后，标准 Python 自动埋点加载器可以发现它：

```bash
export OTEL_SERVICE_NAME=rag-agent-service
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export TRACELOOP_TRACE_CONTENT=false

opentelemetry-instrument python app.py
```

这里所谓“零代码”是：

```text
opentelemetry-instrument
  → Python sitecustomize 初始化
  → 扫描 opentelemetry_instrumentor Entry Point
  → 创建 LangchainInstrumentor
  → 调用 instrument()
  → Patch LangChain / LangGraph
```

它仍然需要安装包、调整启动命令，并不表示完全没有运行时注入。

### 9.7.5 `instrument()` 初始化时做了什么

`LangchainInstrumentor` 继承自 OpenTelemetry `BaseInstrumentor`。当前实现的 `_instrument()` 大致执行以下步骤：

```mermaid
sequenceDiagram
    participant App as 应用启动代码
    participant Inst as LangchainInstrumentor
    participant OTel as OTel API / Provider
    participant LC as LangChain
    participant LG as LangGraph

    App->>Inst: instrument(...)
    Inst->>OTel: get_tracer(...)
    Inst->>OTel: get_meter(...)
    Inst->>OTel: 创建 duration / token Histogram
    opt use_attributes=false
        Inst->>OTel: get_logger(...)
    end
    Inst->>Inst: 创建 TraceloopCallbackHandler
    Inst->>LC: Patch BaseCallbackManager.__init__
    Inst->>LG: Patch Pregel / Command / Agent Factory / Middleware
    opt 开启模型上下文传播
        Inst->>LC: Patch 部分 OpenAI Model 方法
    end
```

核心对象只有一个 `TraceloopCallbackHandler` 实例，它持有：

```text
Tracer
Duration Histogram
Token Histogram
run_id → SpanHolder 映射
CallbackManager 引用
```

因此它不是“给每次调用临时创建一个 Handler”，而是在初始化阶段创建 Handler，再注入后续产生的 Callback Manager。

### 9.7.6 Callback Manager 自动注入机制

最核心的自动接入点是：

```text
langchain_core.callbacks.BaseCallbackManager.__init__
```

Instrumentation 使用 `wrapt.wrap_function_wrapper` 包装构造方法。简化逻辑如下：

```python
def wrapped_callback_manager_init(original, instance, args, kwargs):
    original(*args, **kwargs)

    for handler in instance.inheritable_handlers:
        if isinstance(handler, TraceloopCallbackHandler):
            break
    else:
        instance.add_handler(traceloop_handler, inherit=True)
```

关键点如下。

#### 先调用原始构造方法

必须先让 LangChain 初始化自身状态：

```text
handlers
inheritable_handlers
parent_run_id
tags
metadata
```

然后才能安全插入 Handler。

#### 检查重复 Handler

通过遍历 `inheritable_handlers` 避免相同类型 Handler 重复加入。

不过，这只能避免同类型 Traceloop Handler 重复，无法阻止另一套官方 OTel Handler 同时存在。

#### 设置 `inherit=True`

Handler 被加入可继承列表后，子 Chain、Tool、Retriever 和 Model 创建的 Callback Manager 能继续继承它。

因此无需在每个 Runnable 上显式写：

```python
config={"callbacks": [handler]}
```

#### `run_inline=True`

`TraceloopCallbackHandler` 设置 `run_inline=True`，目的是让回调尽量在调用路径内执行，减少异步调度造成的 Context 丢失和事件乱序。

### 9.7.7 运行期数据真正来自 Callback

Monkey Patch 只负责插入 Handler。真正的 Agent 运行数据来自 LangChain Callback 生命周期。

| Callback | 捕获时机 | 主要动作 |
|---|---|---|
| `on_chain_start` | Chain／Runnable／Node 开始 | 创建 Workflow 或 Task Span |
| `on_chain_end` | Chain 正常结束 | 写输出、状态并结束 Span |
| `on_chain_error` | Chain 异常 | 写 `error.type`、异常事件并结束 Span |
| `on_chat_model_start` | Chat Model 开始 | 创建 Chat Client Span，解析 Messages 和参数 |
| `on_llm_start` | Text Completion 开始 | 创建 Completion Client Span，解析 Prompt |
| `on_llm_end` | 模型调用结束 | 提取 Model、Response ID、Token、Finish Reason、输出 |
| `on_llm_error` | 模型异常 | 设置 ERROR、记录 Exception |
| `on_tool_start` | Tool 开始 | 创建 `execute_tool` Span，记录名称和参数 |
| `on_tool_end` | Tool 正常结束 | 写 Tool Result 并结束 Span |
| `on_tool_error` | Tool 异常 | 设置 ERROR 并结束 Span |
| `on_retriever_start` | Retriever 开始 | 创建检索 Span，记录 Query |
| `on_retriever_end` | Retriever 完成 | 记录文档与数量并结束 Span |
| `on_retriever_error` | Retriever 异常 | 设置 ERROR 并结束 Span |

当前实现没有覆盖任意普通 Python 函数。下面的内部逻辑仍不可见：

```python
def route(state):
    score = calculate_business_risk(state)
    return "manual_review" if score > 0.8 else "auto_execute"
```

要观察 `score`、分支原因和策略版本，仍需应用层 Span／Event。

### 9.7.8 Span 创建与映射规则

当前 OpenLLMetry 实现同时维护：

- Traceloop／OpenLLMetry 兼容属性；
- OpenTelemetry GenAI 属性；
- 部分 OpenLLMetry 自定义操作名。

实际映射比“所有 Chain 都变成 `invoke_workflow`”更复杂。

| LangChain／LangGraph 对象 | 当前 Span 名称示例 | SpanKind | `gen_ai.operation.name` |
|---|---|---:|---|
| 根 Chain | `rag_chain.workflow` | `INTERNAL` | 当前实现按 Agent 类操作处理为 `invoke_agent` |
| 嵌套 Chain／Node | `execute_task rewrite_query` | `INTERNAL` | 自定义 `execute_task` |
| Chat Model | `ChatOpenAI.chat` | `CLIENT` | `chat` |
| Text LLM | `OpenAI.completion` | `CLIENT` | `text_completion` |
| Tool | `execute_tool search_documents` | `INTERNAL` | `execute_tool` |
| Retriever | `vector_db_retrieve VectorStoreRetriever` | `CLIENT` | 自定义 `vector_db_retrieve` |
| LangGraph 流式图调用 | `invoke_agent support_graph` | 默认内部 Span | `invoke_agent` |
| LangGraph Command 路由 | `goto reviewer` | `INTERNAL` | 自定义 `goto` |
| Agent Factory | `create_agent react_agent` | `INTERNAL` | `create_agent` |
| AgentMiddleware Hook | `execute_task Guardrail.before_model` | `INTERNAL` | 自定义 `execute_task` |

这意味着：

> OpenLLMetry 当前实现正在向 OTel GenAI Semantic Conventions 对齐，但并非所有对象都与官方最新规范一一等价。

尤其需要注意以下三点。

#### Workflow 的映射

根 Chain 会被标记为 OpenLLMetry `workflow` 类型，但当前实现把 Workflow／Agent 一并写为：

```text
gen_ai.operation.name = invoke_agent
```

如果平台需要严格区分：

```text
invoke_workflow
invoke_agent
```

应在内部 SemConv Mapper 或 Collector 中做规范化，不能只看 `traceloop.span.kind` 或 Span 名称推断。

#### Task 的映射

`execute_task`、`gen_ai.task.*` 属于 OpenLLMetry 扩展语义，不应假设所有 OTel 后端都原生理解。

#### Retriever 的映射

Retriever 当前使用：

```text
vector_db_retrieve
```

而不是直接使用本文前面描述的标准化 `retrieval` 模型。因此跨不同 Instrumentation 聚合检索次数时，需要统一操作词典。

### 9.7.9 `run_id`、`parent_run_id` 与父子 Span 恢复

LangChain Callback 会提供：

```text
run_id
parent_run_id
```

Handler 内部维护：

```python
self.spans: dict[UUID, SpanHolder]
```

`SpanHolder` 主要保存：

```text
span
context token
显式 Context
children run_id 列表
workflow_name
entity_name
entity_path
association properties token
start_time
request_model
```

创建子 Span 时，优先使用：

```python
context=set_span_in_context(parent_span)
```

而不是只依赖“当前 Span”。这有两个优点：

- 即使 Callback 在复杂调用路径中触发，也能按 `parent_run_id` 找到预期父节点；
- 能恢复 LangChain 自身的运行树，而不是仅恢复 Python 栈上的临时调用关系。

简化过程：

```text
on_chain_start(run=A, parent=None)
  → spans[A] = Workflow Span

on_retriever_start(run=B, parent=A)
  → context = set_span_in_context(spans[A].span)
  → spans[B] = Retrieval Span
  → spans[A].children += B

on_retriever_end(run=B)
  → end spans[B]
  → 删除 B
```

父 Span 结束时，Handler 还会尝试结束并清理尚未结束的子 Span，防止：

- Span 泄漏；
- 内存中的 `run_id` 映射无限增长；
- 异常路径留下永久未结束 Span。

### 9.7.10 同步与异步 Context 管理

在同步调用中，典型过程是：

```text
start_span
  → attach(set_span_in_context(span))
  → 执行业务
  → detach(token)
  → span.end()
```

LangGraph 和 LangChain 的异步、流式、并发执行更复杂：

- Callback 可能在不同 `asyncio.Task` 中触发；
- Context token 可能在一个 Task 中创建、另一个 Task 中清理；
- 并行 Node 会同时维护多个 `run_id`；
- Generator／Async Generator 的真正结束时间晚于方法返回；
- ContextVars 在 Task 切换时复制，但 token 不能任意跨 Context 重置。

当前实现为此提供：

```text
_safe_attach_context
_safe_detach_context
SpanHolder.token
ContextVar 形式的当前 LangGraph Node
LangGraph Graph SpanHolder
first_child_pending 标记
```

`_safe_detach_context` 采用 Fail-safe 策略：异步边界中 token 无效或已在其他 Context 清理时，不让观测异常影响业务执行。

这能提高稳定性，但也意味着：

> “没有抛 Context 异常”不等于 Span 父子关系一定正确。

生产验收必须覆盖：

```text
同步 invoke
异步 ainvoke
stream
astream
并行节点
子图
ToolNode
异常中断
用户取消
Generator 提前关闭
```

### 9.7.11 LangGraph 的专项 Patch

Traceloop `LangchainInstrumentor` 不只注入 Callback Handler，还会检测 LangGraph 是否安装，并 Patch 多个内部边界。

#### `Pregel.stream` 与 `Pregel.astream`

这两个方法代表 LangGraph 编译图的流式执行入口。

Wrapper 会：

1. 获取 Graph 名称；
2. 创建 `invoke_agent {graph_name}` Span；
3. 写入 `gen_ai.provider.name=langgraph`；
4. 写入 Agent 名称；
5. 从 `config.configurable.thread_id` 提取 Conversation ID；
6. 尝试提取 Graph Nodes 和 Edges；
7. 把 Graph Span 放入 Context；
8. 在 Generator／Async Generator 完成或异常后结束 Span。

因为它包裹的是 Generator 生命周期，所以不是在 `stream()` 返回迭代器时立即结束 Span，而是在迭代完成时结束。

#### `Command.__init__`

LangGraph 的 `Command(goto=...)` 可表达动态路由。Wrapper 会在能够同时识别源节点和目标节点时创建：

```text
goto reviewer
```

并记录类似：

```text
langgraph.command.source_node
langgraph.command.goto_node
langgraph.command.goto_nodes
```

这让 Trace 可以显示：

```text
planner
  └── goto reviewer
      └── reviewer
```

但创建对象并不总等于路由已经成功执行，因此该 Span 更准确地表达“路由命令生成”，而不是完整的节点切换耗时。

#### Agent Factory

Instrumentation 会包装：

```text
langgraph.prebuilt.chat_agent_executor.create_react_agent
langgraph.prebuilt.create_react_agent
langchain.agents.factory.create_agent
langchain.agents.create_agent
```

用于创建：

```text
create_agent {agent_name}
```

并尽量提取：

- Agent 名称；
- Provider；
- System Instructions；
- Tool Definitions；
- Tool 参数 Schema。

需要注意，这是 **Agent 创建／配置 Span**，不是一次 Agent 运行 Span。

#### AgentMiddleware Hook

当前实现包装：

```text
before_model
after_model
before_agent
after_agent

abefore_model
aafter_model
abefore_agent
aafter_agent
```

每个 Hook 形成：

```text
execute_task {MiddlewareClass}.{hook_name}
```

适合定位：

- Guardrail 耗时；
- Prompt 改写耗时；
- 动态模型路由；
- 权限检查；
- 预算控制；
- 前后处理异常。

不过，如果 Middleware 本身又创建手工 Span，需要治理重复嵌套。

### 9.7.12 LangGraph Graph Span 与 Callback Span 如何衔接

单纯包装 `Pregel.stream` 会产生 Graph 根 Span，而 Callback Handler 又会为第一个 Chain／Node 创建 Span。为了让两者正确嵌套，当前实现使用几个 Context Key：

```text
langgraph_flow
langgraph_graph_span
langgraph_first_child_pending
```

逻辑如下：

```mermaid
flowchart TD
    A["Pregel.stream / astream"] --> B["创建 Graph Span"]
    B --> C["把 Graph SpanHolder 写入 OTel Context"]
    C --> D["标记 first_child_pending = true"]
    D --> E["第一个 Callback on_chain_start"]
    E --> F["显式使用 Graph SpanHolder.context 作为父 Context"]
    F --> G["first_child_pending = false"]
    G --> H["后续 Span 按 run_id / parent_run_id 连接"]
```

这是 Traceloop 针对 LangGraph 图入口和 Callback 树之间“缺一层父 Context”问题增加的专项修复逻辑。

### 9.7.13 模型 Provider 识别

`on_chat_model_start`／`on_llm_start` 进入时，Instrumentation 会从 LangChain 序列化对象的类名中检测 Provider，例如：

```text
ChatOpenAI     → openai
ChatAnthropic  → anthropic
ChatBedrock    → aws.bedrock
其他类         → 由 vendor detection 规则判断
```

并写入：

```text
gen_ai.provider.name
```

请求阶段还会尝试提取：

```text
gen_ai.request.model
gen_ai.request.max_tokens
gen_ai.request.temperature
gen_ai.request.top_p
gen_ai.tool.definitions
```

响应阶段提取：

```text
gen_ai.response.model
gen_ai.response.id
gen_ai.response.finish_reasons
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
总 Token 与缓存 Token 扩展字段
```

不同 LangChain Provider 返回结构差异较大，Instrumentation 会从多个候选字段回退提取，例如：

```text
model_name
model_id
response_metadata
llm_output.token_usage
llm_output.usage
message.usage_metadata
```

因此 Token 缺失可能来自：

- Provider 没有返回 Usage；
- LangChain Adapter 没有把 Usage 传入 `LLMResult`；
- 流式调用未产生最终聚合结果；
- Provider 字段名发生变化；
- Instrumentation 版本不匹配。

### 9.7.14 模型 Span 去重与 Suppression

当同时启用：

```text
LangChain Instrumentation
OpenAI / Anthropic / Bedrock Provider Instrumentation
HTTP Client Instrumentation
```

可能出现：

```text
LangChain LLM Span
└── Provider SDK LLM Span
    └── HTTP Span
```

如果前两层都表示同一次逻辑模型推理，就会重复计算：

- LLM 调用次数；
- Token；
- 成本；
- 延迟；
- 错误率。

OpenLLMetry 的策略是：

1. LangChain Callback Handler 创建 LLM Span；
2. 在 Context 中设置 `SUPPRESS_LANGUAGE_MODEL_INSTRUMENTATION_KEY=True`；
3. 尽量抑制下游模型 Provider Instrumentation；
4. 仍可保留 HTTP Transport Span。

因此其默认所有权更接近：

```text
LangChain Callback Handler
  → 权威逻辑 LLM Span

Provider SDK Instrumentation
  → 被 Suppress

HTTPX / Requests Instrumentation
  → 可保留网络 Span
```

这与“由 OpenAI Instrumentation 负责权威 Chat Span”的另一种架构不同。两种方案都可以，但必须统一。

### 9.7.15 OpenAI 请求 Trace Context 注入

除 Suppression 外，当前实现还会包装部分 LangChain OpenAI 方法：

```text
langchain_community.llms.openai.BaseOpenAI
langchain_openai.llms.base.BaseOpenAI
langchain_openai.chat_models.base.BaseChatOpenAI
```

包装器从：

```text
run_manager.run_id
```

找到当前 LangChain LLM Span，然后：

```python
ctx = set_span_in_context(span_holder.span)
TraceContextTextMapPropagator().inject(extra_headers, context=ctx)
```

最终把：

```text
traceparent
tracestate
```

写入请求的 `extra_headers`。

其作用是让支持 W3C Trace Context 的远端模型网关、代理或企业自建模型服务继续当前 Trace。

典型链路：

```text
LangChain LLM Span
└── 企业 Model Gateway Server Span
    └── Provider 调用
```

对于不识别 `traceparent` 的公共模型 API，该 Header 通常不会自动产生远端 Span，但仍可能经过企业代理或网关使用。

可以通过：

```python
LangchainInstrumentor(
    disable_trace_context_propagation=True,
)
```

关闭这类模型请求头注入。

适合关闭的情况：

- 安全策略不允许向第三方发送追踪 Header；
- 网关会自行生成新的 Trace；
- 下游 SDK 对 `extra_headers` 不兼容；
- 已经有另一层标准 HTTP Propagator 负责传播；
- 需要排查 Header 注入导致的兼容问题。

### 9.7.16 当前 OpenAI 包装范围的边界

当前发布源码对下面的方法实施 Trace Header 注入：

```text
BaseOpenAI._generate
BaseOpenAI._agenerate
BaseOpenAI._stream
BaseOpenAI._astream
BaseChatOpenAI._generate
BaseChatOpenAI._agenerate
```

其中 Chat Model 的 `_stream`／`_astream` 包装在当前源码中未启用。因此不能假设所有 ChatOpenAI 流式路径都会由这层 Wrapper 注入 Header。

此外：

- 其他 Provider 不一定使用相同 Header 注入逻辑；
- HTTPX Instrumentation 仍可能在传输层自动注入 Context；
- 自定义 BaseChatModel 子类可能完全绕过已知包装点。

生产测试应直接检查出站请求是否包含预期 `traceparent`，而不是仅根据代码配置推断。

### 9.7.17 内容采集模式：Span Attribute 与 OTel Event

`LangchainInstrumentor` 当前支持两种内容表达模式。

#### 模式一：`use_attributes=True`，默认

```python
LangchainInstrumentor(
    use_attributes=True,
).instrument(...)
```

模型输入输出被序列化到 Span Attribute，例如：

```text
gen_ai.system_instructions
gen_ai.input.messages
gen_ai.output.messages
gen_ai.tool.definitions
gen_ai.tool.call.arguments
gen_ai.tool.call.result
```

结构化消息通常被编码为 JSON 字符串：

```json
[
  {
    "role": "user",
    "parts": [
      {
        "type": "text",
        "content": "查找订单 1001"
      }
    ]
  }
]
```

优点：

- 大多数 Trace 后端容易展示；
- Span 与内容天然同生命周期；
- 查询简单。

缺点：

- Span 体积大；
- Attribute 长度限制可能截断；
- 敏感内容随 Trace 扩散；
- Sampling 会同时丢失内容；
- 多模态 Base64 可能造成严重负载。

#### 模式二：`use_attributes=False`

```python
LangchainInstrumentor(
    use_attributes=False,
).instrument(
    tracer_provider=tracer_provider,
    meter_provider=meter_provider,
    logger_provider=logger_provider,
)
```

Instrumentation 使用 Logs API 发出带 `event_name` 的 LogRecord，例如：

```text
gen_ai.user.message
gen_ai.system.message
gen_ai.assistant.message
gen_ai.tool.message
gen_ai.choice
```

事件 Body 中保存结构化 Message 或 Choice，Attribute 中设置：

```text
gen_ai.provider.name = langchain
```

这一模式需要有效的 LoggerProvider 和 Logs Exporter，否则内容事件不会进入后端。

需要注意：

> 这些 EventName 反映 OpenLLMetry 当前兼容实现，不应直接等同于本文 GenAI Semantic Conventions 章节中的最新标准推理详情事件。

跨 Instrumentation 查询时，应通过 Schema Version 或 Mapper 统一。

### 9.7.18 `TRACELOOP_TRACE_CONTENT` 隐私开关

PyPI 包默认采集 Prompt、Completion 和 Embedding 等内容。可以全局关闭：

```bash
export TRACELOOP_TRACE_CONTENT=false
```

代码中的判断逻辑等价于：

```text
环境变量未设置
  → 默认 true

TRACELOOP_TRACE_CONTENT=false
  → 不发送正文

Context 中存在显式 override
  → 可以按局部调用重新开启
```

关闭后，Instrumentation 仍可记录：

- Span 名称；
- Provider；
- Model；
- Token；
- Duration；
- Finish Reason；
- 状态和异常；
- 部分不含正文的 Tool Call 元数据。

会被删除或不写入的内容包括：

- Prompt 文本；
- Completion 文本；
- Tool Arguments；
- Tool Result；
- Retriever Query 和 Document 正文；
- Chain 输入输出；
- Message Event 的 Content。

生产环境建议默认：

```bash
TRACELOOP_TRACE_CONTENT=false
```

再通过受控的调试策略，对低比例、低敏感度、特定租户或特定 Trace 临时开启，而不是全局永久开启。

### 9.7.19 多模态内容风险

当前内容转换逻辑可以把 LangChain Message 中的多模态 Part 转成：

```text
text
uri + modality=image
blob + modality=image + mime_type + content
```

如果输入包含 Base64 图片，`blob.content` 可能直接进入 Span Attribute 或 Event Body。

因此必须限制：

- 单条内容最大长度；
- 单 Span 最大内容总量；
- 是否允许 Base64；
- 是否只记录媒体类型、大小和 Artifact 引用；
- Collector 的 Redaction 与 Truncation；
- 数据保留期和访问控制。

更稳妥的生产模型是：

```text
图片 / 音频 / PDF 原文
  → Artifact Store

Span / Event
  → media_type
  → size_bytes
  → content_hash
  → artifact_id
  → 不透明 storage_ref
```

不要依赖 `TRACELOOP_TRACE_CONTENT=false` 作为唯一安全边界，还应在应用侧和 Collector 侧实施双层防护。

### 9.7.20 Chain、Tool 与 Retriever 输入输出

除模型消息外，Instrumentation 还可能采集：

#### Chain／Task

```text
inputs
tags
metadata
kwargs
outputs
```

#### Tool

```text
input_str
inputs
kwargs
output
```

并映射到：

```text
traceloop.entity.input
traceloop.entity.output
gen_ai.task.input
gen_ai.task.output
gen_ai.tool.call.arguments
gen_ai.tool.call.result
```

#### Retriever

```text
query
tags
metadata
返回 documents
page_content
document metadata
document count
```

这里的隐私风险有时比 Prompt 更高，因为 Tool 或 Retriever 可能返回：

- 数据库完整记录；
- 个人身份信息；
- 文件正文；
- Access Token；
- 内部 URL；
- 源代码；
- 搜索结果和权限数据。

生产系统应按数据类型治理，而不是只按“Prompt／Completion”治理。

### 9.7.21 Metadata 与 Association Properties

LangChain Callback 的 `metadata` 会被清洗为 OTel 可接受的标量或标量数组，然后以：

```text
{metadata_key_prefix}.{key}
```

写入 Span。

构造函数默认：

```python
metadata_key_prefix=SpanAttributes.TRACELOOP_ASSOCIATION_PROPERTIES
```

可以改成企业自己的命名空间：

```python
LangchainInstrumentor(
    metadata_key_prefix="app.association",
)
```

例如 LangChain 调用：

```python
graph.invoke(
    input_state,
    config={
        "metadata": {
            "tenant.id_hash": "t-42",
            "experiment.name": "router-v2",
            "prompt.version": "2026-09-03",
        }
    },
)
```

可映射为：

```text
app.association.tenant.id_hash
app.association.experiment.name
app.association.prompt.version
```

不要把以下字段直接放入普通 Metadata：

```text
原始 user_id
email
session transcript
完整文件路径
动态 request body
大对象
任意嵌套业务对象
```

Metric 不应复制高基数 Association Properties。

### 9.7.22 Conversation ID 提取

当前实现会尝试从 LangGraph Runnable Config 中读取：

```python
config["configurable"]["thread_id"]
```

并映射到：

```text
gen_ai.conversation.id
```

示例：

```python
graph.invoke(
    {"messages": [...]},
    config={
        "configurable": {
            "thread_id": "conversation-123"
        },
        "run_name": "support_graph",
    },
)
```

注意边界：

- `thread_id` 应是应用真实存在的会话标识；
- 不要把 Trace ID 复制成 Conversation ID；
- 不要把每次执行的随机 Run ID 当成 Conversation ID；
- 如包含个人身份，应使用不可逆或受控的稳定标识；
- Conversation ID 不应成为 Metric Label。

### 9.7.23 Agent ID 的语义兼容问题

当前 OpenLLMetry 源码会把 LangChain `run_id` 写入：

```text
gen_ai.agent.id
```

但本文前面的规范分析已经指出：

```text
gen_ai.agent.id
  → 更适合表示稳定 Agent 资源 ID

一次执行的 run_id
  → 更适合 app.agent.run.id
```

因此企业统一模型中建议增加规范化：

```text
原始字段：gen_ai.agent.id = <run_id>

内部标准：
  app.agent.run.id = <run_id>
  gen_ai.agent.id = <稳定 Agent Definition ID，可用时>
```

如果无法修改 Instrumentation，可以在 Collector 或入库 Mapper 中：

1. 检测 Instrumentation Scope 为 OpenLLMetry LangChain；
2. 把原 `gen_ai.agent.id` 复制到 `app.agent.run.id`；
3. 从 Agent Registry 补充稳定 Agent ID；
4. 避免按错误的 Agent ID 做资源级聚合。

这类兼容处理应附带：

```text
telemetry.schema.version
instrumentation.name
instrumentation.version
mapper.version
```

### 9.7.24 Metrics 生成机制

初始化时，Instrumentor 会创建两个 Histogram：

```text
LLM operation duration Histogram
LLM token usage Histogram
```

在 `on_llm_end` 阶段记录：

#### Duration

```text
value = 当前时间 - SpanHolder.start_time

attributes:
  gen_ai.provider.name
  gen_ai.response.model
```

#### Token Usage

分别记录：

```text
input token
output token
```

常用属性：

```text
gen_ai.provider.name
gen_ai.token.type
gen_ai.response.model
```

Token 来源会从多个结构回退：

```text
prompt_tokens
input_token_count
input_tokens

completion_tokens
generated_token_count
output_tokens

total_tokens
usage_metadata
```

指标层的注意事项：

- 不要再由 Span-to-Metrics Pipeline 重复统计同一 Token；
- Model 名称可能存在请求名与响应名差异；
- `unknown` 需要单独监控比例；
- 缓存 Token 是否包含在 Input Token 中要统一口径；
- 流式响应没有最终 Usage 时指标可能缺失；
- 不要把 `run_id`、`thread_id`、Prompt 名称动态值作为 Metric 属性。

### 9.7.25 该 Instrumentor 不提供完整流式 Token 时间线

当前 Callback Handler 没有实现 `on_llm_new_token` 的逐 Token 处理。主要 LLM Duration 和 Token 指标在 `on_llm_end` 时完成。

因此仅使用该 Instrumentor 时，通常不能直接获得：

```text
Time To First Token
每个 Chunk 间隔
每秒输出 Token
客户端取消发生在哪个 Chunk
流式响应中间状态
```

可选方案：

1. 增加自定义 LangChain Callback，专门记录首 Token 时间；
2. 使用 Provider SDK Instrumentation，但必须重新设计 Suppression 和 Span 所有权；
3. 在模型网关层记录流式 Chunk；
4. 只生成低基数 TTFT Metric，不为每个 Token 创建 Event；
5. 对 Stream 关闭、取消和异常单独记录 Event。

不要为每个 Token 创建 Span 或 Log Event，这会造成极大的遥测放大。

### 9.7.26 Error 与 Fail-open 机制

Chain、LLM、Tool、Agent、Retriever 的异常最终进入统一错误处理：

```text
gen_ai.task.status = failure
error.type = 异常类名
Span Status = ERROR
Span.record_exception(error)
结束并清理 Span
```

同时，Callback 方法使用 `dont_throw` 包装。

其设计原则是：

> Instrumentation 失败不能让 Agent 业务失败。

当序列化、属性转换或 Context 操作异常时：

- 记录 Debug 日志；
- 可调用用户传入的 `exception_logger`；
- 不把 Instrumentation 异常重新抛给 LangChain；
- 业务继续执行。

可配置：

```python
def report_instrumentation_error(error: Exception) -> None:
    logger.warning(
        "langchain instrumentation failed",
        exc_info=error,
    )

LangchainInstrumentor(
    exception_logger=report_instrumentation_error,
).instrument(...)
```

这里需要额外监控：

```text
Instrumentation 错误次数
序列化失败次数
找不到 run_id 的次数
Context attach / detach 失败次数
Span 结束时找不到 Holder 的次数
```

否则 Fail-open 会让业务保持可用，但可观测数据可能静默缺失。

### 9.7.27 序列化机制与风险

Chain、Tool、Retriever 的输入输出会经过 JSON Encoder。它尝试处理：

```text
dict
Dataclass
Pydantic BaseModel
带 to_json() 的对象
datetime
其他对象的 str()
```

并会尝试移除 Callback 对象，避免递归或序列化无关运行时状态。

风险包括：

- `dataclasses.asdict()` 会递归深拷贝字段；
- 对象内部锁、Socket、文件句柄可能无法复制；
- `str()` 可能输出 Secret 或大对象；
- 深层嵌套对象会放大 CPU 和内存；
- 自定义对象的 `to_json()` 可能有副作用；
- Tool Result 可能不可序列化。

因此生产系统应：

```text
优先关闭正文采集
限制属性长度
限制嵌套深度
使用安全序列化白名单
Collector 二次截断
监控 Instrumentation Error
```

### 9.7.28 直接接入企业 OpenTelemetry SDK

下面示例展示 Trace、Metric 和 Logs Provider 统一配置。代码重点是所有 Instrumentation 共享同一组 Provider，而不是让 Traceloop 单独创建第二套 SDK。

```python
from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_observability() -> None:
    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://otel-collector:4317",
    )

    resource = Resource.create(
        {
            "service.name": "rag-agent-service",
            "service.version": "2.3.0",
            "deployment.environment.name": "production",
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                insecure=True,
            )
        )
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=endpoint,
            insecure=True,
        )
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=endpoint,
                insecure=True,
            )
        )
    )

    LangchainInstrumentor(
        use_attributes=False,
        disable_trace_context_propagation=False,
        metadata_key_prefix="app.association",
    ).instrument(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
    )
```

生产中还应补充：

- TLS；
- OTLP 认证 Header；
- Sampler；
- SDK Limits；
- Shutdown／ForceFlush；
- Resource Detector；
- Collector Redaction；
- 指标 View；
- Exporter 重试和队列。

### 9.7.29 使用 Traceloop SDK 的快速接入

```bash
pip install traceloop-sdk langchain langgraph langchain-openai
```

```python
from traceloop.sdk import Traceloop

Traceloop.init(
    app_name="rag-agent-service",
    resource_attributes={
        "service.version": "2.3.0",
        "deployment.environment.name": "development",
    },
    disable_batch=True,
)
```

本地调试时 `disable_batch=True` 可以让 Span 更快被导出，但生产环境通常应使用 Batch Processor。

如果输出到自建 Collector，应根据 Traceloop SDK 当前配置接口设置 OTLP Endpoint 与 Header，并确认：

- Endpoint 是 gRPC 还是 HTTP；
- 是否需要 `/v1/traces`；
- TLS 与 `insecure` 是否匹配；
- Trace、Metric、Log 是否都启用；
- 后端是否理解 OpenLLMetry 扩展字段。

### 9.7.30 LangGraph 示例与预期 Trace

```python
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from opentelemetry.instrumentation.langchain import LangchainInstrumentor


class State(TypedDict):
    question: str
    answer: str


LangchainInstrumentor(
    use_attributes=True,
).instrument()

model = ChatOpenAI(model="gpt-4.1-mini")


def answer_node(state: State) -> State:
    response = model.invoke(state["question"])
    return {
        **state,
        "answer": str(response.content),
    }


builder = StateGraph(State)
builder.add_node("answer", answer_node)
builder.add_edge(START, "answer")
builder.add_edge("answer", END)
graph = builder.compile()

graph.invoke(
    {
        "question": "OpenTelemetry 如何观测 LangGraph？",
        "answer": "",
    },
    config={
        "run_name": "otel_qa_graph",
        "configurable": {
            "thread_id": "conversation-001",
        },
        "metadata": {
            "prompt.version": "v3",
        },
    },
)
```

预期结构近似：

```text
invoke_agent otel_qa_graph              ← Pregel Graph Wrapper
└── otel_qa_graph.workflow              ← Callback 根 Chain
    └── execute_task answer             ← LangGraph Node / Chain
        └── ChatOpenAI.chat             ← LangChain LLM Span
            └── HTTP POST               ← 可选传输层 Span
```

实际层级取决于：

- LangGraph 版本；
- Callback 发出方式；
- 是否调用 `invoke`、`stream`、`ainvoke` 或 `astream`；
- 是否启用 HTTP／Provider Instrumentation；
- Suppression 是否生效；
- 后端是否隐藏内部 Span。

### 9.7.31 与 OpenAI Instrumentation 的组合方式

#### 组合 A：LangChain 拥有 LLM Span

```text
LangGraph / LangChain Span
└── LangChain LLM Span
    └── HTTP Client Span
```

配置原则：

- 启用 `LangchainInstrumentor`；
- 允许其 Suppress OpenAI LLM Instrumentation；
- 使用 LangChain Callback 提供 Token 和消息；
- HTTP Instrumentation 只表示传输。

优点：

- 与 LangChain `run_id` 树一致；
- Provider 统一；
- 不重复 Token。

缺点：

- Provider SDK 独有字段可能缺失；
- TTFT 和流式 Chunk 细节可能不足；
- Usage 依赖 LangChain 返回结构。

#### 组合 B：Provider SDK 拥有 LLM Span

```text
LangGraph / LangChain Task Span
└── OpenAI SDK Chat Span
    └── HTTP Client Span
```

配置原则：

- LangChain 层只创建 Workflow、Agent、Task、Tool、Retriever；
- 不让 LangChain Handler 创建权威 LLM Span，或过滤该 Span；
- OpenAI Instrumentation 负责模型、流式、Token；
- 通过当前 Context 自动作为 LangChain Task 的子 Span。

优点：

- Provider 数据更完整；
- 更容易观察 SDK 重试和流式；
- 语义由 Provider Instrumentation 统一。

缺点：

- 需要定制或过滤 OpenLLMetry 默认 LLM 行为；
- 可能失去 LangChain Callback 的模型运行语义；
- Parent Context 必须正确传播。

不能同时把两个 Span 都计为一次模型调用。

### 9.7.32 与官方 `LangChainInstrumentor` 的选择

| 选择条件 | 更适合 OpenLLMetry `LangchainInstrumentor` | 更适合官方 `LangChainInstrumentor` |
|---|---:|---:|
| 已使用 Traceloop SDK | 是 | 通常否 |
| 需要 OpenLLMetry Workflow／Task 字段 | 是 | 否 |
| 需要 LangGraph Pregel、Command、Middleware 专项 Span | 当前实现更丰富 | 以官方当前支持为准 |
| 要求严格贴合最新 OTel GenAI SemConv | 需要 Mapper | 更优先 |
| 默认不允许采集内容 | 必须显式关闭 | 官方默认策略更保守 |
| 已有企业统一 OTel SDK | 两者均可 | 两者均可 |
| 希望最少第三方扩展字段 | 否 | 是 |
| 已有 Traceloop Dashboard／数据模型 | 是 | 需要兼容验证 |

不要根据“哪个包名字更像官方”做选择，应根据：

```text
Span模型
SemConv版本
内容治理
LangGraph覆盖范围
后端兼容性
依赖稳定性
升级策略
```

进行验证。

### 9.7.33 OpenLLMetry 语义与官方 GenAI 语义的兼容层

建议在企业平台中维护显式 Mapper：

```text
OpenLLMetry 原始遥测
    ↓
Instrumentation Scope 识别
    ↓
Version-aware SemConv Mapper
    ↓
企业 Canonical GenAI Schema
    ↓
Trace / Metric / Eval 分析
```

示例映射：

| OpenLLMetry 当前值 | 企业统一值 |
|---|---|
| 根 `.workflow` + `invoke_agent` | `invoke_workflow` |
| `execute_task` | `app.agent.task.execute` 或内部 Task 类型 |
| `vector_db_retrieve` | `retrieval` |
| `gen_ai.agent.id=<run_id>` | `app.agent.run.id=<run_id>` |
| `traceloop.entity.input` | 受控 `app.operation.input.ref` |
| `traceloop.entity.output` | 受控 `app.operation.output.ref` |
| `gen_ai.user.message` Event | 统一 Message Content Schema |
| `gen_ai.choice` Event | 统一 Output Message／Inference Details Schema |

不要在 Collector 中无条件按字段名改写全部服务，应至少判断：

```text
otel.scope.name
otel.scope.version
service.name
telemetry.schema.version
```

### 9.7.34 自动接入失效的常见原因

#### Instrumentor 没有被加载

检查：

```bash
python -c "from opentelemetry.instrumentation.langchain import LangchainInstrumentor; print(LangchainInstrumentor)"
```

以及：

```bash
opentelemetry-bootstrap -a requirements | grep langchain
```

#### 初始化顺序错误

应尽量在构造 LangChain Graph、Model、Callback Manager 之前执行 Instrumentation：

```python
LangchainInstrumentor().instrument()

# 然后再创建模型、图和 Agent
```

#### 同时启用了两套 LangChain Instrumentation

检查安装包和 Entry Point，避免：

```text
opentelemetry-instrumentation-langchain
opentelemetry-instrumentation-genai-langchain
```

同时自动加载。

#### 全局 Provider 已被提前设置

OpenTelemetry 全局 Provider 通常只能设置一次。初始化顺序错误可能导致：

- Instrumentor 使用 No-op Provider；
- Traceloop SDK 无法替换已有 Provider；
- 数据发往错误 Exporter；
- Resource 缺失。

#### Callback 被显式覆盖

某些代码会创建新的 Callback Manager 或传入只包含局部 Handler 的配置。应检查 Traceloop Handler 是否仍在 `inheritable_handlers`。

#### LangGraph 内部路径变化

专项 Patch 依赖：

```text
langgraph.pregel.Pregel
langgraph.types.Command
langgraph.prebuilt...
langchain.agents.middleware.types.AgentMiddleware
```

版本变化可能使某些 Wrapper 不再生效，但 Callback 基础路径仍可能工作。

#### Sampling 丢弃根 Span

Head Sampling 可能让完整 Trace 不记录。先用 AlwaysOn 或 Console Exporter 验证，不要直接判断 Instrumentation 失效。

#### 内容关闭被误判为无遥测

`TRACELOOP_TRACE_CONTENT=false` 只应关闭内容，不应关闭 Span、Token 和延迟。后端看不到 Prompt 不代表探针失效。

### 9.7.35 重复 Span 排查

出现下面结构时：

```text
ChatOpenAI.chat
└── openai.chat
    └── POST /v1/chat/completions
```

先判断三层含义：

| Span | 是否应保留 |
|---|---|
| LangChain LLM Span | 作为逻辑模型调用时保留 |
| OpenAI SDK LLM Span | 如仅重复同一逻辑调用，应 Suppress 或过滤 |
| HTTP Client Span | 作为网络尝试通常保留 |

排查字段：

```text
otel.scope.name
otel.scope.version
gen_ai.operation.name
gen_ai.request.model
gen_ai.response.id
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
parent_span_id
```

治理方法：

1. 明确 Span 所有权；
2. 只启用一套 LangChain Instrumentation；
3. 验证 Suppression Key 是否跨异步边界传播；
4. 在 Collector 过滤非权威模型 Span；
5. Token 成本系统按权威 Scope 统计；
6. 用 `response.id + trace_id` 辅助识别重复调用，但不要把它做 Metric Label。

### 9.7.36 Trace 断链排查

典型断链：

```text
Graph Span
├── Node Span
└── HTTP Span 出现在 Trace 根部
```

排查：

```text
Callback run_id / parent_run_id 是否完整
当前 Span 是否正确 attach
异步 Task 是否复制 Context
线程池是否显式传 Context
Generator 是否在另一个 Task 中消费
Graph 第一子节点是否使用 Graph SpanHolder.context
HTTP Client 是否读取当前 Context
自定义 Runnable 是否丢弃 callbacks/config
```

建议构造最小矩阵：

```text
同步单节点
同步两节点
异步单节点
异步并行节点
stream
astream
ToolNode
子图
异常节点
取消
```

并断言每个 Span 的 `parent_span_id`，而不是只检查 Span 数量。

### 9.7.37 生产配置建议

推荐基线：

```text
1. 固定 LangChain、LangGraph、OpenLLMetry 和 OTel 版本
2. 同一进程只启用一套 LangChain Instrumentation
3. 由宿主应用拥有统一 OTel SDK
4. 默认 TRACELOOP_TRACE_CONTENT=false
5. 只在授权调试会话开启正文
6. 统一模型 Span 所有权
7. 使用 Collector 执行 Redaction、Limits 和 Tail Sampling
8. 对 OpenLLMetry 自定义字段增加版本化 Mapper
9. 单独监控 Instrumentation 自身错误
10. 为 sync / async / stream / astream 建立契约测试
```

建议 Resource：

```text
service.name
service.version
deployment.environment.name
service.instance.id
telemetry.sdk.language
telemetry.sdk.name
telemetry.sdk.version
```

建议额外应用字段：

```text
app.workflow.run.id
app.agent.run.id
app.prompt.version
app.tool.version
app.eval.dataset.version
app.security.content_capture_policy
```

### 9.7.38 接入验收清单

#### 初始化

- [ ] 只安装或启用一套 LangChain Instrumentation；
- [ ] Instrumentor 在 Graph／Model 构造前初始化；
- [ ] TracerProvider、MeterProvider、LoggerProvider 指向预期实例；
- [ ] `service.name` 正确；
- [ ] OTLP Endpoint、协议、TLS 和 Header 正确；
- [ ] Shutdown／ForceFlush 已实现。

#### Trace

- [ ] 根 Graph／Chain Span 存在；
- [ ] Node／Task 正确嵌套；
- [ ] Tool Span 正确；
- [ ] Retriever Span 正确；
- [ ] LLM Span 只有一个权威逻辑层；
- [ ] HTTP Span 位于 LLM／Tool／Retriever 下方；
- [ ] 异常 Span 设置 `error.type` 与 ERROR Status；
- [ ] `thread_id` 正确映射为 Conversation ID；
- [ ] 并行与异步父子关系正确。

#### Metrics

- [ ] LLM Duration 有数据；
- [ ] Input／Output Token 有数据；
- [ ] `unknown` Model 比例可监控；
- [ ] Token 未重复统计；
- [ ] Metric Attribute 无高基数 Run ID；
- [ ] 缓存 Token 口径清晰。

#### 内容与安全

- [ ] 生产默认关闭 Prompt／Completion 正文；
- [ ] Tool Arguments／Result 同样受控；
- [ ] Retriever Document 正文受控；
- [ ] Base64 多模态内容不会进入遥测；
- [ ] Collector 有脱敏和长度限制；
- [ ] 数据访问和保留策略已配置；
- [ ] 预签名 URL、Secret 和 Authorization Header 不进入遥测。

#### 兼容性

- [ ] `invoke_agent`／Workflow 映射已确认；
- [ ] `execute_task` 自定义语义已处理；
- [ ] `vector_db_retrieve` 已映射到统一 Retrieval 模型；
- [ ] `gen_ai.agent.id=<run_id>` 已按内部模型修正；
- [ ] Instrumentation Scope 和版本已入库；
- [ ] 升级前后有 Golden Trace 对比。

### 9.7.39 升级测试建议

每次升级以下任一组件时都应重新验证：

```text
LangChain
LangGraph
langchain-openai / langchain-anthropic / langchain-aws
OpenTelemetry API / SDK
wrapt
opentelemetry-semantic-conventions
opentelemetry-semantic-conventions-ai
opentelemetry-instrumentation-langchain
```

Golden Trace 至少断言：

```text
Span 数量
Span 名称
SpanKind
parent_span_id
gen_ai.operation.name
gen_ai.provider.name
request / response model
Token
Finish Reason
Tool Name
Retriever Name
Conversation ID
Error Status
内容开关行为
```

还应断言“不应该存在”的数据：

```text
重复 LLM Span
重复 Token
完整 Prompt
Tool Secret
文档正文
Base64 媒体
高基数 Metric Series
未结束 Span
```

### 9.7.40 本节结论

Traceloop／OpenLLMetry 的 `LangchainInstrumentor` 不是简单的“一个 Hook”。其完整机制是：

```text
Entry Point / Traceloop.init / 手工 instrument()
    ↓
Monkey Patch BaseCallbackManager.__init__
    ↓
自动注入可继承 TraceloopCallbackHandler
    ↓
LangChain Callback 捕获 Chain / LLM / Tool / Retriever
    ↓
run_id / parent_run_id 恢复 Span 树
    ↓
LangGraph Pregel / Command / Agent Factory / Middleware 专项 Patch
    ↓
Suppression 避免下游 LLM Span 重复
    ↓
Trace Context Header 连接模型网关
    ↓
OTel API 创建 Span / Metric / LogRecord Event
    ↓
统一 OTel SDK 与 OTLP 导出
```

它的优势是 LangChain／LangGraph 覆盖面广、接入成本低，并能与 OpenTelemetry 基础设施兼容；主要工程风险则是：

- 与官方同名 Instrumentation 混淆；
- 自定义 OpenLLMetry 语义与官方 SemConv 不完全一致；
- 默认内容采集带来的安全风险；
- LangGraph 异步与流式 Context 复杂度；
- Provider Instrumentation 重复；
- 版本升级导致 Patch 失效；
- Fail-open 造成遥测静默缺失。

因此企业生产接入的重点不是“成功调用 `instrument()`”，而是建立：

```text
Span所有权
语义映射
内容治理
兼容性锁定
Golden Trace契约测试
Instrumentation自监控
```

---

### 7.9.8 `opentelemetry-instrumentation-langchain` 包级详解

第 9.7 节从运行机制角度解释了 Traceloop／OpenLLMetry 的 `LangchainInstrumentor`。本节进一步从**发行包、源码模块、公共 API、生命周期、数据模型、兼容性和生产治理** 角度，对 `opentelemetry-instrumentation-langchain` 做包级说明。

首先必须确认名称：

```text
PyPI发行包：
  opentelemetry-instrumentation-langchain

Python导入命名空间：
  opentelemetry.instrumentation.langchain

Instrumentor类：
  LangchainInstrumentor

维护项目：
  Traceloop / OpenLLMetry
```

它不等同于 OpenTelemetry 官方 GenAI 仓库中的：

```text
PyPI发行包：
  opentelemetry-instrumentation-genai-langchain

Python导入命名空间：
  opentelemetry.instrumentation.genai.langchain

Instrumentor类：
  LangChainInstrumentor

维护项目：
  open-telemetry / opentelemetry-python-genai
```

二者都可以观测 LangChain／LangGraph，但实现、字段、内容模型、版本策略和 Span 所有权并不完全相同，不能因为名称相近而混为同一包。

### 9.8.1 包的定位

`opentelemetry-instrumentation-langchain` 是一个 **OpenTelemetry Instrumentation Library**。它不提供 LangChain 本身，也不提供可观测后端。其职责是：

```text
LangChain / LangGraph运行时事件
    ↓
Callback、Monkey Patch和Wrapper
    ↓
OpenTelemetry Span / Metric / LogRecord Event
    ↓
应用配置的OpenTelemetry SDK
    ↓
OTLP Exporter / Collector / Backend
```

它主要解决四个问题：

1. 自动把 OpenTelemetry Callback Handler 注入 LangChain Callback Manager；
2. 把 Chain、Agent、LLM、Tool、Retriever 等生命周期映射为 Span；
3. 对 LangGraph 的 Graph、Node、Command、Agent Factory 和 Middleware 增加专项包装；
4. 提取模型参数、消息、Tool Call、Usage、Metadata、Conversation ID 等数据，并生成指标或事件。

它不负责：

- 创建业务 Agent；
- 执行 LangGraph；
- 存储 Prompt 或 Artifact；
- 提供 Trace 查询界面；
- 自动理解任意自研 Python 决策逻辑；
- 保证所有 LangChain／LangGraph 版本都兼容；
- 代替应用配置 TracerProvider、MeterProvider、LoggerProvider 和 Exporter。

### 9.8.2 当前发行状态与依赖边界

截至 2026-09-03，PyPI 上可见的当前版本为：

```text
opentelemetry-instrumentation-langchain == 0.62.3
Python：>=3.10,<4
License：Apache-2.0
```

发行版本号应以 PyPI／Wheel METADATA 为准。用于审计的对应仓库快照中，`pyproject.toml` 仍可能显示前一开发版本号；因此供应链核验应同时保存 Wheel 哈希、PyPI 元数据、源码 Commit 和构建 Provenance，不能只读取仓库中的单个版本字段。

该版本的发行依赖包括：

```text
opentelemetry-api >= 1.38.0, < 2
opentelemetry-instrumentation >= 0.59b0
opentelemetry-semantic-conventions >= 0.63b1
opentelemetry-semantic-conventions-ai >= 0.5.1, < 0.6.0
```

可选 extra：

```text
opentelemetry-instrumentation-langchain[instruments]
    └── langchain
```

需要注意三点。

第一，基础包没有把全部 Provider 集成作为强依赖。即使安装了 `[instruments]`，通常仍要按业务需要单独安装：

```text
langgraph
langchain-openai
langchain-anthropic
langchain-community
其他模型或向量库集成
```

第二，源码中的自动依赖检查声明较宽：

```text
langchain-core > 0.1.0
```

这表示 `BaseInstrumentor` 的依赖检查可能允许较宽的 `langchain-core` 版本进入初始化流程，但“依赖检查通过”不等于该组合已被当前版本的测试矩阵完整验证。

第三，该包同时使用 OpenTelemetry 官方语义约定包和 OpenLLMetry 的 AI 扩展语义包，所以输出并非天然等同于某一版官方 GenAI Semantic Conventions。生产系统应根据 Instrumentation Scope 和版本执行规范化映射。

### 9.8.3 推荐安装方式

#### 方式一：项目已安装 LangChain／LangGraph

```bash
pip install \
  opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-exporter-otlp \
  opentelemetry-instrumentation-langchain
```

这是最可控的方式。LangChain、LangGraph和各模型 Provider 由应用自己的锁文件管理。

#### 方式二：让 Instrumentation 安装基础 LangChain

```bash
pip install 'opentelemetry-instrumentation-langchain[instruments]'
```

该方式适合实验环境，但不能替代完整的依赖锁定。`[instruments]` 只声明 `langchain`，不代表自动安装所有 Provider、向量库和 LangGraph 组合。

#### 方式三：使用 Traceloop SDK 统一初始化

```bash
pip install traceloop-sdk
```

然后由：

```python
from traceloop.sdk import Traceloop

Traceloop.init(...)
```

间接初始化 OpenLLMetry Instrumentation。此方式接入快，但需要检查 Traceloop SDK 是否创建或替换了应用原有的 OTel Provider、Exporter、Resource 和采样策略。

#### 方式四：零代码自动加载

```bash
opentelemetry-instrument python app.py
```

前提是：

- 包已安装在目标 Python 环境中；
- OTel 自动埋点启动链已经加载；
- LangChain 依赖检查通过；
- 没有通过环境配置禁用该 Instrumentation；
- 应用没有同时加载冲突的 LangChain Instrumentation。

### 9.8.4 包、模块与职责地图

当前发行源码的核心模块可以概括为：

```text
opentelemetry.instrumentation.langchain
├── __init__.py
├── callback_handler.py
├── config.py
├── event_emitter.py
├── event_models.py
├── langgraph_utils.py
├── patch.py
├── span_utils.py
├── utils.py
├── vendor_detection.py
└── version.py
```

| 模块 | 主要职责 |
|---|---|
| `__init__.py` | 定义 `LangchainInstrumentor`，完成 instrument／uninstrument、Callback Manager 注入、LangGraph Patch 与模型调用传播包装 |
| `callback_handler.py` | 接收 LangChain Callback，维护运行状态并创建、补充、结束 Span |
| `config.py` | 保存进程级配置，如内容模式、异常回调、Event Logger 和 Metadata 前缀 |
| `event_emitter.py` | 将消息和 Choice 转为带 `event_name` 的 OpenTelemetry LogRecord Event |
| `event_models.py` | 定义 Message、Choice、Tool Call 等事件数据结构 |
| `langgraph_utils.py` | 提取 LangGraph Graph Node 和 Edge 结构 |
| `patch.py` | 实现 Pregel、Command、Agent Factory、Middleware 等专项 Wrapper |
| `span_utils.py` | 维护 `SpanHolder`，处理消息、Tool、Usage、Finish Reason 和指标属性 |
| `utils.py` | 内容开关、事件模式判断、安全序列化、Fail-open 装饰器等通用逻辑 |
| `vendor_detection.py` | 根据 LangChain 类名推断模型 Provider |
| `version.py` | 暴露 Instrumentation 版本 |

这个模块拆分说明，该包并不是单纯的 Callback Handler：

```text
自动注入层
+ Callback翻译层
+ LangGraph运行时Patch层
+ 模型传播包装层
+ 数据规范化层
+ Metric/Event生成层
```

### 9.8.5 公共 API

最直接的公共入口是：

```python
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
```

构造器当前提供以下关键参数：

```python
LangchainInstrumentor(
    exception_logger=None,
    disable_trace_context_propagation=False,
    use_attributes=None,
    metadata_key_prefix=...,
    use_legacy_attributes=None,
)
```

| 参数 | 含义 | 推荐策略 |
|---|---|---|
| `exception_logger` | Instrumentation 内部异常的回调出口 | 生产环境接入内部诊断日志或计数器 |
| `disable_trace_context_propagation` | 是否禁用对部分 OpenAI LangChain Wrapper 的 `traceparent` 注入 | 调用支持 Trace Context 的企业模型网关时通常保持 `False` |
| `use_attributes` | `True` 使用 Span Attribute 内容模式；`False` 使用 OTel Event 模式 | 新代码优先使用该参数 |
| `metadata_key_prefix` | LangChain Metadata 写入 Span 时使用的属性前缀 | 使用稳定、受治理的低冲突命名空间 |
| `use_legacy_attributes` | 旧名称，当前已弃用 | 不再使用 |

`use_attributes` 与 `use_legacy_attributes` 不能同时传入，否则会抛出 `TypeError`。

`use_legacy_attributes` 会触发弃用警告。虽然内部配置字段仍叫 `use_legacy_attributes`，但对外应把模式理解为：

```text
use_attributes=True
    → 以Span Attribute为主要内容载体

use_attributes=False
    → 以OpenTelemetry LogRecord Event为主要消息输入载体
```

### 9.8.6 `BaseInstrumentor` 生命周期

`LangchainInstrumentor` 继承 OpenTelemetry Python Contrib 的 `BaseInstrumentor`。这带来以下行为：

```text
同一Instrumentor子类通常是进程级单例
        ↓
instrument()检查依赖并避免重复初始化
        ↓
调用子类的_instrument()
        ↓
uninstrument()调用子类的_uninstrument()
```

工程上应把它视为**进程级基础设施初始化**，而不是每个请求、每个 Agent 或每个租户都创建一套实例。

错误用法：

```python
def handle_request(request):
    LangchainInstrumentor().instrument()
    return graph.invoke(...)
```

推荐用法：

```python
def bootstrap_telemetry():
    LangchainInstrumentor().instrument(...)

bootstrap_telemetry()
start_application()
```

因为 Instrumentation 修改的是进程内共享类方法、Callback Manager 构造过程和全局配置，不适合在请求路径中反复开关。

### 9.8.7 零代码自动发现入口

发行包声明了 OpenTelemetry Instrumentor Entry Point：

```toml
[project.entry-points."opentelemetry_instrumentor"]
langchain = "opentelemetry.instrumentation.langchain:LangchainInstrumentor"
```

当使用：

```bash
opentelemetry-instrument python app.py
```

自动加载器会发现该 Entry Point，并执行与以下逻辑等价的初始化：

```python
LangchainInstrumentor().instrument()
```

这里有一个重要约束：自动加载路径通常不能为该 Instrumentor 传入应用级构造参数。因此：

- 需要 `use_attributes=False`；
- 需要自定义 `metadata_key_prefix`；
- 需要设置 `exception_logger`；
- 需要禁用特定传播 Wrapper；

这些场景更适合显式初始化，而不是只依赖零代码启动。

### 9.8.8 `_instrument()` 的执行顺序

`instrument()` 最终进入 `_instrument()`。其主要步骤可以概括为：

```mermaid
flowchart TD
    A[LangchainInstrumentor.instrument] --> B[解析TracerProvider MeterProvider LoggerProvider]
    B --> C[创建Tracer和Meter]
    C --> D[创建模型耗时和Token Histogram]
    D --> E[按模式初始化OTel Logger]
    E --> F[创建共享TraceloopCallbackHandler]
    F --> G[包装BaseCallbackManager.__init__]
    G --> H[包装LangGraph Pregel stream和astream]
    H --> I[包装Command和Agent Factory]
    I --> J[包装AgentMiddleware生命周期]
    J --> K{禁用Trace Context传播?}
    K -- 否 --> L[包装部分OpenAI LangChain模型入口]
    K -- 是 --> M[跳过OpenAI传播包装]
```

这说明初始化不只是“增加一个 Handler”，还会修改多个类方法。版本兼容测试必须覆盖所有实际使用的包装点。

### 9.8.9 `uninstrument()` 的真实边界

`_uninstrument()` 会对初始化时包装的方法执行 `unwrap`，主要包括：

```text
BaseCallbackManager.__init__
LangGraph Pregel.stream / astream
LangGraph Command.__init__
Agent Factory
AgentMiddleware各生命周期方法
部分LangChain OpenAI模型入口
```

但应准确理解它的边界。

#### 可以做到

- 阻止后续新建 Callback Manager 自动注入 Handler；
- 恢复被 `wrapt` 包装的类方法；
- 阻止后续新调用继续经过这些 Wrapper。

#### 不应假设自动做到

- 从已创建的 Callback Manager 中删除已经注入的 Handler；
- 自动结束仍在执行的 Span；
- 自动清空所有已经产生的后端数据；
- 自动撤销其他 Instrumentation 创建的 Wrapper；
- 在并发请求执行中安全切换观测状态。

从源码行为推断，`uninstrument()` 主要回滚方法级 Patch，并没有遍历所有已存在 Callback Manager 删除共享 Handler。因此，在测试之外，不建议把它当作生产运行时的动态 Feature Toggle。需要彻底切换 Instrumentation 时，进程重启通常更可预测。

### 9.8.10 进程级全局配置模型

`config.py` 中的 `Config` 使用类属性保存：

```text
exception_logger
use_legacy_attributes
Event Logger
metadata_key_prefix
```

这意味着配置是进程级共享状态，而不是：

```text
每次Run独立
每个Graph独立
每个Agent独立
每个租户独立
```

由此产生几个工程结论：

1. 同一 Python 进程中不能稳定地让一部分 Graph 使用 Attribute 模式、另一部分 Graph 使用 Event 模式；
2. 不应在请求中动态修改 `metadata_key_prefix`；
3. 多租户内容策略不能只靠重新构造 Instrumentor；
4. 单元测试之间必须恢复全局配置或隔离进程；
5. 动态开关内容采集应使用受控 Context Override 或应用自己的策略层，而不是反复 `instrument()`。

### 9.8.11 Callback Manager 注入机制

核心自动接入点是：

```text
langchain_core.callbacks.BaseCallbackManager.__init__
```

包装器在原构造函数完成后：

1. 检查 `inheritable_handlers` 中是否已有同类型 Handler；
2. 如果没有，则加入共享的 `TraceloopCallbackHandler`；
3. 设置为 inheritable，使其传播给下游 Chain、Tool、Retriever 和模型调用。

简化伪代码：

```python
def wrapped_init(original, manager, *args, **kwargs):
    original(manager, *args, **kwargs)

    if not contains_traceloop_handler(manager.inheritable_handlers):
        manager.add_handler(shared_handler, inherit=True)
```

这一机制带来两个边界。

#### 初始化时机边界

在 `instrument()` 之前已经创建好的 Callback Manager 不会因为后续 Patch 自动重新执行构造函数。因此推荐：

```text
初始化OTel SDK
    ↓
初始化LangchainInstrumentor
    ↓
创建Graph、Chain、Agent和Callback Manager
    ↓
开始接收请求
```

#### 自定义 Callback 边界

如果业务在调用时替换、过滤或重建 Callback Manager，需要检查 OTel Handler 是否仍然位于 `inheritable_handlers`，而不是只检查普通 `handlers`。

### 9.8.12 Handler 的运行状态模型

`TraceloopCallbackHandler` 内部维护：

```python
self.spans: dict[UUID, SpanHolder]
```

其中 `SpanHolder` 保存的典型状态包括：

```text
Span对象
Context Token
父子关系
子Run ID列表
Workflow名称
Entity名称和路径
Association Properties Token
开始时间
请求模型
```

状态主键是 LangChain 的：

```text
run_id
```

父子关系主要来自：

```text
parent_run_id
```

因此，该包恢复调用树的核心不是 Python 调用栈，而是 LangChain Callback 协议携带的运行标识。

```text
on_chain_start(run_id=A, parent_run_id=None)
    → 创建根Span A

on_tool_start(run_id=B, parent_run_id=A)
    → 找到A
    → 创建B并以A为父Span

on_tool_end(run_id=B)
    → 补充结果
    → 结束并移除B
```

这比单纯依赖“当前 Span”更能应对部分异步、批处理和回调顺序场景，但仍要求 `run_id` 生命周期正确且回调完整。

### 9.8.13 Callback 到 Span 的精确映射

| Callback | 典型 Span | 主要信息 |
|---|---|---|
| `on_chain_start` | Workflow、Agent或Task Span | 名称、输入、Tag、Metadata、父子关系 |
| `on_chain_end` | 结束对应 Chain Span | 输出、状态、耗时 |
| `on_chain_error` | Chain Span Error | `error.type`、Exception、失败状态 |
| `on_chat_model_start` | Chat Model Client Span | Messages、模型、参数、Tool Definitions |
| `on_llm_start` | Text Completion Client Span | Prompt、模型、参数 |
| `on_llm_end` | 结束 Model Span | Choices、Response Model、Usage、Finish Reason |
| `on_llm_error` | Model Span Error | Error Type、Exception、失败状态 |
| `on_tool_start` | `execute_tool` Span | Tool 名称、描述、输入、Call ID |
| `on_tool_end` | 结束 Tool Span | Tool Result、状态 |
| `on_tool_error` | Tool Span Error | Error Type、Exception |
| `on_retriever_start` | Retrieval Span | Query、Retriever 名称 |
| `on_retriever_end` | 结束 Retrieval Span | Documents及Metadata，可受内容开关控制 |
| `on_retriever_error` | Retrieval Span Error | Error Type、Exception |

当前 Handler 没有通过 `on_llm_new_token` 生成每个 Token 的原生时间线。因此，不应仅凭该包假设可以得到：

```text
每个Token到达时间
精确TTFT
每个Chunk间隔
流式Chunk大小分布
```

这些指标通常还需要底层模型 SDK Instrumentation、HTTP 流式探针或应用级流包装。

### 9.8.14 Span 名称、Kind 与操作语义

该包的命名不完全等同于官方 GenAI Semantic Conventions。

典型模式包括：

```text
<name>.workflow
<name>.agent
execute_task <name>
execute_tool <name>
vector_db_retrieve <name>
<model-name>.chat
<model-name>.completion
```

典型 SpanKind：

| Span | 常见 Kind |
|---|---|
| Workflow／Agent／Task | `INTERNAL` |
| Tool | 通常为 `INTERNAL` |
| LLM／Chat Model | `CLIENT` |
| Retriever | 通常为 `INTERNAL` |

当前实现中，根 Workflow 和部分 Agent 语义可能使用：

```text
gen_ai.operation.name = invoke_agent
```

Task 可能使用 OpenLLMetry 自定义：

```text
gen_ai.operation.name = execute_task
```

Retriever 可能使用：

```text
gen_ai.operation.name = vector_db_retrieve
```

因此企业侧建议增加 Mapper：

```text
根.workflow + invoke_agent
    → invoke_workflow

execute_task
    → app.agent.task.execute

vector_db_retrieve
    → retrieval
```

不要只根据 Span 名称猜测语义，应同时读取：

```text
otel.scope.name
或 instrumentation_scope.name

otel.scope.version
或 instrumentation_scope.version

gen_ai.operation.name
span.kind
```

### 9.8.15 Attribute 内容模式

默认模式是：

```python
LangchainInstrumentor(use_attributes=True)
```

它会把可采集的消息、输出、Tool 定义等内容写入 Span Attribute。典型数据包括：

```text
gen_ai.system_instructions
gen_ai.input.messages
gen_ai.output.messages
模型请求参数
Tool Definitions
Tool Arguments
Tool Results
Retriever Query
Retriever Documents
```

优点：

- Trace 后端通常可以直接显示；
- 不要求部署 Logs Pipeline；
- 便于单条 Trace 调试；
- 与部分既有 OpenLLMetry 后端兼容。

风险：

- Span 体积膨胀；
- Attribute 长度和数量限制导致截断；
- Prompt、代码、文档、Tool 参数泄露；
- Tail Sampling 前的 Collector 内存压力增加；
- Trace 存储保留期通常长于业务内容所需保留期；
- 同一内容可能被 LangChain 层和模型 SDK 层重复采集。

生产环境不应因为该模式是默认值就直接采用默认内容策略。

### 9.8.16 Event 内容模式

设置：

```python
LangchainInstrumentor(use_attributes=False)
```

会初始化 OpenTelemetry Logger，并把模型输入消息等内容表达为 LogRecord Event。典型 `event_name`：

```text
gen_ai.user.message
gen_ai.system.message
gen_ai.assistant.message
gen_ai.tool.message
gen_ai.choice
```

这一模式的完整出口是：

```text
LangChain Callback
    ↓
event_emitter.py
    ↓
Logger.emit(event_name=...)
    ↓
Logs SDK
    ↓
OTLP Logs
    ↓
Collector
```

使用 Event 模式必须同时确认：

- 已配置 `LoggerProvider`；
- 已安装并配置 OTLP Log Exporter；
- Collector 开启 OTLP Logs Receiver；
- 后端保留 `event_name` 字段；
- 日志脱敏、采样和保留期已经配置；
- TraceId／SpanId 能随当前 Context 自动关联。

否则可能出现“Trace 有 Span，但消息 Event 看不到”的情况。

### 9.8.17 Event 模式不是完全无 Attribute

需要特别注意当前实现的兼容行为。

模型开始时：

```text
Attribute模式
    → 把输入消息写到Span Attribute

Event模式
    → 发出gen_ai.*.message Event
```

模型结束时：

```text
Event模式
    → 发出gen_ai.choice Event
    → 同时仍可能为了向后兼容写入部分响应Span Attribute
```

所以 `use_attributes=False` 更准确的含义是：

> **消息输入优先切换到 Event 模型，而不是保证所有内容相关 Span Attribute 都绝对消失。**

此外，Chain、Tool 和 Retriever 并没有与模型消息完全对称的领域 Event 集合。切换到 Event 模式后，应通过 Golden Trace／Golden Event 验证每类数据究竟位于：

```text
Span Attribute
LogRecord Event
两者同时存在
两者都不存在
```

不能仅凭构造参数名称推断后端数据形态。

### 9.8.18 内容采集开关与优先级

全局环境变量：

```bash
export TRACELOOP_TRACE_CONTENT=false
```

用于关闭 Prompt、Completion、Embedding、Tool 参数、Retriever 文档等敏感正文采集。

默认行为通常是：

```text
未设置TRACELOOP_TRACE_CONTENT
    → 允许内容采集
```

这与许多“默认不采集正文”的安全策略不同，必须显式治理。

该包还支持通过 OpenTelemetry Context 中的：

```text
override_enable_content_tracing
```

覆盖全局内容策略。由此得到推荐优先级：

```text
平台强制安全策略
    > 请求级受控Context Override
    > TRACELOOP_TRACE_CONTENT
    > 包默认值
```

不要把 Context Override 暴露为普通用户可任意设置的请求参数，否则用户可能绕过默认的内容禁用策略。

即使关闭内容采集，通常仍可保留：

```text
Span名称
Operation名称
模型Provider
请求模型
响应模型
Token Usage
耗时
错误类型
Tool名称
Retriever名称
Conversation ID
受控Metadata
```

### 9.8.19 Message 与多模态内容规范化

`span_utils.py` 会把 LangChain Message 转换为统一消息部件。典型输入：

```text
SystemMessage
HumanMessage
AIMessage
ToolMessage
FunctionMessage
ChatMessage
```

典型部件：

```text
text
image URL
base64 image blob
tool call
tool result
其他JSON结构
```

多模态处理大致是：

```text
文本块
    → text part

image_url
    → URI形式的image part

base64 image
    → blob形式的image part

未知结构块
    → JSON序列化后作为文本或通用内容
```

这种规范化有助于跨 Provider 展示，但也会增加风险：

- Base64 图片体积巨大；
- Data URL 可能包含完整敏感图片；
- 未知对象转字符串可能泄露内部字段；
- JSON 序列化可能复制大段文档或状态；
- Attribute／Event 后端未必适合保存二进制内容。

生产建议：

```text
大型图片和文件
    → Artifact Store

遥测
    → 只记录artifact_id、media_type、size、hash和安全引用
```

### 9.8.20 Tool 定义、调用与结果

模型请求中的 Tool Definition 通常被规范化为：

```text
tool name
tool description
input schema
```

模型输出中的 Tool Call 通常包含：

```text
tool call id
tool name
arguments
```

Tool 执行 Span 通常包含：

```text
gen_ai.operation.name = execute_tool
gen_ai.tool.name
gen_ai.tool.type
gen_ai.tool.description
gen_ai.tool.call.id
arguments
result
```

安全边界：

- JSON Schema 可能包含内部系统字段；
- Tool Arguments 可能包含 Token、密码、文件正文或 SQL；
- Tool Result 可能包含客户数据；
- Shell Tool 可能包含完整命令和工作区路径；
- Browser Tool 可能包含 Cookie、页面正文和下载链接。

建议将 Tool 名称和结构性属性作为默认可观测字段，而将 Arguments、Result 和 Schema 正文设为 Opt-In。

### 9.8.21 模型请求与响应映射

模型开始阶段通常提取：

```text
Provider
请求模型
最大输出Token
Temperature
Top P
System Instructions
Input Messages
Tool Definitions
```

模型结束阶段通常提取：

```text
响应模型
Response ID
Output Messages或Choices
Finish Reason
Input Token
Output Token
Total Token
Cache Read Token
Cache Creation Token
```

Finish Reason 会做跨 Provider 归一化，例如：

| Provider 原始值 | 归一化结果 |
|---|---|
| `stop` | `stop` |
| `end_turn` | `stop` |
| `stop_sequence` | `stop` |
| `length` | `length` |
| `max_tokens` | `length` |
| `tool_calls` | `tool_call` |
| `function_call` | `tool_call` |
| `tool_use` | `tool_call` |
| `content_filter` | `content_filter` |

归一化有助于跨 Provider 聚合，但原始 Provider 值仍可能包含额外语义。需要精确审计时，可以额外保留受控的原始值字段。

### 9.8.22 Token Usage 与指标

该包创建模型操作耗时和 Token Usage Histogram。Token 维度通常包括：

```text
input
output
total
cache_read
cache_creation
```

指标属性可能包括：

```text
Provider
Request Model
Response Model
Token Type
```

需要防止三类重复统计：

1. LangChain Instrumentation 生成 Token Metric；
2. OpenAI／Anthropic Provider Instrumentation 生成 Token Metric；
3. 后端从 Span Attribute 再派生 Token Metric。

推荐指定唯一权威口径：

```text
模型逻辑调用Span
    → 由Provider SDK Instrumentation拥有

Workflow / Tool / Retriever
    → 由LangChain Instrumentation拥有

成本指标
    → 从唯一模型Span或统一Usage事件派生
```

如果选择保留 LangChain 模型 Span，则应通过 Scope 过滤避免对同一调用重复计费。

### 9.8.23 Retriever 数据模型

Retriever 开始阶段可记录：

```text
Retriever名称
Query
父Run ID
Metadata
```

Retriever 结束阶段可以记录：

```text
Document page_content
Document metadata
Document数量
```

这是最容易造成遥测放大的路径之一。一个 `top_k=20` 的检索调用可能把 20 份长文档及其 Metadata 全部复制到 Trace。

生产推荐只保留：

```text
query hash或脱敏摘要
top_k
returned_count
collection / index name
rerank model
latency
score统计
artifact_id或document_id
```

文档正文进入 Artifact Store 或受访问控制的 Evidence Store，Trace 中只记录引用。

### 9.8.24 Metadata 与 Association Properties

LangChain Callback 中的 `metadata` 会经过类型清洗，再使用可配置前缀写入 Span。

默认前缀与 OpenLLMetry 的 Association Properties 体系相关。可配置：

```python
LangchainInstrumentor(
    metadata_key_prefix="app.association",
)
```

适合的 Metadata：

```text
environment
release_channel
workflow_type
agent_version
prompt_version
experiment_group
tenant_tier
```

不适合直接写入：

```text
user_id原值
session_id作为Metric标签
完整文件路径
Prompt正文
任意嵌套业务对象
大数组
预签名URL
认证Token
```

Metadata 进入 Span 后，会影响：

- 存储体积；
- 属性基数；
- 查询索引；
- Tail Sampling 规则；
- 隐私与租户隔离。

因此应先定义 Allowlist，而不是把任意 LangChain Metadata 透传到遥测。

### 9.8.25 Conversation ID 映射

对于 LangGraph，当前实现会尝试从配置中提取：

```text
configurable.thread_id
```

并映射为：

```text
gen_ai.conversation.id
```

该映射只有在 `thread_id` 真正代表业务会话或线程时才成立。不要把以下值机械映射为 Conversation ID：

```text
Trace ID
随机Request ID
Graph Run ID
用户ID
Prompt Hash
```

如果 `thread_id` 属于敏感、高基数或跨租户标识，应在进入遥测前执行：

```text
稳定哈希
租户作用域前缀
访问控制
保留期治理
```

### 9.8.26 Provider 识别机制

该包主要根据 LangChain 对象的类名和模式推断 Provider，覆盖的典型类别包括：

```text
OpenAI
Azure OpenAI
Anthropic
AWS Bedrock
Google Vertex AI
Google GenAI
Cohere
Hugging Face
Ollama
Together
Replicate
Fireworks
Groq
Mistral
```

识别失败时可能回退到：

```text
langchain
```

这是启发式识别，不是 Provider 的权威协议协商。以下情况容易误判：

- 企业自定义 ChatModel Wrapper；
- 类名被代理或重新导出；
- 一个统一网关兼容多个 Provider；
- 自研模型类名包含类似关键字；
- Provider SDK 升级后重命名类；
- 动态 Router 在一次运行中切换模型。

推荐让 Provider SDK Instrumentation 或模型网关提供权威 Provider／Model 信息，并把 LangChain 检测值作为候选或回退值。

另一个细节是：Span 上的 Provider 可能来自类名检测，而 Event Emitter 的公共 Event Attribute 使用 `langchain` 作为 Provider。由此可能出现：

```text
同一次调用的Span：provider=openai
对应消息Event：provider=langchain
```

后端关联时应明确一个字段表示“Instrumentation来源”，另一个字段表示“实际模型Provider”，不要把二者当作完全同义字段。

### 9.8.27 LangGraph 专项覆盖范围

该包不仅依赖 LangChain Callback，还会包装 LangGraph：

```text
Pregel.stream
Pregel.astream
Command.__init__
create_react_agent
create_agent
AgentMiddleware.before_model
AgentMiddleware.after_model
AgentMiddleware.before_agent
AgentMiddleware.after_agent
对应异步方法
```

它可以补充：

```text
Graph根Span
Graph Nodes
Graph Edges
Node Task Span
Command goto动态路由
Agent Factory创建信息
System Instructions
Tool Definitions
Middleware Task Span
```

但仍然存在边界：

- 普通 Node 函数内部的任意 Python 逻辑不可见；
- 未经这些入口执行的自定义 Runtime 不一定被覆盖；
- LangGraph 内部模块路径变化可能使 Patch 失效；
- 自定义 Middleware 若绕过标准基类可能无法捕获；
- Checkpointer 内部数据库操作仍需数据库 Instrumentation；
- Graph 状态 Diff 并不会天然完整记录；
- Streaming Chunk 时间线仍需额外观测。

### 9.8.28 模型 Span 抑制与 Trace Context 传播

该包使用 OpenTelemetry Context 中的语言模型 Instrumentation 抑制标记，避免下游 Provider Instrumentation 再次生成同义模型 Span。

目标结构：

```text
LangChain模型Span
└── HTTP Client Span
```

而不是：

```text
LangChain模型Span
└── OpenAI模型Span
    └── HTTP Client Span
```

但是否抑制下游 Provider Span，应根据 Span 所有权决定。

#### 选择 LangChain 模型 Span 为权威

```text
保留LangChain模型Span
抑制OpenAI／Anthropic模型Span
保留HTTP Transport Span
```

#### 选择 Provider 模型 Span 为权威

```text
保留LangChain Workflow／Tool／Retriever
抑制或过滤LangChain模型Span
保留Provider模型Span
保留HTTP Transport Span
```

该包还会对部分 LangChain OpenAI 模型入口注入：

```text
traceparent
```

写入 `extra_headers`，用于连接支持 W3C Trace Context 的企业模型网关。

需要注意：公共 OpenAI API 是否使用该 Header 建立服务端 OTel 父子 Span，取决于服务端能力；单纯发送 `traceparent` 不意味着远端一定继续该 Trace。

当前部分 ChatOpenAI 流式包装点在源码中未启用，因此同步、异步、流式和不同 Provider 的传播覆盖并不完全对称。上线前必须以实际调用方式做契约测试。

### 9.8.29 Instrumentation Scope 与语义版本识别

所有下游查询和规范化都应保存：

```text
instrumentation_scope.name
instrumentation_scope.version
service.name
service.version
telemetry.schema.version
mapper.version
```

对于该包，Scope 通常可识别为：

```text
opentelemetry.instrumentation.langchain
```

企业后端应按 Scope 区分：

```text
OpenLLMetry LangChain Instrumentation
OpenTelemetry官方GenAI LangChain Instrumentation
Provider SDK Instrumentation
HTTP Instrumentation
应用手工Span
```

不要只用 Span 名或 `gen_ai.operation.name` 判断来源，因为不同 Instrumentation 可能产生相似名称，却采用不同内容结构和 Requirement Level。

### 9.8.30 自动加载冲突

如果环境中同时安装：

```text
opentelemetry-instrumentation-langchain
opentelemetry-instrumentation-genai-langchain
```

并使用：

```bash
opentelemetry-instrument python app.py
```

两套 Instrumentor 都可能被自动发现。可能出现：

```text
两个Callback Handler
两个Workflow Span
两个Tool Span
两个Retriever Span
模型Span与Token重复
内容被采集两次
父子关系变深或错位
```

治理方法：

1. 生产环境只选择一个 LangChain 框架层 Instrumentation；
2. 通过 `pip freeze` 和 Entry Point 枚举确认实际加载集合；
3. 对未选中的 Instrumentation 使用禁用配置；
4. 对 Provider SDK 和 HTTP 层单独定义 Span 所有权；
5. 在 CI 中对 Golden Trace 的 Span 数量和拓扑做断言。

### 9.8.31 测试覆盖与已知缺口

当前源码测试目录覆盖了多类场景，包括：

```text
Agent
Chain
LCEL
LangGraph
LLM
Tool Call
Structured Output
非ASCII消息
Finish Reason
Generation Role
JSON Encoder
Context Token生命周期
Association Properties清理
Metrics
```

其中 Context 生命周期测试关注：

- 模型抑制标记在 Span 结束后被清理；
- Association Properties Context 被清理；
- 重复 `run_id` 生命周期；
- 根 Chain Context 不持续堆积。

这类测试非常重要，因为 Context 泄漏会导致后续无关请求错误继承父 Span 或抑制状态。

同时，当前源码快照中部分 Batch Metadata 测试因录制测试问题处于跳过状态。这不等于功能必然错误，但意味着依赖批量调用与 Metadata 组合的生产系统应自行补充回归测试。

建议企业测试矩阵至少覆盖：

| 维度 | 场景 |
|---|---|
| 调用模式 | invoke、ainvoke、stream、astream、batch、abatch |
| LangGraph | 普通 Node、条件边、Command goto、子图、Middleware |
| 模型 | OpenAI、Anthropic、企业网关、自定义 ChatModel |
| Tool | 正常、异常、超时、结构化输出、大结果 |
| Retriever | 空结果、多文档、大文档、Metadata异常类型 |
| Context | asyncio、线程池、并发请求、取消、重试 |
| 内容 | Attribute、Event、关闭内容、请求级 Override |
| 组合探针 | LangChain＋Provider＋HTTP＋数据库 |
| 生命周期 | instrument、重复instrument、uninstrument、进程重启 |

### 9.8.32 并发与生命周期风险

共享 Handler 通过 `run_id` 字典管理活动 Span。设计上可以处理大量并发 Run，但生产仍需监控：

```text
活动spans字典大小
未结束Span数量
超长Span
重复run_id
Callback缺失
Context attach/detach异常
取消后残留状态
```

常见风险：

1. 流式调用未消费完且没有显式关闭；
2. 自定义 Runnable 只触发 start，不触发 end/error；
3. 任务被强制取消，框架未完整回调；
4. 进程在 BatchSpanProcessor Flush 前退出；
5. 线程池或自定义 Task 调度没有传播 Context；
6. 同一个 `run_id` 被错误复用；
7. 运行中调用 `uninstrument()`。

建议设置守护指标：

```text
app.instrumentation.langchain.active_runs
app.instrumentation.langchain.orphaned_runs
app.instrumentation.langchain.callback_errors
app.instrumentation.langchain.context_cleanup_errors
app.instrumentation.langchain.serialization_errors
```

这些不是当前包保证提供的标准指标，可由应用的 `exception_logger`、Collector 或扩展 Handler 补充。

### 9.8.33 Fail-open 与自监控

Instrumentation 内部多个辅助函数采用 Fail-open 思路：

```text
遥测序列化或设置属性失败
    → 捕获异常
    → 记录调试信息或调用exception_logger
    → 尽量不影响Agent主流程
```

优点是遥测故障不阻断业务；缺点是可能出现“业务正常但遥测静默缺失”。

因此生产环境应设置：

```python
def report_instrumentation_error(error: Exception) -> None:
    # 写入独立、不会再次触发LangChain Instrumentation的诊断通道
    internal_logger.exception(
        "langchain instrumentation failure",
        exc_info=error,
    )
```

不要把 `exception_logger` 再接回会触发同一 LangChain 调用的 LLM 日志分析器，以免形成递归。

### 9.8.34 Telemetry Amplification 控制

该包可能从多个位置采集内容：

```text
Chain input / output
Model input / output
Tool arguments / result
Retriever query / documents
LangGraph state or metadata
Multimodal image content
```

一次 RAG Agent 请求的遥测体积可能远大于业务请求本身。

推荐限制：

```text
单Attribute最大长度
单Span最大属性数
单Event Body最大长度
单Trace最大Span数
单Retriever最大文档数
单Tool Result最大记录字节数
单Graph最大Node／Edge数量
```

治理顺序：

1. 应用内在采集前截断和脱敏；
2. 关闭默认正文采集；
3. 大内容写 Artifact Store；
4. Collector 再执行 Redaction、Filter 和 Transform；
5. 后端按 Scope 设置独立保留期；
6. 对超大 Trace 设置保护性采样和丢弃策略。

### 9.8.35 推荐生产初始化示例

下面示例采用：

- 统一 Traces、Metrics、Logs Provider；
- OTLP 导出；
- Event 模式；
- 默认关闭正文；
- 自定义 Metadata 前缀；
- Instrumentation 自诊断；
- 由应用统一拥有 Provider 生命周期。

```python
from __future__ import annotations

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

internal_logger = logging.getLogger("telemetry.langchain")


def instrumentation_error(error: Exception) -> None:
    internal_logger.error(
        "opentelemetry-instrumentation-langchain internal error: %s",
        error,
        exc_info=error,
    )


def configure_telemetry() -> None:
    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://127.0.0.1:4317",
    )

    # OpenLLMetry默认倾向于采集内容，生产环境显式关闭。
    os.environ.setdefault("TRACELOOP_TRACE_CONTENT", "false")

    resource = Resource.create(
        {
            "service.name": "agent-service",
            "service.version": "1.0.0",
            "deployment.environment.name": os.environ.get(
                "DEPLOYMENT_ENVIRONMENT",
                "production",
            ),
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint)
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint))
    )
    set_logger_provider(logger_provider)

    LangchainInstrumentor(
        exception_logger=instrumentation_error,
        use_attributes=False,
        metadata_key_prefix="app.association",
        # 企业模型网关支持traceparent时保留False。
        disable_trace_context_propagation=False,
    ).instrument(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
    )
```

应用关闭时应执行 Provider 的 Flush／Shutdown，而不是只调用 `uninstrument()`。

### 9.8.36 最小安全配置

如果暂时不接入 Logs Pipeline，可使用 Attribute 模式，但必须关闭正文：

```bash
export TRACELOOP_TRACE_CONTENT=false
```

```python
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

LangchainInstrumentor(
    use_attributes=True,
    metadata_key_prefix="app.association",
).instrument()
```

此时仍需验证：

```text
模型名是否存在
Token是否存在
Tool名称是否存在
错误是否存在
父子关系是否正确
Prompt正文是否确实不存在
Tool Arguments是否确实不存在
Retriever Documents是否确实不存在
```

### 9.8.37 包身份与加载诊断脚本

下面的诊断脚本可以确认实际安装的是哪一个包、版本和 Entry Point：

```python
from __future__ import annotations

from importlib import metadata


def show_distribution(name: str) -> None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        print(f"{name}: not installed")
        return

    print(f"{name}: {dist.version}")
    print(f"  requires-python: {dist.metadata.get('Requires-Python')}")


show_distribution("opentelemetry-instrumentation-langchain")
show_distribution("opentelemetry-instrumentation-genai-langchain")

print("\nOpenTelemetry instrumentor entry points:")
for entry_point in metadata.entry_points(group="opentelemetry_instrumentor"):
    if "langchain" in entry_point.name.lower() or "langchain" in entry_point.value.lower():
        print(f"  {entry_point.name} -> {entry_point.value}")

try:
    from opentelemetry.instrumentation.langchain import LangchainInstrumentor

    instrumentor = LangchainInstrumentor()
    print("\nOpenLLMetry class:", type(instrumentor))
    print("dependencies:", instrumentor.instrumentation_dependencies())
except ImportError as error:
    print("\nOpenLLMetry import failed:", error)

try:
    from opentelemetry.instrumentation.genai.langchain import LangChainInstrumentor

    print("Official GenAI class:", LangChainInstrumentor)
except ImportError as error:
    print("Official GenAI import failed:", error)
```

Callback Manager 诊断：

```python
from langchain_core.callbacks import CallbackManager

manager = CallbackManager.configure()

for handler in manager.inheritable_handlers:
    print(
        type(handler).__module__,
        type(handler).__qualname__,
    )
```

期望能看到类似：

```text
opentelemetry.instrumentation.langchain.callback_handler
TraceloopCallbackHandler
```

如果同时出现官方和 OpenLLMetry Handler，应检查是否发生重复自动加载。

### 9.8.38 Golden Trace 验收样例

对以下最小 Graph：

```text
Graph
├── Retriever Node
├── LLM Node
└── Tool Node
```

建议定义期望拓扑：

```text
invoke_workflow rag_graph                 1个
├── retrieval knowledge_base             1个
├── chat model-x                          1个权威模型Span
│   └── HTTP POST                         0或1个Transport Span
└── execute_tool calculator               1个
```

验收断言：

```text
根Span数量 = 1
权威模型Span数量 = 1
Tool Span数量 = 实际Tool调用次数
Retriever Span数量 = 实际Retriever调用次数
input_tokens只统计一次
output_tokens只统计一次
所有Span具有同一Trace ID
父Span结束后不存在孤立活动Run
TRACELOOP_TRACE_CONTENT=false时正文检索为0条
Event模式下event_name和TraceId／SpanId可查询
```

### 9.8.39 与官方 `genai-langchain` 包的选择

| 判断维度 | `opentelemetry-instrumentation-langchain` | `opentelemetry-instrumentation-genai-langchain` |
|---|---|---|
| 维护方 | Traceloop／OpenLLMetry | OpenTelemetry 官方 GenAI 项目 |
| 主要类 | `LangchainInstrumentor` | `LangChainInstrumentor` |
| 语义体系 | 官方 OTel + OpenLLMetry AI 扩展的混合 | 面向官方 GenAI Semantic Conventions |
| LangGraph专项Patch | 较多 | 以当前官方实现能力为准 |
| Traceloop后端兼容 | 原生路径 | 需验证 |
| 内容默认策略 | 默认倾向采集，需显式关闭 | 以官方包当前配置为准 |
| Event模型 | OpenLLMetry消息和Choice Event | 官方 GenAI Event模型 |
| 迁移成本 | 已有OpenLLMetry数据模型时较低 | 追求官方SemConv一致性时较低 |

选择原则：

```text
已有Traceloop／OpenLLMetry后端、字段和Dashboard
    → 优先评估opentelemetry-instrumentation-langchain

新建平台且希望紧跟官方GenAI SemConv
    → 优先评估opentelemetry-instrumentation-genai-langchain

需要OpenLLMetry更深的LangGraph Patch
    → 做兼容性PoC后选择OpenLLMetry包

两套都不能完整表达自研Loop、权限、预算和审批
    → 仍需应用层原生埋点
```

### 9.8.40 版本升级清单

升级该包前至少检查：

- [ ] Python 版本范围；
- [ ] `opentelemetry-api`、SDK、Instrumentation 和 SemConv 版本；
- [ ] `langchain-core`、`langchain`、`langgraph` 版本；
- [ ] Provider 集成版本；
- [ ] `BaseCallbackManager.__init__` 路径；
- [ ] Pregel、Command、Agent Factory、Middleware 路径；
- [ ] 同步／异步／流式 Wrapper 是否仍命中；
- [ ] Attribute 和 Event 模式输出差异；
- [ ] `TRACELOOP_TRACE_CONTENT=false` 是否仍生效；
- [ ] 请求级内容 Override 是否符合安全策略；
- [ ] Provider 识别结果；
- [ ] Finish Reason 映射；
- [ ] Token、Cache Token 提取；
- [ ] Metadata 前缀和类型清洗；
- [ ] Instrumentation Scope 和版本；
- [ ] Provider Span 是否重复；
- [ ] `traceparent` 是否被正确注入；
- [ ] `uninstrument()` 行为；
- [ ] Context 清理与并发隔离；
- [ ] Collector Transform 和后端 Dashboard 是否兼容；
- [ ] Golden Trace／Golden Event 契约测试是否通过。

### 9.8.41 “可加载依赖”与维护者测试基线

Instrumentor 对自动加载器声明的最低条件是：

```python
_instruments = ("langchain-core > 0.1.0",)
```

这只是“是否允许尝试加载”的依赖门槛。当前发行源码的主要测试依赖则集中在更窄、更新的组合，例如：

```text
langchain >= 1.0, < 2.0
langgraph >= 1.0, < 2.0
langchain-openai >= 1.0, < 2.0
langchain-community >= 0.4, < 0.5
openai >= 1.52.2, < 3
```

两者的含义不同：

| 约束 | 回答的问题 |
|---|---|
| `instrumentation_dependencies()` | 当前环境是否具备基本加载条件 |
| 包的测试依赖 | 维护者主要在哪些版本组合上执行回归 |
| 企业兼容矩阵 | 当前业务实际使用的调用路径是否经过验证 |

因此：

> 自动加载器没有跳过该包，只能证明依赖检查通过，不能证明所有 Callback、LangGraph Patch、Provider Wrapper 和流式路径都兼容。

尤其是以下内部路径对版本变化敏感：

```text
langgraph.pregel.Pregel.stream
langgraph.pregel.Pregel.astream
langgraph.types.Command.__init__
langgraph.prebuilt.create_react_agent
langchain.agents.factory.create_agent
langchain.agents.middleware.types.AgentMiddleware
```

框架升级后可能出现“主 Callback 链仍有数据，但 Graph、Command 或 Middleware 细粒度 Span 静默消失”的部分失效状态。

### 9.8.42 把观测依赖作为 Release Train 锁定

不要只声明：

```text
opentelemetry-instrumentation-langchain>=0.62
```

建议把以下组件作为一个经过验证的 Release Train：

```text
Python
OpenTelemetry API
OpenTelemetry SDK
OpenTelemetry Instrumentation Base
OpenTelemetry Semantic Conventions
OpenLLMetry AI Semantic Conventions
opentelemetry-instrumentation-langchain
LangChain
LangGraph
Provider集成
Provider SDK
OTLP Exporter
Collector
```

约束文件示例：

```text
# observability-constraints.txt
opentelemetry-api==1.38.0
opentelemetry-sdk==1.38.0
opentelemetry-instrumentation==0.59b0
opentelemetry-semantic-conventions==0.63b1
opentelemetry-semantic-conventions-ai==0.5.1
opentelemetry-instrumentation-langchain==0.62.3

# framework-constraints.txt
langchain==x.y.z
langgraph==x.y.z
langchain-openai==x.y.z
openai==x.y.z
```

示例中的 `x.y.z` 必须替换为项目自身测试通过的版本，不能机械复制。

升级流程应是：

```text
更新候选依赖
    ↓
pip check和静态导入检查
    ↓
运行单元与集成测试
    ↓
回放Golden Trace / Golden Event数据集
    ↓
比较Span树、属性、Token和内容策略
    ↓
验证Collector与Dashboard
    ↓
灰度发布
```

### 9.8.43 多进程、热重载与 Notebook

#### 多进程 Worker

每个 Python Worker 都有独立的：

```text
模块级Config
LangchainInstrumentor状态
Callback Handler
TracerProvider / MeterProvider / LoggerProvider
Batch Processor后台线程
Exporter连接
```

推荐每个 Worker 在自己的启动阶段初始化一次。Pre-fork 部署需要特别验证：

- Provider 和 Batch Processor 是否在 Fork 前创建；
- Exporter 后台线程是否能在子进程正常工作；
- Fork 后是否复用了不可安全共享的网络连接；
- 每个 Worker 是否正确 Flush／Shutdown；
- `service.instance.id` 是否能区分不同 Worker。

更可预测的方式通常是：

```text
Master不创建OTel后台线程
    ↓
Worker Fork完成
    ↓
每个Worker独立初始化Provider和Instrumentation
```

#### 开发热重载

Uvicorn、Watchfiles 等热重载工具可能通过新进程或模块重导入启动应用。初始化函数应：

- 输出 PID、Instrumentation 包名和版本；
- 只在实际服务进程中初始化；
- 避免模块副作用导致重复调用；
- 在进程退出时 Flush；
- 不依赖旧进程中的 `uninstrument()` 清理新进程状态。

#### Jupyter Notebook

Notebook 中反复执行初始化 Cell，容易产生：

```text
instrument()重复警告
旧Callback Manager仍持有Handler
新旧Provider混用
Exporter重复
Trace层级异常
```

实验结束后可以尝试：

```text
uninstrument()
释放旧Graph、Agent和Callback Manager
Shutdown旧Provider
```

但最可靠的恢复方式仍是重启 Kernel。

### 9.8.44 与官方包之间的迁移方法

从 OpenLLMetry 包迁移到官方：

```text
opentelemetry-instrumentation-langchain
    →
opentelemetry-instrumentation-genai-langchain
```

不应采用“直接替换包，看界面是否还有 Trace”的方式。推荐：

1. 固定旧环境并建立 Golden Trace／Golden Event；
2. 导出所有依赖旧字段的 Dashboard、Alert、成本统计、Eval 和审计查询；
3. 在独立环境安装官方包，避免同进程双 Instrumentor；
4. 使用同一组离线数据回放；
5. 对比 Span 数量、拓扑、Kind、属性、Event、Token、错误和内容策略；
6. 建立 OpenLLMetry 字段到官方 GenAI 字段的版本化 Mapper；
7. 修改下游查询并执行回归；
8. 灰度发布并监控数据缺口；
9. 保留可快速回滚的镜像；
10. 在最终镜像中枚举 Entry Point，确认只存在目标 LangChain Instrumentor。

反向迁移也应采用同样方法。

迁移比较表至少应包含：

| 对象 | 旧实现 | 新实现 | 迁移动作 |
|---|---|---|---|
| Workflow | Span名和Operation | Span名和Operation | Mapper／查询调整 |
| Agent | ID、名称、版本 | ID、名称、版本 | 重新定义Run ID边界 |
| Model | 输入输出、Usage | 输入输出、Usage | 防止Token重复 |
| Tool | Arguments、Result | Arguments、Result | 内容策略迁移 |
| Retriever | 自定义Operation | 官方Retrieval语义 | 属性改名 |
| Event | OpenLLMetry消息Event | 官方GenAI Event | EventName与Body转换 |
| Metric | 旧Meter常量 | 官方Metric | Dashboard重建 |

### 9.8.45 生产验收清单

#### 包与依赖

- [ ] 已记录完整 PyPI 包名、版本和源码 Commit；
- [ ] Python、LangChain、LangGraph、Provider 和 OTel 版本已锁定；
- [ ] `pip check` 通过；
- [ ] 镜像中不存在两套 LangChain Instrumentation；
- [ ] Entry Point 枚举结果已写入构建日志；
- [ ] 自动加载依赖范围与实际测试范围已区分。

#### 初始化与生命周期

- [ ] Instrumentation 在 Graph／Callback Manager 创建前初始化；
- [ ] 每个进程只初始化一次；
- [ ] Pre-fork、Worker、热重载和 Notebook 模式已验证；
- [ ] Provider 所有权明确；
- [ ] 退出时可以 Flush／Shutdown；
- [ ] 不依赖运行中反复 `instrument()`／`uninstrument()` 做控制面。

#### Trace、Metric 与 Event

- [ ] Workflow、Agent、LLM、Tool、Retriever 的 Span 所有权明确；
- [ ] 没有重复 LLM Span 和重复 Token；
- [ ] `run_id`／`parent_run_id` 层级正确；
- [ ] 同步、异步、流式和 Batch 路径均已验证；
- [ ] Metrics 没有高基数标签；
- [ ] Event 模式具备 LoggerProvider、Processor、Exporter 和 Collector Logs Pipeline；
- [ ] Event 与 Span 的 TraceId／SpanId 可以关联。

#### 内容与安全

- [ ] `TRACELOOP_TRACE_CONTENT` 的生产值明确；
- [ ] 请求级内容 Override 受信任策略控制；
- [ ] Prompt、Completion、Tool 参数和检索文档默认不外发；
- [ ] Metadata 使用 Allowlist；
- [ ] 多模态和大内容进入 Artifact Store；
- [ ] Collector 有二次 Redaction；
- [ ] 访问控制、保留期和审计策略已配置。

#### 兼容与运维

- [ ] 已建立 Golden Trace 和 Golden Event；
- [ ] 已监控 Instrumentation 内部异常；
- [ ] 已监控活动 Run、孤儿 Span 和 Context 清理失败；
- [ ] 已监控 Collector 拒收、队列和 Exporter 失败；
- [ ] 已验证 LangGraph 专项 Patch；
- [ ] 已准备升级和回滚矩阵；
- [ ] 后端能够按 Instrumentation Scope 区分数据来源。

### 9.8.46 本节结论

`opentelemetry-instrumentation-langchain` 应被理解为一个完整的 OpenLLMetry LangChain／LangGraph 适配包，而不是只有一个 `LangchainInstrumentor` 类。

其实际能力由以下部分共同构成：

```text
PyPI发行与Entry Point
        ↓
BaseInstrumentor生命周期
        ↓
进程级Config
        ↓
Callback Manager自动注入
        ↓
TraceloopCallbackHandler
        ↓
run_id / parent_run_id状态恢复
        ↓
LangGraph专项Patch
        ↓
模型抑制与Trace Context传播
        ↓
Span Attribute / LogRecord Event / Metric
        ↓
统一OTel SDK与OTLP管线
```

生产使用时最关键的不是安装命令，而是明确：

1. **只保留一套 LangChain 框架层 Instrumentation；**
2. **定义 LangChain、Provider SDK 和 HTTP Span 的所有权；**
3. **显式关闭或严格治理 Prompt、Tool、Retriever 正文；**
4. **把该包的 OpenLLMetry 字段映射到企业统一语义；**
5. **将 `instrument()` 视为进程级启动动作，而不是请求级开关；**
6. **通过 Scope、版本和 Golden Trace 管理升级；**
7. **监控 Fail-open 导致的静默遥测缺失；**
8. **对 LangGraph、异步、流式和 Batch 场景单独回归。**

---

## 7.10 OpenAI Python SDK 的接入机制

### 7.10.1 核心机制

OpenAI Python SDK 的 OpenTelemetry 接入主要采用：

> **Monkey Patch + Method Wrapper + Stream Proxy**

典型包装点包括：

```text
Completions.create
AsyncCompletions.create
Embeddings.create
AsyncEmbeddings.create
Responses.create
Responses.stream
Responses.retrieve
对应异步方法
```

业务代码仍然调用原 API，但运行时关系变为：

```text
业务代码
    ↓
OpenTelemetry Wrapper
    ↓
OpenAI SDK 原始方法
    ↓
HTTP Client
    ↓
OpenAI API
```

### 7.10.2 包装器获取的数据

调用前可从参数和 Client 中提取：

```text
model
temperature
max_tokens
top_p
stream
server.address
server.port
messages
tools
```

返回后提取：

```text
response.id
response.model
finish_reason
usage.input_tokens
usage.output_tokens
exception
```

### 7.10.3 典型包装流程

```python
def traced_call(original, instance, args, kwargs):
    invocation = start_span_and_extract_request(kwargs)

    try:
        result = original(*args, **kwargs)

        if is_stream(result):
            return StreamingProxy(result, invocation)

        extract_response_and_usage(result)
        invocation.stop()
        return result

    except Exception as error:
        invocation.record_exception(error)
        invocation.fail(error)
        raise
```

### 7.10.4 能看到什么

- 模型；
- 请求参数；
- Token；
- 响应 ID；
- Finish Reason；
- 流式首 Chunk 延迟；
- Chunk 间隔；
- SDK 异常；
- 某些自动重试。

### 7.10.5 看不到什么

- 哪个 Agent 为什么调用模型；
- 这是 Planning 还是 Reflection；
- 当前 Agent Loop 轮次；
- 为什么选择该模型；
- 业务成功与否；
- Tool 是否最终完成目标。

这些需要上层框架或应用层补充。

### 7.10.6 Prompt 为什么默认可能不可见

出于安全和隐私考虑，生产默认通常不应采集：

- Prompt 正文；
- Completion 正文；
- Tool Arguments；
- Tool Result；
- RAG 文档正文。

更合理的是记录：

```text
prompt.name
prompt.version
content_hash
storage_ref
content_length
truncated
```

正文进入受控对象存储，Trace 中只保存引用。

---

## 7.11 OpenAI Agents SDK 的接入机制

### 7.11.1 核心机制

OpenAI Agents SDK 是 Agent 框架，其接入机制是：

> **原生 Tracing Processor**

框架运行时会主动发出：

```text
on_trace_start
on_span_start
on_span_end
on_trace_end
```

OpenTelemetry 适配器注册 Processor：

```text
OpenAI Agents Runtime
    ↓
TracingProcessor
    ↓
OpenTelemetry Span
```

### 7.11.2 典型映射

```text
Agents Trace
    → invoke_workflow

AgentSpanData
    → invoke_agent

FunctionSpanData
    → execute_tool
```

### 7.11.3 为什么还需要 OpenAI SDK Instrumentation

Agents Processor 主要负责：

- Workflow；
- Agent；
- Function Tool。

底层模型调用需要 OpenAI Python SDK Instrumentation 补充：

```text
OpenAI Agents Instrumentation
    → Workflow / Agent / Tool

OpenAI Python SDK Instrumentation
    → Chat / Responses / Embeddings

HTTPX Instrumentation
    → HTTP
```

完整链路：

```text
invoke_workflow customer_support
└── invoke_agent support_agent
    ├── execute_tool lookup_order
    └── chat model-x
        └── HTTP POST /v1/responses
```

### 7.11.4 实现边界

框架拥有原生 Span 类型，并不代表当前 OTel 适配器已映射全部类型。某些 Handoff、Guardrail、Speech、Transcription 或特殊 Span 可能仍需：

- 新版适配器；
- 自定义 Processor；
- 应用领域埋点。

---

## 7.12 ChromaDB 的接入机制

ChromaDB 不是 Agent 框架，而是 Agent 常用的向量数据库。需要分为客户端和服务端。

### 7.12.1 Agent 侧 Chroma Client

如果通过 LangChain Retriever 调用 Chroma：

```text
retrieval product_knowledge
└── HTTP POST /collections/query
```

其中：

- `retrieval`：由 LangChain 或 RAG Runtime 产生；
- HTTP Span：由 HTTP Instrumentation 产生。

只有 HTTP Span 时，只能看到网络请求，无法知道它是一次语义检索。

### 7.12.2 Chroma Server 内部

Chroma 后端采用：

> **框架源码内建 `@trace_method` Decorator／Native Instrumentation**

典型形式：

```python
@trace_method(
    "SegmentAPI.get_collection",
    OpenTelemetryGranularity.OPERATION,
)
def get_collection(...):
    ...
```

Decorator 内部大致执行：

```python
def decorator(original):
    def wrapper(*args, **kwargs):
        if tracing_disabled:
            return original(*args, **kwargs)

        with tracer.start_as_current_span(trace_name):
            return original(*args, **kwargs)

    return wrapper
```

它可以观测：

- Collection 操作；
- Segment 操作；
- 内部查询阶段；
- 服务端耗时；
- 异常。

### 7.12.3 完整链路

```text
retrieval product_documents       ← Agent / RAG 框架
└── HTTP POST /query              ← HTTP Client
    └── HTTP Server               ← Chroma Server
        ├── frontend.query        ← Chroma 内建 Span
        ├── executor.query
        └── segment.search
```

### 7.12.4 Chroma 的 SDK 所有权问题

独立 Chroma Server 自己配置：

- TracerProvider；
- BatchSpanProcessor；
- OTLP Exporter；

是合理的，因为 Chroma Server 本身就是应用所有者。

但在同一 Python 进程中嵌入 Chroma 时，应验证：

- 是否与应用全局 Provider 冲突；
- `service.name` 是否被覆盖；
- 是否产生多个 Exporter；
- Shutdown 生命周期是否冲突；
- 是否重复导出。

---

## 7.13 其他 Agent 与 LLM 框架的机制对照

| 框架或组件 | 主要观测层 | 核心机制 | 主要可见内容 |
|---|---|---|---|
| OpenAI Python SDK | SDK 层 | Monkey Patch、Wrapper、Stream Proxy | 模型、Token、流式、异常 |
| OpenAI Agents SDK | 框架层 | Tracing Processor | Workflow、Agent、Function Tool |
| LangChain | 框架层 | Callback Manager、Handler | Chain、Model、Tool、Retriever |
| LangGraph | 框架层 | LangChain Callback、Graph Runtime | Graph、Node、Agent、Tool |
| Traceloop／OpenLLMetry `LangchainInstrumentor` | 框架层（含 SDK 传播补丁） | Callback Handler、CallbackManager Monkey Patch、LangGraph 专项 Wrapper | Chain、Graph、Task、LLM、Tool、Retriever |
| LlamaIndex | 框架层 | Dispatcher、EventHandler、SpanHandler | Agent、RAG、LLM、Retriever |
| CrewAI | 框架层 | Event Bus、Listener | Crew、Agent、Task、Tool、LLM |
| Haystack | 框架层 | Tracer Interface、Connector | Pipeline、Component |
| AutoGen | 框架层 | Native OpenTelemetry | Runtime、Agent、Tool、消息 |
| PydanticAI | 框架 + SDK | Native OTel、InstrumentedModel | Agent、模型、工具、Usage |
| Semantic Kernel | 框架层 | ActivitySource / Native OTel | Kernel Function、Connector |
| LiteLLM SDK | SDK 层 | Input / Success / Failure Callback | 模型、Usage、异常 |
| LiteLLM Proxy | 框架 + 协议 | Native OTel + FastAPI | HTTP、认证、缓存、Guardrail、模型 |
| ChromaDB | 服务／SDK 层 | `@trace_method` | Collection、Segment、查询 |
| FastAPI | 协议层 | ASGI Middleware | Route、状态码、服务端耗时 |
| HTTPX | 协议层 | Client Instrumentation | HTTP 请求、状态码、网络耗时 |
| gRPC | 协议层 | Client／Server Interceptor | RPC method、状态、延迟 |
| 自研 Agent Runtime | 应用层 | Lifecycle Hook、Manual Span | Loop、预算、权限、审批、沙箱 |

---

## 7.14 API、SDK、语义约定、OTLP 如何协同

### 7.14.1 六个角色的职责

| 组件 | 负责什么 | 不负责什么 |
|---|---|---|
| LangChain、OpenAI、ChromaDB | 执行业务、模型、检索 | 不统一遥测格式 |
| Instrumentation | 捕获并翻译框架事件 | 不负责长期存储 |
| OTel API | 创建遥测对象 | 不负责采样和导出 |
| Semantic Conventions | 规定统一名称和含义 | 不执行代码 |
| OTel SDK | 采样、处理、批量、聚合、导出 | 不理解框架语义 |
| OTLP | 传输遥测 | 不负责产生 Span |

一句话概括：

> **Instrumentation 负责翻译，API 负责写入，语义约定负责统一语言，SDK 负责运行处理，OTLP 负责运输。**

### 7.14.2 线性数据流

```text
框架行为
    ↓
Instrumentation
    ↓
OpenTelemetry API
    ↓
OpenTelemetry SDK
    ↓
OTLP Exporter
    ↓
Collector
    ↓
Backend
```

Semantic Conventions 从侧面约束 Instrumentation：

```text
Semantic Conventions
    ───────────────→ Instrumentation
```

### 7.14.3 为什么 Instrumentation 应依赖 API，而不是 SDK

Instrumentation 作者不应替用户决定：

- Sampler；
- Exporter；
- Collector；
- Backend；
- `service.name`；
- Batch 策略；
- 安全策略。

正确分工：

```text
Instrumentation 作者
    → 调用 OpenTelemetry API

应用所有者
    → 配置 OpenTelemetry SDK
```

这样同一个 LangChain 或 OpenAI Instrumentation 可以输出到不同后端。

### 7.14.4 同进程统一 SDK

推荐同一应用进程只配置一个统一 Provider：

```python
resource = Resource.create({
    "service.name": "rag-agent-service",
    "service.version": "1.3.0",
})

provider = TracerProvider(resource=resource)

provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint="http://otel-collector:4317"
        )
    )
)

trace.set_tracer_provider(provider)
```

然后让所有 Instrumentation 共享：

```python
LangChainInstrumentor().instrument(
    tracer_provider=provider
)

OpenAIInstrumentor().instrument(
    tracer_provider=provider
)
```

应用自己的手工 Span 也使用同一个 Provider。

### 7.14.5 LangChain 的协同过程

```text
LangGraph 运行
    ↓ Callback
LangChain OTel Handler
    ↓ 按 GenAI 语义约定映射
OTel API
    ↓
统一 SDK
    ↓
OTLP
    ↓
Collector
```

这里的 `LangChain OTel Handler` 可能来自 OpenTelemetry 官方 `opentelemetry-instrumentation-genai-langchain`，也可能来自 Traceloop／OpenLLMetry `opentelemetry-instrumentation-langchain`。两者不能仅凭相似类名混用；应按第 9.7 节确定唯一 Span 所有者、内容策略和语义映射。

### 7.14.6 OpenAI SDK 的协同过程

```text
responses.create()
    ↓ Monkey Patch Wrapper
提取 model / messages / tools
    ↓
OTel API 创建模型 Span
    ↓
执行原始 OpenAI SDK
    ↓
提取 usage / response / error
    ↓
SDK 处理
    ↓
OTLP
```

### 7.14.7 Chroma Server 的协同过程

```text
Chroma 内部方法
    ↓ @trace_method
OTel API 创建 Span
    ↓
Chroma 或宿主应用 SDK
    ↓
OTLP
    ↓
Collector
```

---

## 7.15 一次完整 RAG Agent 调用的观测链路

假设系统包含：

```text
LangGraph
    ├── OpenAI
    └── ChromaDB
```

### 7.15.1 运行时序

```mermaid
sequenceDiagram
    participant App as Agent 应用
    participant LG as LangGraph
    participant LCI as LangChain Instrumentation
    participant CC as Chroma Client
    participant CS as Chroma Server
    participant OAI as OpenAI SDK
    participant OI as OpenAI Instrumentation
    participant SDK as OTel SDK
    participant COL as Collector

    App->>LG: graph.invoke()
    LG-->>LCI: on_chain_start
    LCI->>SDK: API 创建 invoke_workflow Span

    LG-->>LCI: on_retriever_start
    LCI->>SDK: API 创建 retrieval Span

    LG->>CC: collection.query()
    CC->>CS: HTTP + traceparent
    CS->>SDK: 创建 Chroma 内部 Span
    CS-->>CC: 返回文档

    LG-->>LCI: on_retriever_end
    LCI->>SDK: 结束 retrieval Span

    LG->>OAI: responses.create()
    OI->>SDK: 创建 chat Span
    OAI-->>OI: 返回模型结果与 Token
    OI->>SDK: 结束 chat Span

    LG-->>LCI: on_chain_end
    LCI->>SDK: 结束 workflow Span

    SDK->>COL: OTLP 批量导出
```

### 7.15.2 最终 Trace

```text
POST /agent/run
└── invoke_workflow rag_graph
    ├── retrieval chroma
    │   └── HTTP POST /query
    │       └── Chroma HTTP Server
    │           ├── query_frontend
    │           └── vector_search
    └── chat model-x
        └── HTTP POST /v1/responses
```

### 7.15.3 每层的解释能力

| Span | 解释的问题 |
|---|---|
| `invoke_workflow` | 哪个图工作流运行 |
| `retrieval` | 为什么检索、检索什么 |
| HTTP Client | 网络请求耗时和状态 |
| Chroma 内部 Span | 向量数据库内部哪里慢 |
| `chat` | 模型、Token、响应 |
| OpenAI HTTP | 模型 API 网络尝试 |

---

## 7.16 Trace、Metric、Log、Event、Artifact 与 Eval 的职责

### 7.16.1 Trace：回答单次任务的因果关系

Trace 适合分析：

```text
总耗时 32 秒
├── 规划 3 秒
├── 模型调用 5 秒
├── 检索 2 秒
├── 测试执行 15 秒
├── 修复重试 5 秒
└── 最终输出 2 秒
```

模型逻辑 Span 应覆盖一次完整逻辑调用，包括 SDK 自动重试。

底层网络重试可以建模为：

```text
chat model-x
├── HTTP POST /responses    429
├── Event retry             backoff=1000ms
└── HTTP POST /responses    200
```

### 7.16.2 Metric：回答整体趋势

推荐指标：

```text
gen_ai.client.operation.duration
gen_ai.client.token.usage
gen_ai.client.operation.time_to_first_chunk
gen_ai.client.operation.time_per_output_chunk

gen_ai.invoke_workflow.duration
gen_ai.invoke_agent.duration
gen_ai.invoke_agent.inference_calls
gen_ai.invoke_agent.tool_calls
gen_ai.execute_tool.duration
```

应用指标：

```text
app.agent.run.count
app.agent.run.duration
app.agent.run.success

app.agent.loop.iterations
app.agent.loop.detected
app.agent.retry.count
app.agent.cancel.count
app.agent.timeout.count

app.agent.tool.success
app.agent.tool.failure
app.agent.permission.denied

app.agent.human_approval.wait_duration
app.agent.budget.exhausted
app.agent.eval.score
app.agent.cost
```

### Metric 标签

适合：

```text
agent_name
agent_version
workflow_name
provider
request_model
tool_name
tool_type
result
error_type
environment
```

不适合：

```text
run_id
trace_id
conversation_id
user_id
tool_call_id
prompt_text
file_path
raw_query
```

原因是高基数会增加聚合状态、内存和存储压力。

### 7.16.3 Log：记录结构化上下文和明细

推荐自动携带：

```text
trace_id
span_id
service.name
service.version
workflow_name
agent_name
```

Log 适合记录：

- 详细错误；
- 工具 stdout／stderr 摘要；
- 状态变更；
- 审批记录；
- 调试信息；
- 外部工件引用。

### 7.16.4 Event：记录离散生命周期事件

OpenTelemetry 中需要区分两类 Event：

```text
Log-based Event
  → LogRecord.EventName非空
  → 通过Logs Pipeline导出
  → 可独立于Trace存在
  → 支持Severity和Body

Span Event
  → 附加在Span内部
  → 随Trace导出
  → 必须依附正在记录的Span
```

对于新的 Agent 领域事件，优先考虑 Log-based Event；只需要紧贴 Span 且后端主要消费 Trace 时，可使用 Span Event，或通过 Event-to-Span-Event Bridge 兼容。

推荐 EventName：

```text
app.agent.lifecycle.started
app.agent.lifecycle.completed
app.agent.loop.iteration.started
app.agent.retry.scheduled
app.agent.loop.detected
app.agent.handoff.completed
app.agent.guardrail.triggered
app.agent.permission.decision
app.agent.human_approval.requested
app.agent.human_approval.resolved
app.agent.context.compacted
app.agent.memory.promoted
app.agent.budget.exhausted
app.agent.lifecycle.cancelled
gen_ai.evaluation.result
gen_ai.client.operation.exception
```

Event 适合瞬时事实。模型推理、工具执行、检索、Agent 运行等具有持续时间的操作仍应使用 Span。

详细数据模型、API／SDK 管线、命名、Severity、Bridge、GenAI Events 与治理规则见 [2.8 Events 详解](#28-events-详解基于-logs-数据模型的结构化事件)。

### 7.16.5 Artifact：保存可交付内容、证据和可恢复对象

Artifact 负责保存遥测之外的真实内容，例如：

```text
报告、代码补丁、图片、表格
Tool完整结果、终端输出归档
测试报告、截图、引用证据
数据集、Checkpoint、沙箱快照
```

它与 OpenTelemetry 的职责分工是：

| 对象 | 负责内容 |
|---|---|
| Artifact Store | 正文、文件字节和 Part |
| Artifact Catalog | ID、版本、哈希、状态、ACL、保留期和血缘 |
| Trace | 生成、转换、读取和发布路径 |
| Event | 创建、校验、发布、替代、过期和删除等时间点 |
| Metric | 数量、字节、耗时、错误率和隔离数量 |
| Log | 运维诊断和访问审计 |
| Eval | 通过 Artifact ID 引用评分证据和报告 |

关键原则：

- Artifact 不是 OpenTelemetry 信号；
- 正文不应通过 OTLP 发送；
- Trace 可能被采样，不能作为 Artifact Catalog；
- Span 和 Event 只保存不透明引用及小型元数据；
- Artifact ID、Version ID 和 Content Hash 不应作为 Metric 标签；
- 跨 Trace 的输入输出血缘应保存在 Catalog，Span Link 只能补充关联 SpanContext。

详细模型见 [2.9 Artifact 详解](#29-artifact-详解agent-可交付物外部内容与遥测关联)。

### 7.16.6 Eval：回答质量问题

OpenTelemetry 可以回答：

- 发生了什么；
- 哪里慢；
- 调用了多少次；
- 使用多少 Token；
- 哪个环节失败。

但它不能单独判断：

- 回答是否正确；
- 是否幻觉；
- 检索是否相关；
- 代码是否满足需求；
- 规划是否合理；
- 工具是否真正完成业务目标。

因此需要将 Eval 与 Trace 关联：

```text
trace_id
workflow_name
agent_name
agent_version
prompt_version
model
dataset_version
evaluator_name
evaluator_version
score
result
```

最终可以分析：

```text
每个成功任务平均 Token
Agent 版本成功率
模型的成本成功比
工具次数与成功率关系
循环次数与失败率关系
首 Token 延迟与取消率关系
Prompt 版本升级前后效果
```

---

## 7.17 跨线程、跨进程和跨服务上下文传播

### 7.17.1 同进程

Python 通常通过 `ContextVar` 维护当前 Span。

同步和标准异步调用可自动形成父子关系。

### 7.17.2 HTTP／gRPC

客户端：

```text
Inject traceparent / tracestate
```

服务端：

```text
Extract Context
创建 Server Span
```

### 7.17.3 MCP

MCP 的一个流式 HTTP 连接可能传输多个逻辑请求，因此不能只依赖外层 HTTP Span。

可把上下文注入 MCP 消息：

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "search-code",
    "_meta": {
      "traceparent": "00-...",
      "tracestate": "..."
    }
  },
  "id": 1
}
```

服务端从 `_meta` 中提取上下文。

典型链路：

```text
execute_tool search-code
└── mcp.client tools/call
    └── mcp.server tools/call
```

### 7.17.4 CLI 和子进程

CLI 支持 OpenTelemetry 时，父进程可通过环境变量或 IPC 传递：

```text
TRACEPARENT
TRACESTATE
```

子进程继续创建内部 Span。

CLI 不支持 OTel 时，由宿主 Runtime 创建外层 Span：

```text
invoke_agent coding_cli
├── execute_tool process_start
├── Event cli.stdout
├── Event cli.stderr
└── execute_tool process_wait
```

不要把完整标准输出放入 Span 属性，推荐：

```text
app.cli.output.bytes
app.cli.output.truncated
app.cli.output.artifact_ref
app.cli.exit_code
app.cli.signal
```

### 7.17.5 OTLP 与 Trace Context 的区别

```mermaid
flowchart LR
    subgraph A["Agent 进程"]
        A1["Client Span"]
        ASDK["OTel SDK"]
    end

    subgraph C["Chroma Server"]
        C1["Server Span"]
        C2["Internal Span"]
        CSDK["OTel SDK"]
    end

    COL["Collector"]

    A1 -->|"业务请求 + traceparent"| C1
    C1 --> C2

    ASDK -->|"OTLP"| COL
    CSDK -->|"OTLP"| COL
```

- 业务请求中的 `traceparent` 串联 Trace；
- OTLP 将 Span 发送给 Collector。

---

## 7.18 Span 所有权与重复 Span 治理

多层 Instrumentation 同时启用时容易重复。

### 7.18.1 合理嵌套

```text
invoke_agent researcher          ← Agent 框架
└── chat model-x                 ← 模型 SDK
    └── HTTP POST /responses     ← 协议层
```

三层含义不同，应保留。

### 7.18.2 不合理重复

```text
chat model-x                     ← LangChain
└── chat model-x                 ← OpenAI SDK
```

两者都表示同一次逻辑模型调用，可能导致：

- 模型调用次数翻倍；
- Token 重复统计；
- 成本重复；
- 告警失真；
- Trace 噪声。

### 7.18.3 推荐 Span 所有权

| Span 类型 | 推荐创建者 |
|---|---|
| `invoke_workflow` | Agent Runtime／LangGraph |
| `invoke_agent` | Agent 框架 |
| `plan` | Agent Runtime |
| `execute_tool` | Agent 框架／Tool Runtime |
| `retrieval` | Retriever／RAG Runtime |
| `search_memory` | Memory Runtime |
| `chat`、`embeddings` | 模型 SDK Instrumentation |
| HTTP Client／Server | HTTP Instrumentation |
| gRPC Client／Server | gRPC Instrumentation |
| Chroma 内部查询 | Chroma Server |
| 权限、预算、循环、审批 | 应用领域层 |
| Eval | Eval 系统 |

### 7.18.4 治理方式

- 禁用某层重复 Instrumentation；
- 使用抑制机制；
- 在 Collector 中过滤；
- 明确逻辑 Span 与 Transport Span；
- Token 和成本只认一个权威 Span；
- 在指标侧去重；
- 对 Span 增加 `instrumentation_scope.name`；
- 建立自动化 Trace 结构回归测试。

---

## 7.19 Prompt、Completion 与敏感数据治理

### 7.19.1 默认不采集正文

高风险内容包括：

```text
System Prompt
用户输入
模型完整输出
Tool Arguments
Tool Result
RAG 文档
源代码
文件路径
API Key
个人信息
业务机密
```

生产环境推荐默认关闭正文采集。

### 7.19.2 推荐记录元数据

```text
gen_ai.prompt.name
gen_ai.prompt.version

app.prompt.content_hash
app.prompt.storage_ref
app.prompt.length
app.prompt.truncated

app.output.content_hash
app.output.storage_ref
app.output.length
app.output.truncated
```

### 7.19.3 双层脱敏

```mermaid
flowchart LR
    A["Agent Runtime"] --> B["进程内脱敏"]
    B --> C["OTel SDK"]
    C --> D["Collector 二次脱敏"]
    D --> E["可观测后端"]
```

进程内脱敏确保秘密不离开应用；Collector 负责第二道防线。

### 7.19.4 常见策略

- Allowlist 优先于 Blocklist；
- API Key、Token、Cookie 全部删除；
- Prompt 仅在测试环境开启；
- 内容长度限制；
- 文件路径归一化或哈希；
- 文档正文放受控对象存储；
- Trace 仅保留引用；
- 独立权限和审计；
- 不把敏感内容放 Metric 标签；
- 对 Tool Arguments 做字段级脱敏。

### 7.19.5 Artifact 化内容治理

当 Prompt、Completion、Tool Result、检索文档、代码、图片或评估证据确实需要保留时，不应把完整正文直接写入通用 Trace／Log 后端，而应使用 Artifact 化外部存储：

```text
敏感内容
  → 进程内分类和脱敏
  → Artifact Store加密保存
  → Catalog记录版本、哈希、ACL和保留期
  → Span／Event只记录artifact_id和策略元数据
```

推荐：

```text
app.gen_ai.input.artifact.id
app.gen_ai.output.artifact.id
app.tool.result.artifact.id
app.retrieval.result.artifact.id
app.eval.evidence.artifact.id
```

以上均为应用自定义属性，不是当前官方稳定 GenAI Semantic Conventions。

安全要求：

- 遥测保存不透明 Artifact ID，不保存预签名 URL；
- Resolver 每次访问重新执行租户和对象级鉴权；
- 内容执行 DLP、恶意文件、MIME 和最大解压尺寸检查；
- Prompt、Completion、源代码和个人信息使用独立保留策略；
- Artifact 删除时同步清理对象、Part、派生预览、向量索引和缓存；
- Eval 引用不可变 Artifact Version，确保评分证据可重现；
- Trace 被采样或过期后，Artifact Catalog 仍应独立保持正确状态。

完整设计见 [2.9 Artifact 详解](#29-artifact-详解agent-可交付物外部内容与遥测关联)。

---

## 7.20 Collector、采样与生产部署

### 7.20.1 推荐 Agent-to-Gateway 架构

```mermaid
flowchart LR
    subgraph Client["应用侧"]
        UI["Web / Desktop / API"]
        RT["Agent Runtime"]
        CLI["CLI / Sandbox / MCP"]
        SDK["OpenTelemetry SDK"]
        UI --> RT
        RT --> CLI
        RT --> SDK
        CLI --> SDK
        AS["Artifact Store / Catalog"]
        RT -->|"正文与Manifest"| AS
        CLI -->|"大体积产物"| AS
    end

    subgraph Local["本地采集层"]
        LC["Local Collector<br/>Batch / Redaction / Buffer"]
    end

    subgraph Gateway["中心采集层"]
        GC["Gateway Collector<br/>Transform / Tail Sampling / Routing"]
    end

    subgraph Backend["存储与分析"]
        T["Trace Backend"]
        M["Metrics Backend"]
        L["Logs Backend"]
        E["Eval / Analytics"]
        X["Artifact Metadata Analytics"]
    end

    SDK -->|"OTLP"| LC
    LC -->|"OTLP + TLS"| GC
    GC --> T
    GC --> M
    GC --> L
    GC --> E
    GC --> X
```

### 7.20.2 常见 Pipeline

```yaml
receivers:
  otlp:

processors:
  memory_limiter:
  resource:
  attributes:
  redaction:
  filter:
  transform:
  tail_sampling:
  batch:

exporters:
  otlp/traces:
  otlp/metrics:
  otlp/logs:
```

### 7.20.3 可靠性配置

建议启用：

```text
sending_queue
retry_on_failure
file_storage / WAL
```

需要监控：

```text
队列使用率
发送失败
重试次数
拒绝 Span 数
Collector 内存
Exporter 延迟
持久队列磁盘
```

### 7.20.4 采样策略

Agent Trace 通常长、重、包含大量嵌套，不建议所有成功任务永久全量保存。

建议 100% 保留：

```text
error.type 存在
工作流超时
Loop 检测触发
最大迭代耗尽
Token 预算耗尽
权限拒绝
人工接管
Eval 失败
异常取消
总耗时超过阈值
工具调用次数异常
新版本灰度
```

正常成功请求按比例采样。

Tail Sampling 的关键约束：

> 同一条 Trace 的全部 Span 必须被路由到同一个 Tail Sampling 实例。

---

## 7.21 自研 Agent Runtime 的原生可观测设计

自研 Runtime 不应主要依靠 Monkey Patch 自己内部实现，而应提供正式生命周期契约。

### 7.21.1 推荐接口

```python
class AgentTracingProcessor:
    def on_workflow_start(self, event): ...
    def on_workflow_end(self, event): ...

    def on_agent_start(self, event): ...
    def on_agent_end(self, event): ...

    def on_plan_start(self, event): ...
    def on_plan_end(self, event): ...

    def on_model_start(self, event): ...
    def on_model_chunk(self, event): ...
    def on_model_end(self, event): ...

    def on_tool_start(self, event): ...
    def on_tool_end(self, event): ...

    def on_memory_search_start(self, event): ...
    def on_memory_search_end(self, event): ...

    def on_handoff(self, event): ...
    def on_permission_decision(self, event): ...
    def on_loop_iteration(self, event): ...
    def on_budget_exhausted(self, event): ...
    def on_human_approval(self, event): ...
```

### 7.21.2 推荐架构

```text
Agent Runtime
    ↓ 发布类型化领域事件
OpenTelemetryTracingProcessor
    ↓ 映射为 Span / Event / Metric
OpenTelemetry API
    ↓
统一 SDK
    ↓
OTLP
```

### 7.21.3 职责边界

Runtime 负责：

```text
Workflow
Agent
Plan
Loop
Permission
Budget
Cancellation
Memory
Handoff
Sandbox
Human Approval
Eval 关联
```

第三方 Instrumentation 负责：

```text
OpenAI 模型调用
LangGraph 框架执行
Chroma 查询
HTTP / gRPC
数据库
消息队列
```

### 7.21.4 建议领域事件模型

```text
WorkflowStarted
WorkflowCompleted
WorkflowFailed

AgentStarted
AgentCompleted
AgentFailed
AgentCancelled

PlanCreated
PlanRevised

LoopIterationStarted
LoopIterationCompleted
LoopDetected
BudgetExhausted

ToolRequested
ToolApproved
ToolDenied
ToolStarted
ToolCompleted
ToolFailed

MemorySearched
MemoryRetrieved
MemoryPromoted
MemoryUpdated

HandoffRequested
HandoffCompleted

SandboxCreated
SandboxTerminated

HumanApprovalRequested
HumanApprovalCompleted

EvaluationCompleted
```

### 7.21.5 设计原则

1. 领域事件与 OTel 解耦；
2. OTel Adapter 只是一个 Listener；
3. Event 具有稳定 Schema Version；
4. 事件携带 Run ID 和 Parent Run ID；
5. 正文与元数据分离；
6. 异常、取消、超时使用不同终止语义；
7. 明确哪些是 Span，哪些是 Event；
8. Metric 只使用低基数标签；
9. 支持测试替身和 In-Memory Exporter；
10. 对 Trace 结构做契约测试。

---

## 7.22 推荐落地路径

### 第一阶段：打通基础 Trace

先实现：

```text
invoke_workflow
invoke_agent
plan
chat
retrieval
execute_tool
```

目标是获得完整执行树。

### 第二阶段：补 Metrics 和 Logs

增加：

```text
总耗时
模型耗时
首 Chunk 延迟
Token 用量
工具耗时
工具成功率
模型调用次数
工具调用次数
循环次数
重试次数
取消次数
权限拒绝次数
```

让日志自动携带 Trace ID 和 Span ID。

### 第三阶段：补跨边界传播

覆盖：

```text
主进程 → 子 Agent
主进程 → CLI
Agent → MCP
Agent → Chroma
Agent → 沙箱
Agent → 消息队列
```

### 第四阶段：接入 Collector

增加：

```text
OTLP
Batch
Memory Limiter
Redaction
Filter
Transform
Tail Sampling
Retry
Persistent Queue
```

### 第五阶段：接入 Eval

形成：

```text
Trace + Token + Cost + Eval + User Feedback
```

### 第六阶段：治理和回归

- Span 所有权；
- 重复 Span；
- 语义约定版本；
- 性能开销；
- 敏感数据；
- 高基数；
- 采样偏差；
- Trace 结构测试；
- 指标口径测试；
- Collector 容量测试。

---

## 7.23 常见误区与排查方法

### 7.23.1 只安装 HTTP 探针

现象：

```text
POST api.openai.com/v1/responses
POST chromadb:8000/query
```

缺少：

```text
invoke_agent
chat
execute_tool
retrieval
```

原因：HTTP Instrumentation 不理解 Agent 和 GenAI 语义。

解决：安装对应框架和模型 SDK Instrumentation。

### 7.23.2 认为零侵入能看到所有内部逻辑

自动探针只能看到已知 Hook 或方法边界。

自研：

```text
Loop
预算
权限
路由
状态迁移
记忆晋升
```

仍需原生 Hook 或手工埋点。

### 7.23.3 Instrumentation 与框架版本不兼容

Monkey Patch 依赖：

- 类路径；
- 方法签名；
- 返回对象；
- 异步类型；
- Stream 类型。

框架升级后应验证：

- Instrumentation 是否加载；
- 关键 Span 是否产生；
- Token 是否正确；
- 流式 Span 是否结束；
- 是否出现异常或重复。

### 7.23.4 流式 Span 不结束

可能原因：

- Stream 未消费完；
- 未调用关闭；
- 发生取消但 Wrapper 未处理；
- 异步生成器泄漏；
- Instrumentation 版本不兼容。

### 7.23.5 Token 或成本翻倍

可能原因：

- LangChain 模型 Span；
- OpenAI SDK 模型 Span；
- 自研模型 Span；

三者重复统计。

应指定一个权威模型逻辑 Span。

### 7.23.6 两个服务都导出 OTLP，但 Trace 没串起来

原因：没有在业务请求中传播 `traceparent`。

OTLP 只负责导出，不负责业务调用上下文。

### 7.23.7 Metric 基数失控

错误标签：

```text
trace_id
run_id
conversation_id
user_id
file_path
prompt
```

应移到 Trace 或 Log。

### 7.23.8 HTTP 200 但业务失败

HTTP 200 只代表传输成功，不代表：

- Tool 达成目标；
- Agent 输出正确；
- 检索相关；
- 代码测试通过。

需要框架层、应用层和 Eval。

### 7.23.9 Prompt 看不到

通常是安全默认设置，不一定是探针失效。

检查：

- 内容采集开关；
- 环境策略；
- 脱敏 Processor；
- Collector Filter；
- Backend 权限。

### 7.23.10 嵌入式组件覆盖全局 SDK

如果框架内部自行设置全局 Provider，可能导致：

- Provider 冲突；
- Resource 覆盖；
- 多 Exporter；
- 重复发送；
- Shutdown 冲突。

应优先允许宿主应用注入 Provider。

### 7.23.11 Trace 中有 Artifact ID，但内容无法访问

常见原因：

- Artifact 已过期、删除或被新版本替代；
- Trace 记录了预签名 URL，而 URL 已失效；
- Provider File ID 被错误当成平台全局 ID；
- Catalog 与对象存储双写不一致；
- 当前调用者没有租户或对象级权限；
- Artifact 被安全扫描隔离；
- 向量索引仍返回已删除版本；
- Trace 和 Artifact 的保留期配置不一致。

排查顺序：

```text
不透明Artifact ID
  → Catalog状态和版本
  → ACL与保留策略
  → Object Store对象和哈希
  → Provider外部引用
  → 索引和缓存副本
  → Producer Trace与生命周期Event
```

不要通过延长预签名 URL 有效期来掩盖身份、Catalog 或权限模型缺陷。

---

## 7.24 术语表

| 术语 | 说明 |
|---|---|
| Trace | 一次端到端调用的完整因果链 |
| Span | Trace 中的一个操作单元 |
| Event／EventRecord | `EventName` 非空的 LogRecord；属于 Logs 数据模型，可独立于 Trace 存在 |
| Artifact | Agent 任务中可寻址、可持久化、可版本化和可验证的输入、输出、证据、中间结果或 Checkpoint；不是 OTel 信号 |
| Artifact Part | 一个 Artifact Version 中的独立内容单元，例如正文、图片、数据或引用清单 |
| Artifact Manifest | 描述 Artifact 身份、版本、Part、哈希、存储、生产者、血缘和安全策略的版本化元数据 |
| Artifact Store | 保存 Artifact 正文、文件字节和 Part 的受控存储系统 |
| Artifact Catalog | Artifact 身份、版本、状态、ACL、保留策略、哈希和引用关系的事实源 |
| Artifact Resolver | 根据不透明 Artifact 引用执行鉴权并解析实际内容位置的服务 |
| Content Addressing | 通过内容哈希标识或校验 Artifact 内容的机制 |
| Lineage／Provenance | Artifact 的输入、派生、转换、生产者、版本替代和执行证据关系 |
| Span Event | Span 生命周期中的离散事件；属于 Trace 数据模型，必须依附 Span |
| Metric | 聚合趋势数据 |
| Log | 结构化运行记录 |
| Instrumentation | 把框架行为翻译为 OTel 遥测的适配器 |
| OpenLLMetry | Traceloop 维护的开源 GenAI OpenTelemetry Instrumentation 集合，可接入 Traceloop或既有 OTel 后端 |
| Traceloop SDK | 统一初始化 OpenLLMetry Instrumentation、OTel Provider、Exporter 和 Resource 的便捷层 |
| Traceloop `LangchainInstrumentor` | `opentelemetry-instrumentation-langchain` 中的 Instrumentor，通过 Callback 注入及 LangGraph 专项 Patch 观测 LangChain／LangGraph |
| `opentelemetry-instrumentation-langchain` | Traceloop／OpenLLMetry 发布的 LangChain／LangGraph Instrumentation Distribution；导入类为 `opentelemetry.instrumentation.langchain.LangchainInstrumentor` |
| `opentelemetry-instrumentation-genai-langchain` | OpenTelemetry 项目维护的官方 GenAI LangChain Instrumentation；与 Traceloop 包不是同一实现 |
| `opentelemetry_instrumentor` Entry Point | Python OTel 自动埋点加载器发现和实例化 Instrumentor 的包元数据入口组；同名实现并存时必须治理冲突 |
| Golden Trace | 用固定输入、依赖版本和预期遥测结构验证 Span 数量、拓扑、属性、Event、Metric、Context 与敏感字段边界的契约样本 |
| Instrumentation Suppression | 通过 OTel Context 标记抑制下游同类 Instrumentation，防止同一模型调用生成重复 Span |
| Association Properties | OpenLLMetry 将业务 Metadata 写入 Span 时使用的可配置属性命名空间 |
| API | 创建遥测对象的稳定接口 |
| SDK | API 的运行时实现，负责采样、处理和导出 |
| Semantic Conventions | 统一 Span、属性、Metric 和 Event 命名的规范 |
| OTLP | OTel 原生遥测传输协议 |
| Collector | 遥测接收、处理、采样和路由组件 |
| Context | 当前调用链上下文 |
| Propagation | 跨执行边界传播 Context |
| `traceparent` | W3C Trace Context 标准头 |
| Resource | 描述产生遥测的实体，如服务名和版本 |
| Instrumentation Scope | 标识产生遥测的库及版本 |
| Sampler | 决定 Trace 是否记录 |
| Span Processor | 处理已创建或结束的 Span |
| Exporter | 把遥测发送到外部系统 |
| Head Sampling | Trace 开始时决定是否采样 |
| Tail Sampling | 收集完整 Trace 后再决定是否保留 |
| Monkey Patch | 运行时替换目标方法 |
| Callback | 框架在生命周期节点调用观察者 |
| Processor | 接收框架 Trace／Span 生命周期事件 |
| Middleware | 包裹调用并在前后执行逻辑 |
| Interceptor | 拦截协议或客户端调用 |
| Wrapper | 包装原对象并代理调用 |
| Native OTel | 框架内部直接使用 OpenTelemetry API |
| Eval | 对 Agent 输出质量和业务结果进行评测 |
| Profile | 代码级 CPU、内存、锁和调用栈等资源使用剖析信号 |
| Baggage | 随 Context 传播的键值上下文，本身不是独立存储型遥测 |
| SpanContext | 可跨边界传播的 TraceId、SpanId、TraceFlags 和 TraceState |
| SpanKind | Span 的调用角色：INTERNAL、CLIENT、SERVER、PRODUCER、CONSUMER |
| Span Link | 在非严格父子关系下关联其他 SpanContext |
| LogRecord | OpenTelemetry 统一日志数据记录；`EventName` 非空时表示 Event |
| EventName | LogRecord 中标识事件类型和结构的一等字段；OTLP 中为 `event_name` |
| `otel.event.name` | 日志 Bridge 或非 OTLP Exporter 的兼容属性，用于映射 EventName |
| Meter | 创建 Metric Instrument 的作用域对象 |
| Metric Instrument | Counter、Histogram、Gauge 等测量入口 |
| Aggregation | 将 Measurement 聚合为 Sum、Histogram、LastValue 等数据 |
| Temporality | Metric 的累计或增量时间语义 |
| View | 修改 Metric 名称、属性、聚合和 Bucket 的 SDK 配置 |
| Cardinality | Metric 属性组合形成的时间序列数量 |
| Exemplar | 将代表性 Metric 测量关联到 TraceId／SpanId |
| MetricReader | 触发 Metrics 收集并衔接 Exporter 的 SDK 组件 |
| LogRecordProcessor | 处理和批量导出 LogRecord 的 SDK 组件 |
| Schema URL | 标识遥测所遵循语义 Schema 版本的 URL |
| Receiver | Collector 中接收或拉取遥测的组件 |
| Connector | 在 Collector 两条 Pipeline 之间转换或传递信号的组件 |
| Extension | Collector 的健康检查、认证、存储等非 Pipeline 组件 |
| Pipeline | Collector 中由 Receiver、Processor、Exporter 组成的信号处理链 |
| Resource Detector | 自动检测服务、进程、主机、容器、云环境等 Resource 的组件 |

---

## 7.25 参考资料

以下资料均为本次会话整理过程中涉及的主要官方或上游来源。

### OpenTelemetry 核心

- OpenTelemetry 概览：<https://opentelemetry.io/docs/what-is-opentelemetry/>
- OpenTelemetry Specification：<https://opentelemetry.io/docs/specs/otel/>
- OpenTelemetry 客户端架构：<https://opentelemetry.io/docs/specs/otel/overview/>
- Signals：<https://opentelemetry.io/docs/concepts/signals/>
- Traces：<https://opentelemetry.io/docs/concepts/signals/traces/>
- Metrics：<https://opentelemetry.io/docs/concepts/signals/metrics/>
- Logs：<https://opentelemetry.io/docs/concepts/signals/logs/>
- Baggage：<https://opentelemetry.io/docs/concepts/signals/baggage/>
- Profiles：<https://opentelemetry.io/docs/concepts/signals/profiles/>
- Resource：<https://opentelemetry.io/docs/specs/otel/resource/>
- InstrumentationScope：<https://opentelemetry.io/docs/specs/otel/common/instrumentation-scope/>
- Trace API：<https://opentelemetry.io/docs/specs/otel/trace/api/>
- Trace SDK：<https://opentelemetry.io/docs/specs/otel/trace/sdk/>
- Metrics API：<https://opentelemetry.io/docs/specs/otel/metrics/api/>
- Metrics SDK：<https://opentelemetry.io/docs/specs/otel/metrics/sdk/>
- Logs Specification：<https://opentelemetry.io/docs/specs/otel/logs/>
- Instrumentation Library Guidelines：<https://opentelemetry.io/docs/specs/otel/library-guidelines/>
- Semantic Conventions：<https://opentelemetry.io/docs/specs/semconv/>
- Telemetry Schemas：<https://opentelemetry.io/docs/specs/otel/schemas/>
- OTLP Specification：<https://opentelemetry.io/docs/specs/otlp/>
- Context Propagation：<https://opentelemetry.io/docs/concepts/context-propagation/>
- Propagators API：<https://opentelemetry.io/docs/specs/otel/context/api-propagators/>
- Sampling：<https://opentelemetry.io/docs/concepts/sampling/>
- Collector Architecture：<https://opentelemetry.io/docs/collector/architecture/>
- Collector Configuration：<https://opentelemetry.io/docs/collector/configuration/>
- Collector Components：<https://opentelemetry.io/docs/collector/components/>
- Collector Scaling：<https://opentelemetry.io/docs/collector/scaling/>
- Python Propagation：<https://opentelemetry.io/docs/languages/python/propagation/>
- Python Zero-code Instrumentation：<https://opentelemetry.io/docs/zero-code/python/>
- Python Logs API：<https://opentelemetry-python.readthedocs.io/en/latest/api/_logs.html>
- OpenTelemetry Python Changelog：<https://github.com/open-telemetry/opentelemetry-python/blob/main/CHANGELOG.md>

### GenAI Semantic Conventions

- GenAI Semantic Conventions 仓库：<https://github.com/open-telemetry/semantic-conventions-genai>
- GenAI 规范总览：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md>
- GenAI Agent Spans：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md>
- GenAI Model Spans：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md>
- GenAI Metrics：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md>
- GenAI Events：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md>
- GenAI Exceptions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-exceptions.md>
- GenAI Attribute Registry：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md>
- OpenAI Provider Conventions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/openai.md>
- Anthropic Provider Conventions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/anthropic.md>
- AWS Bedrock Provider Conventions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md>
- Azure AI Inference Provider Conventions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/azure-ai-inference.md>
- MCP Semantic Conventions：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md>
- MCP 2026-07-28 对齐 Issue：<https://github.com/open-telemetry/semantic-conventions-genai/issues/437>
- GenAI Metrics Model：<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/model/gen-ai/metrics.yaml>

### Python Instrumentation

- OpenTelemetry Python Contrib：<https://github.com/open-telemetry/opentelemetry-python-contrib>
- OpenTelemetry Python GenAI：<https://github.com/open-telemetry/opentelemetry-python-genai>
- OpenAI Instrumentation：<https://github.com/open-telemetry/opentelemetry-python-genai/tree/main/instrumentation/opentelemetry-instrumentation-genai-openai>
- OpenAI Agents Instrumentation：<https://github.com/open-telemetry/opentelemetry-python-genai/tree/main/instrumentation/opentelemetry-instrumentation-genai-openai-agents>
- LangChain Instrumentation：<https://github.com/open-telemetry/opentelemetry-python-genai/tree/main/instrumentation/opentelemetry-instrumentation-genai-langchain>

### Traceloop／OpenLLMetry

- OpenLLMetry 仓库：<https://github.com/traceloop/openllmetry>
- `opentelemetry-instrumentation-langchain` PyPI：<https://pypi.org/project/opentelemetry-instrumentation-langchain/>
- `opentelemetry-instrumentation-langchain` 包 README：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/README.md>
- 0.62.3 PyPI Provenance 对应源码快照：<https://github.com/traceloop/openllmetry/tree/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain>
- 包依赖、Extra 与 `opentelemetry_instrumentor` Entry Point：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/pyproject.toml>
- 包内模块目录：<https://github.com/traceloop/openllmetry/tree/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain>
- `LangchainInstrumentor` 初始化、Patch 与 Unpatch：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/__init__.py>
- LangChain Callback Handler：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/callback_handler.py>
- LangGraph 专项 Patch：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/patch.py>
- LangGraph 图结构提取：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/langgraph_utils.py>
- Instrumentor 进程级共享配置：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/config.py>
- Span、消息、Tool、Token 与响应规范化：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/span_utils.py>
- OTel Log Event Emitter：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/event_emitter.py>
- Event 数据模型：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/event_models.py>
- 内容开关、Event 模式与 Fail-open 工具：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/utils.py>
- Provider 识别：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/opentelemetry/instrumentation/langchain/vendor_detection.py>
- 包级测试目录：<https://github.com/traceloop/openllmetry/tree/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/tests>
- Context Token 生命周期测试：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/tests/test_context_token_lifecycle.py>
- Batch Metadata 测试：<https://github.com/traceloop/openllmetry/blob/9965d248308340ab47f29ccf84b360ebf3fde41a/packages/opentelemetry-instrumentation-langchain/tests/test_batch_metadata.py>
- OpenTelemetry Python `BaseInstrumentor`：<https://github.com/open-telemetry/opentelemetry-python-contrib/blob/main/opentelemetry-instrumentation/src/opentelemetry/instrumentation/instrumentor.py>
- OpenTelemetry 官方 `opentelemetry-instrumentation-genai-langchain` PyPI：<https://pypi.org/project/opentelemetry-instrumentation-genai-langchain/>
- OpenTelemetry 官方包 `pyproject.toml`：<https://github.com/open-telemetry/opentelemetry-python-genai/blob/main/instrumentation/opentelemetry-instrumentation-genai-langchain/pyproject.toml>
- OpenTelemetry 官方 LangChain GenAI Instrumentation：<https://github.com/open-telemetry/opentelemetry-python-genai/tree/main/instrumentation/opentelemetry-instrumentation-genai-langchain>

### 框架资料

- OpenAI Agents Tracing：<https://openai.github.io/openai-agents-python/tracing/>
- LangChain Custom Middleware：<https://docs.langchain.com/oss/python/langchain/middleware/custom>
- LlamaIndex Observability：<https://developers.llamaindex.ai/python/framework/module_guides/observability/>
- Haystack Tracing：<https://docs.haystack.deepset.ai/docs/tracing>
- AutoGen Telemetry：<https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/framework/telemetry.html>
- Semantic Kernel Observability：<https://learn.microsoft.com/en-us/semantic-kernel/concepts/enterprise-readiness/observability/>
- LiteLLM Callbacks：<https://docs.litellm.ai/docs/observability/callbacks>
- LiteLLM OpenTelemetry v2：<https://docs.litellm.ai/docs/observability/opentelemetry_v2>
- Chroma Observability：<https://docs.trychroma.com/guides/deploy/observability>
- Chroma OpenTelemetry 实现：<https://github.com/chroma-core/chroma/blob/main/chromadb/telemetry/opentelemetry/__init__.py>

### Artifact、A2A、MCP 与框架资料

- OpenTelemetry Agentic Systems Artifact 提案：<https://github.com/open-telemetry/semantic-conventions-genai/issues/35>
- OpenTelemetry Evaluation Evidence Artifact 讨论：<https://github.com/open-telemetry/semantic-conventions-genai/issues/470>
- A2A 最新规范：<https://a2a-protocol.org/latest/specification/>
- A2A Definitions：<https://a2a-protocol.org/latest/definitions/>
- A2A Life of a Task：<https://a2a-protocol.org/latest/topics/life-of-a-task/>
- LangChain Messages／ToolMessage Artifact：<https://docs.langchain.com/oss/python/langchain/messages>
- LangChain MCP Adapter：<https://docs.langchain.com/oss/python/langchain/mcp>
- MCP Tools：<https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- MCP Resources：<https://modelcontextprotocol.io/specification/2026-07-28/server/resources>
- MCP Schema：<https://modelcontextprotocol.io/specification/2026-07-28/schema>
- OpenAI Code Interpreter：<https://platform.openai.com/docs/guides/tools-code-interpreter>
- OpenAI Image Generation：<https://platform.openai.com/docs/guides/image-generation>
- Chroma Adding Data：<https://docs.trychroma.com/docs/collections/add-data>

### Collector 与生产治理

- Agent-to-Gateway Deployment：<https://opentelemetry.io/docs/collector/deploy/other/agent-to-gateway/>
- Collector Resiliency：<https://opentelemetry.io/docs/collector/resiliency/>
- Security Best Practices：<https://opentelemetry.io/docs/security/config-best-practices/>
- Tail Sampling Processor：<https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md>

---

## 结语

Agent 可观测性不是“给模型调用加一个 Trace”这么简单。一个完整体系需要同时覆盖：

```text
框架语义
模型 SDK
传输协议
应用领域逻辑
跨服务上下文
统一语义约定
采样与脱敏
Artifact存储、版本与血缘
质量评测
```

最合理的职责分工是：

- **框架层解释 Agent 做了什么；**
- **SDK 层解释调用了什么能力；**
- **协议层解释请求如何传输；**
- **应用层解释为什么这么做；**
- **Instrumentation 负责翻译；**
- **Traceloop／OpenLLMetry 等第三方 Instrumentation 负责补充框架适配，但必须治理语义版本、内容采集与重复 Span；**
- **同一进程只保留一个权威 LangChain Instrumentor，并把 Distribution、Entry Point、Provider、内容策略和 Golden Trace 纳入发布治理；**
- **OpenTelemetry API 负责统一写入；**
- **Semantic Conventions 负责统一表达；**
- **SDK 负责处理和导出；**
- **OTLP 负责运输；**
- **Trace Context 负责跨边界串联；**
- **Collector 负责生产治理；**
- **Artifact Store 与 Catalog 负责保存真实内容、版本、权限和血缘；**
- **Eval 负责判断结果质量并引用不可变证据 Artifact。**

只有把这些部分组合起来，OpenTelemetry 才能从普通的调用链监控，升级为 Agent 执行分析、故障定位、成本治理、安全审计、Artifact 交付追踪和质量闭环的统一基础设施。

## 7.26 本书的 OTel 用件对照表

全书用到的 OTel 机制一表收束——也是"读完本附录回正文"的导航：

| OTel 机制 | 本书位置 | 用来做什么 |
|---|---|---|
| Span 树 + 低基数命名 | 第 14 章 2.1/2.6 | 四层追踪树（task.run/agent.turn/tool.call） |
| Counter / Histogram | 第 14 章 2.3/§3 | 指标模板"类型"栏与桥接器仪表选择 |
| Resource | 第 14 章 §3 | service.name 与租户归因 |
| BatchSpanProcessor + OTLP | 第 14 章 §3 | 生产导出形态 |
| traceparent 传播 | 第 14 章 2.6、第 18 章 2.7、第 21 章 | 跨工具/跨 Agent/跨网关的链路贯通 |
| baggage | 第 14 章 2.6、第 19 章 | tenant/session_id/graph_run 归因随行 |
| Collector（脱敏/tail_sampling） | 第 14 章 2.4/坑 3、第 20 章 | 出边界脱敏、失败会话全保留 |
| GenAI 语义约定 | 第 14 章 2.4/§4 [C-04] | gen_ai.* 属性与内容采集开关 |
| Logs 关联 trace_id | 第 14 章 §4 | 消息内容采集的正确承载 |
| span links | 第 19 章（归因） | 批任务关联多来源 Trace 而不伪造父子 |

---

> **使用提示**：与其他附录的分工——1 讲模型机制、2 讲方法论、3 记来源、4 列产品、5 辨异同、6 索引图版、**7 详解 OTel 与 Agent 观测**、8 上手 DeepEval、9 评测观测平台选型、10 上手 Mem0、11 详解记忆晋升机制、12 盘点 Coding Agent 赛道、13 盘点可观测赛道、14 盘点评估赛道、15 盘点 Memory 赛道、16 盘点自进化赛道、17 盘点多 Agent 赛道、18 盘点 MCP 生态、19 盘点沙箱赛道、20 盘点 RAG 赛道、21 盘点 LLM Wiki 赛道、22 盘点 Loop Engineering 赛道、23 解析 Pi 源码、24 解析 Claude Code 源码、25 解析 Codex 源码、26 解析 OpenCode 源码。对照阅读：OTel 本体详解（7.2）对第 14 章用法与 [C-28]/[C-04]、四层观测模型（7.6）对第 14 章 2.1 四层 Span 树、Agent 对象映射（7.5）对第 12 章事件模型、敏感数据治理（7.19）对第 14 章 2.6 内容三级与第 13/20 章、生产部署与采样（7.20）对第 14 章坑 3、自研 Runtime 观测（7.21）对第 12 章六大件与贯穿项目 otel_bridge、平台选型见附录 9/13。顺序建议：先读第 14 章带着问题回来查；7.26 用件对照表是"读完回正文"的导航。
