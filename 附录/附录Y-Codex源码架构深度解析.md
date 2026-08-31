# 附录 Y：OpenAI Codex 源码架构深度解析

> 定位：**OpenAI Codex 本地运行时的整仓源码解析**（全文收录）。分析对象是 `openai/codex` 官方开源仓库（Rust Workspace），快照锁定 `main` 提交 `0ae94fd`（2026-08-28 提交，2026-08-29 生成，仓库入口见 [C-35]）；文中源码链接均指向固定提交，仓库快速演进不影响快照结论。全文区分"源码事实 / 设计解释 / 演进推断"三级证据。与附录 W（Pi，TypeScript）、附录 X（Claude Code 还原源码，TypeScript）互为对照——三套 Coding Agent 运行时对同类问题（Agent Loop、工具治理、沙箱审批、会话持久化、MCP 扩展）的不同工程答案，Codex 的差异点是 Rust 内核与跨平台沙箱纵深。第六篇（第 23–25 章）的通用机制是阅读框架。

---

### 文档说明

这不是一份只复述 README 的功能介绍，而是一份围绕 **OpenAI Codex 本地运行时为何能够同时支撑交互式终端、非交互自动化、App Server、MCP、远程控制、多 Agent、审批与跨平台沙箱** 的源码级技术报告。文档把项目拆成六条可独立验证的主线：

1. **控制面**：CLI/TUI/App Server 如何创建、恢复、分叉、驱动和关闭 Thread；
2. **推理面**：一次 Turn 如何在模型请求、流式响应、工具调用、工具结果回填之间形成闭环；
3. **能力面**：ToolRouter、ToolRegistry、ToolOrchestrator 如何把模型可见工具映射为受控执行；
4. **安全面**：PermissionProfile、审批策略、Guardian、SandboxManager、网络代理如何共同形成纵深防御；
5. **数据面**：JSONL rollout、SQLite 派生索引、ThreadStore 抽象如何兼顾可回放性与查询性能；
6. **扩展面**：MCP、Skill、Plugin、Hook、Code Mode、多 Agent 与扩展 API 如何接入共享内核。

文中所有源码路径均以固定提交为准。示例链接采用如下形式：

```text
https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/<path>#Lx-Ly
```

由于仓库仍在快速演进，本文不把 `main` 分支之后的行为视为已存在能力。涉及未来方向的内容均放在“演进推断”小节，并说明推断依据。

---

## 第一篇·全局认识

## Y.1 项目定位：Codex 不是一个单一 CLI，而是一套本地 Agent 运行时

### Y.1.1 从产品表象到源码本质

从使用者视角看，Codex 最直观的形态是一个在终端中启动的编码助手；从源码结构看，它更接近一套“**共享核心 + 多种宿主适配器**”的本地 Agent 平台。仓库顶层的 Rust workspace 同时包含 `cli`、`tui`、`exec`、`app-server`、`mcp-server`、`core`、`protocol`、`thread-store`、`state`、`sandboxing`、`linux-sandbox`、`network-proxy`、`skills`、`plugin`、`hooks`、`code-mode`、`agent-*` 等大量 crate。这种拆分说明项目关注的并非单纯的命令行交互，而是稳定的运行时边界、跨宿主协议、安全执行、会话恢复与可扩展能力。

**源码事实**：`codex-rs/Cargo.toml` 将几十个 crate 纳入统一 workspace，并使用 Rust 2024 edition。工作区 lint 对 `await_holding_lock`、`unwrap_used`、`expect_used` 等高风险模式采用严格策略，反映出项目对并发正确性和失败语义的重视。

**设计解释**：如果所有能力都堆在一个 CLI 可执行程序里，那么 App Server、非交互执行器和 TUI 会各自复制会话管理、模型调用、工具执行、审批、日志持久化逻辑。Codex 通过把 `core` 设计为无界面的共享内核，把差异限制在输入适配和事件消费层，从而使多个产品形态共享同一套 Agent Loop 和安全策略。

```mermaid
flowchart TB
    User[用户或上层系统]

    subgraph Hosts[宿主与入口适配层]
        CLI[Multitool CLI]
        TUI[交互式 TUI]
        Exec[非交互 codex exec]
        AppServer[App Server]
        MCPServer[MCP Server]
        Remote[Remote Control / Exec Server]
    end

    subgraph Shared[共享运行时]
        Protocol[codex-protocol\nOp / Event / Item]
        Core[codex-core\nThread / Turn / Agent Loop]
        Tools[Tool Router / Registry / Orchestrator]
        Security[Approval / Guardian / Sandbox]
        Persistence[ThreadStore / Rollout / State]
        Extensions[MCP / Skill / Plugin / Hook / Code Mode]
    end

    subgraph Infra[基础设施]
        Model[OpenAI Responses / Realtime]
        OS[Shell / PTY / File System / Git]
        DB[(JSONL + SQLite)]
        Proxy[Managed Network Proxy]
    end

    User --> Hosts
    Hosts --> Protocol
    Protocol --> Core
    Core --> Tools
    Core --> Persistence
    Tools --> Security
    Tools --> Extensions
    Core --> Model
    Security --> OS
    Persistence --> DB
    Security --> Proxy
```

### Y.1.2 三个核心名词：Thread、Turn、Item

Codex 的主要领域模型可以压缩为 **Thread → Turn → Item**。

- **Thread** 是可长期存在、可恢复、可分叉、可归档的会话容器。它拥有线程标识、配置快照、历史记录、rollout 路径、会话级服务与状态。
- **Turn** 是一次由输入触发、直到产生最终结果或被中断为止的执行事务。一个 Turn 内可能进行多次模型采样和多次工具调用。
- **Item** 是事件流中可观察、可持久化、可重放的最小语义单元，例如用户消息、助手消息、推理片段、命令执行、文件变更、MCP 调用、子 Agent 活动和计划更新。

这种模型比“消息列表”更适合 Agent。传统聊天只需要记录 user/assistant 消息，而编码 Agent 必须表达命令开始与结束、增量输出、审批等待、工具失败、补丁进度、上下文压缩、子 Agent 通信等状态。把这些行为统一建模为 Item，再通过 EventMsg 发给客户端，就能在不改变核心循环的前提下让 TUI、App Server 和其他宿主各自渲染。

```mermaid
classDiagram
    class Thread {
      +ThreadId id
      +ThreadConfigSnapshot config
      +Session session
      +RolloutPath rollout
      +resume()
      +fork()
      +archive()
      +shutdown()
    }

    class Turn {
      +String turn_id
      +TurnContext context
      +TurnStartOptions options
      +run()
      +steer()
      +interrupt()
    }

    class Item {
      <<union>>
      UserMessage
      AgentMessage
      Reasoning
      CommandExecution
      FileChange
      McpToolCall
      Plan
      SubAgentActivity
    }

    class Event {
      +String id
      +EventMsg msg
    }

    Thread "1" o-- "0..*" Turn
    Turn "1" o-- "0..*" Item
    Item --> Event : 生命周期与增量
```

### Y.1.3 共享内核与宿主适配器

`codex-core` 的一个重要约束是避免直接写 stdout/stderr。核心通过操作输入、事件输出以及抽象服务与外界交互。TUI 可以把事件渲染成终端组件，`exec` 可以把事件转换为标准输出和进程退出码，App Server 可以把事件映射为 JSON-RPC 通知。这个约束不是代码洁癖，而是可复用性的前提。

```mermaid
flowchart LR
    subgraph InputAdapters[输入适配]
        Key[键盘/鼠标]
        Args[命令行参数]
        RPC[JSON-RPC 请求]
        MCP[MCP 请求]
    end

    subgraph Boundary[统一边界]
        Op[Op / TurnInputRequest]
        Event[EventMsg 流]
    end

    subgraph Kernel[Codex Core]
        TM[ThreadManager]
        CT[CodexThread]
        S[Session]
        Loop[run_turn / sampling loop]
    end

    subgraph OutputAdapters[输出适配]
        Widget[TUI Widget]
        Stdout[stdout / JSONL]
        Notify[App Server 通知]
        ToolResult[MCP Tool Result]
    end

    Key --> Op
    Args --> Op
    RPC --> Op
    MCP --> Op
    Op --> TM
    TM --> CT
    CT --> S
    S --> Loop
    Loop --> Event
    Event --> Widget
    Event --> Stdout
    Event --> Notify
    Event --> ToolResult
```

### Y.1.4 为什么主体迁移到 Rust

仓库仍保留 npm 包装和跨平台分发相关内容，但核心功能已经以 Rust workspace 为主体。源码体现出的收益包括：

- 用强类型枚举定义 `Op`、`EventMsg`、`ResponseItem`、审批结果和沙箱策略，降低协议状态组合的歧义；
- 用 Tokio 组织事件循环、并行工具 Future、后台 writer、状态 watcher 和可取消任务；
- 用所有权与显式生命周期约束会话级、轮次级、步骤级对象，减少跨 Turn 泄漏状态；
- 便于把 Linux seccomp/Landlock、macOS Seatbelt、Windows Restricted Token 封装为同一执行接口；
- 通过 workspace 拆分控制编译依赖和职责边界，同时仍可共享内部类型。

必须注意，Rust 并不会自动消除竞态和死锁。Codex 仍通过 lint 禁止持锁跨 `await`，通过 `Weak<ThreadManagerState>` 打破 Agent 控制面的循环引用，通过每线程 writer/lifecycle lock 保证 rollout 顺序，通过 watch/mpsc channel 明确状态传播。这些“额外设计”才是可靠性的关键。

---

## Y.2 源码快照与阅读方法

### Y.2.1 为什么固定到提交而不是只写 main

主分支在高频变化。若只标注 `main`，几天后路径、字段和默认行为可能已经变化，文档中的行号无法复核。因此本文固定到 `0ae94fdd49b05ee7faa4d984d06a68492cb32b54`。该提交的主题是为 TTY 子进程响应终端查询，它也侧面展示了 Codex 已经深入处理伪终端协议边界，而非只把命令交给普通管道。

```mermaid
flowchart TD
    A[选择分析日期] --> B[读取默认分支最新提交]
    B --> C[记录完整 SHA 与提交时间]
    C --> D[所有源码链接固定 SHA]
    D --> E[按入口反向追踪调用链]
    E --> F[以协议事件交叉核对]
    F --> G[以持久化和测试反向验证]
    G --> H[事实 / 解释 / 推断分层写作]
```

### Y.2.2 五层阅读法

本文采用五层阅读法，而不是按目录机械翻译：

**第一层：入口层。** 从 `cli/src/main.rs` 的子命令路由出发，确认交互 TUI、`exec`、App Server、MCP Server、远程控制、诊断、沙箱调试等入口最终调用哪些共享库。

**第二层：会话层。** 顺着 `ThreadManager → CodexThread → Session → run_turn` 追踪线程创建、恢复、分叉、输入提交、事件接收、转向和中断。

**第三层：能力层。** 从模型返回的 FunctionCall/ToolSearchCall/CustomToolCall 进入 ToolRouter，继续追踪 Registry、Hook、Orchestrator、审批、沙箱、执行环境和结果回填。

**第四层：数据层。** 从事件和 canonical items 进入 rollout recorder，再核对 ThreadStore、LocalThreadStore、SQLite state runtime、列表查询、恢复、分叉与回滚。

**第五层：风险层。** 对所有越界行为追问：谁决定模型可见工具？谁决定是否审批？谁决定沙箱类型？失败时是否 fail closed？网络和文件权限冲突如何合并？后台任务如何终止？

### Y.2.3 事实置信度标记

| 标记 | 含义 | 示例 |
|---|---|---|
| 源码事实 | 能由固定提交中的类型、函数、注释或测试直接验证 | `AgentControl` 在根线程树范围共享 |
| 设计解释 | 对多个源码事实的架构归纳 | JSONL 是事实日志，SQLite 是查询投影 |
| 演进推断 | 根据模块边界、TODO、兼容层或近期提交作出的方向判断 | App Server 可能继续成为桌面与 IDE 的稳定宿主边界 |
| 风险提示 | 源码能力在部署或配置不当时可能产生的问题 | 禁用沙箱后审批仍不能等同于隔离 |

### Y.2.4 分析边界

本文覆盖开源仓库中的本地 Codex 运行时，不推断闭源服务端的内部实现。模型端的路由、配额、训练、安全分类器和服务端工具执行，只有在客户端协议明确暴露时才讨论。对配置项的描述以源码结构为主；官方在线文档仍是面向最终用户的配置权威，且可能晚于或早于当前源码快照。

---

## Y.3 仓库与工作区全景

### Y.3.1 按职责而不是按目录理解 crate

数十个 crate 初看会显得碎片化，但可以归为九组：

| 组别 | 代表 crate | 主要职责 |
|---|---|---|
| 产品入口 | `cli`、`tui`、`exec`、`app-server`、`mcp-server` | 接收不同形态输入并消费统一事件 |
| 核心运行时 | `core`、`core-api`、`protocol`、`history` | Thread/Turn/Item、模型循环、协议与历史 |
| 工具执行 | `tools`、`shell-command`、`unified-exec`、`apply-patch`、`file-search` | 把模型意图转为可控系统操作 |
| 安全治理 | `sandboxing`、`linux-sandbox`、`execpolicy`、`network-proxy`、`secrets`、`guardian` 相关模块 | 权限、审批、隔离、网络与敏感信息保护 |
| 持久化 | `rollout`、`thread-store`、`state` | 追加日志、索引、恢复、查询、分叉、归档 |
| 扩展生态 | `mcp-*`、`skills`、`plugin`、`core-plugins`、`hooks`、`extension-api` | 外部工具、技能、插件、生命周期扩展 |
| 多 Agent | `agent-graph-store`、`agent-identity`、`agent-roles`、core agent modules | 子 Agent 创建、角色、图谱、通信和预算 |
| 模型与认证 | `model-provider`、`models-manager`、`login`、`backend-client`、`aws-auth` | 模型元数据、认证、服务端交互 |
| 基础设施 | `otel`、`analytics`、`trace`、大量 `utils/*`、`v8`、`websocket-client` | 可观测性、跨平台工具与运行时支撑 |

```mermaid
flowchart TB
    subgraph Product[产品入口层]
      CLI[cli]
      TUI[tui]
      EXEC[exec]
      AS[app-server]
      MS[mcp-server]
    end

    subgraph Domain[核心领域层]
      CORE[core]
      API[core-api]
      PROTO[protocol]
      HIST[history]
    end

    subgraph Capability[能力与治理层]
      TOOLS[tools / unified-exec]
      SAFE[sandboxing / execpolicy]
      EXT[mcp / skills / plugin / hooks]
      AGENT[agent-*]
    end

    subgraph Data[数据与基础设施层]
      ROLL[rollout]
      STORE[thread-store]
      STATE[state]
      OTEL[otel / analytics / trace]
      UTIL[utils / v8 / websocket]
    end

    Product --> Domain
    Domain --> Capability
    Domain --> Data
    Capability --> Data
```

### Y.3.2 为什么要拆成大量小 crate

Codex 的模块拆分并非单纯追求“微包”。它解决了四类工程问题。

第一，**依赖方向**。协议类型可以被入口和核心共同依赖，但协议层不应依赖 TUI；ThreadStore 接口可以被核心依赖，但本地 SQLite 实现不应反向污染协议。

第二，**平台裁剪**。Linux 沙箱、Windows restricted token、macOS Seatbelt 需要不同系统依赖。单独 crate 可以按目标平台编译，避免把所有系统 API 传播到整个工作区。

第三，**可执行边界**。`codex-linux-sandbox`、`code-mode-host`、App Server 等可能以独立进程运行。独立 crate 能让进程边界和库边界一致。

第四，**测试隔离**。协议序列化、沙箱命令变换、rollout 解析、SQLite 迁移、TUI 快照可以在不同层级测试。大型单 crate 往往迫使所有测试加载过多依赖。

### Y.3.3 依赖方向原则

```mermaid
flowchart LR
    Protocol[protocol\n稳定数据合同]
    CoreAPI[core-api\n较窄核心接口]
    Core[core\n业务编排]
    Entrypoints[cli / tui / exec / app-server]
    StoreAPI[thread-store\n存储接口]
    StoreImpl[local store / state / rollout]
    ExtAPI[extension-api]
    ExtImpl[skills / plugins / hooks / MCP]

    Entrypoints --> Core
    Entrypoints --> Protocol
    Core --> Protocol
    Core --> CoreAPI
    Core --> StoreAPI
    StoreImpl --> StoreAPI
    Core --> ExtAPI
    ExtImpl --> ExtAPI
    Core --> ExtImpl
```

架构上最值得学习的是：**协议稳定性、运行时编排和具体基础设施没有揉在一起**。不过这并不意味着所有依赖都已经理想化。`core` 仍然是聚合中心，承担大量跨模块协调；随着功能增长，它容易成为编译时间和认知复杂度热点。项目通过逐步抽出 `core-api`、`core-plugins`、扩展 API 和工具 crate 来缓解，而不是一次性重写。

### Y.3.4 编译与质量约束

工作区使用统一依赖版本与 lint 规则。禁止无意的 `unwrap`/`expect` 有助于迫使代码表达错误传播或显式不变量；禁止持锁跨 await 可降低异步死锁；格式化和依赖检查在 CI 中独立运行。源码还采用大量枚举和 newtype，避免把线程 ID、会话 ID、调用 ID、路径和普通字符串混用。

这种严格性也会增加样板代码，但对一个能执行 shell、改文件、联网并长时间运行的 Agent 来说，失败路径比 happy path 更重要。宁可多写显式分支，也不能让一个隐含 panic 在后台 writer 或审批流程中破坏会话一致性。

---

## Y.4 总体架构与数据流

### Y.4.1 逻辑分层

Codex 可以理解为七层：宿主层、协议层、会话控制层、推理循环层、工具治理层、操作系统执行层、持久化与观测层。层次之间并不是同步调用链那么简单，事件通道、Future、watch channel、后台 writer 和流式响应共同构成异步数据流。

```mermaid
flowchart TB
    L1[宿主层\nTUI / Exec / App Server / MCP]
    L2[协议层\nOp / EventMsg / TurnItem]
    L3[会话控制层\nThreadManager / CodexThread / Session]
    L4[推理循环层\nrun_turn / ModelClientSession / Prompt]
    L5[工具治理层\nRouter / Registry / Hooks / Orchestrator]
    L6[执行与安全层\nApproval / Guardian / Sandbox / Proxy]
    L7[数据与观测层\nRollout / ThreadStore / SQLite / OTEL]

    L1 <--> L2
    L2 <--> L3
    L3 --> L4
    L4 <--> L5
    L5 <--> L6
    L3 --> L7
    L4 --> L7
    L5 --> L7
    L6 --> L7
```

### Y.4.2 一次完整请求的主路径

```mermaid
sequenceDiagram
    autonumber
    actor U as 用户
    participant H as TUI/Exec/App Server
    participant CT as CodexThread
    participant S as Session
    participant R as run_turn
    participant MC as ModelClientSession
    participant TR as ToolRouter
    participant TO as ToolOrchestrator
    participant SB as Sandbox/OS
    participant PS as Rollout/ThreadStore

    U->>H: 提交输入
    H->>CT: submit / start_or_steer_turn
    CT->>S: Op 或 TurnInputRequest
    S->>PS: 记录用户输入与 TurnStarted
    S->>R: 创建 TurnContext 并启动 Turn
    R->>MC: 构建 Prompt 并流式采样
    MC-->>R: ResponseItem 增量

    alt 返回工具调用
        R->>TR: 解析 FunctionCall/CustomToolCall
        TR->>TO: dispatch ToolInvocation
        TO->>TO: 权限合并与审批判定
        opt 需要审批
            TO-->>H: ApprovalRequested Event
            H-->>TO: 用户或 Guardian 决策
        end
        TO->>SB: 按平台和权限执行
        SB-->>TO: 输出/错误/拒绝
        TO-->>TR: ToolOutput
        TR-->>R: 回填模型上下文
        R->>MC: 下一次采样
    else 返回最终助手消息
        R-->>S: TurnComplete
    end

    S->>PS: 追加 canonical items / flush
    S-->>CT: EventMsg 流
    CT-->>H: next_event
    H-->>U: 渲染结果
```

### Y.4.3 控制流、事件流与持久化流分离

一次操作同时存在三条流：

- **控制流**：用户提交 `Op`，核心启动/转向/中断任务；
- **事件流**：核心产生 `EventMsg`，宿主异步消费；
- **持久化流**：可回放语义项写入 rollout，并更新查询索引。

它们不能简单合并。比如命令输出可能连续产生许多增量事件，但 canonical rollout 未必逐字节持久化；审批请求需要先发事件等待响应，不能阻塞整个宿主事件循环；SQLite 更新失败时，JSONL 仍应保留可恢复事实，不能让索引成为唯一真相。

```mermaid
flowchart LR
    subgraph Control[控制流]
      U[User] --> O[Op]
      O --> C[Core]
    end

    subgraph Events[事件流]
      C --> E[EventMsg]
      E --> A1[TUI]
      E --> A2[Exec]
      E --> A3[App Server]
    end

    subgraph Persistence[持久化流]
      C --> I[Canonical Items]
      I --> J[(JSONL Rollout)]
      I --> X[(SQLite Projection)]
    end

    J -.重建.-> X
```

### Y.4.4 核心架构不变量

从源码可以归纳出几条重要不变量：

1. **每个 Turn 使用独立的模型会话状态**，避免粘性路由、响应 ID 或传输回退状态泄漏到下一轮；
2. **工具结果必须在 Hook、审批与沙箱决策之后才能被接受**，不能绕过治理链路直接写回模型；
3. **rollout 追加顺序是恢复语义的一部分**，热线程的元数据更新需经由 live thread 保序；
4. **Guardian 异常必须拒绝而不是默认放行**；
5. **权限冲突遵循拒绝优先**，文件和网络能力不能靠后出现的宽松配置覆盖前面的禁止；
6. **核心不绑定具体界面**，宿主只消费协议事件；
7. **中断和关闭必须可传播到活动 Turn、工具执行和后台任务**，否则“退出界面”不等于“停止副作用”。

这些不变量是理解后续源码的主轴。

---

## 第二篇·入口、协议与会话控制

## Y.5 CLI 总入口：MultitoolCli 如何成为能力路由器

### Y.5.1 顶层 CLI 的角色

`codex-rs/cli/src/main.rs` 并不直接实现 Agent 推理，而是定义顶层参数、解析子命令、准备配置覆盖并把控制权交给相应 crate。无子命令时进入交互式 TUI；有子命令时则路由到非交互执行、代码审查、认证、MCP、App Server、远程控制、沙箱调试、功能开关或线程管理等能力。

这是一种典型的 **multitool binary** 设计。它的优点是安装一个 `codex` 可执行文件即可暴露多种工作模式；代价是顶层命令枚举会持续增长，因此真正的实现必须下沉到独立 crate，避免 `main.rs` 变成业务中心。

从当前提交可以看到的主要命令族包括：

- 交互与执行：默认 TUI、`exec`、`review`、`resume`、`fork`；
- 账户与模型：`login`、`logout`、模型与功能诊断；
- 扩展：`mcp`、`plugin`、MCP Server；
- 服务化：App Server、Remote Control、Exec Server、Responses API 代理；
- 会话管理：队列、归档、删除、迁移、取消归档；
- 系统工具：completion、update、doctor、sandbox、debug、execpolicy、apply；
- 云端和实验能力：cloud、features 等。

```mermaid
flowchart TD
    Start[codex 进程启动] --> Parse[解析 MultitoolCli]
    Parse --> HasSub{是否有子命令}
    HasSub -- 否 --> TUI[启动交互式 TUI]
    HasSub -- 是 --> Kind{命令族}

    Kind -->|exec/review| NonInteractive[非交互执行适配器]
    Kind -->|app-server| AppServer[JSON-RPC 服务]
    Kind -->|mcp-server| MCPServer[MCP 服务]
    Kind -->|login/logout| Auth[认证管理]
    Kind -->|sandbox/debug/doctor| Diagnostics[系统与安全诊断]
    Kind -->|resume/fork/archive| Threads[线程生命周期]
    Kind -->|remote/exec-server| Remote[远程与进程服务]

    TUI --> Core[共享 codex-core]
    NonInteractive --> Core
    AppServer --> Core
    MCPServer --> Core
    Threads --> Core
```

### Y.5.2 配置覆盖的入口语义

CLI 不只是选择子命令，还承担配置叠加的第一步：命令行参数通常具有最高优先级，之后才是会话或项目配置、用户配置和默认值。顶层入口把通用覆盖参数向下传递，使 TUI 和 `exec` 不必各自重新解析同一组模型、审批、沙箱与工作目录设置。

**设计解释**：把配置解析与运行时配置解析分开很重要。命令行层只收集“用户表达”，真正的有效配置需要考虑配置层、管理员约束、平台能力和 feature gate。若在 CLI 阶段就把字符串直接解释为最终权限，会绕过 `requirements.toml` 等管理策略。

### Y.5.3 命令路由的错误边界

顶层进程必须把三类失败区分开：

1. 参数或配置错误：在任何副作用前返回；
2. 运行时启动错误：例如认证失败、状态库无法初始化、沙箱组件缺失；
3. Turn 执行错误：已经创建会话，需要通过事件和退出码向上层表达。

`exec` 的退出码与 TUI 的错误展示不同，但它们应该来自同一个核心错误语义。App Server 更进一步，需要把内部错误转换为 JSON-RPC error 或线程/轮次通知。分层的价值正在于“错误表现不同，错误来源一致”。

---

## Y.6 TUI：事件驱动的交互宿主

### Y.6.1 TUI 不是核心逻辑的所有者

`codex-rs/tui` 使用终端 UI 组件、输入事件和状态模型组织交互。它负责键盘映射、输入编辑、历史区渲染、命令输出、审批弹窗、状态栏、模型与模式选择，但不重新实现 Agent Loop。TUI 把用户输入提交给 `CodexThread`，再持续调用事件接收接口，将 `EventMsg` 归约为界面状态。

```mermaid
flowchart LR
    Terminal[终端输入事件] --> App[TUI App]
    App --> Chat[ChatWidget / Composer]
    Chat --> Submit[提交 TurnInput]
    Submit --> Thread[CodexThread]
    Thread --> Core[Session / run_turn]
    Core --> Events[EventMsg Stream]
    Events --> Reducer[TUI 事件归约]
    Reducer --> History[历史区]
    Reducer --> Status[状态栏]
    Reducer --> Modal[审批/选择弹窗]
    History --> Render[终端绘制]
    Status --> Render
    Modal --> Render
```

### Y.6.2 输入与输出为什么要异步

一个 Turn 可能持续数分钟，并同时产生模型 token、命令 stdout/stderr、文件补丁进度、审批请求和子 Agent 状态。若 TUI 用“提交后同步等待最终字符串”的方式工作，界面会冻结，也无法及时接受 Ctrl-C、方向键、批准/拒绝等输入。因此 TUI 的主循环必须并行处理：

- 终端输入事件；
- 核心事件；
- 定时刷新或动画；
- 后台任务完成；
- 终端尺寸变化和焦点变化；
- 退出和恢复终端模式。

这种结构要求界面状态更新是幂等或至少可按事件顺序重放，否则增量消息和最终 Item 可能重复渲染。协议中同时存在 `ItemStarted`、delta、`ItemCompleted`，正是为了让宿主既能实时展示，又能在完成后收敛成稳定对象。

### Y.6.3 转向、排队与中断

Codex 允许在活动 Turn 中追加输入。源码层面并非简单创建第二个并行 Turn，而是通过 `start_or_steer_turn` 判断当前状态：空闲时启动新 Turn；执行中时把输入作为 steer 提交给活动任务。这样可以让用户纠正方向，而不破坏同一轮工具调用和上下文关系。

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Running: 提交新输入
    Running --> Running: steer / 追加约束
    Running --> WaitingApproval: 工具请求审批
    WaitingApproval --> Running: 批准或 Guardian 放行
    WaitingApproval --> Running: 拒绝并回填工具错误
    Running --> Interrupting: 用户中断
    Interrupting --> Idle: 取消传播完成
    Running --> Idle: TurnCompleted
    Idle --> ShuttingDown: 退出
    ShuttingDown --> [*]
```

### Y.6.4 终端状态恢复

TUI 需要切换 raw mode、alternate screen、光标可见性和粘贴模式。异常退出时若不恢复，用户终端会处于不可用状态。因此终端 UI 的资源管理必须类似 RAII：启动时进入受控模式，正常或异常离开作用域时恢复。最新提交围绕 TTY 子进程查询响应继续补强终端边界，也说明“终端不是普通字节流”是项目长期面对的复杂性。

---

## Y.7 非交互 Exec：面向自动化的稳定外壳

### Y.7.1 与 TUI 的相同点和不同点

`codex exec` 与 TUI 共享 Thread、Turn、模型、工具、安全和持久化逻辑，但输出契约不同：自动化调用者更关心机器可解析结果、标准输出/错误、退出码、最终消息或 JSONL 事件，而不是可交互组件。

| 维度 | TUI | Exec |
|---|---|---|
| 输入 | 持续交互、可 steer | 通常一次性参数/标准输入 |
| 输出 | 富终端、增量渲染 | 文本、JSON/JSONL、退出码 |
| 审批 | 可弹窗等待用户 | 必须预配置、拒绝或使用自动审查 |
| 生命周期 | 长会话、多轮 | 常见为单任务，也可恢复线程 |
| 中断 | 键盘事件 | 信号、超时、上层进程取消 |
| 主要用户 | 人 | CI、脚本、其他 Agent、IDE 后端 |

### Y.7.2 自动化模式的关键约束

非交互模式最危险的误区是“没人点击审批，因此默认允许”。正确做法相反：在无法交互时，审批策略必须预先确定，无法获得授权的操作应拒绝或由明确启用的 Guardian 处理。输出也不能只打印最终自然语言，因为调用者需要区分模型失败、工具失败、权限拒绝、用户中断与协议错误。

```mermaid
sequenceDiagram
    participant CI as CI/脚本
    participant Exec as codex exec
    participant Core as Codex Core
    participant Tool as ToolOrchestrator
    participant Policy as Approval/Sandbox Policy

    CI->>Exec: prompt + 配置 + cwd
    Exec->>Core: 创建线程并启动 Turn
    Core->>Tool: 请求执行命令
    Tool->>Policy: 判断权限与审批
    alt 已授权且可沙箱
        Policy-->>Tool: allow + sandbox
        Tool-->>Core: 执行结果
    else 需要人工审批但无交互通道
        Policy-->>Tool: deny / fail closed
        Tool-->>Core: 结构化拒绝结果
    end
    Core-->>Exec: EventMsg + 最终状态
    Exec-->>CI: stdout/JSONL + exit code
