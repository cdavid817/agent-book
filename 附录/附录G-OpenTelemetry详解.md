# 附录 G：OpenTelemetry 详解

> 定位：**OTel 本身的机制兜底**。正文第 14 章讲"Agent 怎么用 OTel"（四层 Span、GenAI 语义约定、采样策略、方案阵营），本附录讲"OTel 本身是什么"——数据模型、SDK 流水线、上下文传播、Collector 与语义约定，供没有 OTel 背景的读者补课、有背景的读者查细节。词条式组织，每节末尾标注正文的消费位置；规范与文档入口见 [C-28]。

---

## G.1 是什么：观测标准化的收敛点

OpenTelemetry 由 OpenTracing 与 OpenCensus 两个前辈项目于 2019 年合并而来，托管在 CNCF，是当前**供应商中立的遥测标准**事实上的唯一候选——API、SDK、传输协议、语义约定四件套全部开放，几乎所有观测后端（开源与商业）都支持接收它的数据。对本书读者，这个中立性就是第 14 章"语义兼容即反锁定"的制度基础：**埋点写一次，后端随便换**。

一句话记住它的野心与边界：OTel 标准化的是**遥测数据的产生、加工与搬运**，不做存储与查询——后端（追踪库、时序库、日志库）仍是你自己选的（第 14 章四阵营）。

## G.2 数据模型：三支柱的字段级认识

**Trace / Span。** 一条 Trace 是一棵 Span 树，由同一个 `trace_id`（16 字节）串联；每个 Span 有 `span_id`（8 字节）与 `parent_span_id`。Span 的字段面：**name**（低基数操作名——第 14 章 `agent.turn` 命名纪律的出处）、**kind**（`INTERNAL` / `SERVER` / `CLIENT` / `PRODUCER` / `CONSUMER`——队列场景用后两者，第 21 章 MQ 集成的 Span 该标 `CONSUMER`）、**attributes**（键值对，语义约定的挂载点）、**events**（Span 内的时间点标记，如"重试第 2 次"）、**links**（跨 Trace 的弱关联——批处理任务关联多个来源 Trace 时用它，不伪造父子关系）、**status**（`OK` / `ERROR` + 描述）。

**Metrics。** 六种仪表（instrument），按"同步/异步 × 语义"记：同步三种——`Counter`（只增，如 token 消耗）、`UpDownCounter`（可增减，如在途会话数）、`Histogram`（分布，如轮次/时延——第 14 章"长尾禁均值"的载体）；异步三种——`ObservableCounter` / `ObservableUpDownCounter` / `ObservableGauge`（回调式采样，适合"当前值"类读数如队列深度）。两个易错概念：**Gauge 记"此刻是多少"，Counter 记"累计发生了多少"**——选错仪表聚合就是错的；**时间性（temporality）** 分 delta（区间增量）与 cumulative（累计值），导出时由 SDK/后端协商，跨后端迁移时对不上号常出在这里。

**Logs。** LogRecord = 时间戳 + severity + body + attributes + **trace 上下文字段**（`trace_id` / `span_id`）——最后一项是三支柱互通的关键：日志挂上 trace_id，排障就能从 Span 一键跳到当时的日志。第 14 章 GenAI 内容采集"以日志承载而非塞 Span 属性"的建议，机制上正是利用这条关联。

**正文消费位置**：第 12 章（事件是 Span 原料）、第 14 章 2.1/2.2/2.6（四层树、指标口径、span 命名）、第 19 章（links 与 graph_run 归因）。

## G.3 API / SDK 分离与信号流水线

**API 与 SDK 是刻意分离的两个包**：库代码只依赖 API（`get_tracer().start_span(...)`）——没有装 SDK 时这些调用是零开销空操作；**部署方**在进程入口装配 SDK，决定采样、加工与导出。这就是"框架与库可以放心内建埋点"的原因，也是第 14 章桥接器"循环零侵入"的同款思想。

SDK 内部是一条流水线（以 Trace 为例）：

- **TracerProvider**：全局装配点，携带 **Resource**（进程级属性：`service.name`、`service.version`、部署环境——第 14 章 `RESOURCE` 即此；所有信号共享，多租户的 tenant 归因也常挂这里）；
- **Sampler**：头部采样决策——`always_on` / `always_off` / `traceidratio`（按比例）/ `parentbased_*`（跟随父 Span 的决定，跨服务一致的关键）。注意：**尾部采样不在 SDK 里**，那是 Collector 的活（G.5，第 14 章坑 3）；
- **SpanProcessor**：`SimpleSpanProcessor`（逐条同步导出，只配调试）与 **`BatchSpanProcessor`**（缓冲批量异步导出，生产唯一正解——第 14 章代码用的就是它）；
- **Exporter**：OTLP（标准）、console（调试）或厂商专有——生产统一 OTLP，把"接谁家后端"的决策推迟到 Collector。