```

### Y.7.3 Exec 作为集成基座

由于 Exec 是“最薄的人机界面”，它适合作为其他系统的子进程集成点。但对长期稳定集成，App Server 往往更合适：它能维持连接、管理多个 Thread、收发通知并避免反复冷启动。文档后文会说明两者的边界：Exec 偏单任务进程契约，App Server 偏长生命周期服务协议。

---

## Y.8 App Server：把核心运行时服务化

### Y.8.1 初始化握手

App Server 使用请求/响应与服务器通知组织协议。连接建立后，客户端必须先发送一次初始化请求，再发送 `initialized` 通知；初始化前调用其他方法或重复初始化会被拒绝。这类握手用于协商客户端信息、能力和兼容行为，避免服务端在未知版本假设下直接创建线程。

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant T as Transport
    participant MP as MessageProcessor
    participant AS as AppServer

    C->>T: initialize(params)
    T->>MP: ClientRequest
    MP->>MP: 校验尚未初始化
    MP->>AS: 构建连接级上下文
    AS-->>C: initialize result
    C->>T: initialized notification
    T->>MP: ClientNotification
    MP->>MP: 标记连接可用
    C->>T: thread/start 等请求
    T->>MP: 进入正常路由
```

### Y.8.2 Thread、Turn、Item 的远程化表达

App Server 把核心领域模型投射成远程 API：客户端可启动、恢复、分叉和读取 Thread，启动或中断 Turn，并通过通知观察 Item 的开始、增量和完成。核心并不直接知道 JSON-RPC 客户端；MessageProcessor 和线程状态映射负责把远程请求转换为核心操作，再把核心事件转换为协议通知。

```mermaid
flowchart LR
    Request[JSON-RPC Request]
    Processor[MessageProcessor]
    ThreadState[Connection Thread State]
    Manager[ThreadManager]
    Thread[CodexThread]
    Event[Core EventMsg]
    Notify[JSON-RPC Notification]

    Request --> Processor
    Processor --> ThreadState
    ThreadState --> Manager
    Manager --> Thread
    Thread --> Event
    Event --> Processor
    Processor --> Notify
```

### Y.8.3 连接级状态与线程级状态

App Server 至少要区分三种状态：

- **连接级状态**：是否初始化、客户端能力、传输、请求 ID 与响应通道；
- **线程级状态**：远端客户端订阅或拥有的 `CodexThread`、转发任务和最近事件；
- **全局管理状态**：ThreadManager、认证、模型列表、配置和持久化服务。

如果把这些状态混在一个全局互斥锁中，一个慢客户端就可能阻塞所有线程。源码采用异步消息处理、线程映射和事件转发任务，使不同 Thread 的事件可以独立流动。

### Y.8.4 为什么 App Server 是关键架构边界

TUI 适合人直接使用，Exec 适合单任务自动化；App Server 则适合作为桌面端、IDE、浏览器壳或其他本地客户端的后端。它的价值包括：

- 核心进程可以管理多个 Thread；
- 客户端重绘或重连不必重启模型会话；
- 结构化 Item 比解析 ANSI 终端输出可靠；
- 可通过能力协商演进协议；
- 审批、实时会话、模型切换和线程读取都有明确方法。

**演进推断**：随着产品形态增加，App Server 很可能继续承担“稳定宿主 API”的角色，而 CLI/TUI 更像官方参考客户端。但这一判断是基于当前模块化方向，不代表公开路线承诺。

---

## Y.9 协议层：Op、EventMsg 与 Item 生命周期

### Y.9.1 为什么需要独立 protocol crate

协议类型被核心、TUI、Exec、App Server、测试工具和可能的 SDK 共同使用。如果它们定义在 `core` 内部，宿主就必须依赖整个运行时；如果各自复制，序列化和兼容性会漂移。`codex-protocol` 通过强类型合同把“能向核心做什么”和“核心可能发生什么”分开。

- `Op`：客户端或宿主向核心提交的操作；
- `Event`：带关联 ID 的事件信封；
- `EventMsg`：事件联合类型；
- `TurnItem`/`ResponseItem`：对话与工具语义项；
- 各类 `...Event`：具名事件负载；
- 配置和策略枚举：审批、会话来源、协作模式、推理设置等。

### Y.9.2 操作面

操作大体可以归为以下几类：

| 类别 | 典型语义 |
|---|---|
| Turn 控制 | 用户输入、追加输入、steer、中断、关闭 |
| 审批响应 | 对命令、文件变更、MCP 或网络请求给出决策 |
| 上下文操作 | 压缩、回退、恢复、加载历史 |
| 配置变更 | 模型、推理、权限、协作模式、人格等 |
| 多 Agent | Agent 间通信、子线程控制 |
| 交互响应 | 工具询问、终端输入、MCP elicitation |

### Y.9.3 事件面

`EventMsg` 的体量很大，因为它覆盖完整 Agent 生命周期。除普通文本外，还包括：

- 助手消息、推理、计划的增量与完成；
- Turn 开始、完成、取消、错误和警告；
- 命令执行开始、输出增量、结束；
- 文件补丁开始、更新、完成和 Turn diff；
- 审批请求与结果；
- MCP 工具调用、服务器启动状态、资源与 elicitation；
- 模型重路由、验证、认证恢复和速率限制；
- 上下文压缩、token 使用和历史变更；
- 子 Agent 活动与 Agent 间通信；
- 终端交互和统一执行进程状态；
- 安全缓冲、审核与 Guardian 结果。

```mermaid
mindmap
  root((EventMsg))
    会话
      ThreadConfigured
      TurnStarted
      TurnComplete
      ShutdownComplete
    内容
      AgentMessage
      Reasoning
      Plan
      Delta
    工具
      CommandExecution
      PatchApply
      MCPCall
      ToolSearch
    治理
      ApprovalRequest
      GuardianAssessment
      Warning
      Error
    上下文
      TokenCount
      Compaction
      HistoryChanged
    多Agent
      SubAgentActivity
      InterAgentCommunication
    系统
      Auth
      ModelReroute
      TerminalInteraction
```

### Y.9.4 Item 生命周期

一个可渲染项目通常经历 Started → Delta* → Completed。并非每种 Item 都必须有增量，但统一生命周期使客户端容易实现占位、进度更新和最终收敛。

```mermaid
stateDiagram-v2
    [*] --> NotSeen
    NotSeen --> Started: ItemStarted
    Started --> Streaming: 首个 Delta
    Streaming --> Streaming: 后续 Delta
    Started --> Completed: 无增量直接完成
    Streaming --> Completed: ItemCompleted
    Started --> Failed: Error/Cancelled
    Streaming --> Failed: Error/Cancelled
    Completed --> [*]
    Failed --> [*]
```

### Y.9.5 兼容事件与新旧客户端

源码中存在从完成事件转换为 legacy events 的逻辑，说明协议在演进中保留兼容桥。兼容桥的合理位置应在协议/适配层，而不是让核心业务同时维护两套状态机。对客户端作者来说，应优先采用新 Item 生命周期，并把 legacy 事件视为迁移支持，避免重复处理同一语义。

### Y.9.6 关联 ID 的重要性

事件信封、Turn ID、工具 call ID、Item ID 和 Thread ID 共同构成因果链。没有这些 ID，多个并行工具调用、多个线程和多 Agent 活动会在客户端混在一起。可靠客户端不应只依赖事件到达顺序，而应使用关联 ID 将增量归并到正确实体，并对未知事件保持前向兼容。

---

## Y.10 ThreadManager 与 CodexThread：会话控制面的两级抽象

### Y.10.1 ThreadManager 的职责

`ThreadManager` 是进程内线程注册表和生命周期协调器。它负责创建、恢复、分叉、查找、移除线程，连接持久化状态与 live `CodexThread`，并在子 Agent 场景中提供共享管理状态。它不是数据库 DAO，也不是单个会话对象，而是“线程控制平面”。

```mermaid
classDiagram
    class ThreadManager {
      +start_thread(options) NewThread
      +resume_thread(id, history)
      +fork_thread(id, point)
      +get_thread(id) CodexThread
      +remove_thread(id)
      +shutdown()
    }

    class ThreadManagerState {
      +threads Map~ThreadId,CodexThread~
      +thread_store ThreadStore
      +auth_manager AuthManager
      +model_manager ModelsManager
    }

    class CodexThread {
      +session Arc~Session~
      +submit(op)
      +next_event()
      +start_or_steer_turn()
      +interrupt()
      +config_snapshot()
    }

    ThreadManager --> ThreadManagerState
    ThreadManagerState "1" o-- "0..*" CodexThread
```

### Y.10.2 NewThread 返回值为什么不只是 ID

创建线程后，调用者通常需要线程对象、初始配置事件、rollout 路径或初始状态。若 API 只返回 ThreadId，宿主还要再查一次，并可能错过启动阶段事件。因此 `NewThread` 聚合必要结果，让入口能在创建后立即开始订阅。

### Y.10.3 live thread 与 cold thread

一个 Thread 可能正在内存中运行，也可能只存在于 rollout/SQLite 中。对热线程做元数据变更时，源码强调通过 live thread 路径写入，以保持事件和 rollout 的顺序；对冷线程才可直接走 store。这个区别非常关键：

- 对冷线程，存储本身就是唯一活动状态；
- 对热线程，内存状态、事件顺序和持久化 writer 共同定义一致性。

```mermaid
flowchart TD
    Request[修改线程元数据] --> Lookup{线程是否 live}
    Lookup -- 是 --> Live[发送到 CodexThread/Session]
    Live --> Ordered[按会话顺序追加 rollout]
    Ordered --> Projection[更新 SQLite 投影]
    Lookup -- 否 --> Store[直接调用 ThreadStore]
    Store --> Projection
```

### Y.10.4 CodexThread 是宿主句柄

`CodexThread` 持有 `Arc<Session>` 与事件/输入通道，为宿主提供小而稳定的操作集合。其配置快照包含模型、模型提供方、服务等级、审批策略、审查器、权限配置、环境、工作区根目录、是否临时、推理设置、人格、协作模式、会话来源、父线程和历史来源等信息。

这种快照有两个用途：

1. 宿主可以展示当前线程配置，而不直接读取 Session 内部可变状态；
2. 子 Agent、恢复和分叉流程可以继承或调整明确字段，而不是复制整个 Session 对象。

### Y.10.5 提交新轮与 steer

`start_or_steer_turn` 把“开始”和“转向”统一成一个外部入口，并返回 Started、Steered 或 NotSubmitted 等结果。这样宿主无需先查询状态再提交，避免典型的 TOCTOU 竞态：查询时空闲、提交时已运行。

```mermaid
sequenceDiagram
    participant H as Host
    participant CT as CodexThread
    participant S as Session State
    participant T as Active Turn

    H->>CT: start_or_steer_turn(request)
    CT->>S: 原子检查当前任务
    alt 无活动 Turn
        S->>T: 创建并启动
        CT-->>H: Started(turn_id)
    else 有活动 Turn 且允许 steer
        S->>T: 注入新输入
        CT-->>H: Steered(turn_id)
    else 状态不允许
        CT-->>H: NotSubmitted(reason)
    end
```

### Y.10.6 引用关系与 Weak

多 Agent 控制面需要访问 ThreadManagerState，但 Session 又被 ThreadManagerState 中的 CodexThread 持有。若双方都用强 `Arc`，会形成：

```text
ThreadManagerState -> CodexThread -> Session -> SessionServices -> ThreadManagerState
```

源码用 `Weak<ThreadManagerState>` 打破环。这一细节体现了 Rust 引用计数架构中的常见原则：全局注册表拥有实体，实体如需回调注册表，应持弱引用；注册表销毁后，回调应返回明确错误，而不是让隐藏强引用延长整个运行时寿命。

### Y.10.7 关闭语义

可靠关闭至少包含：停止接受新输入、取消活动 Turn、终止工具进程、等待或放弃事件转发、flush rollout、更新最终状态、移出注册表。只从 HashMap 删除线程并不足够，因为后台任务可能仍持有 Session。ThreadManager 的移除与 Session 的 shutdown 需要协作，避免“界面消失但命令仍在执行”。

---

## Y.11 Session：线程级服务容器与状态协调器

### Y.11.1 Session 的定位

Session 是单个 Thread 的运行时中心。它持有配置、历史、活动任务状态、模型客户端、事件发送器、rollout、工具与扩展服务、MCP 状态、多 Agent 控制、环境和各种会话级缓存。它比 `CodexThread` 更内部，也比单个 Turn 生命周期更长。

```mermaid
flowchart TB
    Session[Session]
    Session --> Config[Session Config / Dynamic State]
    Session --> History[Conversation History]
    Session --> Model[ModelClient]
    Session --> Rollout[Rollout Recorder]
    Session --> Tools[Tool Services]
    Session --> MCP[MCP Connections]
    Session --> Ext[Extensions / Skills / Plugins / Hooks]
    Session --> Agent[AgentControl]
    Session --> EventTx[Event Sender]
    Session --> Active[Active Turn Task]
```

### Y.11.2 SessionServices 与依赖聚合

大型 Session 若直接声明所有依赖，构造函数会极其复杂。源码通过 services/manager 类对象聚合认证、模型、存储、扩展、网络、遥测等依赖。这类似应用服务容器，但不是动态 IoC：依赖仍以 Rust 类型显式表达。

需要警惕“服务容器变成全局万能对象”。Codex 通过 TurnContext、StepContext 等更短生命周期对象裁剪每一步真正需要的数据，避免所有函数都拿到整个 Session 后任意访问。

### Y.11.3 会话级、轮次级与步骤级状态

| 生命周期 | 代表对象 | 适合保存 | 不应保存 |
|---|---|---|---|
| Session | `Session`、`ModelClient` | 线程配置、历史、连接池、持久化句柄 | 单次工具调用的临时授权 |
| Turn | `TurnContext`、`ModelClientSession` | Turn ID、模型会话状态、token 预算、环境快照 | 下一个 Turn 的粘性响应状态 |
| Step | `StepContext`、resolved settings | 当前采样/工具步骤的已解析设置 | 跨步骤可变的全局状态 |
| Tool call | `ToolInvocation`、attempt | call ID、参数、沙箱尝试、审批结果 | 其他工具调用的结果 |

```mermaid
flowchart LR
    Session[Session 生命周期]
    Turn1[TurnContext A]
    Turn2[TurnContext B]
    Step1[StepContext A1]
    Step2[StepContext A2]
    Tool1[ToolInvocation]

    Session --> Turn1
    Session --> Turn2
    Turn1 --> Step1
    Turn1 --> Step2
    Step1 --> Tool1
```

### Y.11.4 操作分派

Session 接收来自 `CodexThread` 的操作并根据类型处理：开始输入、转向、中断、审批响应、上下文压缩、配置更新、MCP 交互、终端输入等。开始 Turn 时会建立 TurnContext、记录输入、发出 TurnStarted，并启动异步任务；中断时取消活动任务并等待清理路径发出一致事件。

### Y.11.5 事件发送与 canonical record

不是所有 UI 增量事件都等同于需要永久保存的 canonical item。Session 需要同时完成：

- 向事件通道发送高频 EventMsg；
- 把稳定的语义项写入对话历史；
- 把可回放项交给 rollout；
- 更新 token 或运行状态；
- 在完成边界 flush 必要数据。

这要求 API 区分 `send_event_raw`、记录响应项、持久化 canonical items 等语义，避免一次 UI 更新意外被重复写入事实日志。

### Y.11.6 延迟物化 rollout

新会话可以在真正需要记录前延迟创建文件，减少空线程或启动失败留下的垃圾文件；恢复会话则必须立即打开既有 rollout。延迟物化要求任何可能持久化的路径先调用“确保 rollout 已物化”的逻辑，并在并发情况下只创建一次。

### Y.11.7 Session 状态机

```mermaid
stateDiagram-v2
    [*] --> Constructing
    Constructing --> Ready: 服务与历史初始化完成
    Ready --> Running: 启动 Turn
    Running --> Running: steer / 工具循环
    Running --> Ready: Turn 正常结束
    Running --> Cancelling: interrupt / shutdown
    Cancelling --> Ready: 仅中断当前 Turn
    Cancelling --> Closing: 会话关闭
    Ready --> Closing: shutdown
    Closing --> Flushing: flush rollout / state
    Flushing --> Closed
    Closed --> [*]
```

---

## Y.12 run_turn：Agent Loop 的真正心脏

### Y.12.1 循环终止条件

`run_turn` 的源码注释清楚描述了循环：模型可能返回函数调用，也可能返回助手消息；函数调用结果会加入下一次请求；当本轮只有助手输出且无需继续工作时，Turn 结束。因此 Agent Loop 不是“模型调用一次”，而是一个受 token、取消、工具结果和策略控制的采样循环。

```mermaid
flowchart TD
    Start[Turn 开始] --> Prepare[准备输入、上下文、技能、插件]
    Prepare --> Build[构建 Prompt]
    Build --> Sample[调用模型并消费流]
    Sample --> Items{响应包含什么}
    Items -->|工具调用| Dispatch[并行/串行分派工具]
    Dispatch --> Results[收集 ToolOutput]
    Results --> Append[追加到历史]
    Append --> Budget{需自动压缩?}
    Budget -- 是 --> Compact[压缩上下文]
    Budget -- 否 --> Build
    Compact --> Build
    Items -->|待处理用户输入| AppendInput[合并 steer 输入]
    AppendInput --> Build
    Items -->|最终助手消息且无需后续| Complete[Turn 完成]
    Sample -->|取消/错误| Abort[中断或错误结束]
```

### Y.12.2 Turn 启动准备

源码中的准备阶段包含多项容易被忽略的工作：

- 获取或刷新 world state；
- 解析用户显式提及的 Skill、Plugin 或 Connector；
- 计算需要注入的技能说明；
- 执行与 Turn 相关的 Hook；
- 将输入记录到历史和 rollout；
- 建立 TurnDiffTracker；
- 创建一个且仅一个 Turn-scoped `ModelClientSession`；
- 解析环境选择、权限、模型和推理参数；
- 准备上下文基线与子 Agent 信息。

这说明 Prompt 并不是简单的“系统提示 + 用户消息”。它是配置、仓库指令、历史、技能、插件、环境、工具规范和安全上下文的合成结果。

### Y.12.3 采样请求状态

一次模型采样过程中需要维护：

- 流式响应解析器；
- 当前活动 Item；
- `FuturesOrdered` 形式的在途工具 Future；
- 是否需要 follow-up；
- 新到达的 steer 输入；
- token 使用与自动压缩判定；
- 工具参数增量消费者；
- 计划模式与结构化输出解析；
- 取消信号和错误重试状态。

```mermaid
flowchart LR
    Stream[模型流] --> Parser[ResponseItem Parser]
    Parser --> Msg[消息/推理/计划]
    Parser --> Call[工具调用]
    Call --> Futures[FuturesOrdered]
    Futures --> Output[ToolOutput]
    Output --> Follow[needs_follow_up = true]
    Msg --> Final{是否最终}
    Follow --> Next[下一次采样]
    Final -- 是 --> Done[结束]
    Final -- 否 --> Next
    Steer[新输入] --> Next
```

### Y.12.4 为什么工具 Future 要保持顺序

模型可能声明多个可并行工具调用。执行可以并发，但回填历史时顺序必须稳定，否则同一响应在不同机器上可能形成不同上下文，影响下一次采样和可回放性。`FuturesOrdered` 允许任务并发推进，同时按插入顺序产出结果。这是一种在吞吐与确定性之间的折中。

### Y.12.5 follow-up 语义

并非看到助手文本就立即结束。若同一响应还包含工具调用，或工具执行后需要让模型解释结果，`needs_follow_up` 会驱动下一次采样。相反，如果没有工具、没有待处理输入、没有必须继续的协议状态，最终助手消息可以结束 Turn。

### Y.12.6 中途输入与一致性

Steer 输入可能在模型流式响应或工具执行期间到达。核心不能随意把它插入当前 Prompt 的中间，因为请求已经发送；通常需要在安全边界收集，并在下一次采样前追加到历史。这样既保留用户干预能力，也不破坏当前工具调用的 call/output 配对。

### Y.12.7 错误与取消

错误至少分为模型传输、响应解析、工具构造、审批、沙箱执行、持久化和取消。可靠循环应做到：

- 可重试错误仅在策略允许时重试；
- 非幂等工具不能因网络错误无条件重复执行；
- 取消信号传到模型流、工具进程和子任务；
- 已产生的稳定 Item 仍按规则记录；
- Turn 产生明确终态，宿主不会永久等待。

### Y.12.8 Agent Loop 时序图

```mermaid
sequenceDiagram
    autonumber
    participant S as Session
    participant R as run_turn
    participant C as Context/Prompt
    participant M as ModelClientSession
    participant Q as Response Stream
    participant T as ToolRouter
    participant P as Persistence

    S->>R: run_turn(turn_context, input)
    R->>C: 注入 world/skills/plugins/history
    R->>P: record user input

    loop 直到无需 follow-up
        R->>C: build_prompt(history, tool specs)
        C-->>R: Prompt
        R->>M: sample(Prompt)
        M-->>Q: stream
        loop 消费响应项
            Q-->>R: delta / response item
            R->>S: emit EventMsg
            alt tool call
                R->>T: dispatch(call)
                T-->>R: ToolOutput future
            else assistant/reasoning
                R->>P: 记录稳定 item
            end
        end
        R->>R: 按声明顺序收集工具输出
        R->>P: 追加 tool outputs
        R->>R: 检查 steer、token 与压缩
    end

    R->>P: flush / final items
    R-->>S: TurnComplete
```

---

## 第三篇·模型、上下文与工具执行

## Y.13 ModelClient 与 ModelClientSession：把连接复用和轮次隔离同时做好

### Y.13.1 两级模型客户端

源码明确区分会话级 `ModelClient` 与轮次级 `ModelClientSession`。前者保存认证管理器、模型提供方、Thread ID、会话来源、originator、默认传输能力等可以跨 Turn 复用的资源；后者保存只应在一个 Turn 内存在的响应关联、WebSocket 粘性状态、传输回退和采样过程信息。

这不是命名差异，而是隔离边界。Responses API 或 WebSocket 可能返回用于增量请求、路由粘性或延续上下文的状态。如果把这些状态保存在整个 Session 上，第二个 Turn 可能错误继承第一个 Turn 的路由或 `previous_response_id`，导致难以复现的跨轮污染。

```mermaid
classDiagram
    class ModelClient {
      +AuthManager auth
      +ModelProvider provider
      +ThreadId thread_id
      +SessionSource source
      +new_session() ModelClientSession
    }

    class ModelClientSession {
      +transport_state
      +websocket_connection
      +turn_state_token
      +previous_response_id
      +sample(prompt)
      +compact(input)
    }

    class TurnContext {
      +String turn_id
      +ModelInfo model
      +ReasoningConfig reasoning
    }

    ModelClient "1" --> "0..*" ModelClientSession : 每个 Turn 创建
    TurnContext "1" --> "1" ModelClientSession
```

### Y.13.2 HTTP 与 WebSocket 回退

模型层可以尝试 WebSocket 以获得连接复用和低开销增量交互，但必须能够回落到 HTTP。合理的回退策略包含：

- 建连或协议不兼容时回退；
- 回退一旦在当前模型会话触发，应保持一致，避免一次 Turn 中反复切换；
- 不能因传输错误盲目重放已产生副作用的工具；
- 传输层只负责模型请求，工具执行由本地循环单独控制。

```mermaid
stateDiagram-v2
    [*] --> Uninitialized
    Uninitialized --> WebSocketReady: 预连接成功
    Uninitialized --> HttpMode: 不支持或连接失败
    WebSocketReady --> Streaming: 发送采样
    Streaming --> WebSocketReady: 响应完成
    Streaming --> HttpMode: 可回退的传输错误
    HttpMode --> HttpMode: 后续请求保持 HTTP
    WebSocketReady --> Closed: Turn 结束
    HttpMode --> Closed: Turn 结束
```

### Y.13.3 Prompt 的模型请求结构

`build_prompt` 聚合输入项、模型可见工具规范、是否允许并行工具调用、基础指令、输出 JSON Schema 与 cyber access 等信息。这里最重要的不是字段列表，而是 **Prompt 是结构化请求对象**：工具定义、输出约束和消息历史是独立字段，不应全部拼接成一段文本。

结构化 Prompt 带来三点好处：

1. 工具调用可以由协议解析，而非依赖模型输出正则；
2. 严格输出 schema 能让 Guardian、计划模式等路径 fail closed；
3. 模型能力差异可以在 ModelInfo/Provider 层适配，而不是散落在业务循环。

### Y.13.4 模型切换

线程允许模型或推理参数在后续 Turn 调整，但切换不能只替换一个字符串。新的模型可能有不同上下文窗口、工具能力、并行调用支持、推理摘要模式和压缩阈值。Session 需要生成新的有效配置，并在必要时压缩历史或重建 Turn-scoped client。

### Y.13.5 采样错误的层次

| 错误层 | 示例 | 推荐处理 |
|---|---|---|
| 认证 | token 失效、登录恢复 | 产生认证事件，允许宿主引导恢复 |
| 限流 | 配额或速率限制 | 结构化事件、按策略退避 |
| 传输 | WebSocket 断开、HTTP 超时 | 在安全条件下回退或重试 |
| 协议 | 无法解析响应项 | 终止当前采样，保留诊断 |
| 模型行为 | 工具名未知、参数不合法 | 作为工具错误回填，允许模型纠正 |
| 内容安全 | 缓冲或审核结果 | 按协议进入受控终态 |

---

## Y.14 上下文构建：历史、AGENTS.md、环境、Skill 与动态状态

### Y.14.1 上下文不是聊天记录的简单截取

编码 Agent 的上下文由多种来源组成：系统基础指令、开发者/管理员约束、用户输入、历史消息、工具结果、仓库 `AGENTS.md`、当前目录、工作区根、环境快照、Skill/Plugin 指令、子 Agent 列表、计划状态以及压缩摘要。每类来源的信任级别和更新频率不同，必须在合成时保持边界。

```mermaid
flowchart TB
    Base[模型基础指令]
    Managed[管理员与托管要求]
    Agents[AGENTS.md 层级指令]
    Config[模型/人格/协作配置]
    History[历史与压缩摘要]
    User[本轮用户输入]
    Skills[Skill / Plugin 注入]
    Env[环境与工作区快照]
    Subagents[子 Agent 状态]
    Tools[工具规范]

    Base --> Prompt[结构化 Prompt]
    Managed --> Prompt
    Agents --> Prompt
    Config --> Prompt
    History --> Prompt
    User --> Prompt
    Skills --> Prompt
    Env --> Prompt
    Subagents --> Prompt
    Tools --> Prompt
```

### Y.14.2 AGENTS.md 的层级语义

仓库指令通常按目录层级生效：上层文件提供广泛规则，下层文件可针对子树补充更具体规则。运行时必须基于当前工作目录和目标文件解析作用域，而不是只读取仓库根目录。合理实现还要处理文件缺失、超长、编码异常、符号链接和工作区外路径。

**设计解释**：`AGENTS.md` 本质上是“代码仓库内的声明式 Agent 策略”。它不应拥有比管理员配置更高的权限，也不能直接绕过沙箱；它影响模型决策，但真正执行仍要经过 ToolOrchestrator。

### Y.14.3 world state 与 Turn 环境快照

Turn 启动时读取的环境信息需要快照化。若在每个工具调用前重新读取所有配置，用户或后台进程对环境的变化可能使同一 Turn 行为不一致。另一方面，某些状态又必须动态更新，例如活动子 Agent、连接状态和新 steer 输入。因此上下文设计需要区分：

- Turn 开始固定的不可变快照；
- Step 开始重新解析的有效设置；
- 工具执行时实时检查的操作系统事实；
- 仅用于展示的最新状态。

### Y.14.4 上下文污染防护

外部工具输出、仓库文件和 MCP 返回内容都可能包含提示注入。Codex 的安全不能只依赖系统提示；关键操作由权限和审批层硬约束。上下文侧仍应做以下防护：

- 标明数据来源和角色，不把工具结果伪装成系统指令；
- 限制单个工具输出的 token；
- 压缩时保留用户原始意图而不是只保留工具文本；
- 对敏感内容做日志与遥测脱敏；
- 对外部来源的“要求执行命令”继续走正常工具治理。

### Y.14.5 上下文合成时序

```mermaid
sequenceDiagram
    participant R as run_turn
    participant A as AGENTS/Context Loader
    participant E as Environment Snapshot
    participant S as Skills/Plugins
    participant H as History
    participant P as Prompt Builder

    R->>A: 解析 cwd 与目录指令
    A-->>R: scoped instructions
    R->>E: 获取 Turn 环境快照
    E-->>R: roots / cwd / variables / agent info
    R->>S: 解析显式提及与可用能力
    S-->>R: injected prompts + metadata
    R->>H: 读取当前模型上下文
    H-->>R: messages / tool results / summary
    R->>P: 合并基础指令与工具 specs
    P-->>R: Prompt
```

---

## Y.15 上下文压缩：从 token 超限处理到可恢复状态变换

### Y.15.1 两条压缩路径

源码包含本地总结和远端 `/responses/compact` 路径。两者目标相同：在上下文接近模型限制时，用更短表示替代较早历史，同时保留继续完成任务所需的信息。区别在于压缩算法执行位置和返回格式。

- **本地压缩**：向模型发送专门的 summarization prompt，获得摘要，再重建历史；
- **远端压缩**：调用 Responses compact 能力，让服务端按协议返回压缩后的输入项。

```mermaid
flowchart TD
    Check[检查 token 使用] --> Trigger{是否触发压缩}
    Trigger -- 否 --> Continue[继续采样]
    Trigger -- 手动或自动 --> Impl{压缩实现}
    Impl -- 本地 --> Summarize[发送总结 Prompt]
    Summarize --> Rebuild[保留关键用户消息 + 摘要 + 基线]
    Impl -- 远端 --> CompactAPI["/responses/compact"]
    CompactAPI --> Filter[过滤并验证返回项]
    Filter --> Rebuild
    Rebuild --> Persist[记录压缩元数据与新上下文]
    Persist --> Continue
```

### Y.15.2 压缩不是删除历史

canonical rollout 仍保留原始事实，压缩改变的是“下一次模型看到的上下文投影”。这一区分极其重要：

- 用户需要恢复或审计时，原始执行历史仍存在；
- 模型请求只携带压缩后的上下文，控制 token；
- SQLite 或线程列表不必重写全部历史；
- 分叉和回退可以基于 canonical items 重新计算投影。

### Y.15.3 保留策略

源码保留选定用户消息、摘要前缀和初始上下文基线，并对保留用户消息设置 token 上限。保留用户原始意图是必要的，因为纯模型摘要可能遗漏否定条件、路径范围或安全限制。工具输出往往体积最大，但可以提炼为“执行了什么、结果是什么、哪些文件被修改、还有什么未解决”。

### Y.15.4 压缩钩子

Hook 体系包含 `PreCompact` 和 `PostCompact`。这允许组织在压缩前保存额外状态、阻止某些压缩、在压缩后更新外部索引或审计。Hook 不应随意篡改 canonical 历史；更合适的是参与压缩输入和记录结果。

### Y.15.5 压缩的失败策略

自动压缩失败时不能无限重试，否则 Turn 会陷入循环；也不能静默丢弃历史。合理策略是发出警告、尝试可用的替代实现、在仍无法满足模型上下文限制时明确终止，并保留可恢复 rollout。

### Y.15.6 压缩前后数据模型

```mermaid
flowchart LR
    subgraph Before[压缩前模型上下文]
      U1[早期用户消息]
      A1[早期助手消息]
      T1[大量工具输出]
      U2[近期用户消息]
      A2[近期工作状态]
    end

    subgraph Canonical[持久化事实]
      J[(完整 JSONL rollout)]
    end

    subgraph After[压缩后模型上下文]
      B[初始上下文基线]
      S[历史摘要]
      KU[保留的关键用户消息]
      R[近期未压缩项]
    end

    Before --> J
    Before -->|总结/compact| After
    After -.不覆盖.-> J
```

---

## Y.16 ToolRouter：从模型响应到内部调用对象

### Y.16.1 工具路由的输入

模型可能返回普通 FunctionCall、工具搜索调用或 CustomToolCall。`ToolRouter` 将这些协议对象转换为内部 `ToolCall`，其中包含命名空间化工具名、call ID、payload 和可选加密参数。之后才交给 Registry 查找执行器。

```mermaid
flowchart LR
    RI[ResponseItem]
    FC[FunctionCall]
    TS[ToolSearchCall]
    CT[CustomToolCall]
    Build[ToolRouter::build_tool_call]
    TC[ToolCall]
    Registry[ToolRegistry]

    RI --> FC
    RI --> TS
    RI --> CT
    FC --> Build
    TS --> Build
    CT --> Build
    Build --> TC
    TC --> Registry
```

### Y.16.2 工具名为什么要命名空间化

内置工具、MCP 工具、插件工具、Code Mode 嵌套工具和延迟发现工具可能同名。只用一个裸字符串会导致覆盖或调用歧义。ToolName/namespace 元数据让 Router 可以：

- 检测注册冲突；
- 只向模型暴露允许的名称；
- 将名称映射回所有者；
- 为遥测和权限规则提供稳定标识；
- 在 Code Mode 中把外层工具映射到内层能力。

### Y.16.3 可见工具与已注册工具不是同一个集合

Registry 可以包含运行时已知工具，但模型可见 specs 可能是其子集。原因包括：feature gate、权限、环境能力、插件是否启用、延迟搜索、模型是否支持某种工具模式。Router 保存 model-visible specs，使“存在能力”和“向模型公开能力”分离。

```mermaid
flowchart TD
    Registered[所有已注册工具] --> Filter[能力/配置/环境过滤]
    Filter --> Exposure{暴露方式}
    Exposure --> Direct[直接工具 spec]
    Exposure --> CodeMode[Code Mode 嵌套]
    Exposure --> Deferred[延迟工具搜索]
    Direct --> Model[模型可见工具集]
    CodeMode --> Model
    Deferred --> Search[需要时发现]
```

### Y.16.4 未知工具与参数错误

未知工具、无效 JSON 参数或 payload 类型不匹配不应 panic。它们应成为结构化工具错误，回填给模型以便纠正，并产生可观察事件。只有协议严重破坏或安全不变量失效时才应终止 Turn。

### Y.16.5 加密参数

ToolCall 可携带可选加密参数，说明部分敏感或远端生成参数不一定需要以明文进入所有中间层。文档不推断具体服务端加密方案，但从类型设计可确认 Router 需要保留原始安全元数据，而不能在解析时无条件丢弃。

---

## Y.17 ToolRegistry：能力注册、Hook 和生命周期的统一入口

### Y.17.1 Registry 不是 HashMap 的薄包装

`ToolRegistry` 维护工具名到 runtime/handler 的映射，同时处理暴露策略、所有者、MCP 搜索、差异跟踪和 Hook。注册时检测冲突；分派时建立完整执行上下文，并在工具结果被核心接受前完成治理。

```mermaid
flowchart TD
    Call[ToolCall] --> Lookup[按 ToolName 查 Registry]
    Lookup --> Runtime[CoreToolRuntime]
    Runtime --> Count[增加活动工具计数]
    Count --> Pre[PreToolUse Hook]
    Pre --> Decision{Hook 结果}
    Decision -- Block --> Blocked[返回阻止结果]
    Decision -- Rewrite --> Rewrite[重写受允许字段]
    Decision -- Continue --> Start[Tool lifecycle start]
    Rewrite --> Start
    Start --> Handler[调用 handler / executor]
    Handler --> Telemetry[记录耗时与结果]
    Telemetry --> Post[PostToolUse Hook]
    Post --> Accept[接受最终 ToolOutput]
```

### Y.17.2 PreToolUse 的价值

PreToolUse 可以阻止、调整或记录工具调用。适合的用途包括组织策略、命令规范化、审计标签、禁止访问某些资源。它不应被理解为沙箱替代品：Hook 属于应用逻辑，恶意或故障工具仍必须受操作系统隔离。

### Y.17.3 PostToolUse 的时机

源码将 Post Hook 放在 handler 完成之后、结果正式接受之前。这允许 Hook 检查输出、补充元数据或阻止不合规结果进入模型上下文。若先把结果追加历史再执行 Hook，拒绝就已经太晚。

### Y.17.4 生命周期计数

活动工具计数、开始/结束事件、耗时和调用标识用于：

- Turn 完成前等待所有工具收敛；
- 中断时定位活动工具；
- UI 展示并行执行；
- 观测工具成功率与延迟；
- 防止 Session 在工具尚未结束时被错误清理。

### Y.17.5 工具注册冲突

插件或 MCP 动态加入工具后，名称冲突不可避免。可靠系统必须选择确定策略：拒绝重复注册、通过命名空间消歧，或显式优先级。静默覆盖最危险，因为模型看到的 spec 和最终执行器可能不一致。源码的冲突检测与命名空间机制正是为了维护这一不变量。

---

## Y.18 ToolOrchestrator：审批、沙箱选择与重试的策略中心

### Y.18.1 集中编排的原因

如果每个 shell、补丁、MCP handler 自己判断审批和沙箱，策略会迅速漂移。`ToolOrchestrator` 把通用流程集中为：

1. 解析工具执行环境与权限；
2. 判定是否需要审批；
3. 选择初始沙箱策略；
4. 执行第一次尝试；
5. 识别是否是沙箱拒绝或可升级失败；
6. 在已有或新获得授权下选择升级策略重试；
7. 返回标准化结果。

```mermaid
flowchart TD
    I[ToolInvocation] --> Resolve[解析 Step 设置与权限]
    Resolve --> Approval{是否需要审批}
    Approval -- 是 --> Review[用户 / Guardian / Hook]
    Review --> Allowed{是否批准}
    Allowed -- 否 --> Deny[返回拒绝]
    Allowed -- 是 --> Select[选择初始沙箱]
    Approval -- 否 --> Select
    Select --> Attempt1[执行 Attempt 1]
    Attempt1 --> Result{结果}
    Result -- 成功 --> Done[返回 ToolOutput]
    Result -- 普通错误 --> Fail[返回执行错误]
    Result -- 沙箱拒绝 --> Escalate{策略允许升级?}
    Escalate -- 否 --> Fail
    Escalate -- 是 --> Cached{已有批准?}
    Cached -- 否 --> Review2[请求升级审批]
    Cached -- 是 --> Attempt2[无沙箱或扩权重试]
    Review2 --> Attempt2
    Attempt2 --> Done
```

### Y.18.2 审批与沙箱不是同一件事

审批回答“用户是否授权这项动作”，沙箱回答“即使程序行为偏离预期，操作系统允许它触碰什么”。已审批命令仍应在能满足目标的最小沙箱内执行；沙箱失败也不能自动解释为用户同意取消隔离。

### Y.18.3 初始尝试与升级重试

“先受限执行，遇到明确 sandbox denial 再申请升级”能减少不必要的高权限运行。但重试必须满足：

- 能识别失败确实由沙箱造成，而不是命令自身错误；
- 升级范围与原始动作一致；
- 审批结果可缓存到具体调用，而不是整轮无限复用；
- 对可能产生部分副作用的命令谨慎重试。

### Y.18.4 网络审批

网络访问可能通过代理层被拦截并请求授权。网络审批与文件/命令审批需要共享 Turn 和工具调用上下文，否则用户无法判断哪个动作在访问哪个域。Orchestrator 将网络触发信息纳入审批请求，有助于统一审查。

### Y.18.5 重试幂等性

工具重试最难的不是“再执行一次”，而是第一次是否已产生部分效果。比如包管理器可能下载了一部分文件，脚本可能创建了目录，补丁可能部分应用。因此系统应优先在执行前决定权限；只有可以判断第一次被隔离层阻止、尚未进入目标副作用时，升级重试才安全。

---

## Y.19 并行工具调用：吞吐、顺序与取消

### Y.19.1 并行的条件

模型请求中包含 `parallel_tool_calls` 能力，但并不表示所有工具都安全并行。工具规范和 handler 需要表达是否支持并行，运行时还要考虑共享工作目录、文件写入、终端会话和依赖关系。

适合并行的例子是读取多个互不相关文件、独立搜索、只读 MCP 查询；不适合的是两个修改同一文件的补丁、先生成文件后编译、共享同一交互式 PTY 的命令。

### Y.19.2 执行并发与结果顺序

```mermaid
sequenceDiagram
    participant M as Model Response
    participant R as run_turn
    participant A as Tool A
    participant B as Tool B
    participant C as Tool C
    participant H as History

    M-->>R: calls [A,B,C]
    par 并行推进
      R->>A: execute
      R->>B: execute
      R->>C: execute
    end
    B-->>R: B 先完成
    C-->>R: C 次完成
    A-->>R: A 最后完成
    R->>H: 按 A,B,C 声明顺序追加输出
```

这种做法牺牲了“谁先完成谁先回填”的最低延迟，但换来确定的历史顺序。若需要流式展示，UI 事件仍可按真实完成时间发送，而模型历史按声明顺序收敛。

### Y.19.3 取消传播

并行工具场景中，中断 Turn 必须取消尚未启动的任务，并向已启动进程发送终止。某个工具失败是否取消其他工具取决于语义：如果调用彼此独立，可以收集所有结果；如果失败使后续结果无意义，应尽快取消。核心需要在“完整诊断”和“停止副作用”之间选择明确策略。

### Y.19.4 并行写冲突

文件写入工具可通过 TurnDiffTracker 观察变更，但它不能自动解决竞态。安全设计应避免模型把互相依赖的写操作标记为并行，或由工具层对同一路径串行化。否则最后写入者覆盖前者，rollout 仍记录两个“成功”，但代码状态不符合任一工具预期。

---

## Y.20 Shell、Unified Exec 与 PTY

### Y.20.1 为什么 shell 执行不是一个 `Command::output()`

编码 Agent 需要支持长命令、增量输出、交互输入、工作目录、环境变量、超时、信号、后台进程、伪终端和跨平台沙箱。普通一次性进程 API 无法覆盖这些需求。Codex 因此包含 shell-command、unified-exec、exec-server、PTY/terminal query 处理等组件。

```mermaid
flowchart TB
    Tool[Shell Tool Call] --> Parse[解析命令与环境]
    Parse --> Policy[审批/沙箱策略]
    Policy --> Runtime{执行模式}
    Runtime --> Pipe[普通管道进程]
    Runtime --> PTY[伪终端进程]
    Runtime --> Unified[Unified Exec Session]
    Pipe --> Stream[stdout/stderr 增量]
    PTY --> Query[TTY 查询响应器]
    Query --> Stream
    Unified --> Stream
    Stream --> Events[CommandExecution Events]
    Events --> Model[聚合/截断后 ToolOutput]
```

### Y.20.2 流式输出与模型输出不同

终端用户希望看到实时输出，但把所有字节原样送入模型会迅速耗尽上下文。运行时可以向 UI 发送较高频增量，同时对模型回填做大小限制、头尾保留或摘要。二者共享 call ID，但服务对象不同。

### Y.20.3 PTY 与 TTY 查询

某些程序在 PTY 中会发送设备状态、窗口大小、光标位置或 DEC 私有模式查询，并等待终端响应。若 Codex 只转发输出而不响应，子进程可能挂起。固定提交新增的终端查询响应器会识别受支持查询、处理跨 chunk 拆分并生成有界响应。这说明执行层不仅管理进程，还模拟了必要的终端协议行为。

```mermaid
sequenceDiagram
    participant P as TTY 子进程
    participant PTY as PTY Driver
    participant Q as Terminal Query Responder
    participant UI as Codex TUI

    P->>PTY: 输出普通文本
    PTY-->>UI: 流式展示
    P->>PTY: 发送终端状态查询
    PTY->>Q: 解析控制序列
    Q-->>PTY: 生成有界终端响应
    PTY-->>P: 写回查询结果
    P->>PTY: 继续运行
```

### Y.20.4 进程终止

中断需要处理进程树而不仅是父 PID。不同平台的进程组、Job Object、信号和控制事件语义不同。可靠终止通常分为温和请求、短暂等待和强制清理，并保证等待本身有界。否则 Session shutdown 会被卡死的子进程永久阻塞。

### Y.20.5 工作目录与 PathUri

跨本机、容器或远端执行环境时，“路径”不一定是本机 `PathBuf`。Codex 使用 PathUri/AbsolutePath 等类型，把逻辑路径与执行边界上的本机转换分开。这样工具规范可以描述环境内路径，而沙箱转换在真正执行前完成。

---

## Y.21 apply_patch、文件变更与 Turn Diff

### Y.21.1 文件变更是一级语义

编码 Agent 的核心产物通常是文件差异。Codex 不把所有修改都当成匿名 shell 副作用，而是为补丁和 Turn diff 定义事件。这让宿主可以展示开始、逐步更新和最终差异，也便于审批、恢复和审计。

```mermaid
flowchart LR
    Model[模型生成补丁调用] --> Parse[解析 patch]
    Parse --> Validate[路径与格式校验]
    Validate --> Approval[写权限/审批]
    Approval --> Apply[应用补丁]
    Apply --> Track[TurnDiffTracker]
    Track --> PatchEvents[Patch Begin/Update/End]
    Track --> FinalDiff[Turn Diff]
    FinalDiff --> UI[预览与审查]
    FinalDiff --> Rollout[持久化语义项]
```

### Y.21.2 路径校验

补丁工具必须防止绝对路径逃逸、`..` 穿越、符号链接绕过和对禁止目录写入。文件策略在逻辑层判定，实际打开文件时仍需依赖沙箱减少 TOCTOU 风险。仅做字符串前缀检查不够，因为符号链接和大小写规则会改变真实目标。

### Y.21.3 TurnDiffTracker

Turn 可能通过 apply_patch、shell、格式化器或生成器修改文件。单看工具返回无法获得最终工作树变化，因此 TurnDiffTracker 在轮次范围追踪差异。它适合回答“这一轮总体改了什么”，而不是只列出每个工具自报的文件。

### Y.21.4 部分应用与失败

补丁可能因上下文不匹配部分失败。工具结果应明确区分完全应用、部分应用和完全失败；模型下一步应先重新读取文件再修正，而不是假设原补丁已落地。对用户显示最终 diff 能避免自然语言声称与磁盘事实不一致。

---

## Y.22 Code Mode：把工具能力投射到受控代码执行环境

### Y.22.1 模块边界

工作区包含 `code-mode`、协议、服务、host 和工具相关 crate。`code-mode/src/lib.rs` 暴露 gRPC session provider、进程拥有的 session 以及 disabled provider。核心 ToolRouter 也支持把工具以 Code Mode 嵌套方式暴露。

**源码事实**：Code Mode 并不是在核心进程中随意 `eval` 一段代码；它具有独立协议、host/服务与 session provider，说明执行被放在明确进程或 RPC 边界中。

### Y.22.2 为什么需要 Code Mode

当模型需要组合大量细粒度工具时，逐个 FunctionCall 会增加往返延迟和 token。Code Mode 可以让模型生成一段受控程序，在宿主环境中调用一组嵌套工具，完成循环、分支和数据加工，再返回较小结果。

```mermaid
flowchart TB
    Model[模型] --> CodeTool[Code Mode 外层工具]
    CodeTool --> Session[CodeModeSession]
    Session --> Host[独立 Host / Process]
    Host --> Runtime[受控 JS/V8 或执行运行时]
    Runtime --> Nested[嵌套工具调用]
    Nested --> Router[ToolRouter]
    Router --> Governance[Hook/Approval/Sandbox]
    Governance --> Result[结果]
    Result --> Runtime
    Runtime --> Summary[代码执行结果]
    Summary --> Model
```

### Y.22.3 安全不变量

Code Mode 不能成为绕过工具治理的“后门”。即使外层代码本身在隔离运行时中，每次触达文件、shell、网络或 MCP 的嵌套调用仍应经过 Registry/Orchestrator。否则模型只需把危险操作包进代码，就能绕过原有审批。

### Y.22.4 可靠性风险

- host 二进制缺失或版本不匹配；
- gRPC/session 中断；
- 代码无限循环或内存膨胀；
- 嵌套工具调用数量失控；
- 序列化结果过大；
- 代码运行时与主进程取消信号不同步。

因此 Code Mode 需要会话超时、资源配额、调用预算和显式关闭。它提升表达力，也扩大了需要治理的执行面。

---

## 第四篇·权限、审批与纵深安全

## Y.23 PermissionProfile：把“允许什么”变成可计算的数据结构

### Y.23.1 权限模型的组成

Codex 的权限不是一个简单的 `dangerously_allow_all` 布尔值。源码中的 PermissionProfile、AdditionalPermissionProfile、文件访问规则、网络沙箱策略、审批模式和 sandbox enforcement 共同决定某次工具调用的有效能力。

可以把有效权限理解为：

```text
有效权限 = 平台能力
        ∩ 管理员上限
        ∩ 用户/项目/会话配置
        ∩ Turn 环境选择
        ∩ 工具自身声明
        ∩ 本次审批授予范围
```

其中“∩”强调取交集而不是后配置覆盖前配置。安全策略合并的核心原则是 **deny 优先、最小权限、授权范围不可扩张**。

```mermaid
flowchart TB
    Platform[平台可提供能力]
    Managed[管理员要求]
    User[用户配置]
    Project[项目配置]
    Turn[Turn 环境与模式]
    Tool[工具所需能力]
    Approval[本次审批]

    Platform --> Merge[权限合并器]
    Managed --> Merge
    User --> Merge
    Project --> Merge
    Turn --> Merge
    Tool --> Merge
    Approval --> Merge
    Merge --> Effective[Effective Permission Profile]
```

### Y.23.2 文件权限

文件访问至少需要区分 Read、Write、Deny，以及 Restricted、Unrestricted、ExternalSandbox 等文件系统模式。特殊根可以代表项目根、临时目录、系统根或最小运行目录。通配规则与路径根组合后，冲突按 deny > write > read 处理。

| 情况 | 期望结果 |
|---|---|
| 路径同时命中 read 与 deny | deny |
| 路径同时命中 write 与 deny | deny |
| 父目录只读、子目录明确可写且管理策略允许 | 子目录可写 |
| 工具请求工作区外路径但无显式授权 | 拒绝或请求审批 |
| ExternalSandbox 模式 | 不重复假设本机沙箱语义，遵从外部边界 |

### Y.23.3 网络权限

网络策略不是“能否启动 curl”这么简单。命令可通过语言运行时、包管理器、Git、DNS、Unix socket 或子进程联网。Codex 的网络代理与沙箱策略用于把网络能力收敛到 Restricted/Enabled 等明确状态，并在需要时触发域或动作级审批。

### Y.23.4 附加权限

工具或环境可能提出 AdditionalPermissionProfile。附加权限不能直接覆盖基础 deny，而应作为请求进入合并和审批流程。把“工具声明需要写 `/tmp`”直接解释为允许，会让任何插件自我扩权。

### Y.23.5 权限解析时机

权限需要在 Step/Tool call 边界重新解析，因为不同工具要求不同；但管理员上限和 Turn 环境快照应保持稳定。解析过早会让工具看不到其具体资源需求，解析过晚则可能已启动不受控进程。

```mermaid
sequenceDiagram
    participant T as Tool Handler
    participant S as Step Settings
    participant P as Permission Resolver
    participant A as Approval
    participant X as Executor

    T->>S: 声明命令、路径、网络需求
    S->>P: 基础配置 + 管理要求 + 环境
    P-->>T: 初步有效权限
    T->>A: 对超出自动范围的能力请求授权
    A-->>T: allow/deny + 限定范围
    T->>P: 合并本次授权
    P-->>X: 最终执行权限
```

---

## Y.24 审批策略：授权决策必须绑定具体动作

### Y.24.1 审批的目标

审批不是提醒框，而是一个授权协议。审批请求需要包含足够信息让用户或 Guardian 判断：要执行什么命令、访问哪个路径或域、为什么需要升级、由哪个工具和 Turn 发起、是否可能产生持久副作用。

### Y.24.2 AskForApproval 的策略语义

不同模式决定哪些动作自动执行、哪些动作询问、哪些动作直接禁止。无论枚举名称如何演进，设计上应区分：

- 从不请求且严格受限；
- 仅在受限执行失败后请求升级；
- 风险动作执行前请求；
- 由自动 Reviewer 先审查；
- 显式完全放开——只应在用户理解风险时使用。

### Y.24.3 审批缓存

同一个工具调用在沙箱拒绝后升级重试，可以复用针对该动作的审批；不同命令、不同路径或不同 Agent 不能无条件共享。缓存键至少应绑定 call ID、规范化动作和权限范围。宽泛缓存会形成“批准一次，整轮任意执行”的权限放大。

```mermaid
flowchart TD
    Req[审批请求] --> Normalize[规范化动作与资源]
    Normalize --> Key[生成审批作用域键]
    Key --> Cache{已有决定?}
    Cache -- 允许 --> Execute[按批准范围执行]
    Cache -- 拒绝 --> Denied[返回拒绝]
    Cache -- 无 --> Reviewer{审查者}
    Reviewer --> User[用户]
    Reviewer --> Guardian[Guardian]
    User --> Store[记录限定决定]
    Guardian --> Store
    Store --> Execute
```

### Y.24.4 非交互场景

在 `exec` 或服务端调用中，可能没有即时用户界面。系统必须在启动前确定审批策略：风险操作被拒绝、由外部客户端通过协议响应，或启用 Guardian 自动审查。等待一个永远不会到来的审批响应是资源泄漏，也会让 CI 永久挂起。

### Y.24.5 审批文案与命令等价性

审批展示应尽量接近最终执行动作。若展示的是原始命令，而执行前 Hook 重写了命令，批准就不再有效。正确顺序通常是：先完成允许的规范化/重写，再对最终动作审批；审批后只允许不扩大权限的转换，例如加上沙箱包装器。

---

## Y.25 Guardian：用独立模型审查降低人工摩擦，但保持 fail closed

### Y.25.1 Guardian 的源码定位

`core/src/guardian/mod.rs` 的模块注释给出四步方法：重建保留用户意图和近期工具上下文的紧凑 transcript；启动专门 Guardian review session 对确切动作返回严格 JSON；超时、执行失败或格式错误时 fail closed；应用明确 allow/deny 结果。Guardian 会克隆父配置，因此继承父 Turn 已有的托管网络代理和 allowlist。

```mermaid
sequenceDiagram
    autonumber
    participant O as ToolOrchestrator
    participant G as GuardianReviewSession
    participant M as Guardian Model
    participant C as Circuit Breaker

    O->>G: GuardianApprovalRequest
    G->>G: 构建受限 transcript + 精确动作
    G->>M: 严格 JSON schema 请求
    alt 合法结构化响应
        M-->>G: risk_level / authorization / outcome / rationale
        G->>C: 记录允许或拒绝
        G-->>O: Allow / Deny
    else 超时、错误、解析失败
        G->>C: 记录拒绝
        G-->>O: Deny（fail closed）
    end
```

### Y.25.2 为什么必须独立会话

让执行任务的同一个模型自我批准，会产生明显的利益冲突和上下文污染。独立 Guardian 会话可以使用专门政策、严格输出 schema、受控 transcript 和独立超时。它仍不是形式化安全证明，但比“主 Agent 说自己安全”有更清晰的职责分离。

### Y.25.3 Transcript 截断

Guardian 不需要整个 Thread 的所有 token，只需要用户授权意图、近期相关消息、工具上下文和精确动作。源码为消息、工具结果、单项内容和动作字符串设置 token 上限，并限制近期条目数。这样既控制成本，也降低无关上下文影响审查。

### Y.25.4 结构化评估

GuardianAssessment 包含风险级别、用户授权判断、结果和 rationale。严格 schema 的意义是：缺字段、额外文本、不可解析输出不能被模糊地当作允许。执行安全路径中，“无法判断”应归入拒绝。

### Y.25.5 拒绝熔断

源码维护每 Turn 连续拒绝和近期拒绝窗口，并为普通与 cyber 模型设置不同阈值。达到阈值后中断 Turn，防止主 Agent 不断换一种表达重复申请同一类危险操作，造成审批风暴和资源消耗。

```mermaid
stateDiagram-v2
    [*] --> Healthy
    Healthy --> Healthy: Guardian Allow / 非拒绝
    Healthy --> Denial1: 第一次拒绝
    Denial1 --> Healthy: 后续允许
    Denial1 --> DenialN: 连续或近期拒绝增加
    DenialN --> DenialN: 未达阈值
    DenialN --> Interrupted: 达到策略阈值
    Interrupted --> [*]
```

### Y.25.6 Guardian 的安全边界

Guardian 只能决定是否授权，不能替代：

- 文件和网络权限合并；
- 操作系统沙箱；
- 秘密信息保护；
- 工具参数校验；
- 进程资源限制；
- canonical 审计记录。

自动审查降低人工摩擦，但它仍是纵深防御中的一层，而非最终信任根。

---

## Y.26 SandboxManager：把统一策略转换为平台特定隔离

### Y.26.1 跨平台抽象

源码中的 SandboxType 包含 None、MacosSeatbelt、LinuxSeccomp、WindowsRestrictedToken 等。`SandboxManager` 负责判断是否应沙箱、选择初始类型，并把原始执行请求转换为平台包装后的命令/进程配置。

```mermaid
flowchart TD
    R[Raw Exec Request] --> M[SandboxManager]
    M --> Enforce{Enforcement}
    Enforce -- Disabled --> None[No Sandbox]
    Enforce -- External --> External[外部执行器负责]
    Enforce -- Managed --> OS{目标平台}
    OS -- Linux --> Linux[seccomp / Landlock / bubblewrap 组合]
    OS -- macOS --> Mac[Seatbelt profile]
    OS -- Windows --> Win[Restricted Token / Job 边界]
    Linux --> Wrapped[Wrapped Exec Request]
    Mac --> Wrapped
    Win --> Wrapped
    External --> Wrapped
    None --> Wrapped
```

### Y.26.2 Linux 路径

Linux 可以组合 syscall 过滤、Landlock 文件规则和 namespace/bubblewrap 风格隔离。不同内核或发行版支持程度不同，因此运行时需要能力探测和明确失败，而不是假设所有机器都支持同一机制。独立 `linux-sandbox` 可执行边界也有利于在启动前完成低级系统调用设置。

### Y.26.3 macOS Seatbelt