Metrics 与 Logs 各有对称的 Provider/Reader/Exporter 结构（第 14 章的 `MeterProvider` + `MetricReader` 即是）。

**OTLP（OpenTelemetry Protocol）**：统一的导出线协议，gRPC（默认 4317 端口）与 HTTP/protobuf（4318）两种载体，三支柱同协议——这是"换后端不改埋点"的技术保证。

## G.4 上下文传播：traceparent、baggage 与传播器

跨进程的 Trace 连续性靠 **W3C Trace Context** 标准。`traceparent` 头的四段结构值得拆开记一次：

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             │  │                                │                │
             版本 trace-id(32 hex)               parent-span-id    flags(01=已采样)
```

配套三件：**tracestate**（各厂商的附加状态，透传即可）；**baggage**（业务键值对的随行机制——`tenant`、`session_id`、`graph_run` 跨服务传播的标准载体；纪律见第 14 章 2.6：明文出现在每一跳，敏感值绝不入）；**Propagator**（注入/提取的执行者——HTTP 头、消息属性、A2A `_meta` 都是它的载体，第 18 章跨协议透传的机制层）。

Agent 场景的特殊之处：传播不只跨 HTTP——跨队列（消息属性带 traceparent，第 21 章）、跨挂起（interrupt 恢复后接回原 trace，第 18 章）、跨组织（A2A 委派，第 18 章 2.7）。**每引入一种新载体，先回答"traceparent 从哪进、从哪出"**——这是多 Agent 排障不退化成"各查各的日志"的前提（第 14 章 2.6）。

## G.5 Collector：遥测的数据工程层

Collector 是独立进程的遥测管道，配置由三类组件拼成流水线：**receiver**（收，OTLP/各协议）→ **processor**（加工）→ **exporter**（发，可多路）；另有 **connector**（把一条流水线的输出接成另一条的输入——如 spanmetrics：从 Trace 实时衍生 RED 指标）。一份最小配置骨架：

```yaml
receivers:
  otlp:
    protocols: { grpc: {}, http: {} }

processors:
  memory_limiter: { limit_mib: 1024 }        # 自保：内存超限丢数据不丢进程
  batch: {}                                   # 批量导出，背压友好
  attributes/scrub:                           # 脱敏：出边界前统一执行（第 14/20 章纪律）
    actions:
      - { key: user.email, action: delete }
  tail_sampling:                              # 尾部采样：会话结束后按状态决定保留（第 14 章坑 3）
    policies:
      - { name: errors, type: status_code, status_code: { status_codes: [ERROR] } }
      - { name: sample-ok, type: probabilistic, probabilistic: { sampling_percentage: 10 } }

exporters:
  otlp/backend: { endpoint: backend:4317 }

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, tail_sampling, attributes/scrub, batch]
      exporters: [otlp/backend]
```

常用 processor 速查：`batch`（必备）、`memory_limiter`（必备，放最前）、`attributes` / `transform`（改删属性——脱敏与规范化）、`filter`（按条件丢弃）、`tail_sampling`（尾部采样，contrib 发行版）、`resource`（补资源属性）。**部署两档**：agent 模式（每节点边车，就近收取、本地打批）与 gateway 模式（集中集群，统一脱敏/采样/路由）——生产常见两级串联，敏感数据处理集中在 gateway（审计一个点，第 14 章图 3 的位置）。

一句话定位：**Collector 之于遥测，如同第 21 章的查询网关之于数仓**——所有出入流量的必经点，治理逻辑集中于此而不散在应用里。

## G.6 语义约定与稳定性

**语义约定（Semantic Conventions）** 规定属性怎么命名：通用域（`service.*`、`http.*`、`db.*`）多已 **stable**；**GenAI 域（`gen_ai.*`）处于 Development 阶段**——属性名相对稳定但未定稿，版本以 [C-04] 为准（第 14 章的 pin 纪律）。两条使用规则：自定义属性先查约定再造词（第 14 章"规范属性与自定义属性分开登记"）；SDK 与约定的版本经 **Schema URL** 声明，升级 semconv 时后端可据此做属性名迁移。

## G.7 本书的 OTel 用件对照表

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

> **使用提示**：与其他附录的分工——A 讲模型机制、B 讲方法论、C 记来源、D 列产品、E 辨异同、F 索引图版、**G 详解 OTel**。第 14 章是"用法"，本附录是"原理"；顺序建议先读第 14 章带着问题回来查。