Seatbelt profile 将允许的文件、进程与网络行为编码为系统沙箱规则。路径中包含特殊字符时必须正确转义；临时目录、工具链缓存、动态库和 shell 初始化文件都可能影响命令可用性。过窄会产生大量误拒绝，过宽又失去隔离，因此 Codex 需要根据 PermissionProfile 生成规则，而不是固定一份模板。

### Y.26.4 Windows Restricted Token

Windows 权限模型与 Unix 不同。Restricted Token、Job Object、ACL 和进程树终止需要协同。路径大小写、盘符、UNC、重解析点和命令行转义也会影响规则。统一 SandboxType 只隐藏选择接口，不应掩盖平台语义差异；测试必须在真实 Windows runner 上执行。

### Y.26.5 沙箱拒绝识别

Orchestrator 需要区分“程序返回非零”和“沙箱阻止”。只有后者才可能触发扩权审批。识别可以来自包装器退出状态、结构化 denial、系统错误或 stderr 特征，但必须避免把任意失败误判为权限问题。

### Y.26.6 PathUri 到本机路径的最后一公里

路径转换尽量推迟到执行边界，因为同一逻辑工作区可能位于本机、容器、远程环境或映射根。权限规则也应对规范化后的执行环境路径判定，防止 URI 与本机路径解释不一致。

### Y.26.7 沙箱启动时序

```mermaid
sequenceDiagram
    participant O as ToolOrchestrator
    participant S as SandboxManager
    participant P as Platform Adapter
    participant X as Child Process
    participant D as Denial Parser

    O->>S: transform(exec, effective permissions)
    S->>S: 规范化路径与网络策略
    S->>P: 生成平台规则和包装命令
    P->>X: spawn
    X-->>P: stdout/stderr/exit
    P->>D: 解析系统拒绝
    D-->>O: Success / ToolError / SandboxDenial
```

---

## Y.27 网络代理、秘密与外部边界

### Y.27.1 托管网络代理

仅靠进程内 HTTP 客户端限制无法控制 shell 子进程。托管代理可以让命令和工具的外联经过统一策略、域名 allowlist、审计和动态审批。SandboxManager 还会把代理 CA 或必要配置以只读方式暴露给受限环境。

```mermaid
flowchart LR
    Tool[工具/子进程] --> Sandbox[网络受限沙箱]
    Sandbox --> Proxy[Managed Network Proxy]
    Proxy --> Policy{域与动作策略}
    Policy -- 已允许 --> Internet[目标服务]
    Policy -- 需审批 --> Approval[网络审批]
    Approval -- 允许 --> Internet
    Approval -- 拒绝 --> Block[阻断并返回原因]
```

### Y.27.2 DNS 与直连绕过

代理策略必须同时考虑 DNS、IPv4/IPv6、localhost、Unix socket 和环境变量覆盖。若沙箱允许任意原始 socket，进程可能绕过 HTTP_PROXY。真正的网络治理需要操作系统层限制只允许访问代理，再由代理决定目标。

### Y.27.3 Secret 处理

认证令牌、API key、代理凭据和环境秘密可能出现在配置、进程环境和工具输出中。安全设计需要：

- 尽量使用专门凭据存储或短期令牌；
- 向子进程只注入必要变量；
- 日志与遥测做脱敏；
- 错误信息避免打印完整请求头；
- MCP/Plugin 只能访问声明的认证上下文；
- rollout 中不应无条件保存秘密原文。

### Y.27.4 外部沙箱

当 Codex 运行在容器、云工作区或由外部 executor 管理的环境中，`ExternalSandbox` 表示隔离责任已经外移。此时本机进程不应误套不兼容路径规则，但应用层审批和工具治理仍可保留。外部隔离必须有明确契约，不能把“在容器里”默认等同于安全。

---

## Y.28 安全威胁模型

### Y.28.1 资产与信任边界

主要资产包括源代码、凭据、用户数据、Git 历史、终端会话、网络身份和本地系统。潜在不可信输入包括用户仓库、依赖包、网页/MCP 内容、插件、Skill 文本、模型输出和子进程输出。

```mermaid
flowchart TB
    subgraph Untrusted[不可信或半可信输入]
      Repo[仓库文件]
      Model[模型输出]
      MCP[MCP/Connector]
      Plugin[Plugin/Skill]
      Process[子进程输出]
    end

    subgraph Boundaries[治理边界]
      Context[上下文角色隔离]
      Registry[工具 Registry]
      Hooks[Hooks]
      Approval[Approval/Guardian]
      Sandbox[Sandbox/Proxy]
      Secret[Secret Redaction]
    end

    subgraph Assets[受保护资产]
      Code[代码与 Git]
      Cred[凭据]
      Host[主机系统]
      Network[网络身份]
      History[会话历史]
    end

    Untrusted --> Boundaries
    Boundaries --> Assets
```

### Y.28.2 典型攻击路径与缓解

| 攻击路径 | 风险 | 主要缓解 |
|---|---|---|
| 仓库 README 注入“上传密钥” | 模型被诱导执行外联 | 权限、网络代理、审批、秘密脱敏 |
| 恶意 MCP 返回伪系统指令 | 上下文角色混淆 | 工具结果边界、模型指令优先级、执行治理 |
| Plugin 注册同名内置工具 | 能力劫持 | 命名空间、冲突检测、来源元数据 |
| shell 使用符号链接逃逸 | 写入工作区外 | 规范化、文件策略、OS 沙箱 |
| Guardian 输出非 JSON 但包含“allow” | 解析歧义放行 | 严格 schema、fail closed |
| 沙箱失败后自动无隔离重试 | 权限升级 | 识别 denial、绑定审批、限定升级 |
| 多 Agent 共享全局注册表 | 跨任务通信或数据污染 | 根线程树范围 AgentControl/Registry |
| rollout 写入失败但 UI 显示成功 | 审计缺口 | writer 错误传播、flush、恢复诊断 |
| 中断后子进程继续运行 | 隐藏副作用 | 取消传播、进程树终止、有界 shutdown |

### Y.28.3 安全不是单点开关

纵深防御链如下：模型只看到允许的工具；Router/Registry 验证调用；Hook 执行组织策略；Orchestrator 解析权限；审批或 Guardian 授权；Sandbox/Proxy 做系统隔离；输出与历史做脱敏；Rollout 记录可审计事实。任何一层都可能失败，因此下一层不能假设上一层完美。

```mermaid
flowchart LR
    M[模型工具意图]
      --> V[工具可见性]
      --> R[参数与注册校验]
      --> H[Hook 策略]
      --> P[权限解析]
      --> A[审批/Guardian]
      --> S[OS 沙箱]
      --> N[网络代理]
      --> E[执行]
      --> O[输出脱敏与审计]
```

### Y.28.4 高风险配置的运维原则

禁用沙箱、允许无限网络或无条件审批时，应在 UI、日志和进程启动信息中明确显示；线程恢复也应重新呈现当前有效策略，避免用户以为仍处于原来的受限模式。组织环境应通过 managed requirements 限制用户层放宽权限，并启用只允许托管 Hook 等策略。

---

## 第五篇·扩展生态与多 Agent

## Y.29 MCP：把外部能力纳入同一工具治理链

### Y.29.1 MCP 在架构中的位置

MCP 让 Codex 连接外部工具、资源或服务。架构上，MCP 不是绕开 ToolRouter 的第二套执行系统，而应被适配为模型可见工具或资源，并继续进入工具注册、生命周期事件、审批和遥测。App Server 协议中也包含 MCP 启动状态、工具调用和 elicitation 等事件，说明 MCP 同时跨越连接管理、模型工具和用户交互三个层面。

```mermaid
flowchart LR
    Config[MCP 配置] --> Manager[MCP Connection Manager]
    Manager --> Server1[MCP Server A]
    Manager --> Server2[MCP Server B]
    Server1 --> Discover[发现 tools/resources/prompts]
    Server2 --> Discover
    Discover --> Namespace[名称与所有者命名空间]
    Namespace --> Registry[ToolRegistry]
    Model[模型] --> Router[ToolRouter]
    Router --> Registry
    Registry --> Approval[Hook/Approval]
    Approval --> Call[MCP Call]
    Call --> Result[Tool Result]
    Result --> Model
```

### Y.29.2 生命周期管理

MCP server 可能是本地 stdio 进程、网络端点或由插件提供的声明。连接管理需要处理：

- 启动与初始化超时；
- 协议能力协商；
- 工具列表刷新；
- server 异常退出与重连；
- 同名工具消歧；
- 会话关闭时终止本地子进程；
- 认证信息与网络策略；
- 启动进度事件向宿主可见。

### Y.29.3 Elicitation

MCP 工具可能在执行中请求额外用户信息。这个交互不能阻塞 App Server 或 TUI 主循环，而应产生 elicitation 事件，等待带关联 ID 的响应。若宿主不支持 elicitation，工具应得到明确不支持或取消结果，而不是永久挂起。

```mermaid
sequenceDiagram
    participant M as Model
    participant C as Codex Core
    participant S as MCP Server
    participant H as Host/User

    M->>C: 调用 MCP 工具
    C->>S: tools/call
    S-->>C: elicitation request
    C-->>H: Elicitation Event(id)
    H-->>C: 用户响应(id)
    C->>S: elicitation response
    S-->>C: tool result
    C-->>M: 回填结果
```

### Y.29.4 MCP 的信任问题

MCP server 是外部能力提供者，其工具描述、参数 schema 和结果都不应自动获得系统级信任。需要记录 server 所有者、来源和配置层；本地插件带来的 MCP 与用户手工配置的远程 MCP 也应可区分。网络型 MCP 还受网络策略和认证治理，不能因其被称为“工具”就绕过数据外发控制。

---

## Y.30 Skill：以文档和资源形式扩展模型行为

### Y.30.1 Skill 的结构

`codex-skills` 包含接口解析、调用检测、加载、mentions、模型、frontmatter 解析和选择逻辑。SkillMetadata 可以表达名称、路径、作用域、依赖和插件归属；核心既追踪显式提及，也能根据命令检测隐式 Skill 使用。

```mermaid
flowchart TD
    Roots[User/Repo/System/Admin Skill Roots] --> Loader[SkillRootLoader]
    Loader --> Parse[解析 SKILL.md frontmatter]
    Parse --> Metadata[SkillMetadata]
    Metadata --> Select{选择方式}
    Select --> Explicit[用户显式 mention]
    Select --> Implicit[根据命令检测隐式调用]
    Explicit --> Inject[注入 Skill 指令/资源]
    Implicit --> Record[记录实际使用]
    Inject --> Prompt[Turn Prompt]
    Record --> Analytics[遥测与插件使用统计]
```

### Y.30.2 系统 Skill 安装

源码把内置系统 Skill 通过 `include_dir` 编入二进制，并安装到 `CODEX_HOME/skills/.system`。安装逻辑计算嵌入目录指纹，marker 匹配时跳过重复写入；不匹配时清理旧目录后重写。这是一种“嵌入资源 + 可见磁盘缓存”设计：运行时可像普通文件一样读取，发布版本又能确保内容与二进制一致。

### Y.30.3 Skill 作用域

显式调用统计区分 User、Repo、System、Admin 等作用域。作用域影响发现优先级、可管理性和审计。管理员 Skill 不应被项目同名文件静默覆盖；仓库 Skill 只在对应项目生效；系统 Skill 由发行版本管理。

### Y.30.4 显式与隐式调用

- **显式调用**：用户在输入中明确 mention Skill，运行时解析并注入；
- **隐式调用**：模型执行的命令符合 Skill 访问特征，运行时记录实际使用。

隐式检测不是自动授予权限。它主要用于使用归因、插件统计和扩展回调。源码用 Turn extension data 保存已见集合，避免同一 Turn 重复记录相同 Skill。

### Y.30.5 Skill 安全

SKILL.md 是影响模型行为的文本，可能来自仓库或插件。它不应越过系统/管理员指令优先级，也不能直接执行脚本。Skill 中引用的脚本最终仍通过工具和沙箱运行。加载器还需限制路径、处理符号链接、校验 frontmatter，并避免把超大资源一次性注入上下文。

---

## Y.31 Plugin：能力包、来源与统一归因

### Y.31.1 PluginCapabilitySummary

`codex-plugin` 定义插件 ID、manifest、provider、解析位置和加载结果。能力摘要包含配置名、显示名、命名空间、描述、是否含 Skill、MCP server 名称和 App Connector ID。这个摘要让 UI 与遥测可以在不加载插件全部实现的情况下展示能力。

```mermaid
classDiagram
    class PluginCapabilitySummary {
      +config_name String
      +display_name String
      +plugin_namespace Option~String~
      +description Option~String~
      +has_skills bool
      +mcp_server_names Vec~String~
      +app_connector_ids Vec~AppConnectorId~
    }

    class LoadedPlugin {
      +PluginId id
      +ResolvedPluginLocation location
      +capabilities
    }

    class PluginHookSource {
      +plugin_id
      +plugin_root
      +plugin_data_root
      +source_path
      +hooks
    }

    LoadedPlugin --> PluginCapabilitySummary
    LoadedPlugin --> PluginHookSource
```

### Y.31.2 插件不是单一动态库

从能力摘要可看出，插件更像资源与声明的组合包：它可以带 Skill、MCP server、App Connector 和 Hook。这样的设计比任意本机动态库更可治理：宿主先解析 manifest，建立来源和命名空间，再分别交给对应子系统加载。

### Y.31.3 PluginProvider 与位置解析

插件可能来自本地缓存、配置源或后端。provider/locator 负责把逻辑插件标识解析为具体位置。解析结果必须包含来源和错误，不能只返回路径；否则 UI 无法解释“哪个版本、来自哪里、为什么加载失败”。

### Y.31.4 插件注入

核心的 plugin injection 会基于显式提及、可发现插件和当前 Turn 构建指令。只有选中的能力应进入 Prompt，避免每个插件都永久占用 token。插件 Skill 的调用还会通过 extension contributors 和 analytics 记录归属。

### Y.31.5 插件数据目录

Hook source 同时持有 plugin root 和 plugin data root，体现代码/只读资源与可写数据的分离。权限规则应优先允许插件写自己的数据目录，而不是整个插件安装目录或项目任意位置。

---

## Y.32 Hook：十二类生命周期扩展点

### Y.32.1 Hook 事件清单

当前 `codex-hooks` 暴露十二个事件名：

1. `PreToolUse`
2. `PermissionRequest`
3. `PostToolUse`
4. `PreCompact`
5. `PostCompact`
6. `SessionStart`
7. `SessionEnd`
8. `UserPromptSubmit`
9. `SubagentStart`
10. `SubagentStop`
11. `Stop`
12. `Interrupt`

其中九类事件的 matcher 有实际意义；用户提交、Stop 和 Interrupt 等事件不是按工具或来源匹配的同类场景。

```mermaid
flowchart LR
    SessionStart --> UserPromptSubmit
    UserPromptSubmit --> PreToolUse
    PreToolUse --> PermissionRequest
    PermissionRequest --> Tool[工具执行]
    Tool --> PostToolUse
    PostToolUse --> PreCompact
    PreCompact --> Compact[压缩]
    Compact --> PostCompact
    PostCompact --> Stop
    Stop --> SessionEnd
    UserPromptSubmit -.可触发.-> SubagentStart
    SubagentStart --> SubagentStop
    UserPromptSubmit -.中断.-> Interrupt
```

### Y.32.2 Hook 的执行模式

Hook 可以是本地处理器，也可能通过 MCP 执行。无论形式如何，都需要：事件 payload schema、超时、取消、输出大小限制、失败策略和来源标识。安全敏感 Hook 尤其要明确“失败时继续还是阻止”。例如 PreToolUse/PermissionRequest 更适合 fail closed，纯通知型 PostToolUse 失败可以记录警告后继续。

### Y.32.3 Hook 配置层与托管限制

`requirements.toml` 可设置 `allow_managed_hooks_only = true`，忽略用户、项目和 session Hook，只保留 requirements/managed config 提供的托管 Hook。该设置只在 requirements 层有效，放进普通 `config.toml` 不会启用。这个设计避免项目仓库自行声明 Hook 后获得组织环境中的执行机会。

### Y.32.4 PreToolUse 重写的约束

重写必须保持可审计：原始参数、重写结果、Hook 来源和最终审批动作都应有关联。Hook 不能把一个只读调用重写成写操作后沿用原审批。任何扩大资源范围的修改都必须重新进入权限解析。

### Y.32.5 输出 spill

Hook 输出可能很大，源码包含 output spill 模块，说明系统会把过大输出转存而不是全部塞入内存或协议消息。设计上 spill 文件也需要生命周期、权限、清理和敏感信息规则。

### Y.32.6 Hook 调用时序

```mermaid
sequenceDiagram
    participant Core as Session/ToolRegistry
    participant Hooks as Hook Dispatcher
    participant Handler as Local/MCP Handler
    participant Policy as Permission Resolver
    participant Tool as Tool Handler

    Core->>Hooks: PreToolUse(payload)
    Hooks->>Handler: dispatch with timeout
    Handler-->>Hooks: continue/block/rewrite
    Hooks-->>Core: outcome
    Core->>Policy: 解析最终动作权限
    Policy-->>Core: 需要审批
    Core->>Hooks: PermissionRequest(payload)
    Hooks-->>Core: 决策或补充
    Core->>Tool: execute
    Tool-->>Core: result
    Core->>Hooks: PostToolUse(payload)
    Hooks-->>Core: final outcome
```

---

## Y.33 扩展 API 与数据作用域

### Y.33.1 为什么需要 extension data

Skill、Plugin、Hook 和未来扩展需要保存状态，但不能都向 Session 增加字段。源码使用 session/thread/turn 等作用域的数据存储，让扩展以类型或键访问自己的状态。正确作用域能防止：

- Turn 临时状态泄漏到下一轮；
- 子 Agent 与根线程意外共享私有数据；
- 插件卸载后残留不可识别状态；
- 并行工具调用修改同一非线程安全对象。

### Y.33.2 作用域模型

```mermaid
flowchart TB
    Process[进程级服务]
    SessionStore[Session Extension Store]
    ThreadStore[Thread Extension Store]
    TurnStore[Turn Extension Store]
    ToolStore[Tool-call 临时状态]

    Process --> SessionStore
    SessionStore --> ThreadStore
    ThreadStore --> TurnStore
    TurnStore --> ToolStore
```

### Y.33.3 Contributor 模式

核心在发生 Skill 调用等事件时遍历 extension contributors，向其传入明确作用域的 store 和结构化 input。这是一种轻量观察者/插件接口：核心定义生命周期事实，扩展决定如何记录或派生行为。Contributor 不应反向控制核心关键安全决策，除非接口明确允许。

---

## Y.34 多 Agent 控制面：根线程树隔离、共享预算与 AgentPath

### Y.34.1 AgentControl 的范围

`AgentControl` 每个根 Thread/Session 树最多创建一次，并与该根下所有子 Agent 共享。它不挂在全局 ThreadManager 上，以避免不同用户任务或根线程之间共享 Agent 注册表。控制对象包含 SessionId、指向 ThreadManagerState 的 Weak、ThreadId 生成器、AgentRegistry、V2 residency、执行并发限制器、rollout 预算和根 service tier。

```mermaid
flowchart TB
    Global[ThreadManagerState]

    subgraph TreeA[根线程树 A]
      AC1[AgentControl A]
      RootA[Root Thread]
      A1[Subagent /a]
      A2[Subagent /b]
      A11[Subagent /a/test]
      AC1 --> RootA
      AC1 --> A1
      AC1 --> A2
      AC1 --> A11
    end

    subgraph TreeB[根线程树 B]
      AC2[AgentControl B]
      RootB[Root Thread]
      B1[Subagent /research]
      AC2 --> RootB
      AC2 --> B1
    end

    AC1 -.Weak.-> Global
    AC2 -.Weak.-> Global
```

### Y.34.2 AgentPath 与昵称

V2 多 Agent 使用类似层级路径的 AgentPath，当前 Agent 可以解析相对引用并定位 live Agent。Registry 还为子 Agent 保留昵称和角色，创建时先做 reservation，避免并发 spawn 产生重复路径或昵称。

### Y.34.3 Spawn 的继承与裁剪

子 Agent 可以从父线程分叉完整历史或最近 N 个 Turn，并继承环境、服务等级、cyber access、角色配置和多 Agent 使用提示。历史裁剪能减少 token 与数据泄漏，但过度裁剪会让子 Agent缺少任务背景。因此 fork mode 是明确选项，而非隐藏行为。

```mermaid
sequenceDiagram
    participant P as Parent Agent
    participant AC as AgentControl
    participant R as AgentRegistry
    participant TM as ThreadManager
    participant C as Child Agent

    P->>AC: spawn(role, path, fork_mode)
    AC->>R: reserve path/nickname
    R-->>AC: SpawnReservation
    AC->>AC: 检查并发与 rollout 预算
    AC->>TM: fork/start child thread
    TM-->>AC: CodexThread
    AC->>R: 提交 AgentMetadata
    AC->>C: 启动子 Turn
    C-->>P: activity / completion notification
```

### Y.34.4 执行限制与 rollout 预算

没有限制的子 Agent 递归会造成线程爆炸、模型费用失控和本地进程耗尽。`AgentExecutionLimiter` 管理最大并发线程，`RolloutBudget` 在根树范围共享，确保子 Agent 的创建和执行消耗同一个预算而不是各自重置。

### Y.34.5 Agent 间通信

`send_inter_agent_communication` 可以向目标线程提交结构化通信，并选择是否触发 Turn。通信带来源、目标、消息、上下文和 parent/root turn 信息。若 `trigger_turn=false`，消息可作为通知进入上下文而不立即启动模型；若为 true，则在启动前检查执行容量。

### Y.34.6 状态订阅与完成 watcher

AgentControl 可订阅目标 Agent 的 watch 状态。对由 ThreadSpawn 创建的子 Agent，会启动 detached watcher，直到状态进入最终态，再向父 Agent发送完成消息或注入 SubagentNotification。watch channel 适合“只关心最新状态及变化”，无需为每次状态更新建立重型事件存储。

```mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Running
    Running --> Waiting
    Waiting --> Running
    Running --> Completed
    Running --> Failed
    Running --> Cancelled
    Completed --> [*]
    Failed --> [*]
    Cancelled --> [*]
```

### Y.34.7 Agent 死亡清理

若线程请求返回 InternalAgentDied，控制面会从 ThreadManagerState 移除线程、清理 V2 residency 并释放 registry 中的 spawned thread。该顺序防止已死亡 Agent 仍占并发额度或可被路径解析到。

### Y.34.8 多 Agent 的一致性风险

- 父 Agent 关闭时子 Agent 是否级联取消；
- 子 Agent 完成通知与父 Turn 结束的竞态；
- 同一路径 reservation 未提交时异常；
- 子 Agent 修改同一工作区造成文件冲突；
- 共享 service tier 或权限变更的可见时机；
- rollout 预算在恢复线程后的重建；
- V1/V2 兼容事件重复。

这些问题说明多 Agent 并非“多开几个 Session”，而是一套需要树形身份、预算、通信和资源治理的控制面。

---

## Y.35 角色、注册表与协作模式

### Y.35.1 角色解析

Agent role 配置可以影响昵称候选、指令、模型或工具策略。角色应通过 resolver 得到有效配置，而不是让模型任意声明自己获得新权限。默认角色与自定义角色都仍受根线程管理策略约束。

### Y.35.2 AgentRegistry

Registry 保存线程 ID、AgentPath、昵称、角色和 spawn 状态之间的映射，并提供 reservation/commit/release。两阶段保留模式适合异步 spawn：先原子占用逻辑名称，线程创建成功后提交；失败则释放。

```mermaid
stateDiagram-v2
    [*] --> Free
    Free --> Reserved: reserve path/nickname
    Reserved --> Active: child thread created + commit
    Reserved --> Free: spawn failed + release
    Active --> Final: agent completed/failed
    Final --> Free: cleanup/release
```

### Y.35.3 Collaboration Mode

协作模式决定主 Agent 如何规划、委派、等待和整合子 Agent。它属于提示与运行策略，不应改变底层安全模型。无论是单 Agent、计划模式还是多 Agent 模板，工具调用最终都进入同一个 Registry/Orchestrator。

### Y.35.4 文件冲突治理

当前架构允许多个线程存在，但共享工作区写入天然有冲突。可行治理策略包括：

- 子 Agent 使用独立 worktree；
- 按文件/目录划分任务并在提示中声明边界；
- 写工具对路径加锁；
- 父 Agent 只让子 Agent 返回建议或 patch，不直接落盘；
- 合并前计算基线 diff 并检测漂移。

源码中独立 `worktree` crate 与 Agent 图相关模块，为更强隔离提供了基础，但具体产品策略仍可能变化。

---

## 第六篇·持久化、恢复与一致性

## Y.36 ThreadStore：存储中立的线程持久化接口

### Y.36.1 接口定位

`codex-thread-store` 的模块注释将其定义为存储中立的线程持久化接口，稳定句柄是 ThreadId，具体实现可以解析到本地 rollout、RPC 或其他后端。当前本地实现以文件和 SQLite 为主，但核心不把该实现写死。

ThreadStore 的能力覆盖创建、恢复、追加、持久化、flush、shutdown、discard、读取历史、获取最新模型上下文、准备 fork、revert、列表、搜索、Turn/Item 时间线、metadata、section、project、archive 与 delete。它已经超出普通 repository CRUD，更接近 Thread 领域的持久化端口。

```mermaid
classDiagram
    class ThreadStore {
      <<trait>>
      +create_thread()
      +resume_thread()
      +append_items()
      +flush()
      +load_history()
      +latest_model_context()
      +prepare_fork()
      +revert()
      +list_threads()
      +search_threads()
      +archive()
      +delete()
    }

    class LocalThreadStore {
      +rollout filesystem
      +SQLite projection
      +per-thread locks
    }

    class RemoteThreadStore {
      <<possible implementation>>
      +RPC backend
    }

    LocalThreadStore ..|> ThreadStore
    RemoteThreadStore ..|> ThreadStore
```

### Y.36.2 PersistContext

持久化调用区分 Standard 与 TurnStart 等上下文。Turn 开始是特殊边界：它可能需要确保 rollout 物化、写入配置或环境基线，并建立后续 Item 的有序锚点。把上下文作为显式枚举比依赖调用顺序猜测更可靠。

### Y.36.3 为什么接口要返回领域对象

列表和读取不只是文件路径。ThreadStore 需要返回 Thread metadata、历史项、模型上下文和时间线，让上层不关心 JSONL 或 SQLite 表结构。这样未来迁移 schema 或引入远端存储时，核心 Agent Loop 不必变化。

---

## Y.37 LocalThreadStore：JSONL 事实日志与 SQLite 查询投影

### Y.37.1 双存储设计

源码注释明确说明：JSONL rollout 是耐久、可回放、即使没有 SQLite 也可读取的格式，并兼容旧版本；SQLite 是用于快速列表和读取的可查询 metadata 索引。live append 先写 canonical JSONL，派生 metadata 再通过 store 更新 SQLite。

```mermaid
flowchart LR
    Core[Session/Core] --> Items[Canonical Rollout Items]
    Items --> Writer[Per-thread Writer]
    Writer --> JSONL[(JSONL Rollout\n事实来源)]
    Items --> Projector[Metadata Projector]
    Projector --> SQLite[(SQLite\n查询投影)]
    JSONL --> Backfill[Backfill/Reconcile]
    Backfill --> SQLite
    SQLite --> List[线程列表/搜索/时间线]
    JSONL --> Replay[恢复/审计/兼容读取]
```

### Y.37.2 为什么不是只用 SQLite

只用 SQLite 的优点是事务和查询方便，但会增加手工查看、流式追加、版本兼容和故障恢复复杂度。JSONL 每行一个带时间戳、ordinal 和 item 的对象，适合追加、复制、诊断与重放。即使数据库损坏，rollout 仍可用于恢复。

### Y.37.3 为什么不是只用 JSONL

当线程数量增长后，按更新时间、项目、归档状态、关键词或 section 查询时，扫描所有文件成本很高。SQLite 投影可以维护 metadata、分页索引和搜索结构。它是性能层，而不是唯一事实源。

### Y.37.4 一致性模型

双写系统最大的风险是 JSONL 与 SQLite 不一致。Codex 的思路接近事件溯源：

1. canonical item 追加到 rollout；
2. 同一语义驱动 SQLite 投影；
3. 启动或发现缺失时从 rollout backfill/reconcile；
4. 查询投影不完整时允许降级或触发重建。

SQLite 更新成功但 JSONL 失败不应被视为线程已持久化，因为投影无法独立重放。反过来，JSONL 成功而 SQLite 失败可以通过修复恢复。

### Y.37.5 每线程锁

LocalThreadStore 使用 per-thread writer/lifecycle locking，目的包括：

- 保证同一线程追加顺序；
- 防止 append 与 discard/shutdown 竞态；
- 避免两个 writer 同时写同一文件；
- 让 flush 对此前追加建立明确 happens-before；
- 不用一个全局锁阻塞所有线程。

```mermaid
sequenceDiagram
    participant A as Append Task A
    participant B as Append Task B
    participant L as Per-thread Lock
    participant W as Writer
    participant J as JSONL

    A->>L: acquire
    A->>W: append item ordinal n
    W->>J: write line n
    A->>L: release
    B->>L: acquire
    B->>W: append item ordinal n+1
    W->>J: write line n+1
    B->>L: release
```

### Y.37.6 热线程元数据更新

热线程的元数据变更必须进入 Session/rollout 顺序；冷线程则可直接修改 store 投影。这避免“SQLite 显示已归档，但 rollout 后到的事件又把状态还原”一类顺序问题。

---

## Y.38 RolloutRecorder：追加日志的写入、flush 与容错

### Y.38.1 Canonical JSONL

RolloutRecorder 将 canonical items 写为一行一个 JSON 对象，包含时间、序号和内容。文本格式利于人工检查和逐行容错；ordinal 则提供显式顺序，不完全依赖文件偏移或时间戳。

### Y.38.2 Writer task

调用方通过 mpsc 把写入请求交给后台 writer。这样模型与工具事件不必每次等待同步磁盘 I/O，但必须处理背压、writer 崩溃和 shutdown flush。

```mermaid
flowchart LR
    Producers[Session / Tool / Context] --> Queue[mpsc Writer Queue]
    Queue --> Task[Rollout Writer Task]
    Task --> Serialize[序列化 + ordinal]
    Serialize --> File[(JSONL File)]
    Flush[Flush Request] --> Queue
    Shutdown[Shutdown Request] --> Queue
    Task --> Ack[持久化确认/错误]
```

### Y.38.3 新建与恢复的差异

新会话可延迟创建 rollout，直到首个需要持久化的 item；恢复会话必须立即打开已有文件并确定后续 ordinal。若恢复时仍按新建逻辑覆盖文件，会造成灾难性数据丢失，因此两条构造路径必须显式区分。

### Y.38.4 解析容错

读取历史时，个别行解析失败可以记录并继续，避免整个长期会话因单条损坏不可恢复。但容错不能悄悄改变语义：应暴露诊断，必要时停止在无法确认顺序的位置。对尾部半写行，可以判断为崩溃残留并安全忽略或修复。

### Y.38.5 幂等与重试

writer 的持久化请求需要避免因超时重试写入重复 item。ordinal、item ID 或上层请求标识可以用于去重。对于 flush，重复调用应安全；对于 discard，则必须与 append 互斥并明确是否删除已落盘内容。

### Y.38.6 崩溃恢复

```mermaid
flowchart TD
    Crash[进程异常退出] --> Scan[启动扫描 rollout]
    Scan --> Tail{尾行是否完整}
    Tail -- 是 --> Parse[逐行解析]
    Tail -- 否 --> Trim[标记/忽略半写尾行]
    Trim --> Parse
    Parse --> Ordinal[恢复最后 ordinal]
    Ordinal --> State{SQLite 投影完整?}
    State -- 否 --> Backfill[从 items 重建]
    State -- 是 --> Ready[线程可恢复]
    Backfill --> Ready
```

---

## Y.39 SQLite State Runtime：查询、迁移与投影重建

### Y.39.1 多数据库文件

源码中可见多个版本化数据库文件，例如 state、logs、goals、memories、queue、thread history。版本化文件名有助于在 schema 大幅变化时并存或迁移，也避免不同逻辑数据库互相放大锁竞争。

### Y.39.2 连接与 PRAGMA

StateRuntime 使用 SQLx/SQLite，并配置 WAL、`synchronous=NORMAL`、增量 auto-vacuum、busy timeout 和有限连接池。选择 WAL 是为了允许读写并行；NORMAL 在耐久性与性能之间折中；busy timeout 避免瞬时锁竞争立刻失败；连接池上限防止本地 CLI 创建过多数据库连接。

```mermaid
flowchart TB
    Handle[StateDbHandle = Arc<StateRuntime>]
    Handle --> Pool[SQLx Pool]
    Pool --> C1[Connection 1]
    Pool --> C2[Connection 2]
    Pool --> C3[Connection ... max 5]
    C1 --> WAL[(SQLite WAL)]
    C2 --> WAL
    C3 --> WAL
    WAL --> Tables[threads/items/metadata/...]
```

### Y.39.3 迁移策略

多个 SQLx migrator 负责不同数据库 schema。源码还考虑“数据库已被更新版本二进制迁移”的情况：旧二进制不能盲目降级 schema，需要校验已知 migration checksum 并安全处理未知新版本。这个问题在 CLI 自动更新/回滚中非常现实。

### Y.39.4 Backfill 与 reconcile

初始化时打开 runtime 并从 rollout backfill；也可以针对单线程 items 重建或扫描文件。Backfill 应具备：

- 可中断与可恢复；
- 幂等 upsert；
- 不阻塞所有交互启动；
- 有进度与错误统计；
- 不把损坏 rollout 的部分数据伪装为完整。

### Y.39.5 投影滞后

SQLite 投影可能短暂落后于 JSONL。查询 API 应明确是否要求强一致；线程列表可以接受轻微延迟，刚完成 Turn 后的详情读取则可能需要先 flush 或直接从 live state 读取。把所有查询都强制等待磁盘同步会降低交互性能。

---

## Y.40 恢复、分叉、回退、归档与删除

### Y.40.1 恢复

恢复 Thread 需要加载 metadata、历史、最新模型上下文和配置来源，重新建立 Session 服务与 writer，并产生宿主可理解的 configured 事件。恢复不是把 JSONL 文本全部发送给模型；应选择当前模型上下文投影，并保留完整历史供 UI 与审计。

```mermaid
sequenceDiagram
    participant H as Host
    participant TM as ThreadManager
    participant TS as ThreadStore
    participant R as Rollout
    participant DB as SQLite
    participant S as Session

    H->>TM: resume_thread(thread_id)
    TM->>TS: read metadata/history/context
    TS->>DB: 查询投影
    alt 投影完整
        DB-->>TS: metadata + indexes
    else 投影缺失
        TS->>R: 重放 canonical items
        R-->>TS: history
        TS->>DB: reconcile
    end
    TS-->>TM: ResumedHistory
    TM->>S: 构造 Session + 打开 writer
    S-->>H: CodexThread + configured event
```

### Y.40.2 分叉

Fork 复制到某个历史边界的模型上下文与必要 metadata，生成新 ThreadId 和新 rollout。新线程不能继续写父线程文件；父子关系应记录，便于 UI 展示和审计。多 Agent spawn 也可以复用 fork 语义，但会附加 SubAgentSource、角色和 parent/root turn 信息。

### Y.40.3 回退

Revert 可能意味着把模型上下文回到某个 Turn，并恢复文件变更。持久化层至少能截取历史；文件系统回退则需要 Git/diff 或工作树快照支持。不能只删除 UI 消息而保留后续工具事实，否则模型状态和磁盘状态分裂。

### Y.40.4 归档

Archive 是可逆的可见性状态，通常更新 metadata 而不删除 rollout。Unarchive 恢复列表可见。由于热线程可能仍有事件，归档请求应先通过 live thread 保序或限制在空闲态。

### Y.40.5 删除

Delete 是不可逆或更强的生命周期操作，需要处理：

- 活动 Turn 与子进程；
- rollout 文件；
- SQLite 投影；
- spill/附件/临时文件；
- 子 Agent 关系；
- 归档与列表缓存；
- 失败后的部分删除恢复。

安全做法是先停止运行并标记删除，再按可重试步骤清理；不要先删事实日志、后发现进程仍在写。

---

## Y.41 一致性与故障模式分析

### Y.41.1 故障矩阵

| 故障 | 用户表象 | 核心风险 | 恢复策略 |
|---|---|---|---|
| rollout writer 失败 | Turn 看似运行但无法持久化 | 会话丢失、审计缺口 | 发出错误、阻止安全结束、允许重试 flush |
| SQLite busy/损坏 | 列表缺失或查询失败 | 投影不可用 | 从 JSONL 降级读取并重建 |
| 模型流中断 | 回复停在中间 | 重试导致重复成本 | 依据传输状态重试或结束 |
| 工具进程卡死 | 一直显示运行 | 资源泄漏、副作用延迟 | 超时、取消、进程树终止 |
| Hook 超时 | 工具迟迟不执行 | 策略不确定 | 按事件安全级别 fail closed/告警 |
| Guardian malformed | 审批无结果 | 错误放行 | 严格拒绝 |
| MCP server 崩溃 | 工具不可用 | 调用悬挂 | 超时、断连事件、可控重启 |
| 子 Agent 异常死亡 | 父 Agent 永久等待 | 预算占用 | 状态 watcher、清理 registry、通知父线程 |
| TUI 崩溃 | 终端状态异常 | 后台任务仍运行 | RAII 恢复终端、Session shutdown |

### Y.41.2 事务边界

Agent 系统无法把模型调用、文件修改、网络请求和 SQLite 更新放进一个 ACID 事务。因此它采用补偿与可回放设计：

- 对不可回滚副作用先审批；
- 对文件修改记录 diff；
- 对历史使用追加日志；
- 对索引允许重建；
- 对执行状态发明确事件；
- 对失败保留足够诊断，让下一轮或用户补偿。

### Y.41.3 exactly-once 幻觉

分布式或异步系统很难实现真正 exactly-once。Codex 更现实的目标应是：对本地 canonical append 近似 exactly-once；对模型请求和外部工具使用幂等键或明确 at-least-once 风险；对非幂等动作不自动重试。文档读者在扩展工具时必须声明副作用与重试安全性。

### Y.41.4 背压

模型 token、命令输出和事件通知速度可能超过宿主消费。无界 channel 会耗尽内存；过小有界 channel 会阻塞核心。合理设计是：

- 稳定语义事件不丢；
- 可合并的进度/delta 做批量或节流；
- 大输出 spill 到文件；
- 工具输出给模型前截断；
- App Server 对慢客户端设置队列上限和断开策略。

```mermaid
flowchart LR
    Fast[高速生产者] --> Buffer[有界缓冲]
    Buffer --> Classify{事件类别}
    Classify -->|不可丢| Backpressure[施加背压]
    Classify -->|可合并 delta| Coalesce[合并/节流]
    Classify -->|大块输出| Spill[落盘并发送引用]
    Backpressure --> Consumer[宿主消费者]
    Coalesce --> Consumer
    Spill --> Consumer
```

---

## 第七篇·配置、认证、实时会话与可观测性

## Y.42 配置系统：多层合并、约束与来源可解释性

### Y.42.1 配置 crate 的真实复杂度

`codex-config` 不是简单的 TOML 反序列化模块。它包含认证策略、浏览器/计算机使用要求、云端配置包、配置层来源、requirements、约束、诊断、指纹、Hook、MCP、权限、Plugin、Profile、项目根标记、执行策略、shell 环境、Skill、严格配置和线程配置加载器等模块。

这反映出 Codex 配置系统同时服务三类主体：个人用户表达偏好，项目表达上下文，组织管理员表达不可突破的边界。三者不能用普通“后加载覆盖先加载”处理，因为安全上限必须是约束，不是默认值。

```mermaid
flowchart TB
    Defaults[内置默认值]
    User[用户 config.toml]
    Project[项目配置]
    Session[会话/CLI 覆盖]
    Cloud[Cloud Config Layers]
    Managed[requirements / managed config]

    Defaults --> Merge[配置层合并]
    User --> Merge
    Project --> Merge
    Session --> Merge
    Cloud --> Merge
    Managed --> Constraints[约束组合]
    Merge --> Validate[类型化解析与严格校验]
    Constraints --> Validate
    Validate --> Effective[有效 Session/Turn 配置]
    Validate --> Diagnostics[来源化错误诊断]
```

### Y.42.2 ConfigLayerSource

每个配置值若只保留最终结果，用户无法理解“为什么这个开关无法修改”。ConfigLayerSource 与 metadata 让系统可以指出值来自默认、用户文件、项目、云端或 requirements。对受约束项，还需要显示约束来源。

### Y.42.3 Constraint 模型

`Constrained<T>` 一类设计表达“当前值及其允许集合/范围”。例如沙箱模式、模型、网络域、MCP server、浏览器使用或自动审查都可能被 requirements 限制。计算有效值时应返回 ConstraintError，而不是静默采用不符合用户请求的值。

### Y.42.4 TOML 合并与结构化路径

普通 TOML merge 在数组、表和删除语义上容易产生歧义。配置系统需要明确哪些路径是结构化 feature、哪些表按键合并、哪些列表整体替换。CLI override 也应先构造成一层 TOML/typed layer，再走相同验证，避免命令行成为绕过严格校验的特殊通道。

### Y.42.5 严格配置

Strict config 检测未知或被忽略字段。快速演进的工具若静默忽略拼写错误，会让用户误以为安全策略已经生效。例如把 `approval_policy` 拼错后继续运行可能导致危险默认值。严格模式应给出文件、行列、字段路径和建议。

### Y.42.6 配置指纹

对配置生成 fingerprint/version 可以用于缓存失效、线程恢复比较和诊断。指纹必须基于规范化表示，避免纯格式变化导致无意义失效；同时不能包含原始秘密。

### Y.42.7 线程配置加载器

源码暴露 Noop、Static、Remote 等 ThreadConfigLoader。线程配置可来自静态本地状态，也可在创建/恢复时由远端加载。核心只依赖 loader trait，便于 App Server、云工作区或企业策略在不修改 Session 构造主线的情况下提供配置。

```mermaid
sequenceDiagram
    participant H as Host
    participant L as ThreadConfigLoader
    participant C as Config Layer Stack
    participant R as Requirements
    participant V as Validator
    participant S as Session

    H->>L: load(ThreadConfigContext)
    L-->>H: UserThreadConfig / source
    H->>C: 合并 defaults、user、project、CLI、cloud
    H->>R: compose requirements for hostname
    C->>V: merged TOML
    R->>V: constraints
    V-->>H: SessionThreadConfig
    H->>S: 构造线程
```

---

## Y.43 Profile、Feature Gate 与渐进式演进

### Y.43.1 Profile 的价值

Profile 将模型、推理、权限、工具暴露和界面偏好组合成可命名配置。相比用户每次输入大量参数，Profile 更适合在“只读分析”“常规编码”“高权限维护”“自动化 CI”之间切换。

### Y.43.2 Profile 不能越过 Requirements

Profile 是便捷层，不是管理员策略。选择更宽松 Profile 时仍要经过 Constraints。恢复线程时还应记录当时使用的 Profile 名与有效配置，避免同名 Profile 后续修改导致历史行为不可解释。

### Y.43.3 Feature Gate

高频演进项目需要 feature gate 控制实验能力、灰度和回滚。Gate 影响工具暴露、协议字段、模型特性和子系统启动。理想状态下，Gate 判定集中，代码路径在关闭时完全不初始化昂贵资源。

```mermaid
flowchart LR
    Build[编译能力]
    Server[服务端能力]
    Config[本地配置]
    Managed[管理员要求]
    Model[模型能力]
    Gate[Feature Resolution]
    Enabled[启用]
    Disabled[关闭并解释原因]

    Build --> Gate
    Server --> Gate
    Config --> Gate
    Managed --> Gate
    Model --> Gate
    Gate --> Enabled
    Gate --> Disabled
```

### Y.43.4 前向与后向兼容

Feature 关闭时，协议客户端可能仍发新字段；旧客户端可能不认识新事件。服务端应忽略可选未知字段、对不可用功能返回结构化错误，并让 EventMsg 序列化保持 tagged union 的可演进性。持久化数据也要记录 schema/version 以便新旧二进制安全处理。

---

## Y.44 认证：多种凭据来源与可恢复登录

### Y.44.1 AuthManager 的职责

`codex-login` 支持 API key、访问令牌、ChatGPT/OAuth、设备码、Bedrock 凭据、外部认证、Agent Identity、Personal Access Token 等模式，并暴露 AuthManager、认证头、凭据存储模式、刷新和未授权恢复。

```mermaid
flowchart TB
    Sources[凭据来源]
    Sources --> API[API Key 环境变量/存储]
    Sources --> OAuth[浏览器 PKCE 登录]
    Sources --> Device[Device Code]
    Sources --> External[External Auth]
    Sources --> Workload[Agent/Workload Identity]
    Sources --> Bedrock[Bedrock Keys]

    API --> Manager[AuthManager]
    OAuth --> Manager
    Device --> Manager
    External --> Manager
    Workload --> Manager
    Bedrock --> Manager
    Manager --> Headers[AuthHeaders]
    Headers --> Model[模型/后端请求]
```

### Y.44.2 浏览器 PKCE 登录

本地 LoginServer 启动回调端点，生成 PKCE challenge，打开浏览器，接收 callback 并保存认证。安全要点包括随机 state、回调端口限制、一次性使用、超时、成功页不泄漏 token，以及恶意本地进程抢占回调的防护。

### Y.44.3 Device Code

无浏览器或远程终端可以使用 device code：客户端请求 code，用户在另一设备完成授权，客户端轮询直到成功或过期。轮询需要遵守 interval、slow_down、取消和超时，不应把设备码写入长期日志。

### Y.44.4 凭据优先级

环境变量、auth.json、keyring、外部 provider 和 CLI 参数可能同时存在。优先级必须可预测，并受 ManagedAuthPolicy 限制。企业环境可能只允许某种身份，不能因为用户设置 API key 就绕过。

### Y.44.5 未授权恢复

模型请求返回 401 时，AuthManager 可判断是否刷新、重新读取外部身份或提示登录。恢复过程要防止并发请求同时刷新导致令牌竞争，可采用 single-flight。刷新失败应产生可识别事件，让 TUI/App Server 引导用户，而不是把 401 伪装成模型错误。

### Y.44.6 退出与撤销

logout 可以只删除本地凭据，也可尝试服务端 revoke。撤销网络失败时，本地清理和远端状态要分别报告；不能因为 revoke 失败就继续保留本地 token。多个存储后端也要清理一致。

---

## Y.45 模型提供方与模型管理

### Y.45.1 ModelProviderInfo

模型提供方抽象需要描述 endpoint、认证方式、传输能力、请求头、模型映射、重试和环境。它让核心 Prompt/Agent Loop 不必写死唯一 URL，也支持官方、云平台或兼容提供方。

### Y.45.2 ModelsManager

模型管理器负责获取、缓存和解析可用模型信息。ModelInfo 不只是名称，还可能包含上下文窗口、默认推理等级、工具能力、WebSocket/Realtime 支持、输出限制和显示信息。Turn 解析有效模型时应拿到完整 ModelInfo。

```mermaid
flowchart LR
    Config[用户选择模型] --> MM[ModelsManager]
    Backend[后端模型目录] --> MM
    Cache[本地缓存] --> MM
    MM --> Resolve[解析 ModelInfo]
    Resolve --> Cap[能力检查]
    Cap --> Turn[TurnContext]
    Turn --> Prompt[工具/并行/输出 schema 配置]
```

### Y.45.3 模型重路由与验证事件

协议包含 model reroute/verification 等事件，说明客户端可能看到实际模型选择变化。宿主应展示而不是静默隐藏，尤其当模型能力、成本或安全策略不同。线程配置快照应区分用户请求模型与最终有效模型。

### Y.45.4 本地或外部模型

即使提供方支持兼容 API，也不意味着具备相同工具调用和严格 schema 能力。适配层需要能力探测或配置，并在缺失时关闭相关工具/模式。用“接口兼容”推断“行为兼容”会导致 Agent Loop 反复解析失败。

---

## Y.46 Realtime Conversation：语音、文本与后台 Agent 的双向协作

### Y.46.1 实时会话不是普通 Turn 流

`realtime_conversation.rs` 维护独立 RealtimeConversationManager，处理音频输入、文本输入、输出事件、WebSocket、WebRTC sideband、existing call 和 handoff。源码为音频、文本、handoff 和输出事件设置有界队列，并为启动上下文、助手输出、初始 items 和模式指令设置 token 限制。

```mermaid
flowchart TB
    User[用户语音/文本] --> Host[App/TUI Host]
    Host --> Manager[RealtimeConversationManager]
    Manager --> AudioQ[有界音频队列]
    Manager --> TextQ[有界文本队列]
    AudioQ --> WS[Realtime WebSocket/WebRTC]
    TextQ --> WS
    WS --> Events[Realtime Events]
    Events --> Transcript[转写与对话状态]
    Events --> AudioOut[语音输出]
    Events --> Handoff[后台 Codex Handoff]
    Handoff --> Agent[普通 Thread/Turn]
    Agent --> BackendMsg[后台进度/最终结果]
    BackendMsg --> Manager
```

### Y.46.2 有界队列

音频帧产生速度稳定且高，若网络或客户端变慢，无界队列会持续增长。源码中的固定容量体现了背压意识。队列满时要选择丢弃、阻塞或结束；音频通常可容忍有限丢帧，但控制事件不可随意丢失。

### Y.46.3 启动上下文预算

实时模型需要知道当前项目与对话，但不能加载完整长线程。启动上下文预算约束信息量，初始 item 数和 token 也有限。该设计强调“为实时延迟建立专用上下文投影”，而不是复用普通大 Prompt。

### Y.46.4 Handoff

实时对话可把任务交给后台编码 Agent。前台继续接收用户语音，后台 Thread 执行工具，再把进度和最终消息通过 handoff 流返回。为了避免输出淹没实时模型，handoff 有刷新间隔和截断标记。

```mermaid
sequenceDiagram
    participant U as 用户
    participant R as Realtime Agent
    participant C as Codex Core
    participant B as Background Turn

    U->>R: “修复测试并告诉我结果”
    R->>C: RealtimeHandoffRequested
    C->>B: 启动后台 Turn
    R-->>U: 确认任务已交给后台
    loop 后台执行
        B-->>C: 工具/进度事件
        C-->>R: [BACKEND] handoff stream
        R-->>U: 必要的简短进度
    end
    B-->>C: Agent Final Message
    C-->>R: 完成通知
    R-->>U: 汇总结果
```

### Y.46.5 结束语义

实时会话可因用户请求、传输关闭或错误结束。结束时需要停止 fanout task、关闭 writer、处理剩余 transcript tail，并决定后台 Agent 是否继续。源码区分 Await/Detach 等任务停止方式，说明“关闭实时通话”与“取消后台编码任务”并非总是等价。

### Y.46.6 音频与隐私

音频帧和 transcript 属于高敏感数据。日志不应默认记录原始音频；遥测应使用大小、时延和状态而非内容。Handoff 也应只把任务相关内容发送给后台 Agent，避免把整个私人对话无差别注入项目线程。

---

## Y.47 OpenTelemetry：从日志到跨边界 Trace

### Y.47.1 OTEL crate

`codex-otel` 包含配置、事件、指标、provider、trace context、OTLP、目标和工具结果处理。它暴露 SessionTelemetry、RuntimeMetrics、Timer、W3C trace context 注入/提取以及 exporter 配置。

```mermaid
flowchart LR
    Host[CLI/App Server] --> Root[Session Span]
    Root --> Turn[Turn Span]
    Turn --> Sample[Model Sample Span]
    Turn --> Tool[Tool Call Span]
    Tool --> Approval[Approval Span]
    Tool --> Exec[Sandbox Exec Span]
    Turn --> Compact[Compaction Span]
    Turn --> Agent[Subagent Span]
    Root --> Export[OTLP/Configured Exporter]
```

### Y.47.2 Trace Context 传播

App Server 或远程执行可携带 W3C `traceparent`/`tracestate`。核心从环境或请求读取父上下文，并向后端或子服务注入。这样一次用户操作可以跨 App Server、模型请求、MCP 和执行服务形成完整链路。

### Y.47.3 指标标签清洗

模型 slug、Skill 名、Plugin ID 等来自动态输入，直接作为标签可能包含非法字符或形成高基数。源码提供 `sanitize_metric_tag_value`。高基数字段如 ThreadId、call ID 更适合 trace attribute/log，而非 metrics tag。

### Y.47.4 ToolDecisionSource

OTEL 类型区分 AutomatedReviewer、Config、User，便于分析工具放行来自谁。安全运营可观察 Guardian 自动批准率、用户拒绝率和配置直接允许比例，从而调整默认策略。

### Y.47.5 内容与元数据分离

可观测性应记录模型名、时延、token、状态、错误类型、工具类别和审批来源，不默认记录完整 Prompt、文件内容、命令输出和密钥。调试捕获需要显式开关、脱敏和保留期限。

---

## Y.48 Analytics：产品事实与运行时遥测的分工

### Y.48.1 Analytics facts

`codex-analytics` 定义大量结构化事实：compaction、Goal、Turn steer、Code Mode、Control Tool、Hook、Plugin 安装、Skill 调用、子 Agent 启动、Turn 配置、状态、token 使用和错误。它与 OTEL 的区别可概括为：OTEL 更关注运行链路和运维，Analytics 更关注用户/产品行为事实。

### Y.48.2 Reducer

事件可能高频且粒度细，analytics reducer 可以把多条运行事件聚合为稳定事实。例如一次 Turn 从 Started 到 Completed 形成一条状态事实，多个 token 更新形成最终 token usage。这样减少上报噪音，也能处理乱序或重试。

```mermaid
flowchart LR
    Runtime[运行时原始事件] --> Reducer[Analytics Reducer]
    Reducer --> Facts[结构化 Facts]
    Facts --> Client[AnalyticsEventsClient]
    Client --> Backend[遥测后端]
    Runtime --> OTEL[Trace/Metrics]
```

### Y.48.3 时间函数与溢出

源码将系统时间转换为 u64 秒/毫秒，并对异常或溢出使用安全默认。虽然看似细节，但遥测代码不应因系统时钟早于 epoch 或整数转换 panic 影响主流程。

### Y.48.4 隐私原则

Analytics fact 应尽量是枚举、计数、布尔和已清洗标识；代码内容、自然语言 Prompt 和完整路径不应成为默认字段。Plugin/Skill 归因可使用稳定 ID，但要避免把用户自定义秘密名称直接上报。

---

## Y.49 日志、诊断与用户可解释错误

### Y.49.1 三类输出

- **用户事件**：通过 EventMsg 告知能行动的信息；
- **结构化日志**：供本地诊断，包含关联 ID；
- **遥测**：经配置导出的指标与 trace。

同一错误需要三种不同表达。用户看到“MCP server 启动超时”，日志包含 server 名、阶段和内部错误链，指标只增加启动失败计数。

### Y.49.2 Error chain

Rust 的错误上下文应保留底层原因，又不能把秘密泄露给 UI。边界层将内部 error 转为 CodexErr/Event/JSON-RPC error，并决定哪些 detail 可见。App Server 还要保留请求 ID，Exec 要映射退出码。

### Y.49.3 Doctor

CLI 的 doctor/diagnostics 类命令可检查认证、配置、沙箱二进制、目录权限、数据库、网络代理和模型连接。诊断应尽量只读，并输出修复建议。对高风险“自动修复”应显式确认。

---

## 第八篇·测试、构建、性能与工程治理

## Y.50 测试金字塔：从纯函数到真实平台

### Y.50.1 为什么 Codex 需要多层测试

Codex 同时处理协议、模型流、文件系统、数据库、子进程、终端、网络和操作系统沙箱。任何单一测试层都无法提供足够信心。纯单元测试适合解析和状态机；集成测试适合 Thread/Turn/Tool 流；快照测试适合 TUI；真实平台 CI 才能验证 Seatbelt、Landlock、Restricted Token、PTY 和路径规则。

```mermaid
flowchart TB
    E2E[跨平台端到端与发布冒烟]
    Integration[核心/协议/数据库/进程集成测试]
    Component[crate 级组件测试]
    Unit[纯函数、解析器、状态机单元测试]
    Static[fmt / clippy / lint / cargo-shear / schema check]

    Static --> Unit
    Unit --> Component
    Component --> Integration
    Integration --> E2E
```

### Y.50.2 协议测试

协议层应覆盖：序列化字段名、可选字段默认、未知事件兼容、旧事件桥、ID 关联、JSON Schema 生成和 App Server 初始化顺序。协议测试的目标不是证明业务正确，而是确保不同版本宿主不会因一个字段改名失去通信。

### Y.50.3 Agent Loop 测试

核心循环需要可控的假模型流和假工具：

- 只返回最终消息时恰好一次采样；
- 返回工具调用后追加 ToolOutput 并再次采样；
- 多个工具并行执行但按声明顺序回填；
- steer 在下一安全边界进入历史；
- 自动压缩触发后继续执行；
- 中断取消模型流和在途工具；
- 未知工具成为可恢复错误；
- 最终 Turn 状态只发一次。

### Y.50.4 持久化测试

对 JSONL 与 SQLite 双存储，必须注入：半写尾行、重复 append、SQLite busy、migration 失败、投影缺行、恢复后继续 ordinal、热线程归档竞态、fork 边界和 delete 部分失败。测试不能只验证“能写能读”，还要验证事实源与投影不一致时的恢复策略。

### Y.50.5 安全测试

安全测试应采用反例驱动：路径穿越、符号链接、命令包装、环境变量代理绕过、MCP 同名工具、Hook 重写扩权、Guardian 非法 JSON、审批缓存误复用、沙箱 denial 误判、Windows 大小写和 UNC、macOS profile 转义、Linux 内核能力缺失。

### Y.50.6 TUI 快照

TUI 快照适合验证复杂事件序列的最终渲染：长命令输出、审批弹窗、并行工具、计划更新、错误、终端窄宽变化和 Unicode。快照不应取代状态测试，因为一张“看起来正确”的画面可能隐藏重复 Item 或错误 ID 合并。

---

## Y.51 CI：快速门禁与完整矩阵的分层

### Y.51.1 快速 PR 工作流

`rust-ci.yml` 先检测变更路径，仅对相关区域运行工作。基础门禁包括 cargo fmt、benchmark smoke、cargo-shear 和自定义 argument comment lint。多个 job 最终由一个 results gatherer 汇总为 required status，避免条件跳过的 job 被误判为失败。

```mermaid
flowchart LR
    Diff[Git Diff] --> Detect[Detect changed areas]
    Detect --> Fmt[cargo fmt / bench smoke]
    Detect --> Shear[cargo shear]
    Detect --> Arg[argument-comment-lint]
    Fmt --> Gather[CI results]
    Shear --> Gather
    Arg --> Gather
    Gather --> Required[Required Status]
```

### Y.51.2 固定 Action 提交

Workflow 中第三方 Action 使用完整 commit SHA，而不是浮动 tag。这减少供应链被 tag 重指向的风险。Rust toolchain 与辅助工具版本也固定，提升可复现性。

### Y.51.3 变更检测的风险

路径过滤能节约资源，但最怕漏跑。CI 注释明确在某些缺失输出情况下采用“必须运行”的保守语义。新增 crate 或跨层依赖后，应同步更新检测规则，否则只改共享协议却跳过下游测试。

### Y.51.4 完整矩阵

快速工作流不能替代全量 CI。完整矩阵应覆盖 Linux、macOS、Windows，必要时包含 x86_64/ARM64、沙箱 feature、V8、安装包和真实 shell。平台相关代码若只在 Linux 编译，会在发布时暴露 Windows API 或路径问题。

### Y.51.5 干净工作树检查

构建、测试或代码生成后检查 worktree clean，可以发现测试隐式改写 fixture、格式化未提交或 schema 生成漂移。对于可生成协议 schema 的项目，这是有效的可重复性门禁。

---

## Y.52 自定义静态检查与代码风格

### Y.52.1 argument-comment-lint

项目维护自定义参数注释 lint，要求在易混淆的布尔值或参数位置写清参数语义。Rust 的位置参数调用若连续出现多个 `true/false/None`，可读性很差，注释能降低错误。专用 lint 比仅靠 code review 更一致。

### Y.52.2 禁止 unwrap/expect

工作区 lint 对 `unwrap_used`、`expect_used` 等严格限制。对 CLI 原型这可能显得繁琐，但 Codex 的后台任务、持久化和安全路径不能因一个意外 None 直接崩溃。合理替代是错误传播、`let else`、显式默认或带领域含义的错误。

### Y.52.3 禁止持锁跨 await

`await_holding_lock` 是异步 Rust 的关键风险。锁持有期间 await 可能让其他任务永久等待，尤其 Session/Agent Registry/ThreadStore 都有共享状态。代码应先复制所需数据、释放锁，再调用异步操作；需要串行的工作用消息队列比长时间 Mutex 更清晰。

### Y.52.4 依赖清理

cargo-shear 检测未使用依赖，有助于控制大型 workspace 的编译成本和供应链面。删除无用依赖不仅是整洁，也减少许可证、漏洞扫描和跨平台构建负担。

---

## Y.53 性能模型：延迟不只来自模型

### Y.53.1 一次 Turn 的延迟分解

```text
Turn 总耗时
= 上下文加载
+ 首次模型排队与首 token
+ 流式生成
+ 工具审批等待
+ 沙箱启动
+ 子进程执行
+ 后续模型采样
+ 持久化与 UI 收敛
```

模型通常占大头，但在短请求中，数据库启动、MCP 冷启动、WebSocket 建连、沙箱包装和 Git 扫描会变得明显。

### Y.53.2 冷启动

冷启动可优化的对象包括：配置加载、系统 Skill 安装、SQLite 打开与迁移、模型目录、MCP server、WebSocket 预连接和 TUI 初始化。不是所有资源都应预热；按需初始化能减少默认开销。项目中的延迟 rollout 物化、WebSocket preconnect、Skill marker 指纹都体现了这类权衡。

### Y.53.3 工具并行

并行只对真正独立的工具有收益。若所有调用争用同一个文件锁、SQLite writer 或单个 PTY，并发只增加上下文切换。性能分析应按工具类别统计排队时间、执行时间和结果等待时间。

### Y.53.4 输出体积

命令输出、MCP 结果和代码搜索是内存与 token 的主要来源。需要三层限制：传输层有界缓冲，UI 层节流/虚拟化，模型层截断/摘要。只在最终 Prompt 截断太晚，因为此前已占用内存和事件队列。

### Y.53.5 数据库性能

线程列表使用 SQLite 索引，避免扫描所有 JSONL；写入仍按线程顺序。WAL 允许读写并行，但过多连接和长事务会导致 checkpoint 压力。后台 backfill 应分批并让前台查询优先。

---

## Y.54 内存与资源生命周期

### Y.54.1 Arc 与 Weak

共享 Session、ThreadManagerState 和服务常用 Arc。所有权图必须有明确根；回调全局管理器使用 Weak。资源泄漏往往不是 Rust 内存不安全，而是 Arc 环导致对象永不 Drop。

### Y.54.2 Channel 生命周期

发送端未释放时，接收循环不会结束。关闭 Session 时要停止生产者、关闭 channel、等待消费者；仅取消一个 JoinHandle 可能留下其他发送端。每个 channel 应记录：谁创建、谁拥有最后 sender、关闭信号是什么、满时策略是什么。

### Y.54.3 子进程与文件句柄

Rollout writer、MCP stdio、PTY、code-mode host、sandbox helper 都持系统资源。Drop 可作为最后防线，但正常 shutdown 应显式完成 flush/terminate/wait。依赖进程退出时操作系统自动清理，无法保证数据完整和子进程树终止。

### Y.54.4 缓存

Skill snapshot、模型目录、配置指纹、工具 specs 和数据库投影都有缓存价值。缓存键必须包含影响结果的配置与版本；安全策略变更时缓存要失效。缓存错误比无缓存更危险，因为它会稳定地产生过时权限。

---

## Y.55 并发模型与取消树

### Y.55.1 任务层次

```mermaid
flowchart TB
    Process[进程 Cancellation]
    Thread[Thread/Session Cancellation]
    Turn[Turn Cancellation]
    Sample[Model Sample Cancellation]
    ToolA[Tool A Cancellation]
    ToolB[Tool B Cancellation]
    Child[Subagent Cancellation]
    Writer[Writer Shutdown]

    Process --> Thread
    Thread --> Turn
    Thread --> Writer
    Turn --> Sample
    Turn --> ToolA
    Turn --> ToolB
    Turn --> Child
```

### Y.55.2 取消不是错误字符串

取消需要独立信号，例如 CancellationToken。任务在 select 中监听取消与正常完成，清理后返回 Cancelled。把取消包装成普通 I/O error 会触发错误重试，甚至重新执行用户已经停止的工具。

### Y.55.3 有界等待

Shutdown 等待每个子任务时需要 deadline。先请求优雅停止，超时后 abort 或强制终止，并记录哪些资源未正常收敛。无限等待会让更新、退出和测试挂死。

### Y.55.4 状态发布

Agent 状态使用 watch channel 的优势是订阅者立即得到最新值。事件历史则用 mpsc/rollout。不要用 watch 承载“每条都必须处理”的审批请求，也不要用无界事件列表模拟最新状态。

---

## Y.56 发布与分发

### Y.56.1 单一命令、多产物

用户安装的是 `codex`，但运行时可能需要平台 sandbox helper、code-mode host 或资源文件。发布流程必须保证相关二进制版本匹配、权限位正确、Windows DLL/可执行名可发现，并为每个平台生成校验和。

### Y.56.2 npm 包装

npm 可作为跨平台安装入口，安装脚本或包内容选择目标二进制。安全要求包括固定下载来源、校验摘要、避免执行不必要脚本、清晰区分官方包名。假冒下载页面是现实风险，文档和 CLI 应引导用户验证来源。

### Y.56.3 自动更新

更新时需考虑正在运行的 App Server、数据库新 schema 和旧二进制回滚。安全策略是先下载并校验，再原子替换；数据库 migration 要向前兼容，旧版本面对更新 schema 时拒绝危险写入而不是尝试降级。

### Y.56.4 Release tag

仓库 release/tag 使用 Rust 版本前缀，当前分析时最新可见稳定发布为 0.150.1，而源码快照固定到之后的 main 提交。文档中的能力描述以 commit 为准，不以 release 二进制是否已包含为准。

---

## Y.57 工程治理与贡献模式

### Y.57.1 贡献政策

仓库贡献文档明确欢迎 issue、复现、根因分析、日志和设计讨论，但不接受外部代码贡献或 PR。原因是有效变更需要整体架构、系统约束和路线图上下文，团队把社区价值集中在问题发现与分析。

### Y.57.2 对源码阅读者的影响

读者可以学习、fork 和验证代码，但不要把本文的改进建议理解为官方待办。向项目反馈时，应提供最小复现、版本、系统、期望/实际、脱敏日志和根因假设，而不是只有“不能用”。

### Y.57.3 安全披露

安全漏洞应按 SECURITY.md 私下报告，不应在公开 issue 给出可利用细节。涉及沙箱逃逸、凭据泄漏或远程执行时尤其如此。

---

## Y.58 架构优点与代价

### Y.58.1 主要优点

1. 共享核心避免多入口重复 Agent Loop；
2. 强类型协议适合复杂事件和客户端兼容；
3. 工具治理集中，审批与沙箱边界清晰；
4. JSONL + SQLite 兼顾审计与查询；
5. 多 Agent 有控制面、预算与树形身份，而非简单递归；
6. 扩展统一归因到 Skill/Plugin/Hook/MCP；
7. 跨平台安全有独立适配，不用最低公分母设计。

### Y.58.2 主要代价

1. workspace crate 数量大，学习和编译成本高；
2. `core` 仍是聚合热点，跨模块变更范围广；
3. 协议事件很多，客户端必须处理兼容和状态归约；
4. 双存储带来投影一致性与 migration 复杂度；
5. 自动审查与多 Agent 增加模型成本和状态空间；
6. 跨平台沙箱行为难以完全一致；
7. 快速演进使文档、release 与 main 容易错位。

### Y.58.3 为什么这些代价值得

一个能执行代码、修改文件、联网、恢复长期会话并被多种客户端调用的 Agent，复杂性本来就存在。架构的目标不是消除复杂性，而是把复杂性放进可测试的边界。Codex 的大量 crate、事件和策略对象正是在“显式化复杂性”。

---


## 第九篇·Rust Workspace 逐 crate 源码导读

## Y.59 导读说明

以下清单覆盖固定提交中 `codex-rs/Cargo.toml` 的全部 workspace members。对前文已经逐文件核对的核心 crate 标为“深入阅读”；其余标为“边界阅读”，即依据 workspace、公开导出、命名、依赖位置和调用关系给出阅读导航，不把未核实的私有实现写成事实。每一项都给出定位、依赖判断、风险与测试路径，便于继续做源码审计。

```mermaid
flowchart LR
    Manifest[Workspace Manifest] --> Entry[入口宿主]
    Manifest --> Domain[核心领域]
    Manifest --> Security[安全隔离]
    Manifest --> Data[持久化]
    Manifest --> Extension[扩展生态]
    Manifest --> Model[模型与认证]
    Manifest --> Infra[通用基础设施]
    Manifest --> Test[测试支撑]
```

### Y.59.1 `aws-auth`

**阅读深度：边界阅读。** `aws-auth` 的存在说明仓库在**模型、认证与网络客户端**上采用了独立边界。为云端模型或服务提供 AWS 身份解析与签名能力，重点是短期凭据、区域与服务名、刷新以及避免把访问密钥扩散到普通工具环境。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `aws-auth` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.2 `analytics`

**阅读深度：深入阅读。** 阅读 `codex-rs/analytics` 时，首先要把它放回**基础设施与通用工具**主链路。定义产品行为事实、归约器和上报客户端，覆盖 Turn、压缩、Skill、Plugin、Guardian、Code Mode 与子 Agent 等事件。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `analytics` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.3 `agent-graph-store`

**阅读深度：边界阅读。** 就职责切分而言，`agent-graph-store` 不是孤立工具包，而是**持久化与状态投影**中的一个可测试节点。保存 Agent 树或协作图中的节点与关系，为多 Agent 可视化、查询和恢复提供独立数据边界。

该 crate 的价值主要体现在解耦。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `agent-graph-store` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.4 `agent-identity`

**阅读深度：边界阅读。** 从架构图上看，`agent-identity` 属于**基础设施与通用工具**。表达 Agent 的稳定身份、路径或来源，避免把显示昵称、线程标识和安全身份混为一个字符串。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `agent-identity` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.5 `agent-roles`

**阅读深度：边界阅读。** `agent-roles` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。加载和解析 Agent 角色配置，使角色提示、昵称候选和能力模板可以独立于核心 Session 演进。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `agent-roles` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.6 `backend-client`

**阅读深度：边界阅读。** 阅读 `codex-rs/backend-client` 时，首先要把它放回**模型、认证与网络客户端**主链路。封装与 Codex 后端服务的客户端交互，为配置、模型目录、云任务或插件元数据提供统一网络边界。

主链路中的关键问题是数据在哪个边界被转换。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `backend-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.7 `bwrap`

**阅读深度：边界阅读。** 就职责切分而言，`bwrap` 不是孤立工具包，而是**安全、权限与隔离**中的一个可测试节点。围绕 bubblewrap 构建 Linux 隔离命令，负责 namespace、挂载和进程启动参数的安全组装。

该 crate 的价值主要体现在解耦。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `bwrap` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.8 `build-info`

**阅读深度：边界阅读。** 从架构图上看，`build-info` 属于**基础设施与通用工具**。把版本、提交、目标平台和构建元数据暴露给运行时、诊断和协议握手。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `build-info` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.9 `ansi-escape`

**阅读深度：边界阅读。** `ansi-escape` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。解析或处理 ANSI 控制序列，避免终端输出、日志与模型上下文被不可见控制字符破坏。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `ansi-escape` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.10 `async-utils`

**阅读深度：边界阅读。** 阅读 `codex-rs/async-utils` 时，首先要把它放回**基础设施与通用工具**主链路。提供跨 crate 复用的异步辅助原语，常见关注点是取消、有界并发、超时和 JoinHandle 收敛。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `async-utils` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.11 `app-server`

**阅读深度：深入阅读。** 就职责切分而言，`app-server` 不是孤立工具包，而是**产品入口与宿主适配**中的一个可测试节点。把共享核心包装成长生命周期服务，处理初始化握手、请求路由、线程映射和事件通知。

该 crate 的价值主要体现在解耦。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `app-server` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.12 `app-server-transport`

**阅读深度：边界阅读。** 从架构图上看，`app-server-transport` 属于**基础设施与通用工具**。抽象 App Server 的字节传输和 framing，使 stdio、socket 或其他通道共享上层消息处理。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `app-server-transport` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.13 `app-server-daemon`

**阅读深度：边界阅读。** `app-server-daemon` 的存在说明仓库在**产品入口与宿主适配**上采用了独立边界。负责守护化运行、进程发现与生命周期，让客户端能连接持久 App Server 而不是每次冷启动。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `app-server-daemon` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.14 `app-server-client`

**阅读深度：边界阅读。** 阅读 `codex-rs/app-server-client` 时，首先要把它放回**产品入口与宿主适配**主链路。提供调用 App Server 的强类型客户端，隐藏请求 ID、通知分派和连接重建细节。

主链路中的关键问题是数据在哪个边界被转换。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `app-server-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.15 `app-server-protocol`

**阅读深度：边界阅读。** 就职责切分而言，`app-server-protocol` 不是孤立工具包，而是**协议与数据合同**中的一个可测试节点。定义远程请求、响应、通知和版本化数据结构，是桌面、IDE 与本地服务之间的兼容合同。

该 crate 的价值主要体现在解耦。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `app-server-protocol` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.16 `app-server-protocol-noop-macros`

**阅读深度：边界阅读。** 从架构图上看，`app-server-protocol-noop-macros` 属于**协议与数据合同**。为协议宏或无操作生成路径提供编译期支持，降低不同构建配置下的条件编译复杂度。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `app-server-protocol-noop-macros` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.17 `app-server-test-client`

**阅读深度：边界阅读。** `app-server-test-client` 的存在说明仓库在**测试与开发支撑**上采用了独立边界。提供测试专用客户端，便于以真实协议顺序验证初始化、线程操作、通知和错误。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。为集成测试、样例和构建定位提供稳定夹具，不进入生产主路径。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查测试工具是否忠实模拟真实边界、是否清理资源、是否避免依赖执行顺序。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应保证测试自身可并行、跨平台、可重复，并在失败时输出足够诊断。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `app-server-test-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.18 `apply-patch`

**阅读深度：边界阅读。** 阅读 `codex-rs/apply-patch` 时，首先要把它放回**基础设施与通用工具**主链路。解析、校验和应用补丁，并把路径约束、部分失败与最终差异转成稳定工具结果。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `apply-patch` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.19 `arg0`

**阅读深度：边界阅读。** 就职责切分而言，`arg0` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。处理可执行程序通过 argv[0] 或多调用二进制方式切换角色的入口语义。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `arg0` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.20 `feedback`

**阅读深度：边界阅读。** 从架构图上看，`feedback` 属于**基础设施与通用工具**。收集用户反馈及必要诊断元数据，重点是用户同意、内容裁剪、脱敏和失败不影响主流程。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `feedback` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.21 `features`

**阅读深度：边界阅读。** `features` 的存在说明仓库在**核心领域与编排**上采用了独立边界。集中声明和解析功能开关，连接编译能力、服务端能力、本地配置和管理员要求。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `features` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.22 `install-context`

**阅读深度：边界阅读。** 阅读 `codex-rs/install-context` 时，首先要把它放回**基础设施与通用工具**主链路。识别安装来源、包管理器和运行环境，为更新、诊断和辅助二进制定位提供信息。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `install-context` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.23 `codex-backend-openapi-models`

**阅读深度：边界阅读。** 就职责切分而言，`codex-backend-openapi-models` 不是孤立工具包，而是**协议与数据合同**中的一个可测试节点。承载由后端 OpenAPI 合同生成或维护的数据模型，避免业务层手写重复 DTO。

该 crate 的价值主要体现在解耦。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `codex-backend-openapi-models` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.24 `code-mode`

**阅读深度：深入阅读。** 从架构图上看，`code-mode` 属于**模型、认证与网络客户端**。提供 Code Mode 会话抽象和 provider，使受控代码运行能够组合嵌套工具而不绕过治理。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `code-mode` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.25 `code-mode-host`

**阅读深度：边界阅读。** `code-mode-host` 的存在说明仓库在**模型、认证与网络客户端**上采用了独立边界。作为独立进程承载代码执行运行时，隔离 V8/脚本资源并接收结构化会话请求。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `code-mode-host` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.26 `code-mode-protocol`

**阅读深度：边界阅读。** 阅读 `codex-rs/code-mode-protocol` 时，首先要把它放回**协议与数据合同**主链路。定义主进程、host 与远端 provider 之间的请求、结果、错误和取消合同。

主链路中的关键问题是数据在哪个边界被转换。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `code-mode-protocol` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.27 `code-mode-runtime`

**阅读深度：边界阅读。** 就职责切分而言，`code-mode-runtime` 不是孤立工具包，而是**模型、认证与网络客户端**中的一个可测试节点。实现代码运行与嵌套工具桥接，需限制时间、内存、输出和调用次数。

该 crate 的价值主要体现在解耦。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `code-mode-runtime` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.28 `codex-home`

**阅读深度：边界阅读。** 从架构图上看，`codex-home` 属于**基础设施与通用工具**。规范化 CODEX_HOME 的发现、创建与路径访问，集中处理用户目录和平台差异。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `codex-home` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.29 `cloud-config`

**阅读深度：边界阅读。** `cloud-config` 的存在说明仓库在**云端与远程任务**上采用了独立边界。拉取、验证和组织云端配置层，使企业或账户设置可进入统一 ConfigLayerStack。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。连接本地运行时和远程配置、任务或服务，同时保持身份与线程因果关系。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查网络重试、幂等键、离线行为、配置来源、租户隔离和本地/远端状态冲突。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用 mock client、故障注入和协议兼容 fixture 验证。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `cloud-config` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.30 `cloud-tasks`

**阅读深度：边界阅读。** 阅读 `codex-rs/cloud-tasks` 时，首先要把它放回**云端与远程任务**主链路。表达云任务领域对象与执行状态，连接本地线程和远端异步任务。

主链路中的关键问题是数据在哪个边界被转换。连接本地运行时和远程配置、任务或服务，同时保持身份与线程因果关系。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查网络重试、幂等键、离线行为、配置来源、租户隔离和本地/远端状态冲突。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用 mock client、故障注入和协议兼容 fixture 验证。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `cloud-tasks` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.31 `cloud-tasks-client`

**阅读深度：边界阅读。** 就职责切分而言，`cloud-tasks-client` 不是孤立工具包，而是**云端与远程任务**中的一个可测试节点。封装云任务 API、鉴权、分页、重试与错误映射。

该 crate 的价值主要体现在解耦。连接本地运行时和远程配置、任务或服务，同时保持身份与线程因果关系。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查网络重试、幂等键、离线行为、配置来源、租户隔离和本地/远端状态冲突。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应使用 mock client、故障注入和协议兼容 fixture 验证。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `cloud-tasks-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.32 `cloud-tasks-mock-client`

**阅读深度：边界阅读。** 从架构图上看，`cloud-tasks-mock-client` 属于**云端与远程任务**。为云任务流程提供确定性假实现，避免核心测试依赖真实网络。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。连接本地运行时和远程配置、任务或服务，同时保持身份与线程因果关系。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查网络重试、幂等键、离线行为、配置来源、租户隔离和本地/远端状态冲突。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应使用 mock client、故障注入和协议兼容 fixture 验证。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `cloud-tasks-mock-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.33 `cli`

**阅读深度：深入阅读。** `cli` 的存在说明仓库在**产品入口与宿主适配**上采用了独立边界。顶层多工具命令路由器，决定进入 TUI、Exec、App Server、MCP、诊断或线程管理。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `cli` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.34 `collaboration-mode-templates`

**阅读深度：边界阅读。** 阅读 `codex-rs/collaboration-mode-templates` 时，首先要把它放回**核心领域与编排**主链路。保存单 Agent、计划或多 Agent 协作模式的模板，使提示策略与核心代码解耦。

主链路中的关键问题是数据在哪个边界被转换。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `collaboration-mode-templates` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.35 `connectors`

**阅读深度：边界阅读。** 就职责切分而言，`connectors` 不是孤立工具包，而是**扩展与生态**中的一个可测试节点。描述和管理外部 App Connector 能力、标识与选择，连接 Plugin、工具发现和授权。

该 crate 的价值主要体现在解耦。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `connectors` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.36 `config`

**阅读深度：深入阅读。** 从架构图上看，`config` 属于**基础设施与通用工具**。完成多层 TOML 合并、严格校验、约束、来源诊断以及线程级有效配置构建。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `config` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.37 `context-fragments`

**阅读深度：边界阅读。** `context-fragments` 的存在说明仓库在**核心领域与编排**上采用了独立边界。以结构化片段表达可注入上下文，支持来源、优先级、预算与去重。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `context-fragments` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.38 `shell-command`

**阅读深度：边界阅读。** 阅读 `codex-rs/shell-command` 时，首先要把它放回**基础设施与通用工具**主链路。解析 shell 命令、环境和工作目录，并形成跨平台可执行请求。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `shell-command` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.39 `shell-escalation`

**阅读深度：边界阅读。** 就职责切分而言，`shell-escalation` 不是孤立工具包，而是**安全、权限与隔离**中的一个可测试节点。识别命令在受限执行失败后的升级需求，辅助生成最小范围审批。

该 crate 的价值主要体现在解耦。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `shell-escalation` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.40 `skills`

**阅读深度：深入阅读。** 从架构图上看，`skills` 属于**扩展与生态**。加载 SKILL.md、解析 frontmatter、选择显式技能并检测隐式访问。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `skills` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.41 `core`

**阅读深度：深入阅读。** `core` 的存在说明仓库在**核心领域与编排**上采用了独立边界。共享 Agent 运行时，集中 Thread、Session、Turn、模型循环、工具治理、上下文和多 Agent 编排。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `core` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.42 `core-api`

**阅读深度：边界阅读。** 阅读 `codex-rs/core-api` 时，首先要把它放回**核心领域与编排**主链路。抽出较稳定且更窄的核心接口，降低外部宿主直接依赖庞大 core 实现的耦合。

主链路中的关键问题是数据在哪个边界被转换。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `core-api` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.43 `core-plugins`

**阅读深度：边界阅读。** 就职责切分而言，`core-plugins` 不是孤立工具包，而是**扩展与生态**中的一个可测试节点。在核心与 Plugin manager 之间提供适配，负责插件能力装配而非通用包模型。

该 crate 的价值主要体现在解耦。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `core-plugins` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.44 `diagnostics`

**阅读深度：边界阅读。** 从架构图上看，`diagnostics` 属于**基础设施与通用工具**。统一结构化诊断、错误展示与环境检查，帮助 CLI/App Server 输出可行动信息。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `diagnostics` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.45 `guardian-context`

**阅读深度：边界阅读。** `guardian-context` 的存在说明仓库在**安全、权限与隔离**上采用了独立边界。构建 Guardian 所需的最小安全上下文，减少主会话无关信息和提示注入面。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `guardian-context` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.46 `hooks`

**阅读深度：深入阅读。** 阅读 `codex-rs/hooks` 时，首先要把它放回**扩展与生态**主链路。定义十二类生命周期 Hook、matcher、dispatcher、输出 spill 和 MCP 执行桥。

主链路中的关键问题是数据在哪个边界被转换。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `hooks` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.47 `history`

**阅读深度：边界阅读。** 就职责切分而言，`history` 不是孤立工具包，而是**核心领域与编排**中的一个可测试节点。定义 canonical 历史项、恢复历史和模型上下文转换，是 rollout 与 Agent Loop 的语义桥。

该 crate 的价值主要体现在解耦。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `history` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.48 `http-client`

**阅读深度：边界阅读。** 从架构图上看，`http-client` 属于**模型、认证与网络客户端**。集中 TLS、代理、CA、超时、重试和请求头策略，避免各 crate 自建不一致客户端。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `http-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.49 `secrets`

**阅读深度：边界阅读。** `secrets` 的存在说明仓库在**安全、权限与隔离**上采用了独立边界。提供秘密值类型、发现或脱敏辅助，避免敏感字符串在 Debug、日志和遥测中泄漏。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `secrets` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.50 `exec`

**阅读深度：深入阅读。** 阅读 `codex-rs/exec` 时，首先要把它放回**产品入口与宿主适配**主链路。非交互式宿主，消费统一 EventMsg 并转换为文本、JSONL、退出码和自动化契约。

主链路中的关键问题是数据在哪个边界被转换。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `exec` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.51 `file-system`

**阅读深度：边界阅读。** 就职责切分而言，`file-system` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。抽象文件读取、写入、元数据和环境路径，便于本地与外部执行器共享语义。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `file-system` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.52 `exec-server-protocol`

**阅读深度：边界阅读。** 从架构图上看，`exec-server-protocol` 属于**协议与数据合同**。定义长生命周期执行服务的命令、流式输出、取消和进程状态协议。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `exec-server-protocol` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.53 `exec-server`

**阅读深度：边界阅读。** `exec-server` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。运行独立执行服务，承载进程生命周期并与主 Agent 通过协议隔离。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `exec-server` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.54 `exec-server/tests/support`

**阅读深度：边界阅读。** 阅读 `codex-rs/exec-server/tests/support` 时，首先要把它放回**测试与开发支撑**主链路。为执行服务集成测试提供假程序、临时目录、协议驱动和断言工具。

主链路中的关键问题是数据在哪个边界被转换。为集成测试、样例和构建定位提供稳定夹具，不进入生产主路径。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查测试工具是否忠实模拟真实边界、是否清理资源、是否避免依赖执行顺序。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应保证测试自身可并行、跨平台、可重复，并在失败时输出足够诊断。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `exec-server/tests/support` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.55 `execpolicy`

**阅读深度：边界阅读。** 就职责切分而言，`execpolicy` 不是孤立工具包，而是**安全、权限与隔离**中的一个可测试节点。解析和匹配管理员执行策略，对命令前缀、参数模式和决策来源进行约束。

该 crate 的价值主要体现在解耦。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `execpolicy` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.56 `ext/agent`

**阅读深度：边界阅读。** 从架构图上看，`ext/agent` 属于**扩展与生态**。它以扩展包方式承载 agent 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `ext/agent` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.57 `ext/connectors`

**阅读深度：边界阅读。** `ext/connectors` 的存在说明仓库在**扩展与生态**上采用了独立边界。它以扩展包方式承载 connectors 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `ext/connectors` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.58 `ext/extension-api`

**阅读深度：边界阅读。** 阅读 `codex-rs/ext/extension-api` 时，首先要把它放回**扩展与生态**主链路。它以扩展包方式承载 extension-api 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

主链路中的关键问题是数据在哪个边界被转换。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `ext/extension-api` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.59 `ext/goal`

**阅读深度：边界阅读。** 就职责切分而言，`ext/goal` 不是孤立工具包，而是**持久化与状态投影**中的一个可测试节点。它以扩展包方式承载 goal 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

该 crate 的价值主要体现在解耦。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `ext/goal` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.60 `ext/git-attribution`

**阅读深度：边界阅读。** 从架构图上看，`ext/git-attribution` 属于**扩展与生态**。它以扩展包方式承载 git-attribution 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `ext/git-attribution` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.61 `ext/guardian-v2`

**阅读深度：边界阅读。** `ext/guardian-v2` 的存在说明仓库在**安全、权限与隔离**上采用了独立边界。它以扩展包方式承载 guardian-v2 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `ext/guardian-v2` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.62 `ext/history-notes`

**阅读深度：边界阅读。** 阅读 `codex-rs/ext/history-notes` 时，首先要把它放回**持久化与状态投影**主链路。它以扩展包方式承载 history-notes 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

主链路中的关键问题是数据在哪个边界被转换。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `ext/history-notes` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.63 `ext/image-generation`

**阅读深度：边界阅读。** 就职责切分而言，`ext/image-generation` 不是孤立工具包，而是**扩展与生态**中的一个可测试节点。它以扩展包方式承载 image-generation 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

该 crate 的价值主要体现在解耦。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `ext/image-generation` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.64 `ext/items`

**阅读深度：边界阅读。** 从架构图上看，`ext/items` 属于**持久化与状态投影**。它以扩展包方式承载 items 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `ext/items` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.65 `ext/memories`

**阅读深度：边界阅读。** `ext/memories` 的存在说明仓库在**持久化与状态投影**上采用了独立边界。它以扩展包方式承载 memories 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `ext/memories` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.66 `ext/mcp`

**阅读深度：边界阅读。** 阅读 `codex-rs/ext/mcp` 时，首先要把它放回**扩展与生态**主链路。它以扩展包方式承载 mcp 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

主链路中的关键问题是数据在哪个边界被转换。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `ext/mcp` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.67 `ext/queue`

**阅读深度：边界阅读。** 就职责切分而言，`ext/queue` 不是孤立工具包，而是**持久化与状态投影**中的一个可测试节点。它以扩展包方式承载 queue 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

该 crate 的价值主要体现在解耦。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `ext/queue` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.68 `ext/skills`

**阅读深度：边界阅读。** 从架构图上看，`ext/skills` 属于**扩展与生态**。它以扩展包方式承载 skills 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `ext/skills` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.69 `ext/web-search`

**阅读深度：边界阅读。** `ext/web-search` 的存在说明仓库在**扩展与生态**上采用了独立边界。它以扩展包方式承载 web-search 领域能力，通过 extension API 接入核心，而不直接修改 Session 的公共结构。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `ext/web-search` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.70 `external-agent-migration`

**阅读深度：边界阅读。** 阅读 `codex-rs/external-agent-migration` 时，首先要把它放回**扩展与生态**主链路。将其他 Agent 工具的配置或历史迁移到 Codex 结构，重点是显式映射和可回滚。

主链路中的关键问题是数据在哪个边界被转换。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `external-agent-migration` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.71 `keyring-store`

**阅读深度：边界阅读。** 就职责切分而言，`keyring-store` 不是孤立工具包，而是**安全、权限与隔离**中的一个可测试节点。封装系统密钥环读写，让凭据存储模式在 macOS、Windows、Linux 上有统一接口。

该 crate 的价值主要体现在解耦。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `keyring-store` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.72 `file-search`

**阅读深度：边界阅读。** 从架构图上看，`file-search` 属于**基础设施与通用工具**。提供面向代码库的文件检索与排序，需控制忽略规则、二进制文件和结果预算。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `file-search` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.73 `file-watcher`

**阅读深度：边界阅读。** `file-watcher` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。观察工作区或配置变化，向缓存和 UI 发失效信号，同时处理事件风暴与路径重命名。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `file-watcher` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.74 `linux-sandbox`

**阅读深度：边界阅读。** 阅读 `codex-rs/linux-sandbox` 时，首先要把它放回**安全、权限与隔离**主链路。在独立 Linux 进程边界设置 seccomp、Landlock 或 namespace 限制。

主链路中的关键问题是数据在哪个边界被转换。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `linux-sandbox` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.75 `lmstudio`

**阅读深度：边界阅读。** 就职责切分而言，`lmstudio` 不是孤立工具包，而是**模型、认证与网络客户端**中的一个可测试节点。适配 LM Studio 本地模型服务，把其模型发现和请求能力映射到提供方抽象。

该 crate 的价值主要体现在解耦。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `lmstudio` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.76 `login`

**阅读深度：深入阅读。** 从架构图上看，`login` 属于**模型、认证与网络客户端**。管理 OAuth、PKCE、设备码、API key、外部身份、刷新、撤销和凭据存储。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `login` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.77 `codex-mcp`

**阅读深度：边界阅读。** `codex-mcp` 的存在说明仓库在**扩展与生态**上采用了独立边界。实现 Codex 侧 MCP 公共能力或适配层，连接配置、rmcp 客户端和工具注册。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `codex-mcp` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.78 `mcp-server`

**阅读深度：边界阅读。** 阅读 `codex-rs/mcp-server` 时，首先要把它放回**产品入口与宿主适配**主链路。让 Codex 自身以 MCP Server 形态暴露能力，需保持工具结果和会话生命周期清晰。

主链路中的关键问题是数据在哪个边界被转换。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `mcp-server` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.79 `memories/read`

**阅读深度：边界阅读。** 就职责切分而言，`memories/read` 不是孤立工具包，而是**持久化与状态投影**中的一个可测试节点。读取、筛选和格式化记忆，使历史偏好或事实以受预算控制的片段进入上下文。

该 crate 的价值主要体现在解耦。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `memories/read` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.80 `memories/write`

**阅读深度：边界阅读。** 从架构图上看，`memories/write` 属于**持久化与状态投影**。对记忆写入进行结构化、去重、作用域和持久化治理，避免模型任意污染长期状态。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `memories/write` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.81 `model-provider-info`

**阅读深度：边界阅读。** `model-provider-info` 的存在说明仓库在**模型、认证与网络客户端**上采用了独立边界。定义模型提供方的 endpoint、认证、能力和传输元数据。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `model-provider-info` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.82 `models-manager`

**阅读深度：边界阅读。** 阅读 `codex-rs/models-manager` 时，首先要把它放回**模型、认证与网络客户端**主链路。拉取、缓存并解析模型目录，为 Turn 提供完整 ModelInfo 而非裸模型名。

主链路中的关键问题是数据在哪个边界被转换。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `models-manager` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.83 `network-proxy`

**阅读深度：边界阅读。** 就职责切分而言，`network-proxy` 不是孤立工具包，而是**安全、权限与隔离**中的一个可测试节点。实现受管理的网络出口、域策略、证书和动态审批，控制 shell 与工具的数据外发。

该 crate 的价值主要体现在解耦。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `network-proxy` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.84 `ollama`

**阅读深度：边界阅读。** 从架构图上看，`ollama` 属于**模型、认证与网络客户端**。适配 Ollama 本地模型服务，处理模型目录、端点和能力差异。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `ollama` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.85 `process-hardening`

**阅读深度：边界阅读。** `process-hardening` 的存在说明仓库在**安全、权限与隔离**上采用了独立边界。在进程启动后应用平台安全加固，减少调试、继承句柄或不必要权限。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `process-hardening` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.86 `protocol`

**阅读深度：深入阅读。** 阅读 `codex-rs/protocol` 时，首先要把它放回**协议与数据合同**主链路。定义 Op、EventMsg、TurnItem、错误和策略枚举，是所有宿主与核心共享的数据合同。

主链路中的关键问题是数据在哪个边界被转换。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `protocol` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.87 `prompts`

**阅读深度：边界阅读。** 就职责切分而言，`prompts` 不是孤立工具包，而是**核心领域与编排**中的一个可测试节点。集中维护基础提示、总结提示和模式模板，便于版本化、测试与 token 管理。

该 crate 的价值主要体现在解耦。位于 Thread、Turn、Item、模型和工具之间，承担因果顺序与生命周期协调。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查状态机是否有遗漏终态、并发任务是否可取消、历史与事件是否重复、错误是否会绕过 flush。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应使用假模型、假工具和可控时钟覆盖多轮采样、steer、压缩、并行工具及中断。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `prompts` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.88 `rollout`

**阅读深度：深入阅读。** 从架构图上看，`rollout` 属于**持久化与状态投影**。实现 canonical JSONL 追加、读取、flush 与恢复，是线程可回放事实日志。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `rollout` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.89 `rollout-trace`

**阅读深度：边界阅读。** `rollout-trace` 的存在说明仓库在**持久化与状态投影**上采用了独立边界。把 rollout 或执行事件转换为可分析 trace，支持离线排障和时序重建。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `rollout-trace` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.90 `rmcp-client`

**阅读深度：边界阅读。** 阅读 `codex-rs/rmcp-client` 时，首先要把它放回**扩展与生态**主链路。基于 RMCP 协议库封装 MCP 客户端连接、能力协商和调用。

主链路中的关键问题是数据在哪个边界被转换。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `rmcp-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.91 `responses-api-proxy`

**阅读深度：边界阅读。** 就职责切分而言，`responses-api-proxy` 不是孤立工具包，而是**产品入口与宿主适配**中的一个可测试节点。提供 Responses API 代理入口，用于本地路由、调试或受控网络接入。

该 crate 的价值主要体现在解耦。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `responses-api-proxy` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.92 `response-debug-context`

**阅读深度：边界阅读。** 从架构图上看，`response-debug-context` 属于**基础设施与通用工具**。构造模型响应调试所需上下文，必须严格控制敏感内容与启用范围。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `response-debug-context` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.93 `sandboxing`

**阅读深度：深入阅读。** `sandboxing` 的存在说明仓库在**安全、权限与隔离**上采用了独立边界。定义跨平台 SandboxManager、SandboxType、转换和 denial 语义。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `sandboxing` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.94 `stdio-to-uds`

**阅读深度：边界阅读。** 阅读 `codex-rs/stdio-to-uds` 时，首先要把它放回**产品入口与宿主适配**主链路。将标准输入输出桥接到 Unix Domain Socket，适合把只支持 stdio 的进程接入守护服务。

主链路中的关键问题是数据在哪个边界被转换。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `stdio-to-uds` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.95 `otel`

**阅读深度：深入阅读。** 就职责切分而言，`otel` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。提供 OTLP exporter、trace context、metrics、SessionTelemetry 和工具决策来源。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `otel` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.96 `tui`

**阅读深度：深入阅读。** 从架构图上看，`tui` 属于**产品入口与宿主适配**。交互式终端宿主，将输入事件转换为 Op，并把 EventMsg 归约为组件状态。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `tui` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.97 `tools`

**阅读深度：深入阅读。** `tools` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。提供共享工具类型、执行接口或工具集合，承载 core 工具编排之外的可复用能力。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `tools` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.98 `v8-poc`

**阅读深度：边界阅读。** 阅读 `codex-rs/v8-poc` 时，首先要把它放回**基础设施与通用工具**主链路。验证或承载 V8 集成实验，重点是构建产物、沙箱配置和宿主资源限制。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `v8-poc` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.99 `websocket-client`

**阅读深度：边界阅读。** 就职责切分而言，`websocket-client` 不是孤立工具包，而是**模型、认证与网络客户端**中的一个可测试节点。封装 WebSocket 建连、帧、重连和 TLS，为模型与实时会话提供共享传输。

该 crate 的价值主要体现在解耦。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `websocket-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.100 `worktree`

**阅读深度：边界阅读。** 从架构图上看，`worktree` 属于**基础设施与通用工具**。管理 Git worktree 的创建、定位与清理，为隔离任务或多 Agent 并行提供基础。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `worktree` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.101 `workload-identity`

**阅读深度：边界阅读。** `workload-identity` 的存在说明仓库在**安全、权限与隔离**上采用了独立边界。提供工作负载身份交换与刷新，适用于无长期静态密钥的托管环境。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把模型意图约束为最小权限系统动作，并在平台边界执行强制策略。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查路径规范化、拒绝优先、审批绑定、重试幂等、进程树终止、网络绕过和秘密泄漏。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。必须在 Linux、macOS、Windows 真实 runner 上运行负向测试，而非只依赖 mock。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `workload-identity` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.102 `utils/absolute-path`

**阅读深度：边界阅读。** 阅读 `codex-rs/utils/absolute-path` 时，首先要把它放回**基础设施与通用工具**主链路。它把 absolute-path 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `utils/absolute-path` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.103 `utils/audio`

**阅读深度：边界阅读。** 就职责切分而言，`utils/audio` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 audio 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/audio` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.104 `utils/path-uri`

**阅读深度：边界阅读。** 从架构图上看，`utils/path-uri` 属于**基础设施与通用工具**。它把 path-uri 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `utils/path-uri` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.105 `utils/cargo-bin`

**阅读深度：边界阅读。** `utils/cargo-bin` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。它把 cargo-bin 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `utils/cargo-bin` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.106 `git-utils`

**阅读深度：边界阅读。** 阅读 `codex-rs/git-utils` 时，首先要把它放回**基础设施与通用工具**主链路。封装仓库发现、diff、状态、引用和 worktree 操作，减少 shell 文本解析。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `git-utils` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.107 `utils/cache`

**阅读深度：边界阅读。** 就职责切分而言，`utils/cache` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 cache 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/cache` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.108 `utils/image`

**阅读深度：边界阅读。** 从架构图上看，`utils/image` 属于**基础设施与通用工具**。它把 image 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `utils/image` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.109 `utils/json-to-toml`

**阅读深度：边界阅读。** `utils/json-to-toml` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。它把 json-to-toml 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `utils/json-to-toml` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.110 `utils/home-dir`

**阅读深度：边界阅读。** 阅读 `codex-rs/utils/home-dir` 时，首先要把它放回**基础设施与通用工具**主链路。它把 home-dir 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `utils/home-dir` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.111 `utils/pty`

**阅读深度：边界阅读。** 就职责切分而言，`utils/pty` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 pty 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/pty` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.112 `utils/readiness`

**阅读深度：边界阅读。** 从架构图上看，`utils/readiness` 属于**基础设施与通用工具**。它把 readiness 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `utils/readiness` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.113 `utils/redacted-string`

**阅读深度：边界阅读。** `utils/redacted-string` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。它把 redacted-string 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `utils/redacted-string` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.114 `utils/rustls-provider`

**阅读深度：边界阅读。** 阅读 `codex-rs/utils/rustls-provider` 时，首先要把它放回**基础设施与通用工具**主链路。它把 rustls-provider 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `utils/rustls-provider` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.115 `utils/string`

**阅读深度：边界阅读。** 就职责切分而言，`utils/string` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 string 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/string` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.116 `utils/cli`

**阅读深度：边界阅读。** 从架构图上看，`utils/cli` 属于**基础设施与通用工具**。它把 cli 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `utils/cli` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.117 `utils/elapsed`

**阅读深度：边界阅读。** `utils/elapsed` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。它把 elapsed 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `utils/elapsed` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.118 `utils/sandbox-summary`

**阅读深度：边界阅读。** 阅读 `codex-rs/utils/sandbox-summary` 时，首先要把它放回**基础设施与通用工具**主链路。它把 sandbox-summary 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `utils/sandbox-summary` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.119 `utils/sleep-inhibitor`

**阅读深度：边界阅读。** 就职责切分而言，`utils/sleep-inhibitor` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 sleep-inhibitor 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/sleep-inhibitor` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.120 `utils/approval-presets`

**阅读深度：边界阅读。** 从架构图上看，`utils/approval-presets` 属于**基础设施与通用工具**。它把 approval-presets 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `utils/approval-presets` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.121 `utils/oss`

**阅读深度：边界阅读。** `utils/oss` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。它把 oss 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `utils/oss` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.122 `utils/output-truncation`

**阅读深度：边界阅读。** 阅读 `codex-rs/utils/output-truncation` 时，首先要把它放回**基础设施与通用工具**主链路。它把 output-truncation 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `utils/output-truncation` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.123 `utils/path-utils`

**阅读深度：边界阅读。** 就职责切分而言，`utils/path-utils` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 path-utils 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/path-utils` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.124 `utils/plugins`

**阅读深度：边界阅读。** 从架构图上看，`utils/plugins` 属于**基础设施与通用工具**。它把 plugins 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `utils/plugins` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.125 `utils/fuzzy-match`

**阅读深度：边界阅读。** `utils/fuzzy-match` 的存在说明仓库在**基础设施与通用工具**上采用了独立边界。它把 fuzzy-match 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `utils/fuzzy-match` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.126 `utils/stream-parser`

**阅读深度：边界阅读。** 阅读 `codex-rs/utils/stream-parser` 时，首先要把它放回**基础设施与通用工具**主链路。它把 stream-parser 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

主链路中的关键问题是数据在哪个边界被转换。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `utils/stream-parser` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.127 `utils/template`

**阅读深度：边界阅读。** 就职责切分而言，`utils/template` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。它把 template 相关的跨平台或纯函数能力抽为小型工具 crate，减少上层重复实现。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `utils/template` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.128 `codex-client`

**阅读深度：边界阅读。** 从架构图上看，`codex-client` 属于**协议与数据合同**。提供面向 Codex 服务或核心协议的客户端封装，供其他二进制复用。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `codex-client` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.129 `codex-api`

**阅读深度：边界阅读。** `codex-api` 的存在说明仓库在**协议与数据合同**上采用了独立边界。封装模型 API、Realtime、Responses 数据与传输，是 ModelClient 的下层协议客户端。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。为不同进程、crate 或版本提供稳定类型边界，避免宿主依赖内部实现。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查序列化兼容、未知字段、枚举扩展、ID 关联、错误信息脱敏和 schema 生成一致性。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应做 round-trip、golden fixture、旧版本样本和模糊输入测试。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `codex-api` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.130 `state`

**阅读深度：深入阅读。** 阅读 `codex-rs/state` 时，首先要把它放回**持久化与状态投影**主链路。维护本地 SQLite runtime、migration、backfill 与查询投影。

主链路中的关键问题是数据在哪个边界被转换。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `state` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.131 `terminal-detection`

**阅读深度：边界阅读。** 就职责切分而言，`terminal-detection` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。识别终端能力、颜色、TTY 与环境，帮助 TUI 和 Exec 选择输出模式。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `terminal-detection` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.132 `test-binary-support`

**阅读深度：边界阅读。** 从架构图上看，`test-binary-support` 属于**测试与开发支撑**。为集成测试定位和启动工作区二进制，减少平台路径与构建模式差异。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。为集成测试、样例和构建定位提供稳定夹具，不进入生产主路径。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查测试工具是否忠实模拟真实边界、是否清理资源、是否避免依赖执行顺序。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应保证测试自身可并行、跨平台、可重复，并在失败时输出足够诊断。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `test-binary-support` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.133 `thread-manager-sample`

**阅读深度：边界阅读。** `thread-manager-sample` 的存在说明仓库在**产品入口与宿主适配**上采用了独立边界。提供 ThreadManager 的示例或最小宿主，帮助验证公开边界与集成方式。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。上接用户、脚本或远程客户端，下接协议与共享核心。此类模块的首要职责是转换输入输出，而不是复制业务循环。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查初始化顺序、取消传播、退出码或通知兼容、慢消费者背压，以及宿主异常退出后核心任务是否仍在运行。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应以真实协议或进程级测试覆盖启动、正常完成、错误、中断、重连和版本不匹配。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `thread-manager-sample` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.134 `thread-store`

**阅读深度：深入阅读。** 阅读 `codex-rs/thread-store` 时，首先要把它放回**持久化与状态投影**主链路。定义存储中立线程接口和本地 JSONL/SQLite 实现。

主链路中的关键问题是数据在哪个边界被转换。保存可回放事实、建立查询索引并支持恢复、分叉、归档和迁移。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查追加顺序、双写不一致、半写文件、migration 回滚、热冷线程竞态和删除原子性。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应注入磁盘失败、SQLite busy、损坏行、旧 schema 与并发 append。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `thread-store` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.135 `uds`

**阅读深度：边界阅读。** 就职责切分而言，`uds` 不是孤立工具包，而是**基础设施与通用工具**中的一个可测试节点。封装 Unix Domain Socket 地址、连接和跨平台条件编译。

该 crate 的价值主要体现在解耦。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 它应避免同时拥有业务决策与外部副作用：前者由核心或策略层决定，后者由适配器执行；必须合并时，也要用显式 trait 或数据对象保留替换空间。

安全性取决于默认拒绝和信息保真。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 当配置缺失、版本不兼容、返回值不可解析或平台能力不足时，应给出明确降级或拒绝，不能为了可用性静默扩大权限。

源码走读完成后，应能回答四个问题：它维护什么不变量、什么状态可跨 Turn、什么错误允许重试、什么内容可以进入日志。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 回答不清时，说明仍停留在目录级理解。

**最小审查清单**：确认 `uds` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.136 `codex-experimental-api-macros`

**阅读深度：边界阅读。** 从架构图上看，`codex-experimental-api-macros` 属于**基础设施与通用工具**。为实验 API 生成标记或样板，帮助在编译期隔离不稳定合同。

它的调用关系应按“谁提供输入、谁消费输出、谁拥有生命周期”来理解。提供跨 crate 的路径、终端、缓存、解析、模板和平台辅助。 若上层开始读取该 crate 的私有状态，或该 crate 反向依赖产品界面，就意味着边界正在泄漏。评审时应检查公开类型是否足以表达成功、拒绝、取消和部分失败，而不是只返回布尔值或字符串。

可靠性与安全审查不能停留在正常路径。重点审查 API 是否保持单一职责、是否隐藏危险默认、是否处理 Unicode/平台边界以及错误是否可诊断。 此外还应检查输入是否有大小限制、异步等待是否有 deadline、取消后是否释放资源、Debug/错误链是否可能暴露路径或凭据。

建议的源码阅读顺序是：先看 `Cargo.toml` 和 `lib.rs` 的公开导出，再定位核心类型及其构造函数，随后搜索错误枚举、取消令牌、测试 fixture 和调用方。应以表驱动和属性测试覆盖边界值，并避免通用 crate 反向依赖上层领域。 最后用一次端到端场景把输入、状态变化、输出事件与持久化结果串起来。

**最小审查清单**：确认 `codex-experimental-api-macros` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.137 `plugin`

**阅读深度：深入阅读。** `plugin` 的存在说明仓库在**扩展与生态**上采用了独立边界。定义插件包、manifest、provider、能力摘要、Hook 来源和遥测元数据。

判断该模块是否设计良好，不能只看文件大小，而要看依赖方向。把外部能力装配到 Skill、Plugin、MCP、Hook 或 Connector 边界。 理想情况下，上层只依赖稳定接口，平台或网络细节留在实现内部；需要跨进程的数据则进入 protocol，而不把内部 Arc、锁或文件句柄暴露出去。

该模块最值得做的是失败注入。重点审查来源、命名空间、版本、权限继承、输出预算、失败隔离和卸载后的残留状态。 对每个外部调用都要回答：调用前是否已有副作用，失败后能否重试，重复调用是否幂等，调用者如何知道结果处于成功、失败还是未知状态。

阅读时不要从测试文件名猜实现。应先列出公开 API，再在工作区搜索其调用点，确认真正的 owner 与生命周期。应使用恶意 manifest、同名能力、超时 server、巨大输出和无效 schema 做测试。 若模块有平台条件编译，还需分别检查各平台分支和 CI 是否真实覆盖。

**最小审查清单**：确认 `plugin` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

### Y.59.138 `model-provider`

**阅读深度：边界阅读。** 阅读 `codex-rs/model-provider` 时，首先要把它放回**模型、认证与网络客户端**主链路。实现具体模型提供方选择与请求适配，连接 ModelProviderInfo 和底层 API。

主链路中的关键问题是数据在哪个边界被转换。连接模型目录、认证、HTTP/WebSocket/Realtime，并把服务能力映射为运行时配置。 任何转换都应保留关联标识、来源和错误原因，使 Event、日志、rollout 与遥测能够对齐。若转换丢失这些信息，后续排障只能依赖模糊文本。

边界模块经常出现“功能正确但生命周期错误”。重点审查令牌刷新并发、请求重放、传输回退、代理与 CA、能力误判、敏感头日志。 评审者应沿着启动、活动、取消、关闭、Drop 五个阶段检查句柄和任务所有权，并确认没有因 Arc 环、遗留 sender 或子进程而无法结束。

验证该 crate 的最好方式，是构造一个最小正常样本和至少三个反例。应覆盖 401 恢复、限流、断流、部分帧、模型重路由和本地提供方差异。 反例应覆盖无效输入、外部依赖失败和中途取消，并确认调用方收到结构化结果而不是悬挂或 panic。

**最小审查清单**：确认 `model-provider` 的公开错误不丢失来源；确认所有外部等待可取消且有界；确认动态输入经过规范化和容量限制；确认敏感数据不进入默认日志；确认测试至少覆盖成功、拒绝、超时和关闭；确认上层不依赖本 crate 私有文件布局。

## 第十篇·端到端场景与故障推演

本篇不再按 crate 分割，而是从真实用户动作出发，沿控制流、事件流、权限流和持久化流同时追踪。每个场景都给出主路径、一致性、安全、失败恢复与测试门禁，用于检验前文架构理解是否能够落到工程实践。

### Y.60.1 TUI 中创建线程并修改代码

**触发条件。** 用户在项目目录启动 codex，在输入框要求定位缺陷、修改文件并运行测试。

**主调用链。** TUI 将输入转换为 TurnInputRequest，ThreadManager 创建或取得 CodexThread，Session 记录输入并启动 run_turn；模型先调用读取或搜索工具，再调用补丁与测试命令，最终事件被 ChatWidget 归约。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 必须保证同一 Turn 的工具调用与 diff 使用相同工作区快照；文件变化既通过事件实时展示，也由 TurnDiffTracker 在完成时收敛。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 补丁写入和测试命令分别经过权限解析；任何工作区外写入或额外网络下载不能因用户只说“修复缺陷”而自动放行。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 模型断流、补丁部分应用、测试卡死或用户 Ctrl-C 时，取消要传播到采样和子进程，rollout 保留已完成步骤，并产生唯一终态。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 使用假模型返回读文件→补丁→测试→最终消息的固定序列，断言事件顺序、最终 diff、JSONL items 和 UI 快照一致。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.2 Exec 在 CI 中执行一次性任务

**触发条件。** CI 通过 codex exec 提交检查或修复任务，并希望根据退出码判断成功。

**主调用链。** Exec 解析参数和配置覆盖，创建线程，消费统一 EventMsg，再把稳定结果转换为文本或 JSONL；核心 Agent Loop 与 TUI 完全共享。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 非交互宿主不能等待无期限人工审批，必须预先确定拒绝、外部审批通道或 Guardian 路径。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 默认应使用受限权限；若任务需要安装依赖或访问网络，应在 CI 配置中明确允许范围，而不是全局关闭沙箱。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 遇到授权不足应返回结构化拒绝和非零退出码；遇到用户配置错误应在创建副作用前失败。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试应同时验证人类可读输出、机器输出和退出码，并注入 stdout 慢消费、SIGTERM 与工具超时。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.3 App Server 初始化并管理多个线程

**触发条件。** 桌面或 IDE 客户端连接 App Server，先初始化，再并行启动多个 Thread。

**主调用链。** Transport 解析消息，MessageProcessor 验证 initialize/initialized 顺序，将请求路由到 ThreadManager，并为每个 CodexThread 启动独立事件转发。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 连接级请求 ID、线程级事件订阅和全局管理器必须分开；一个线程的慢通知不能阻塞其他线程。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 客户端能力协商决定可发送哪些新通知，但不能改变服务端权限上限。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 重复 initialize、初始化前请求、连接中断和客户端重连都要有确定结果；线程是否继续运行应由协议语义明确。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 用测试客户端建立两个线程，交错发送请求并故意延迟消费其中一个，验证通知关联 ID 与背压。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.4 模型返回未知工具名

**触发条件。** 模型响应包含未注册或当前不可见的工具调用。

**主调用链。** ToolRouter 构造 ToolCall 后在 Registry 查找失败，核心把未知工具转换为可回填的工具错误，允许模型在下一次采样纠正。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 错误项必须与原 call ID 配对，不能丢失后导致模型等待一个永远不存在的 ToolOutput。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 系统绝不能按照工具名猜测并调用 shell，也不能从 Plugin 目录动态加载未授权同名能力。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 若模型重复调用同一未知工具，应受循环、token 或 Turn 预算约束，最终给用户可解释错误。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试返回拼写错误、命名空间冲突和已禁用工具，确认无 handler 被执行且历史配对完整。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 4 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.5 多个只读工具并行执行

**触发条件。** 模型一次声明读取多个文件和独立搜索。

**主调用链。** run_turn 将调用分派为并行 Future，工具可同时推进；结果完成时间可能不同，但使用 FuturesOrdered 按模型声明顺序回填历史。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** UI 可以按真实时间展示进度，模型上下文必须确定；两种顺序通过 call ID 解耦。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 只读也要遵守 deny path，不能因并行优化绕过每个调用的路径检查。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 某个读取失败时，其他独立调用可以完成；取消 Turn 时所有 Future 都应停止或忽略迟到结果。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 用不同延迟的假工具验证 B、C 先完成而历史仍是 A、B、C，并检查取消不产生重复 Completed。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.6 补丁写入触发人工审批

**触发条件。** 模型计划修改超出自动写范围的文件。

**主调用链。** ToolOrchestrator 在执行前解析最终路径和写权限，产生 ApprovalRequested 事件；宿主显示规范化动作，用户决策回到同一 call。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 审批结果必须绑定文件集合和调用，不能让后续不同补丁复用宽泛授权。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** PreToolUse 若重写路径，应在审批前完成；审批后只能做不扩大资源范围的沙箱包装。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 用户拒绝后将结构化结果回填模型，让其解释或选择替代方案；不能把拒绝当系统异常自动重试。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试符号链接、相对路径、大小写差异和 Hook 重写，确认展示与最终目标一致。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.7 受限命令因沙箱拒绝申请升级

**触发条件。** 命令在初始沙箱中被系统策略阻止，但用户任务确实需要更高权限。

**主调用链。** Orchestrator 识别 SandboxDenial，而非普通非零退出；根据审批策略生成升级请求，批准后用缓存到该调用的决定重试。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 第一次尝试可能已有部分副作用，因此只有确认在受保护边界前被拒绝时才安全重试。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 升级应只增加解决 denial 所需的最小能力，不自动切换为完全无沙箱。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 无法识别 denial、审批超时或重试再次失败时，返回明确错误，不进入无限升级循环。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 构造包装器在 spawn 前拒绝、执行中拒绝和命令自身退出三种情况，验证只有第一类进入安全重试。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.8 网络代理请求域名审批

**触发条件。** shell 工具尝试访问未在 allowlist 的外部域。

**主调用链。** 沙箱只允许连接托管代理，代理根据域策略阻断并把上下文返回审批链；批准后限定域与当前动作继续。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 网络事件需要保留 Thread、Turn、tool call、目标域和触发原因，便于用户判断。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 不得允许进程通过原始 socket、IPv6、DNS 或 Unix socket 绕过代理；凭据头也不能出现在审批文本。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 审批拒绝后进程应收到确定失败，工具结果回填模型；代理不可无限等待前端。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试域名大小写、重定向、子域、IP 直连和代理变量覆盖，确认策略一致。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 8 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.9 Guardian 返回无法解析的内容

**触发条件。** 自动审查模型超时、报错或输出不符合严格 JSON schema。

**主调用链。** GuardianReviewSession 将结果解析为 GuardianAssessment；任何不合法情况按 fail closed 返回 Deny，并记录失败原因。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 主 Agent 的 transcript 不应因 Guardian 错误被修改，审批 call 仍有确定终态。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 不得通过搜索文本中是否含 allow 来宽松解析，也不能把网络错误视为默认同意。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 连续拒绝进入熔断统计，达到阈值后中断 Turn，避免模型反复申请。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试缺字段、额外散文、错误枚举、超长输出、超时和连接断开，全部不得执行目标工具。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.10 上下文接近窗口触发自动压缩

**触发条件。** 长线程在工具循环中达到自动压缩阈值。

**主调用链。** run_turn 结束当前安全步骤，触发本地总结或远端 compact，保留初始基线、关键用户消息和近期 items，再继续采样。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 压缩改变模型上下文投影，不删除 canonical rollout；压缩元数据记录 trigger、reason、implementation 与 phase。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 外部工具输出中的注入指令不应在摘要中被提升为系统约束；用户否定条件要尽量保留。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 压缩失败时只能有限回退，不能在相同历史上无限重试；仍超限则明确结束。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 用小窗口假模型触发 pre-turn 与 mid-turn 压缩，验证历史可回放、模型输入缩短且用户关键约束存在。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.11 用户手动请求压缩

**触发条件。** 用户认为线程过长，主动发出 compact 操作。

**主调用链。** Session 在当前状态允许时创建压缩流程，发送开始/完成事件，并用新模型上下文替代后续请求输入。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 若当前有不可中断工具调用，应在安全点排队或明确拒绝，不能与历史追加并发重写。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** PreCompact Hook 可以阻止或准备状态，但不能偷偷扩大权限或删除事实日志。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 摘要生成失败时原上下文保持可用，线程不应进入半压缩状态。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试空线程、活动 Turn、已有摘要和连续手动压缩，确认幂等和事件终态。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.12 进程崩溃后恢复线程

**触发条件。** Codex 在写入过程中异常退出，用户重新启动并 resume。

**主调用链。** ThreadStore 先查询 SQLite；若投影不完整，则扫描 JSONL，容忍可识别的半写尾行，恢复最后 ordinal、历史和模型上下文，再打开 writer。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 恢复出的 Session 必须使用新运行时资源，但保留 ThreadId、来源和历史因果；不能复用失效句柄。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 若 rollout 有不可忽略损坏，应提示并限制写入，而不是静默跳过造成错误上下文。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** SQLite 可重建，JSONL 事实丢失则无法完全恢复；因此写入错误必须在崩溃前尽量暴露。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试在每个写入字节位置截断文件、删除 SQLite、重复启动 backfill，确保结果确定。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 12 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.13 SQLite 投影损坏或被锁

**触发条件。** 线程列表查询失败，但 rollout 文件仍完整。

**主调用链。** StateRuntime 返回诊断，LocalThreadStore 可降级读取或启动 reconcile；后台重建分批进行，前台仍可打开明确线程。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 不得把空查询结果解释为没有线程并执行清理，也不能用损坏投影覆盖 rollout。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 数据库文件权限和路径也受本地安全考虑，错误日志不应包含会话内容。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** busy timeout 后仍失败应向用户说明锁竞争；损坏则隔离旧库并重建，而不是无限重试同一连接。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 用独占事务、坏页模拟、旧 migration 和新版本 schema 测试查询与恢复。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.14 从历史点分叉新线程

**触发条件。** 用户在某个早期 Turn 选择 fork，以探索另一方案。

**主调用链。** ThreadStore 准备 fork 边界，复制所需模型上下文和 metadata，ThreadManager 分配新 ThreadId，创建独立 rollout 并记录父关系。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 新旧线程共享历史事实但后续写入完全分离；UI 不能把子线程事件归并回父线程。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 权限和管理员要求应按当前环境重新解析，不能因为父线程曾获一次审批就继承临时授权。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** fork 创建中途失败要清理 reservation 和空文件；父线程保持不变。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 验证在压缩前后、工具调用边界和多 Agent 子线程上 fork，检查历史配对和父子 metadata。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.15 删除仍在运行的线程

**触发条件。** 用户删除一个有活动命令和子 Agent 的 Thread。

**主调用链。** 控制面应先阻止新输入，传播取消，终止工具与子 Agent，flush 或记录最终状态，再清理 rollout、投影和附件。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 删除顺序必须防止后台 writer 在文件删除后重新创建；per-thread lifecycle lock 是关键。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 若删除包含敏感历史，应处理 spill 与临时文件；但安全审计环境可能要求保留策略，需由管理配置决定。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 部分清理失败时标记待重试，而不是列表消失后留下运行进程。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 集成测试让子进程忽略温和终止、writer 延迟和 SQLite busy，验证有界关闭。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.16 MCP Server 启动超时

**触发条件。** 配置中的本地 MCP 进程未完成 initialize 或直接挂起。

**主调用链。** 连接管理器发启动进度事件，在 deadline 后终止子进程并标记 server 不可用；其他 MCP 和核心线程继续工作。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 未完成握手的工具不能进入模型可见集合，避免模型调用不存在能力。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** MCP 进程继承的环境和凭据应最小化，启动错误不得打印秘密。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 可按策略重试，但要退避并限制次数；用户修复配置后可显式刷新。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试不输出、输出非法 framing、握手后崩溃和工具列表重复。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 16 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.17 MCP 工具请求 Elicitation

**触发条件。** 外部工具执行中需要用户补充参数或确认。

**主调用链。** MCP 结果被暂停，核心发带 ID 的 elicitation 事件，TUI/App Server 收集响应再转回原 server。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** Thread 可以有多个并发 elicitation，必须按 ID 和 server owner 路由；关闭线程时全部取消。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** elicitation 文本来自外部 server，UI 应标示来源，不能冒充系统审批；敏感输入的持久化策略需明确。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 宿主不支持、用户拒绝或超时时，server 获得结构化取消，工具调用结束。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试乱序响应、重复 ID、客户端断连和恶意超大表单。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.18 Plugin 注册同名工具

**触发条件。** 新插件声明与内置或另一插件相同的工具名称。

**主调用链。** Plugin manager 解析 manifest 和命名空间，ToolRegistry 在注册时检测冲突；可通过命名空间暴露或拒绝加载。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 模型看到的 spec、Router 映射和实际 handler 必须来自同一注册快照。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 不得使用最后加载覆盖，尤其不能覆盖安全工具或审批实现；插件来源应进入事件与遥测。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 加载失败只禁用该插件能力，不破坏已注册工具；卸载要等待在途调用结束。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试不同加载顺序、大小写、别名和延迟发现，保证结果确定。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.19 PreToolUse Hook 重写命令

**触发条件。** 组织 Hook 将不合规命令替换为受控包装器。

**主调用链。** Registry 先运行 PreToolUse，获得 rewrite 后建立最终 ToolInvocation，再由 Orchestrator 重新解析权限和审批。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 原始与重写动作均应记录，最终 ToolOutput 关联原 call ID，模型无需理解内部包装。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** Hook 不能隐藏新增网络或写权限；任何扩大范围的重写都触发审批或拒绝。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** Hook 超时按管理策略 fail closed；输出过大 spill，不能阻塞整个 Turn。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试把只读命令改成写命令、路径逃逸、空命令和 Hook 自身崩溃。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.20 Code Mode host 缺失或版本不匹配

**触发条件。** 模型选择 Code Mode，但辅助 host 未安装或协议版本不同。

**主调用链。** CodeModeSessionProvider 在启动/握手阶段返回明确不可用，Router 可回落到直接工具或把错误回填模型。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 不能在主进程里临时 eval 代码作为“兼容回退”，否则绕过进程隔离。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** host 路径必须来自可信安装上下文并校验版本，避免执行工作区内同名恶意文件。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 失败后关闭半开的 gRPC/session，释放进程和端口；同一 Turn 不无限重启。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试 host 不存在、不可执行、旧协议、启动后退出和无限循环。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 20 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.21 子 Agent 创建超过并发或 rollout 预算

**触发条件。** 主 Agent 递归委派，达到根线程树的执行上限。

**主调用链。** AgentControl 在 spawn 前通过 execution limiter 和共享 rollout budget 检查，拒绝新 Agent 或让模型等待现有 Agent 完成。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** reservation 若已占用路径/昵称，拒绝后必须释放；预算不能因换角色或新子路径重置。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 子 Agent 权限不应默认高于父线程，且每个工具仍独立审批。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 拒绝作为结构化工具结果回填，主 Agent 可改为自行处理；不能让 spawn 请求永久排队。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 压力测试深层递归、并发完成和取消，确认计数最终回到零。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.22 子 Agent 异常死亡

**触发条件。** 子 Agent 的内部任务崩溃或线程通道关闭。

**主调用链。** AgentControl 识别 InternalAgentDied，从 ThreadManagerState 移除线程、忘记 residency、释放 Registry 记录，并通知父线程最终状态。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 父 Agent 不应继续向死亡路径发送输入；旧 AgentPath 解析必须失败或指向新明确实例。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 死亡 Agent 已执行的文件副作用仍需在工作区和 rollout 中可见，不能因 registry 清理而抹去。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 完成 watcher 必须有终态，即使 watch channel 关闭也查询最新状态。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试在 spawn 后、工具中和完成通知前注入死亡，确认无预算泄漏。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.23 多个 Agent 同时修改同一文件

**触发条件。** 两个子 Agent 在共享工作区对同一文件生成补丁。

**主调用链。** 运行时可通过独立 worktree、任务分区或写锁减少冲突；父 Agent 在整合前比较基线和最终 diff。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 每个线程的 rollout 可以各自正确，但共享磁盘状态仍可能出现最后写覆盖，因此一致性不能只看会话日志。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 审批只说明各自有写权限，不解决业务冲突；安全层与协作层职责要分开。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 检测到基线漂移时应拒绝盲目应用并要求重新读取或合并。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 构造两个基于相同旧内容的 patch，验证冲突检测或 worktree 隔离。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.24 Realtime 将语音任务交给后台 Agent

**触发条件。** 用户在实时会话中口述编码任务，前台模型发起 handoff。

**主调用链。** Realtime manager 从有界音频/文本队列构建启动上下文，创建后台 Turn，持续把后台进度以受限 handoff 流送回实时会话。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 实时 transcript 与后台 rollout 是两个投影，需用 handoff ID 和 Thread/Turn ID 关联。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 只传递任务相关片段，避免整段私人语音进入项目历史；后台工具仍走正常权限。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 用户结束通话时按策略等待或 detach 后台任务，并把剩余 transcript tail 受控交接。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试网络抖动、音频队列满、后台完成与通话结束竞态、输出截断标记。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 24 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.25 认证令牌在模型请求中失效

**触发条件。** 活动 Turn 的模型调用返回未授权。

**主调用链。** ModelClient 通过 AuthManager 进入 UnauthorizedRecovery，可能刷新、重新读取外部身份或发认证恢复事件，成功后在安全条件下重试请求。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 并发请求应共享 single-flight 刷新，避免多个 refresh token 竞争并覆盖。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 请求头和刷新错误不得写入 rollout；宿主只看到可行动状态。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 刷新失败时 Turn 结束或等待用户登录，不能切换到未知凭据来源。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试 401、刷新成功、刷新 token 轮换、撤销和多个并发采样。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.26 WebSocket 采样失败回退 HTTP

**触发条件。** Turn-scoped ModelClientSession 的 WebSocket 在建连或流式阶段失败。

**主调用链。** 若错误满足回退条件，会话将后续采样固定到 HTTP，避免反复摇摆；已确认的响应项必须避免重复。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 粘性 turn state 只能在当前 Turn 使用，回退和下一 Turn 都要重新初始化。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 传输回退不改变工具权限，也不能把失败请求中的敏感头输出给用户。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 无法确定服务端是否已处理时，应谨慎重试并利用响应 ID/协议状态去重。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 用故障代理在握手前、首帧前和中途断开，验证各阶段策略不同。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.27 TTY 子进程等待终端查询响应

**触发条件。** 交互程序在 PTY 中发送窗口大小、光标或设备状态查询并阻塞。

**主调用链。** PTY driver 将控制序列交给 terminal query responder；解析器可跨输出 chunk 保留状态，识别受支持查询并写回有界响应。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 普通程序输出仍送 UI，查询序列不应被当作可见文本或模型内容。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** 响应集合必须白名单化，避免任意控制序列导致注入或无限反射。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 不支持查询应有超时或安全忽略策略；中断仍能终止进程。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试每个字节拆分、多个查询连写、恶意超长序列和 driver-backed TTY。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

### Y.60.28 命令输出过大导致背压

**触发条件。** 构建或日志命令高速输出数百兆字节。

**主调用链。** 执行层对 stdout/stderr 使用有界通道，UI 增量做合并，模型 ToolOutput 做头尾截断或摘要，必要时 spill 到受控文件。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 事件 call ID 和字节计数保留，使 UI 可提示内容已截断并提供诊断位置。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** spill 文件应位于受控目录、权限最小、生命周期可清理，秘密扫描或脱敏策略仍适用。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 慢消费者不能阻塞进程到死锁；也不能无界缓存导致 OOM。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 压力测试持续输出、stderr/stdout 交错、UTF-8 边界和中途取消。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

```mermaid
flowchart LR
    A[场景 28 输入] --> B[宿主适配]
    B --> C[Thread / Turn]
    C --> D[模型与工具]
    D --> E[审批与沙箱]
    E --> F[事件与持久化]
    F --> G[终态验证]
```

### Y.60.29 管理员约束与用户配置冲突

**触发条件。** 用户 Profile 请求无沙箱和全网络，但 requirements 禁止。

**主调用链。** ConfigLayerStack 先合并用户表达，再由 ConfigRequirements/Constrained 校验，返回带来源的冲突诊断或收敛到允许值。 这一链路中，宿主只负责输入与呈现，领域状态由 Thread/Session 持有，工具副作用由统一治理层控制。评审时应逐个标出关联的 ThreadId、TurnId、ItemId 与 call ID，确保任何异步结果都能回到正确实体。

**状态与一致性。** 线程配置快照记录最终有效策略，UI 应显示被管理，而不是展示用户原始值。 同时要区分实时 EventMsg、模型上下文和 canonical rollout：实时事件可以高频，模型上下文可以压缩，事实日志必须保持可回放。不能因为界面已经显示完成，就推断持久化 writer 已经成功 flush。

**权限与安全。** CLI override 不能越过 requirements；managed-hooks-only 也只能由 requirements 启用。 任何来源于模型、仓库、MCP、Plugin 或 Hook 的文本都只是意图或数据，不能直接成为操作系统授权。真正能力仍由配置约束、审批、Guardian、沙箱和网络代理的交集决定。

**故障与恢复。** 云配置暂时不可用时应使用有明确时效的缓存或安全默认，不能自动放宽。 失败路径必须产生确定终态，释放在途 Future、锁、channel sender、子进程和 reservation。对无法确认是否已发生副作用的情况，应向上层报告“结果未知”或要求重新检查磁盘事实，而不是假装完全失败后自动重试。

**验证方案。** 测试各层加载顺序、主机名 requirements、未知字段和远端配置回滚。 除断言最终文本外，还应核对事件数量与顺序、历史 call/output 配对、rollout 行、SQLite 投影、进程存活、临时文件和遥测标签。只有这些观察面一致，场景才算真正通过。

**工程结论。** 该场景再次说明 Codex 的可靠性来自边界协作，而不是某个单一类。入口保证协议正确，核心保证因果顺序，工具层保证能力映射，安全层保证最小权限，数据层保证恢复，观测层保证问题可定位。修改其中任何一层，都必须用端到端用例检查其他观察面是否仍保持一致。

## 第十一篇·结论、阅读路线与源码索引

## Y.61 架构结论

OpenAI Codex 当前开源实现最值得借鉴的地方，不是某一个 Prompt 或某一个工具，而是它把编码 Agent 的复杂性拆成了可验证的边界：

1. **Thread、Turn、Item 建立稳定领域语言。** Thread 管长期生命周期，Turn 管一次执行事务，Item 管可观察语义；这比简单消息数组更能表达工具、审批、补丁、压缩和子 Agent。
2. **共享核心支撑多种宿主。** TUI、Exec、App Server、MCP Server 与远程执行的差异停留在输入适配和事件消费，Agent Loop、安全和持久化保持一致。
3. **模型调用与系统副作用分离。** 模型只产生结构化意图，ToolRouter/Registry/Orchestrator 决定能力映射，Permission/Approval/Guardian/Sandbox 决定能否真正执行。
4. **安全采用纵深防御。** 工具可见性、参数校验、Hook、权限合并、审批、自动审查、操作系统沙箱、网络代理、秘密脱敏和审计日志层层叠加，没有把安全寄托在模型“自觉”。
5. **持久化采用事实与投影分离。** JSONL rollout 保持可回放和人工可读，SQLite 提供高效查询；投影损坏可以从事实重建。
6. **扩展有统一归因。** Skill、Plugin、MCP、Hook、Connector 和 Code Mode 各有边界，并尽量回到同一工具治理链。
7. **多 Agent 是控制面问题。** AgentControl、Registry、AgentPath、执行限制、rollout 预算和完成 watcher 共同约束树形协作，而非无上限递归启动会话。
8. **失败路径是核心设计对象。** 超时、取消、半写、断流、拒绝、进程死亡、配置冲突和版本不兼容都有显式语义，体现生产级 Agent 与演示原型的差别。

```mermaid
flowchart TB
    Intent[用户意图]
    Host[多宿主适配]
    Domain[Thread / Turn / Item]
    Reason[模型推理循环]
    Capability[工具与扩展能力]
    Governance[审批 / Guardian / 沙箱 / 代理]
    Effects[文件 / Shell / 网络 / MCP]
    Record[Rollout 事实 + SQLite 投影]
    Observe[Event / Log / Trace / Analytics]

    Intent --> Host
    Host --> Domain
    Domain --> Reason
    Reason --> Capability
    Capability --> Governance
    Governance --> Effects
    Domain --> Record
    Reason --> Record
    Effects --> Record
    Domain --> Observe
    Governance --> Observe
    Record --> Observe
```

## Y.62 推荐源码阅读路线

### Y.62.1 两小时建立骨架

按以下顺序阅读，可以快速建立正确心智模型：

1. 根 `README.md` 与 `codex-rs/Cargo.toml`，确认产品定位和 workspace 边界；
2. `cli/src/main.rs`，理解入口和子命令路由；
3. `core/src/lib.rs`、`thread_manager.rs`、`codex_thread.rs`，理解控制面；
4. `core/src/session/turn.rs`，定位真正 Agent Loop；
5. `protocol/src/protocol.rs`，把操作和事件映射到循环状态；
6. `core/src/tools/router.rs`、`registry.rs`、`orchestrator.rs`，理解工具治理；
7. `thread-store`、`rollout`、`state`，理解恢复与查询。

### Y.62.2 一天深入执行与安全

继续阅读 PermissionProfile、tools/sandboxing、`sandboxing` crate、`linux-sandbox`、`execpolicy`、`network-proxy`、Guardian、shell/unified exec/PTY。每读一个函数都追问：输入是否来自模型，授权在哪发生，沙箱在哪变换，拒绝如何识别，重试是否可能重复副作用。

### Y.62.3 一天深入扩展与多 Agent

阅读 skills、plugin、hooks、MCP、Code Mode、extension API 与 core agent modules。重点绘制“外部声明 → 发现 → 命名空间 → 模型可见 → ToolRouter → 权限 → 执行 → 归因”的完整链路；随后追踪 spawn、send input、status watch、completion notification 和 cleanup。

### Y.62.4 一天深入持久化与恢复

从一个用户输入开始，找到它如何成为 canonical item、如何进入 writer、JSONL 行和 SQLite 投影；然后模拟进程在每个阶段崩溃，验证 resume、backfill、fork 和 archive。只有能解释事实源与投影的差异，才算真正理解线程存储。

### Y.62.5 最后阅读测试

测试不是附录，而是作者对不变量的最明确表达。搜索类型和函数的测试调用，尤其关注 failure injection、snapshot、旧 fixture、平台条件和取消。对一个没有负向测试的安全分支，应保持额外怀疑。

## Y.63 二次开发原则

### Y.63.1 新增工具

新增工具时，不应直接在 `run_turn` 写分支。应定义稳定 spec 和 payload，注册到 Registry，声明暴露方式和并行能力，通过 Orchestrator 执行，并提供审批、沙箱、取消、输出截断、事件和测试。工具返回值要区分业务失败、权限拒绝、沙箱拒绝和取消。

### Y.63.2 新增宿主

新增 GUI 或 IDE 宿主优先使用 App Server 协议，或依赖核心的稳定边界，不要解析 TUI ANSI 输出。宿主要实现初始化、Thread/Turn/Item 归约、审批响应、断线重连、未知事件兼容和慢消费者背压。

### Y.63.3 新增持久化后端

实现 ThreadStore trait，保持 ThreadId 稳定，明确 canonical 事实格式、append 顺序、flush、fork、revert 和查询一致性。若后端是远程服务，还要增加幂等键、离线语义、重试和认证，不得把网络暂时失败误判为线程不存在。

### Y.63.4 新增扩展类型

优先通过 extension API、Plugin manifest、Skill、Hook 或 MCP 组合已有能力。只有需要新的生命周期事实时才扩展核心；扩展状态应进入正确作用域，并定义来源、版本、卸载和遥测。

### Y.63.5 修改协议

协议变更必须考虑旧客户端、旧 rollout 和不同发布版本。新增可选字段优于改变既有字段语义；新增事件要说明客户端忽略时是否安全；删除 legacy bridge 前应有明确迁移窗口。

## Y.64 关键源码永久链接

以下链接固定到本文快照，不随 `main` 漂移。

| 主题 | 源码路径 | 阅读重点 |
|---|---|---|
| 工作区 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/Cargo.toml | members、统一依赖、edition、lint |
| 项目入口说明 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/README.md | 安装、定位、许可证 |
| Core 导出 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/lib.rs | 核心模块边界与公共类型 |
| CLI | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/cli/src/main.rs | MultitoolCli 与子命令路由 |
| CodexThread | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/codex_thread.rs | 宿主句柄、配置快照、提交与事件 |
| ThreadManager | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/thread_manager.rs | 创建、恢复、分叉、注册表 |
| Session | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/session/mod.rs | 操作分派与会话服务 |
| Session 状态 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/session/session.rs | 线程级状态与持久化协调 |
| Turn 循环 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/session/turn.rs | run_turn、采样、工具 Future |
| 模型客户端 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/client.rs | ModelClient、WebSocket/HTTP |
| 压缩 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/compact.rs | 本地与远端压缩、历史重建 |
| 协议 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/protocol/src/protocol.rs | Op、EventMsg、审批与事件类型 |
| Tool Router | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/tools/router.rs | ResponseItem 到 ToolCall |
| Tool Registry | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/tools/registry.rs | 注册、Hook、生命周期 |
| Tool Orchestrator | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/tools/orchestrator.rs | 审批、沙箱选择与升级重试 |
| App Server 说明 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/app-server/README.md | 初始化、Thread/Turn/Item API |
| App Server 处理 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/app-server/src/message_processor.rs | 请求分派与通知 |
| Exec | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/exec/src/lib.rs | 非交互宿主 |
| TUI | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/tui/src/lib.rs | TUI 启动与事件循环 |
| ThreadStore 接口 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/thread-store/src/store.rs | 存储中立 API |
| LocalThreadStore | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/thread-store/src/local.rs | JSONL 与 SQLite 双存储 |
| Rollout | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/rollout/src/lib.rs | canonical recorder 与 writer |
| State | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/state/src/lib.rs | SQLite runtime 与 backfill |
| 沙箱 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/sandboxing/src/lib.rs | 跨平台 SandboxManager |
| Skill | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/skills/src/lib.rs | 加载、frontmatter、系统 Skill |
| Skill 运行时 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/skills.rs | 显式/隐式调用和归因 |
| Plugin | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/plugin/src/lib.rs | package model 与能力摘要 |
| Hook | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/hooks/src/lib.rs | 十二类事件与公开结果 |
| 多 Agent 控制 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/agent/control.rs | 根树控制、预算、通信、watcher |
| Guardian | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/guardian/mod.rs | fail closed、schema 与熔断 |
| Config | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/config/src/lib.rs | 层、requirements、约束与诊断 |
| Login | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/login/src/lib.rs | OAuth、设备码、API key 与恢复 |
| OTEL | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/otel/src/lib.rs | trace、metrics 与 exporter |
| Analytics | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/analytics/src/lib.rs | 结构化产品事实 |
| Realtime | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/codex-rs/core/src/realtime_conversation.rs | 音频、文本、WebRTC 与 handoff |
| CI | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/.github/workflows/rust-ci.yml | 快速门禁与变更检测 |
| 贡献政策 | https://github.com/openai/codex/blob/0ae94fdd49b05ee7faa4d984d06a68492cb32b54/docs/contributing.md | issue、反馈和安全披露 |

## Y.65 术语表

| 术语 | 本文含义 |
|---|---|
| Thread | 可恢复、可分叉、可归档的长期会话容器 |
| Turn | 一次输入触发到最终结果/中断的执行事务 |
| Item | 消息、推理、命令、补丁、MCP、计划、子 Agent 活动等语义单元 |
| Op | 宿主提交给核心的操作 |
| EventMsg | 核心发给宿主的强类型事件联合 |
| Session | 单 Thread 的运行时状态与服务协调器 |
| ModelClientSession | 仅在一个 Turn 内存在的模型传输和粘性状态 |
| ToolRouter | 把模型响应项解析为内部工具调用并选择暴露模式 |
| ToolRegistry | 工具名到 runtime/handler 的注册、Hook 与生命周期入口 |
| ToolOrchestrator | 审批、沙箱选择、执行尝试和升级重试的策略中心 |
| PermissionProfile | 文件、网络、环境和沙箱能力的结构化权限描述 |
| Guardian | 对 on-request 审批进行独立结构化自动审查的会话 |
| Sandbox | 在操作系统层限制文件、网络、进程和系统调用的执行环境 |
| Rollout | 每线程的 canonical JSONL 可回放事实日志 |
| Projection | 从 rollout 派生、用于查询的 SQLite 状态 |
| Skill | 以 SKILL.md、资源和元数据扩展模型行为的能力包 |
| Plugin | 可组合 Skill、MCP、Connector 与 Hook 的声明式扩展包 |
| Hook | 在工具、压缩、会话、子 Agent、停止等生命周期执行的扩展点 |
| MCP | 外部工具、资源和 elicitation 的标准协议边界 |
| Code Mode | 在独立受控运行时中用代码组合嵌套工具的执行模式 |
| AgentControl | 根线程树范围的子 Agent 控制面 |
| AgentPath | 多 Agent 树中的层级逻辑身份 |
| Steer | 活动 Turn 中追加用户约束，在安全边界进入后续采样 |
| Compaction | 用摘要/压缩项替代模型上下文中的早期历史，不删除事实日志 |
| Fail closed | 无法判断、超时或解析失败时拒绝，而非默认允许 |

## Y.66 文档质量门禁

本文完成后执行自动检查，最终交付快照如下：

| 检查项 | 结果 |
|---|---:|
| 固定源码提交 | `0ae94fdd49b05ee7faa4d984d06a68492cb32b54` |
| Unicode 字符数 | `227,202` |
| 非空白字符数 | `202,721` |
| CJK 统一汉字数 | `110,931` |
| Mermaid 代码块 | `91` |
| Markdown 标题 | `534` |
| 文档行数 | `5,903` |
| Markdown 围栏总数 | `190`，为偶数 |

同时满足以下语义门禁：

- 所有核心源码永久链接使用同一提交 SHA；
- CJK 统一汉字计数不少于 100,000；
- Mermaid 代码块不少于 80；
- 章节、逐 crate 导读与端到端场景均存在；
- 明确区分源码事实、设计解释和演进推断；
- 未把闭源模型服务内部实现写成开源源码事实。

---

**文档结束。**

---

> **使用提示**：与其他附录的分工——A 讲模型机制、B 讲方法论、C 记来源、D 列产品、E 辨异同、F 索引图版、G 详解 OTel、H 上手 DeepEval、I 评测观测平台选型、J 上手 Mem0、K 详解记忆晋升机制、L 盘点 Coding Agent 赛道、M 盘点可观测赛道、N 盘点评估赛道、O 盘点 Memory 赛道、P 盘点自进化赛道、Q 盘点多 Agent 赛道、R 盘点 MCP 生态、S 盘点沙箱赛道、T 盘点 RAG 赛道、U 盘点 LLM Wiki 赛道、V 盘点 Loop Engineering 赛道、W 解析 Pi 源码、X 解析 Claude Code 源码、**Y 解析 Codex 源码**、Z 解析 OpenCode 源码。对照阅读：Agent Loop 与 Turn 闭环见篇二对第 3 章、压缩见 Y.13 一带对第 5 章、工具编排见篇三对第 7 章、审批与沙箱见篇四对第 9/13 章、MCP 与多 Agent 见篇五对第 8/17 章、持久化与恢复见篇六对第 12 章、OTel 见篇七对第 14 章与附录 G。快照为固定提交 0ae94fd（[C-35]），二次开发前先核对上游变更；W/X/Y 三份解析对读，是"同类问题不同答案"的最好教材。
