# 附录 30：LangGraph 实战教程

> 定位：**LangGraph 的完整实战教程**（全文收录，含 19 张架构 SVG 图与官网原图；实验基线 Python 3.13 / LangGraph 1.1.2，部分能力标注需 1.2+，信息基准 2026-09，官方入口见 [C-53]）。与相邻内容的分工：第 18 章讲图执行的机制原理（节点/边/共享状态、条件路由、回边、interrupt），附录 22 把 Graph Engineering 放进四件方法论全景，附录 17 盘点多 Agent 框架——本附录把 LangGraph 这一最主流的显式图执行框架讲透并落到代码：从 StateGraph 三要素、Pregel 与 Superstep 运行时、State/Channels/Reducer、控制流原语（Send/Command/defer/MapReduce/ReAct）、节点执行与重试超时缓存、持久化与 Durable Execution/Time Travel、短长期记忆与运行时上下文、中断与 Human-in-the-loop、工具调用与 ReAct Agent、流式执行、子图与模块化，到多 Agent 模式、部署与企业运行时。读完第 18 章带着"图执行到底怎么落地"的问题来查，这里有可运行的答案。API 与版本会演进，Pregel/Superstep/Channel/Checkpointer 的核心模型不过期。

---

## 30.1 学习路线与核心认知

### 30.1.1 LangGraph 解决什么问题

LangGraph 不是“再封装一层 Prompt”的工具，而是一个面向 **有状态工作流与 Agent 的执行运行时**。它尤其适合以下问题：

- 流程存在多个节点、条件分支、循环与并行；
- 需要把确定性业务步骤与 LLM 决策混合编排；
- 任务可能运行很久，中途会失败、暂停或等待人工输入；
- 需要保存会话状态、恢复执行、回放历史或从历史状态分叉；
- 需要逐 Token、逐节点、逐状态变更地流式输出；
- 需要把大型流程拆分成可复用子图；
- 需要对工具调用、权限、审批和副作用进行精细控制。

可以把 LangChain 与 LangGraph 的关系理解为：

```mermaid
flowchart TB
    APP[业务应用]
    LC[LangChain 高层 Agent API<br/>create_agent / Middleware / Structured Output]
    LG[LangGraph 编排与 Agent Runtime<br/>StateGraph / Checkpoint / Stream / HITL]
    MODEL[模型、工具、数据库、外部服务]

    APP --> LC
    APP --> LG
    LC --> LG
    LG --> MODEL
```

对于简单 Agent，可以直接从高层 Agent API 开始；当需要复杂状态、精细路由、可恢复执行或人工介入时，再直接使用 LangGraph。

![LangChain、LangGraph 与 LangSmith 产品栈](../assets/appendix30/01-langgraph-ecosystem-stack.svg)

> **图 1-1　LangChain 产品定位关系与 LangGraph 运行时（概念重绘）。** 依据 LangGraph Overview 与官方 GitHub README 重绘：LangGraph 处于 Agent Framework 与模型、工具、持久化资源之间，LangSmith 横向提供追踪、评估与部署能力。此图为概念重绘，非官方原图。


### 30.1.2 三个基本要素：State、Node、Edge

### State

State 是运行图在某一时刻的共享状态快照，承担以下职责：

- 保存外部输入；
- 保存节点中间结果；
- 保存消息历史；
- 保存路由决定、错误、重试次数、评估反馈；
- 为下游节点提供上下文。

### Node

Node 是执行单元，通常是 Python 函数、异步函数或 Runnable。节点读取当前状态，完成业务逻辑，只返回需要更新的字段。

### Edge

Edge 表达节点之间的触发与依赖关系：

- 普通边：固定进入某节点；
- 条件边：根据状态选择节点；
- 多条出边：触发并行分支；
- 多前驱汇聚：等待一个或多个上游；
- 指向 `END`：表达当前路径结束；
- `Command(goto=...)`：由节点在运行时决定下一跳。

```mermaid
flowchart LR
    START([START]) --> N1[读取与理解]
    N1 -->|状态更新| S[(State)]
    S --> N2[执行任务]
    N2 -->|条件成立| N3[人工审核]
    N2 -->|条件不成立| END([END])
    N3 --> END
```

### 30.1.3 Pregel 与 Superstep

附件强调，LangGraph 底层运行时借鉴 Pregel 计算模型。图不是“节点执行完立刻直接改全局字典”，而是按 **Superstep（超步）** 推进。

一个超步可理解为三个阶段：

```mermaid
flowchart LR
    P[Plan / Routing<br/>根据当前状态与边确定任务]
    E[Execution<br/>执行本轮节点<br/>并行节点读取同一状态快照]
    C[Update / Commit<br/>合并全部节点更新<br/>形成新状态快照]
    P --> E --> C --> P
```

这带来两个关键结论：

![Graph API 与 Functional API 编译为 Pregel Runtime](../assets/appendix30/02-stategraph-to-pregel-runtime.svg)

> **图 1-2　两种高层 API 共享 Pregel Runtime。** `StateGraph.compile()` 或 `@entrypoint` 最终形成可执行的 Pregel 应用，运行时由节点、Channels、Scheduler 与 Checkpoint/Store/Stream 等服务共同组成。依据官方 Pregel Runtime 文档重绘。

![Pregel Superstep 生命周期](../assets/appendix30/03-pregel-superstep-lifecycle.svg)

> **图 1-3　Pregel Superstep 生命周期。** 同一超步中的节点读取同一状态快照，局部更新在 Commit 阶段统一合并，然后形成下一超步的新状态。依据官方 Graph API 与 Pregel Runtime 文档重绘。


1. 同一个超步中的并行节点读取的是本轮开始时的状态，不能立即看到另一个并行节点刚产生的更新。
2. 并行节点对同一字段写入时必须有明确的合并规则，否则会发生冲突。

### 30.1.4 Graph API 与 Functional API

| 维度 | Graph API | Functional API |
|---|---|---|
| 风格 | 声明式图结构 | 命令式 Python 流程 |
| 核心抽象 | State、Node、Edge | `@entrypoint`、`@task` |
| 状态管理 | 显式 | 更多依赖参数和返回值 |
| 可视化 | 强 | 相对弱 |
| 适用场景 | 多分支、并行、循环、长期维护 | 线性流程、已有代码最小改造 |
| 学习建议 | 优先掌握 | 按需补充 |

本教程以 Graph API 为主，因为它最能体现 LangGraph 的运行时模型。

### 30.1.5 推荐学习路线

```mermaid
flowchart LR
    A[State / Node / Edge] --> B[Reducer 与 Multi Schema]
    B --> C[分支 / 并行 / Send / Command]
    C --> D[Checkpoint 与恢复]
    D --> E[Memory / Context]
    E --> F[HITL 与工具审批]
    F --> G[Streaming]
    G --> H[Subgraph]
    H --> I[生产部署与设计模式]
```

快速入门只需先掌握 A—C；开发可靠 Agent 必须继续掌握 D—G；设计平台级 Agent Runtime，则需要全部掌握。

---

## 30.2 环境配置与项目骨架

### 30.2.1 创建 Conda 环境

附件使用 Python 3.13：

```bash
conda create -n langgraph python=3.13
conda activate langgraph
python --version
```

中国大陆网络环境可按需配置镜像：

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

遇到镜像同步延迟时恢复默认：

```bash
pip config unset global.index-url
```

### 30.2.2 安装依赖

若有附件配套的 `requirements.txt` 与 `requirements_full.txt`，应优先使用锁定文件：

```bash
pip install -r requirements_full.txt
```

没有配套文件时，可先安装用于本教程核心案例的最小集合：

```bash
pip install \
  langgraph \
  langgraph-cli[inmem] \
  langgraph-checkpoint-postgres \
  langchain \
  langchain-core \
  langchain-deepseek \
  python-dotenv \
  pydantic \
  psycopg[binary] \
  jupyter \
  loguru
```

生产项目必须使用 `requirements.lock`、`uv.lock`、`poetry.lock` 或其他版本锁定机制，避免本地、CI 与服务器使用不同 API。

### 30.2.3 配置环境变量

项目根目录创建 `.env`：

```dotenv
DEEPSEEK_API_KEY=sk-替换为自己的密钥
MODEL_NAME=deepseek-v4-flash

# 部署和 LangSmith 追踪时按需启用
# LANGSMITH_API_KEY=lsv2-...
# LANGSMITH_TRACING=true
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com
# LANGSMITH_PROJECT=langgraph-tutorial
```

不要把 `.env` 提交到 Git：

```gitignore
.env
.venv/
__pycache__/
.ipynb_checkpoints/
```

### 30.2.4 推荐项目结构

```text
langgraph-tutorial/
├── .env
├── pyproject.toml
├── README.md
├── src/
│   └── langgraph_tutorial/
│       ├── __init__.py
│       ├── config.py
│       ├── model.py
│       ├── state.py
│       ├── tools.py
│       ├── nodes.py
│       ├── graph.py
│       └── api.py
├── tests/
│   ├── test_nodes.py
│   ├── test_graph.py
│   ├── test_recovery.py
│   └── test_hitl.py
└── notebooks/
    └── experiments.ipynb
```

把模型初始化集中到一个文件，避免每个节点各自创建客户端：

```python
# src/langgraph_tutorial/model.py
import os
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

load_dotenv(override=True)


def create_model() -> ChatDeepSeek:
    model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
    return ChatDeepSeek(
        model=model_name,
        extra_body={"thinking": {"type": "disabled"}},
    )
```

`extra_body` 是附件示例中的模型参数，不是 LangGraph 的统一要求。切换模型供应商时，应在模型适配层处理，而不是把供应商分支写进图节点。

### 30.2.5 环境验证脚本

```python
# verify_env.py
import os
import sys
from importlib.metadata import version
from dotenv import load_dotenv

load_dotenv(override=True)

print("Python:", sys.version)
print("langgraph:", version("langgraph"))
print("langchain-core:", version("langchain-core"))
print("API Key configured:", bool(os.getenv("DEEPSEEK_API_KEY")))
```

运行：

```bash
python verify_env.py
```

### 30.2.6 环境排错

| 问题 | 排查方法 |
|---|---|
| `conda` 找不到 | Windows 使用 Anaconda Prompt；macOS/Linux 执行 `conda init` 后重启终端 |
| `ModuleNotFoundError` | 检查当前解释器与 `pip` 是否属于同一个环境 |
| Jupyter 找不到 Kernel | 安装 `ipykernel` 并注册环境 |
| API 认证失败 | 检查 `.env` 位置、变量名、额度与 `load_dotenv()` |
| C++ 编译报错 | 优先安装有预编译 wheel 的版本；Windows 按需安装 Build Tools |
| 同一代码在 CI 失败 | 输出 Python 和包版本，检查锁文件是否生效 |

---

## 30.3 第一个 StateGraph

### 30.3.1 构建流程

一个图通常经历三个阶段：

```mermaid
flowchart LR
    D[定义 State、Node、Edge] --> C[compile 编译]
    C --> R[invoke / stream 运行]
```

### 30.3.2 完整示例

```python
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class OverallState(TypedDict):
    logs: Annotated[list[str], add]
    current_path: str


def node_1(state: OverallState) -> dict:
    return {
        "logs": ["node_1 运行完毕"],
        "current_path": state["current_path"] + " -> node_1",
    }


def node_2(state: OverallState) -> dict:
    return {
        "logs": ["node_2 运行完毕"],
        "current_path": state["current_path"] + " -> node_2",
    }


builder = StateGraph(state_schema=OverallState)
builder.add_node("node_1", node_1)
builder.add_node("node_2", node_2)

builder.add_edge(START, "node_1")
builder.add_edge("node_1", "node_2")
builder.add_edge("node_2", END)

graph = builder.compile()

result = graph.invoke(
    {
        "logs": [],
        "current_path": "START",
    }
)
print(result)
```

预期状态：

```python
{
    "logs": ["node_1 运行完毕", "node_2 运行完毕"],
    "current_path": "START -> node_1 -> node_2",
}
```

注意：节点返回的是 **局部状态更新**，不要求返回完整状态。

### 30.3.3 图的可视化

获取 Mermaid 源码：

```python
print(graph.get_graph().draw_mermaid())
```

在 Jupyter 中展示：

```python
from IPython.display import display

display(graph)
```

保存 PNG：

```python
png_bytes = graph.get_graph().draw_mermaid_png()
with open("graph.png", "wb") as file:
    file.write(png_bytes)
```

附件指出，默认 Mermaid PNG 渲染可能依赖在线服务，网络不稳定时可能失败。生产文档流水线更适合保存 Mermaid 源码，在 CI 中使用固定版本的本地渲染器。

### 30.3.4 `START` 与 `END`

- `START` 决定图从哪里启动，通常不能省略。
- `END` 是终止标记，不是一个真实业务节点。
- 简单图在没有任何新节点被激活时也会自然结束，但显式连到 `END` 更利于阅读、可视化和维护。

---

## 30.4 State 状态工程

State 设计决定了图的可维护性、并行安全性、恢复粒度和可观测性。复杂 Agent 出问题，很多时候并不是 Prompt 不够好，而是状态模型不清晰。

### 30.4.1 三种 Schema 定义方式

### TypedDict

```python
from typing import TypedDict


class State(TypedDict):
    user_input: str
    answer: str
```

优点：轻量、清晰、与“节点返回部分字典更新”的模式天然一致。附件建议多数场景优先使用它。

### dataclass

```python
from dataclasses import dataclass


@dataclass
class State:
    user_input: str
    answer: str
```

适合希望使用属性访问、默认值或数据类工具的场景。

### Pydantic

```python
from pydantic import BaseModel


class State(BaseModel):
    user_input: str
    answer: str = ""
```

适合需要更严格校验或与结构化数据模型复用的场景，但开销和复杂度更高。

### 30.4.2 节点只返回局部更新

```python
class State(TypedDict):
    user_input: str
    normalized_input: str
    answer: str


def normalize(state: State) -> dict:
    return {"normalized_input": state["user_input"].strip()}
```

规则：

- 没有返回的字段保持原值；
- 返回的字段若无 Reducer，则新值覆盖旧值；
- 返回的字段若有 Reducer，则按 Reducer 合并；
- 返回未知字段时，附件指出对应更新通常会被忽略，因此不要依赖拼写错误触发异常。

### 30.4.3 Reducer：状态合并规则

Reducer 是二元函数：

![State、Channels 与 Reducer 的关系](../assets/appendix30/04-state-channels-and-reducers.svg)

> **图 4-1　State、Channels 与 Reducer。** State Schema 在编译后映射为多个状态 Channel；节点返回局部更新，运行时在超步提交阶段逐字段应用 Reducer，生成新的 StateSnapshot。依据官方 Graph API 与 Pregel Runtime 文档重绘。


```text
(current_value, update_value) -> merged_value
```

通过 `Annotated` 绑定：

```python
from operator import add
from typing import Annotated, TypedDict


class State(TypedDict):
    logs: Annotated[list[str], add]
    latest_status: str
```

- `logs`：追加合并；
- `latest_status`：没有 Reducer，后写覆盖前写。

### 自定义 Reducer

```python
def merge_unique(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


class State(TypedDict):
    tags: Annotated[list[str], merge_unique]
```

### 并行场景中的 Reducer 设计原则

并行任务的提交顺序可能不是业务顺序，因此 Reducer 最好满足：

- **结合律**：分组方式不同，结果不变；
- **幂等性**：重复提交不会造成不可接受的重复副作用；
- **交换性**：若业务不依赖顺序，交换合并次序结果不变；
- **确定性**：相同输入得到相同结果。

`operator.add` 对列表是有序拼接，不具备交换性。若最终结果需要固定顺序，应给每个并行结果携带 `index`，汇聚后显式排序，而不是假定运行完成顺序。

### 30.4.4 `add_messages`

对话历史不能简单使用列表拼接，因为消息可能需要按 ID 更新。LangGraph 提供 `add_messages`：

```python
from typing import Annotated, TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

它的核心语义：

- 新 ID：追加消息；
- 已存在 ID：用新消息替换旧消息；
- 因而既能追加对话，也能修改某条历史消息。

### 30.4.5 `MessagesState`

```python
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    user_id: str
    final_answer: str
```

`MessagesState` 已经内置：

```python
messages: Annotated[list[AnyMessage], add_messages]
```

聊天机器人、Agent、Tool Calling 通常直接继承它。

附件还介绍了 LangChain Agent 内部的 `AgentState`。普通自定义图通常不建议直接依赖其内部控制字段，应优先定义自己的状态边界。

### 30.4.6 `Overwrite`：单次绕过 Reducer

```python
from langgraph.types import Overwrite


def reset_logs(state: State) -> dict:
    return {"logs": Overwrite(["从这里重新开始"])}
```

`Overwrite` 只影响本次更新，不会移除字段原有的 Reducer。后续节点正常返回 `logs` 时，仍会继续执行合并。

### 30.4.7 Multi Schema

复杂图建议区分四类状态：

| 类型 | 作用 |
|---|---|
| Input State | 约束图对外接收的字段 |
| Overall State | 图内部主要共享状态 |
| Output State | 约束图最终对外返回字段 |
| Private State | 少数节点之间传递的临时状态 |

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph


class InputState(TypedDict):
    username: str


class OutputState(TypedDict):
    greeting: str


class OverallState(TypedDict):
    username: str
    nickname: str
    greeting: str


class PrivateState(TypedDict):
    private_message: str


def make_nickname(state: InputState) -> dict:
    return {"nickname": "Dear " + state["username"]}


def make_private_message(state: OverallState) -> dict:
    return {"private_message": state["nickname"] + "，早上好"}


def make_output(state: PrivateState) -> dict:
    return {"greeting": state["private_message"] + "！"}


builder = StateGraph(
    state_schema=OverallState,
    input_schema=InputState,
    output_schema=OutputState,
)
builder.add_node("make_nickname", make_nickname)
builder.add_node("make_private_message", make_private_message)
builder.add_node("make_output", make_output)
builder.add_edge(START, "make_nickname")
builder.add_edge("make_nickname", "make_private_message")
builder.add_edge("make_private_message", "make_output")
builder.add_edge("make_output", END)

graph = builder.compile()
print(graph.invoke({"username": "小黄"}))
```

### Multi Schema 的关键语义

- `input_schema` 限制图边界输入，不是“只给第一个节点看”；
- 节点实际读取字段取决于节点第一个参数的类型注解；
- 节点返回值被视为状态更新，返回类型注解主要用于表达意图；
- `output_schema` 在图完成后裁剪最终输出；
- 底层每个状态字段通常会映射为一个 Channel。

### 30.4.8 状态设计反模式

### 把所有对象都塞进 State

数据库连接、模型客户端、文件句柄、当前请求对象不适合持久化，应放到 Runtime Context 或依赖容器中。

### 用一个超大 `dict[str, Any]`

这会失去类型边界，使节点依赖不透明，状态迁移困难。

### 并行节点写同一字段却没有 Reducer

可能触发 `InvalidUpdateError`，或者让结果依赖不稳定的完成顺序。

### 用消息历史代替业务状态

消息适合对话上下文，不适合承载所有结构化业务数据。任务 ID、审批状态、工具结果、质量分数应使用独立字段。

### 在节点中原地修改 State

推荐返回新更新，不要依赖对传入字典或列表的原地修改。原地修改会让缓存、重放、测试和并行语义变得不清晰。

---

## 30.5 控制流：从顺序到动态编排

### 30.5.1 顺序结构

![LangGraph 控制流原语全景](../assets/appendix30/05-control-flow-primitives.svg)

> **图 5-1　控制流原语全景。** 普通边处理固定流转，条件边处理编译期已知候选路径，`Command` 把状态更新与动态下一跳内聚到节点返回值，`Send` 动态创建任务实例，`defer=True` 适合收尾任务。依据官方 Graph API、Workflows and Agents 文档重绘。


### `add_edge`

```python
builder.add_edge(START, "parse")
builder.add_edge("parse", "execute")
builder.add_edge("execute", END)
```

### `add_sequence`

```python
builder.add_edge(START, "parse")
builder.add_sequence([parse, execute, summarize])
builder.add_edge("summarize", END)
```

`add_sequence` 适合简单线性流程；复杂图仍建议显式命名节点与边，便于稳定演进。

### 30.5.2 静态条件分支

“静态”不是指结果固定，而是指候选下游节点在编译时已经确定。

```python
from typing import Literal


def route(state: State) -> Literal["poem", "joke"]:
    return "poem" if state["task_type"] == "poem" else "joke"


builder.add_conditional_edges(
    "classify",
    route,
    path_map={
        "poem": "write_poem",
        "joke": "write_joke",
    },
)
```

推荐路由函数返回业务标签，再由 `path_map` 映射到工程节点名，这样模型语义与拓扑命名不会过度耦合。

### 30.5.3 固定并行与 Fan-in

```mermaid
flowchart LR
    START([START]) --> A[生成诗]
    START --> B[生成笑话]
    START --> C[生成摘要]
    A --> J[汇总]
    B --> J
    C --> J
    J --> END([END])
```

等待所有上游完成一次再汇聚：

```python
builder.add_edge(["poem", "joke", "summary"], "aggregate")
```

分别独立触发下游：

```python
builder.add_edge("poem", "aggregate")
builder.add_edge("joke", "aggregate")
```

这两种写法不等价。前者类似“与”，后者类似“或”，后者可能让 `aggregate` 执行多次。

### 30.5.4 动态扇出：`Send`

![Send 动态 Fan-out 与 Reducer Fan-in](../assets/appendix30/06-dynamic-fanout-fanin.svg)

> **图 5-2　运行时动态 Fan-out / Fan-in。** Router 根据输入数据返回多个 `Send`，运行时为同一 Worker 节点创建多个任务实例；各任务产物经 State Reducer 合并，再由 Reduce/Synthesize 节点统一处理。


当任务数量在运行时才知道，例如“对输入列表中的每个文档分别分析”，使用 `Send`：

```python
from operator import add
from typing import Annotated, TypedDict
from collections.abc import Sequence
from langgraph.types import Send


class OverallState(TypedDict):
    documents: list[str]
    summaries: Annotated[list[str], add]
    final_summary: str


class WorkerState(TypedDict):
    document: str


def assign_workers(state: OverallState) -> Sequence[Send]:
    return [
        Send("summarize_one", {"document": document})
        for document in state["documents"]
    ]


def summarize_one(state: WorkerState) -> dict:
    return {"summaries": [state["document"][:50]]}


def aggregate(state: OverallState) -> dict:
    return {"final_summary": "\n".join(state["summaries"])}
```

```python
builder.add_conditional_edges(
    START,
    assign_workers,
    path_map=["summarize_one"],
)
builder.add_edge("summarize_one", "aggregate")
```

`Send` 的动态性体现在：

- 运行时决定任务数量；
- 每个任务可以传入独立私有状态；
- 多个任务可执行同一个节点定义；
- 节点本身通常仍需在编译前注册。

### 30.5.5 `Command`

`Command` 可同时表达状态更新与控制流：

```python
from typing import Literal
from langgraph.types import Command


def router_node(state: State) -> Command[Literal["success", "fallback"]]:
    if state["score"] >= 0.8:
        return Command(
            update={"decision": "accepted"},
            goto="success",
        )
    return Command(
        update={"decision": "fallback"},
        goto="fallback",
    )
```

常用字段：

| 字段 | 作用 |
|---|---|
| `update` | 更新状态 |
| `goto` | 指定下一节点或多个目标 |
| `resume` | 恢复中断 |
| `graph` | 指定在当前图还是父图中跳转 |

使用 `Command(goto=...)` 的节点一般不要再配置普通下游边，否则固定边和动态跳转可能同时生效。

### 30.5.6 `defer=True`

收尾节点希望在所有常规任务完成后执行：

```python
builder.add_node("audit", audit_node, defer=True)
```

典型用途：

- 审计日志；
- 统一校验；
- 结果汇总；
- 临时资源清理；
- 统计与计费记录。

附件从 Channel 机制解释了它：延迟节点使用特殊触发通道，常规流程结束后通道才变为可用，并在额外超步中触发。

### 30.5.7 MapReduce

```mermaid
flowchart LR
    I[输入列表] --> R[Router]
    R --> M1[Mapper #1]
    R --> M2[Mapper #2]
    R --> M3[Mapper #N]
    M1 --> REDUCE[Reducer / Synthesizer]
    M2 --> REDUCE
    M3 --> REDUCE
    REDUCE --> O[最终结果]
```

LangGraph 中典型映射：

- Router：生成 `Send` 列表；
- Mapper：处理单个任务；
- State Reducer：合并中间状态；
- Reduce Node：做业务归约与最终合成。

不要混淆“State Reducer”和“名为 reducer 的业务节点”：前者负责字段更新合并，后者负责业务聚合。

### 30.5.8 ReAct 循环

```mermaid
flowchart LR
    LLM[LLM 推理] --> D{有工具调用?}
    D -->|是| TOOL[执行工具]
    TOOL --> LLM
    D -->|否| END([最终答案])
```

可用两种方式实现：

1. `add_conditional_edges()`：路由逻辑放在独立函数中；
2. `Command(goto=...)`：路由逻辑放在 LLM 节点返回值中。

两者本质区别不是能否循环，而是控制逻辑放在哪里。

### 30.5.9 递归限制与优雅退出

循环必须有业务停止条件和系统兜底限制：

```python
result = graph.invoke(
    input_state,
    config={"recursion_limit": 30},
)
```

达到限制后会抛出 `GraphRecursionError`。

更推荐在图内使用 `RemainingSteps` 主动退出：

```python
from langgraph.managed import RemainingSteps


class LoopState(TypedDict):
    remaining_steps: RemainingSteps
    answer: str


def route(state: LoopState):
    if state["remaining_steps"] < 3:
        return END
    return "continue_loop"
```

生产系统应同时具备：

- 图内主动退出；
- 图外捕获 `GraphRecursionError`；
- Token、工具次数、成本、总耗时等额外预算；
- 循环原因与最终降级结果的可观测记录。

---
## 30.6 节点执行、重试、超时与缓存

### 30.6.1 节点函数的完整形态

普通节点至少接收 `state`，还可以让运行时注入配置、Runtime 与流写入器：

```python
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime


def node(
    state: State,
    config: RunnableConfig,
    runtime: Runtime[AppContext],
) -> dict:
    thread_id = config["configurable"]["thread_id"]
    current_step = config["metadata"]["langgraph_step"]
    context = runtime.context
    store = runtime.store
    return {"last_step": current_step, "thread_id": thread_id}
```

概念对应关系：

| 参数 | 典型内容 | 是否适合持久化 |
|---|---|---|
| `state` | 中间结果、业务状态、消息 | 是，由 Checkpointer 管理 |
| `config` | `thread_id`、递归限制、元数据 | 配置本身参与运行，但不等同于业务状态 |
| `runtime.context` | 当前用户、请求来源、权限、模型配置 | 否，仅当前调用 |
| `runtime.store` | 长期记忆 Store | Store 自身负责持久化 |
| `stream_writer` | 自定义进度事件 | 否，发送到流消费者 |

从运行时角度，一个节点不只是“调用函数”，而是由业务逻辑、状态写入器、控制流写入器和触发通道共同组成。

### 30.6.2 重试机制

![节点容错组合顺序](../assets/appendix30/07-node-fault-tolerance-pipeline.svg)

> **图 6-1　节点容错组合顺序。** 当前官网将 Timeout、Retry 与 Error Handler 视为可组合机制：节点尝试超时或抛错后先由 RetryPolicy 判定，重试耗尽后才进入 Error Handler；未处理的异常继续向外传播。此图对应官网 1.2+ 能力，原附件 1.1.2 环境不一定支持全部 API。


节点添加时配置 `RetryPolicy`：

```python
from requests.exceptions import HTTPError
from langgraph.types import RetryPolicy


builder.add_node(
    "call_remote_api",
    call_remote_api,
    retry_policy=RetryPolicy(
        max_attempts=4,
        initial_interval=0.5,
        backoff_factor=2.0,
        max_interval=8.0,
        jitter=True,
        retry_on=(HTTPError, ConnectionError),
    ),
)
```

### 参数解释

| 参数 | 含义 |
|---|---|
| `max_attempts` | 最大尝试次数，包含第一次执行 |
| `initial_interval` | 第一次失败后的等待时间 |
| `backoff_factor` | 指数退避倍数 |
| `max_interval` | 相邻重试最大间隔 |
| `jitter` | 是否加入随机抖动，避免并发重试风暴 |
| `retry_on` | 异常类型、异常元组或判断函数 |

重试时间大致为：

```text
0.5s -> 1s -> 2s -> 4s -> 8s -> 8s ...
```

### 哪些错误应该重试

适合：

- 网络连接暂时失败；
- HTTP 5xx；
- 限流且响应中明确给出可重试信号；
- 模型服务短暂不可用；
- 数据库发生瞬时连接异常。

不适合：

- 参数校验错误；
- 认证失败；
- 权限不足；
- 语法错误、类型错误；
- 业务规则明确拒绝；
- 具有不可重复副作用且没有幂等键的操作。

更精细的判断：

```python
def should_retry(exc: Exception) -> bool:
    if isinstance(exc, ConnectionError):
        return True
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return status_code is not None and 500 <= status_code < 600
```

### 30.6.3 节点超时（1.2+）

附件把节点级超时标记为 LangGraph 1.2+ 能力，且主要面向异步节点：

```python
from langgraph.types import TimeoutPolicy


builder.add_node(
    "call_model",
    call_model,
    timeout=TimeoutPolicy(run_timeout=60),
)
```

核心考虑：

- 异步任务可以被运行时以超时方式取消等待；
- 同步函数一旦阻塞线程，Python 很难安全地从外部强制终止；
- 对同步 HTTP、数据库和 SDK 调用，应优先使用库自身的连接、读取和总超时。

一个生产节点通常需要三层超时：

```text
单次外部调用超时 < 节点超时 < 整个图运行超时
```

### 30.6.4 错误处理（1.2+）

附件将节点 `error_handler` 描述为“重试耗尽后的兜底处理”：

```python
builder.add_node(
    "call_api",
    call_api,
    retry_policy=RetryPolicy(max_attempts=3),
    error_handler=handle_api_error,
)
```

错误处理节点可以：

- 返回降级状态；
- 切换备用模型或备用服务；
- 通过 `Command(goto=...)` 进入人工处理；
- 记录失败原因并结束当前分支；
- 触发补偿事务。

生产流程不要简单吞掉异常。状态中至少应记录：

```python
class ErrorInfo(TypedDict):
    category: str
    message: str
    retryable: bool
    node_name: str
    attempts: int
    occurred_at: str
```

### 30.6.5 节点缓存

节点缓存需要同时配置策略和后端：

```python
import time
from langgraph.cache.memory import InMemoryCache
from langgraph.types import CachePolicy


def expensive_node(state: State) -> dict:
    time.sleep(3)
    return {"result": state["query"].upper()}


builder.add_node(
    "expensive_node",
    expensive_node,
    cache_policy=CachePolicy(ttl=60),
)

graph = builder.compile(cache=InMemoryCache())
```

只有 `cache_policy` 而没有 `cache=` 后端时，缓存不会真正生效。

### 缓存适用条件

- 相同输入通常产生相同输出；
- 节点成本较高；
- 相同输入会重复出现；
- 输入能够稳定序列化并生成缓存键。

### 自定义缓存键

```python
import hashlib
import json


def cache_key(*args, **kwargs) -> str:
    payload = json.dumps(
        {"args": args, "kwargs": kwargs},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


policy = CachePolicy(key_func=cache_key, ttl=300)
```

缓存键应考虑：

- 模型名称与版本；
- Prompt 版本；
- 工具或知识库版本；
- 租户与权限范围；
- 语言、时区等上下文；
- 外部数据时间窗口。

不要让不同租户、不同权限用户共享未隔离的缓存结果。

### 30.6.6 幂等性与副作用

重试、恢复、Replay、中断恢复都可能让节点重新执行。节点设计应区分：

### 纯计算节点

只依赖输入并返回结果，天然容易重试和回放。

### 读操作节点

读取数据库或 API，通常可重试，但要考虑数据随时间变化带来的结果漂移。

### 副作用节点

发送邮件、扣款、创建工单、修改文件、发布内容等必须使用幂等键：

```python
def create_order(state: State, config: RunnableConfig) -> dict:
    idempotency_key = (
        f"{config['configurable']['thread_id']}:create_order:{state['request_id']}"
    )
    order = order_service.create(
        payload=state["order_payload"],
        idempotency_key=idempotency_key,
    )
    return {"order_id": order.id}
```

不要把“节点只执行一次”当作系统保证。可靠性来自业务幂等，而不是侥幸依赖执行路径。

### 30.6.7 生产容错策略矩阵

| 节点类型 | 重试 | 超时 | 缓存 | HITL | 幂等 |
|---|---:|---:|---:|---:|---:|
| LLM 生成 | 是 | 是 | 按业务 | 可选 | 建议 |
| 只读检索 | 是 | 是 | 适合 | 否 | 建议 |
| 数据库写入 | 谨慎 | 是 | 通常否 | 高风险操作可加 | 必须 |
| 发邮件/发布 | 谨慎 | 是 | 否 | 建议 | 必须 |
| 本地纯计算 | 按异常 | 可选 | 适合 | 否 | 天然 |
| 人工审批 | 不应自动重试人类决定 | 可设等待期限 | 否 | 核心 | 需要恢复语义 |

---

## 30.7 持久化与 Durable Execution

### 30.7.1 Persistence 与 Durable Execution

二者的关系：

```mermaid
flowchart LR
    EXEC[图执行] --> CP[Persistence<br/>保存 Checkpoint]
    CP --> RESUME[Durable Execution<br/>利用检查点继续运行]
    CP --> HISTORY[历史查看]
    CP --> REPLAY[Replay]
    CP --> FORK[Fork]
```

- Persistence 负责保存执行状态；
- Durable Execution 负责利用这些状态恢复、继续、回放或分叉。

它解决的不是“变量是否写入数据库”这么简单，而是系统能否记住：

- 当前运行到了哪里；
- 当前状态是什么；
- 下一步有哪些任务；
- 哪些并行任务已经成功；
- 是否存在中断或失败；
- 应从哪个位置继续。

### 30.7.2 核心组件

![Checkpointer 与 Store 的职责边界](../assets/appendix30/08-checkpointer-vs-store.svg)

> **图 7-1　Checkpointer 与 Store。** Checkpointer 按 `thread_id` 保存图状态快照，用于短期记忆、HITL、Time Travel 与故障恢复；Store 保存应用自定义的跨线程长期数据。依据官方 Persistence 文档重绘。


| 概念 | 作用 |
|---|---|
| State | 开发者定义的共享状态 |
| Channel | 底层状态传递与触发通道 |
| Checkpoint | 超步边界上的状态快照 |
| CheckpointMetadata | 步骤、父检查点、来源等元数据 |
| Checkpointer | 检查点存取实现 |
| thread | 一条逻辑执行线或会话 |
| `thread_id` | 会话唯一标识 |
| `checkpoint_ns` | 根图与子图的检查点命名空间 |
| `checkpoint_id` | 某个具体检查点的 ID |
| StateSnapshot | 开发者查看检查点时使用的视图 |

```mermaid
flowchart TB
    T[thread_id]
    T --> C1[Checkpoint step -1]
    C1 --> C2[Checkpoint step 0]
    C2 --> C3[Checkpoint step 1]
    C3 --> C4[Checkpoint step N]
    C4 --> S[StateSnapshot]
```

### 30.7.3 启用检查点

两步缺一不可：

1. 编译图时传入 Checkpointer；
2. 调用图时传入 `thread_id`。

```python
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)

config = {
    "configurable": {
        "thread_id": "conversation-001",
    }
}

result = graph.invoke(input_state, config=config)
```

### 30.7.4 Checkpointer 选型

| 实现 | 后端 | 适用场景 |
|---|---|---|
| `InMemorySaver` | 进程内存 | 学习、单元测试、Notebook |
| `SqliteSaver` | SQLite | 单机轻量持久化 |
| `PostgresSaver` | PostgreSQL | 生产、跨进程、跨服务 |
| `MongoDBSaver` | MongoDB | 文档型存储偏好 |
| `RedisSaver` | Redis | 低延迟、高吞吐场景 |

`InMemorySaver` 对象被重新创建后，历史就丢失；数据库后端则与 Python 进程生命周期解耦。

### 30.7.5 PostgreSQL 示例

开发环境可用 Docker：

```bash
docker run -d \
  --name langgraph-postgres \
  -e POSTGRES_DB=langgraph_db \
  -e POSTGRES_USER=langgraph_user \
  -e POSTGRES_PASSWORD=change-me \
  -p 5432:5432 \
  postgres:16
```

Python：

```python
from langgraph.checkpoint.postgres import PostgresSaver

DB_URL = (
    "postgresql://langgraph_user:change-me@localhost:5432/"
    "langgraph_db?sslmode=disable"
)

with PostgresSaver.from_conn_string(DB_URL) as checkpointer:
    # 首次初始化时执行；生产环境应纳入独立迁移流程
    checkpointer.setup()
    graph = builder.compile(checkpointer=checkpointer)
    result = graph.invoke(
        input_state,
        config={"configurable": {"thread_id": "thread-001"}},
    )
```

生产环境必须：

- 使用强密码和密钥管理；
- 启用 TLS/SSL；
- 配置连接池与超时；
- 将 `setup()` 或数据库建表纳入受控迁移；
- 设计检查点保留和清理策略；
- 以租户、应用和环境隔离 `thread_id`。

### 30.7.6 持久化模式

附件介绍了三种 `durability`：

| 模式 | 写入时机 | 响应延迟 | 容灾能力 |
|---|---|---:|---:|
| `exit` | 正常结束、异常退出或中断时保存 | 最低 | 最弱，无法覆盖中途进程崩溃 |
| `async` | 超步后后台异步写入 | 较低 | 较强，常作为默认权衡 |
| `sync` | 进入下一超步前等待主检查点落盘 | 最高 | 最强 |

```python
result = graph.invoke(
    input_state,
    config=config,
    durability="async",  # exit / async / sync
)
```

选择原则：

- 低价值、可重算任务：`exit`；
- 一般在线 Agent：`async`；
- 高价值、不可轻易重复的长任务：`sync`。

### 30.7.7 查看当前状态与历史

```python
latest: object = graph.get_state(config)
history: list[object] = list(graph.get_state_history(config))
```

`StateSnapshot` 常见字段：

| 字段 | 含义 |
|---|---|
| `values` | 当前检查点状态值 |
| `next` | 从该检查点继续时将运行的节点 |
| `config` | 包含 thread、namespace、checkpoint ID |
| `metadata` | source、step、parents 等 |
| `created_at` | 创建时间 |
| `parent_config` | 上一个检查点配置 |
| `tasks` | 下一超步任务及可能已有的 result/error |
| `interrupts` | 当前中断信息 |

历史通常按新到旧返回。常见步骤：

```text
step = -1：输入检查点，下一步为 __start__
step = 0 ：输入已写入状态，下一步为首批业务节点
step = 1 ：第一批业务节点结果已提交
...
最终检查点：next=()，没有待执行节点
```

### 30.7.8 失败恢复

![Durable Execution、Replay 与 Fork](../assets/appendix30/09-durable-execution-time-travel.svg)

> **图 7-2　Durable Execution 与 Time Travel。** 恢复运行通常从同一线程的最新检查点继续；Replay 显式选择历史 `checkpoint_id` 并重跑后续步骤；Fork 先通过 `update_state()` 生成新检查点分支，再沿新状态继续执行。依据官方 Persistence 与 Time Travel 文档重绘。


恢复最新失败进度时：

```python
result = graph.invoke(
    None,
    config={"configurable": {"thread_id": "thread-001"}},
)
```

要求：

- 仍使用原检查点后端；
- 输入传 `None`；
- 配置包含 `thread_id`，但不指定历史 `checkpoint_id`；
- 修复故障后重新编译的图应与已有状态和节点语义兼容。

### 并行任务的 `pending_writes`

如果同一超步有两个并行节点：

- A 成功；
- B 失败；

LangGraph 可以把 A 的成功结果保存在任务记录中。恢复时只重新运行 B，避免重复执行 A。这是 Durable Execution 的核心价值之一。

### 30.7.9 Replay：检查点重放

Replay 从某个明确的历史检查点重新执行后续节点：

```python
history = list(graph.get_state_history(config))
target = next(snapshot for snapshot in history if snapshot.next == ("worker",))

result = graph.invoke(None, config=target.config)
```

语义：

- 检查点之前不重跑；
- 检查点之后重新执行；
- LLM、API、工具调用会再次发生；
- 结果不保证与原运行相同。

### 30.7.10 Fork：从历史分叉

Fork 先用 `update_state()` 基于历史检查点创建新分支：

```python
fork_config = graph.update_state(
    config=target.config,
    values={"user_input": "把任务改成生成笑话"},
    as_node=START,
)

result = graph.invoke(None, config=fork_config)
```

`update_state()`：

- 不修改原历史；
- 返回新检查点的配置；
- `values` 是局部状态更新，不是完整状态替换；
- `as_node` 表示把这次更新视为哪个节点的输出；
- 后续从该节点的后继逻辑继续。

若 `as_node="router_node"`，相当于伪造路由节点输出，节点函数本身不会执行，但它关联的写入器与条件路由可能被执行。这非常适合测试不同分支，但正式业务中必须谨慎使用。

### 30.7.11 失败恢复、Replay 与 Fork 对比

| 能力 | 起点 | 是否改状态 | 典型用途 |
|---|---|---:|---|
| 失败恢复 | 最新检查点 | 否 | 修复故障后继续 |
| Replay | 指定历史检查点 | 否 | 重放后续步骤、重新采样 |
| Fork | 指定历史检查点产生的新检查点 | 是 | 探索另一条路径、人工修正 |

### 30.7.12 状态兼容与版本迁移

部署新版本图时，已有检查点可能仍在。应为状态设计版本字段：

```python
class State(TypedDict):
    schema_version: int
    user_input: str
    result: str
```

恢复入口先执行迁移：

```python
def migrate_state(state: State) -> dict:
    version = state.get("schema_version", 1)
    if version == 1:
        return {
            "schema_version": 2,
            "result": state.get("result", ""),
        }
    return {}
```

节点重命名、删除状态字段、改变 Reducer、改变路由候选节点，都可能破坏历史恢复。生产发布必须包含检查点兼容测试。

---

## 30.8 短期记忆、长期记忆与运行时上下文

### 30.8.1 三类信息的边界

![State、Checkpointer、Store 与 Runtime Context](../assets/appendix30/10-memory-and-runtime-context.svg)

> **图 8-1　四类数据载体的生命周期。** State 服务于当前执行，Checkpointer 将线程状态延展到同一 `thread_id` 的后续调用，Store 保存跨线程长期信息，Runtime Context 只承载本次调用的租户、权限和请求元数据。依据官方 Persistence、Memory 与 Runtime 文档重绘。


```mermaid
flowchart TB
    CALL[一次图调用]
    CALL --> CTX[Runtime Context<br/>本次调用临时信息]
    CALL --> STATE[State + Checkpointer<br/>线程内短期记忆]
    CALL --> STORE[Store<br/>跨线程长期记忆]

    STATE -->|thread_id| CONV[同一会话历史]
    STORE -->|namespace + key| PROFILE[用户偏好/知识/经验]
```

| 机制 | 生命周期 | 访问方式 | 适合内容 |
|---|---|---|---|
| State | 当前执行与同一 thread | `state[...]` | 中间结果、消息、流程状态 |
| Checkpointer | 同一 `thread_id` 跨调用 | 图自动加载 | 短期记忆、恢复进度 |
| Store | 跨 thread、跨会话 | `runtime.store` | 用户偏好、长期事实、经验 |
| Runtime Context | 仅本次调用 | `runtime.context` | 当前用户、权限、请求来源、Trace ID |

### 30.8.2 短期记忆

只要图启用了 Checkpointer，并复用同一个 `thread_id`，后续调用就能读取该线程的历史状态：

```python
config = {"configurable": {"thread_id": "alice-chat"}}

graph.invoke(
    {"messages": [HumanMessage(content="我叫 Alice")]},
    config=config,
)

result = graph.invoke(
    {"messages": [HumanMessage(content="我叫什么？")]},
    config=config,
)
```

注意：短期记忆不等同于无限保留完整消息。生产对话应设计：

- Token 预算；
- 消息裁剪；
- 摘要压缩；
- 重要事实提取；
- 原始消息归档；
- 恢复时的完整性校验。

### 30.8.3 长期记忆 Store

长期记忆在编译时通过 `store=` 传入：

```python
from langgraph.store.postgres import PostgresStore

with PostgresStore.from_conn_string(DB_URL) as store:
    store.setup()
    graph = builder.compile(checkpointer=checkpointer, store=store)
```

数据通常以三部分组织：

```text
namespace：层级命名空间，必须是元组
key：同一命名空间中的记录键
value：JSON 风格的业务数据
```

```python
USERS_NS = ("users",)
namespace = (*USERS_NS, "Alice")

store.put(
    namespace,
    "preferences",
    {
        "language": "zh-CN",
        "food": "奶皮子酸奶",
        "sports": "跑步",
    },
)

item = store.get(namespace, "preferences")
print(item.value if item else None)
```

查询某类用户记忆：

```python
for item in store.search(("users",)):
    print(item.namespace, item.key, item.value)
```

若需要语义检索，需要额外配置索引与 embedding；未配置时主要使用命名空间和过滤条件进行精确检索。

### 30.8.4 在节点中访问 Store

```python
from langgraph.runtime import Runtime


def load_preferences(state: State, runtime: Runtime) -> dict:
    username = state["username"]
    item = runtime.store.get(("users", username), "preferences")
    return {"preferences": item.value if item else {}}
```

一种常见优化是“长期记忆读入短期状态”：

```mermaid
flowchart LR
    START([START]) --> CHECK{State 已有偏好?}
    CHECK -->|否| STORE[从 Store 读取]
    STORE --> CACHE[写入 State]
    CHECK -->|是| LLM[直接调用模型]
    CACHE --> LLM
    LLM --> END([END])
```

第一次调用读 Store，后续同一 thread 复用 State，减少数据库访问。

### 30.8.5 Runtime Context

定义上下文类型：

```python
from dataclasses import dataclass


@dataclass
class UserContext:
    user_id: str
    tenant_id: str
    membership_level: str
    trace_id: str
```

创建图：

```python
builder = StateGraph(
    state_schema=State,
    context_schema=UserContext,
)
```

节点访问：

```python
from langgraph.runtime import Runtime


def llm_node(state: State, runtime: Runtime[UserContext]) -> dict:
    context = runtime.context
    return {
        "audit_user": context.user_id if context else "anonymous",
    }
```

调用：

```python
result = graph.invoke(
    input_state,
    config={"configurable": {"thread_id": "thread-001"}},
    context=UserContext(
        user_id="u-1",
        tenant_id="tenant-a",
        membership_level="VIP",
        trace_id="trace-001",
    ),
)
```

即使下一次复用相同 `thread_id`，没有再次传 `context`，也不会自动继承上一次 Runtime Context。

### 30.8.6 记忆写入策略

不要让模型无条件把所有对话写入长期记忆。建议经过五个步骤：

```mermaid
flowchart LR
    E[候选信息提取] --> C[分类]
    C --> V[验证与去重]
    V --> P[隐私和权限检查]
    P --> W[写入 Store]
```

可分为：

- **显式事实**：用户明确说“我偏好中文”；
- **稳定偏好**：多次出现并可撤销；
- **任务经验**：某工具的正确调用方式；
- **临时信息**：不进入长期记忆；
- **敏感信息**：默认不写入或需要明确授权。

每条记忆最好包含：

```python
{
    "value": "用户偏好中文回答",
    "source": "user_explicit",
    "confidence": 1.0,
    "created_at": "...",
    "expires_at": None,
    "privacy": "private",
    "version": 1,
}
```

### 30.8.7 多租户隔离

推荐命名空间：

```text
("tenants", tenant_id, "users", user_id, "preferences")
("tenants", tenant_id, "agents", agent_id, "skills")
("tenants", tenant_id, "projects", project_id, "facts")
```

不要只用 `("users", user_id)` 并假设不同租户的 user_id 一定不冲突。

---

## 30.9 中断与 Human-in-the-loop

### 30.9.1 动态中断与静态断点

| 类型 | 配置方式 | 目的 |
|---|---|---|
| 动态中断 | 节点内调用 `interrupt()` | 人工审批、编辑、补充信息，属于业务流程 |
| 静态断点 | `interrupt_before` / `interrupt_after` | 调试运行图，不属于业务逻辑 |

中断依赖可恢复执行，因此必须配置 Checkpointer 和 `thread_id`。

### 30.9.2 基础 HITL

![interrupt 与 resume 生命周期](../assets/appendix30/11-hitl-interrupt-resume.svg)

> **图 9-1　Human-in-the-loop 中断与恢复。** `interrupt()` 暂停图并通过 Checkpointer 保存位置；外部 UI 收集人工决定后，以同一 `thread_id` 调用 `Command(resume=...)`。节点从开头重新执行，`interrupt()` 返回恢复值，因此中断前副作用必须幂等。依据官方 Interrupts 文档重绘。


```python
from typing import TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    username: str


def ask_name(state: State) -> dict:
    username = interrupt(
        {
            "type": "request_input",
            "message": "请输入您的姓名",
        }
    )
    return {"username": username}


builder = StateGraph(state_schema=State)
builder.add_node("ask_name", ask_name)
builder.add_edge(START, "ask_name")
builder.add_edge("ask_name", END)

graph = builder.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "hitl-001"}}

interrupted = graph.invoke({}, config=config)
print(interrupted["__interrupt__"])

resumed = graph.invoke(
    Command(resume="小黄"),
    config=config,
)
print(resumed)
```

```mermaid
sequenceDiagram
    participant Client
    participant Graph
    participant Checkpointer
    participant Human

    Client->>Graph: invoke(input, thread_id)
    Graph->>Checkpointer: 保存中断检查点
    Graph-->>Client: __interrupt__
    Client->>Human: 展示审批/输入界面
    Human-->>Client: 提交恢复值
    Client->>Graph: invoke(Command(resume=value), same thread_id)
    Graph->>Checkpointer: 读取检查点
    Graph-->>Client: 继续执行并返回结果
```

### 30.9.3 常见 HITL 模式

### 基础输入

暂停后要求用户补充姓名、参数、业务资料。

### 多个并行中断

多个并行任务各自产生中断，需要按中断 ID 将恢复值对应到正确任务。调用方不能仅依赖数组顺序。

### 审批模式

```python
approval = interrupt(
    {
        "type": "approval",
        "action": "publish_report",
        "summary": state["report_summary"],
    }
)

if approval["decision"] == "approve":
    return Command(goto="publish")
return Command(goto="cancel")
```

### 审核与编辑

模型先生成草稿，人类返回修改后的文本，后续节点使用人工版本继续。

### 工具执行审批

在发送邮件、删除文件、执行 SQL、发布内容之前中断；人类可以批准、拒绝或修改参数。

### 单节点串行中断

一个节点中依次询问多个问题。每次恢复会重新进入整个节点函数，因此 `interrupt()` 调用顺序必须稳定。

### 输入验证

用户输入不合法时再次中断：

```python
while True:
    age = interrupt("请输入年龄")
    if isinstance(age, int) and 0 <= age <= 150:
        return {"age": age}
```

要注意，每次恢复会重放节点中断前的代码，循环前的副作用必须可重复。

### 30.9.4 四条重要规范

### 不要捕获 `interrupt()` 内部异常

中断通过特殊异常通知运行时。如果用宽泛的 `try/except Exception` 包住它，运行时可能无法识别中断。

错误：

```python
try:
    value = interrupt("请输入")
except Exception:
    value = "default"
```

### 不要改变同一节点内中断顺序

历史恢复值会按调用位置对应。部署新版本时插入、删除或交换中断，可能让旧线程恢复到错误位置。

### 中断参数应可 JSON 序列化

传递字符串、数字、布尔、列表和字典，不要传函数、连接对象、模型实例等复杂对象。

### 中断前副作用必须幂等

```python
def approval_node(state: State) -> dict:
    # 这段在恢复时可能再次运行
    audit_log.write_once(
        key=f"{state['request_id']}:approval_requested",
        payload={"request_id": state["request_id"]},
    )
    decision = interrupt("是否批准？")
    return {"decision": decision}
```

### 30.9.5 静态断点

编译时设置：

```python
graph = builder.compile(
    checkpointer=checkpointer,
    interrupt_before=["dangerous_node"],
    interrupt_after=["planner"],
)
```

调用时设置：

```python
result = graph.invoke(
    input_state,
    config=config,
    interrupt_before=["dangerous_node"],
)
```

调用时断点只对该次调用有效；恢复时需要继续传入一致的断点配置，否则行为可能与预期不同。

静态断点更适合：

- 查看每个超步边界的状态；
- 调试路由结果；
- 检查并行汇聚；
- 验证工具节点前后的消息；
- Studio 中逐步执行。

### 30.9.6 中断数据契约

前端不要直接展示任意 Python 对象，建议定义稳定的中断协议：

```python
class InterruptPayload(TypedDict):
    schema_version: int
    type: str
    title: str
    description: str
    action_id: str
    fields: list[dict]
    allowed_decisions: list[str]
    expires_at: str | None
```

恢复协议：

```python
class ResumePayload(TypedDict):
    action_id: str
    decision: str
    edited_arguments: dict | None
    comment: str | None
```

这能避免图实现、API 层和前端 UI 强耦合。

### 30.9.7 人工审批的安全边界

- 中断值中不要泄露完整密钥或敏感上下文；
- 恢复前校验操作者权限；
- 使用一次性 action token 防止重复提交；
- 记录谁、何时、以什么理由批准；
- 高风险操作在恢复后再次校验资源版本，避免审批期间资源已变化；
- 对长期等待设置过期和重新审批机制。

---

## 30.10 工具调用与 ReAct Agent

### 30.10.1 工具调用消息闭环
<!-- OFFICIAL_IMAGE:augmented_llm -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/augmented_llm.png" alt="LangChain 官方 Augmented LLM 示意图" width="900" />
</p>

> **官网原图 10-A　Augmented LLM。** 官方图把检索、记忆与工具视为对基础 LLM 的三类关键增强。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents)；原图来自 LangChain 官方文档仓库。在线原图需要网络，离线阅读可继续参考下方本地重绘图。
<!-- /OFFICIAL_IMAGE -->


![ReAct、ToolNode 与 ToolRuntime](../assets/appendix30/12-react-toolnode-runtime.svg)

> **图 10-1　ReAct 工具调用闭环。** Model Node 生成 `tool_calls` 后进入 ToolNode；ToolNode 完成参数解析、并行执行和错误处理，并借助 ToolRuntime 注入状态、上下文、Store、配置与流写入器；结果以匹配 `tool_call_id` 的 ToolMessage 回到模型。


```mermaid
sequenceDiagram
    participant LLM
    participant ToolNode
    participant Tool
    participant State

    LLM->>State: AIMessage(tool_calls=[...])
    State->>ToolNode: 读取最后一条 AIMessage
    ToolNode->>Tool: 按名称和参数执行
    Tool-->>ToolNode: 返回结果
    ToolNode->>State: ToolMessage(tool_call_id=...)
    State->>LLM: 带工具观察结果再次推理
```

工具结果必须和原 `tool_call_id` 匹配，模型才能把 Observation 与对应 Action 关联起来。

### 30.10.2 手动实现工具节点

手写工具节点有助于理解原理：

```python
from langchain_core.messages import ToolMessage

TOOLS_BY_NAME = {
    tool.name: tool
    for tool in tools
}


def tool_node(state: AgentState) -> dict:
    ai_message = state["messages"][-1]
    tool_messages = []

    for call in ai_message.tool_calls:
        tool = TOOLS_BY_NAME.get(call["name"])
        if tool is None:
            tool_messages.append(
                ToolMessage(
                    content=f"未知工具: {call['name']}",
                    tool_call_id=call["id"],
                )
            )
            continue

        try:
            result = tool.invoke(call)
            tool_messages.append(result)
        except Exception as exc:
            tool_messages.append(
                ToolMessage(
                    content=f"工具执行失败: {type(exc).__name__}: {exc}",
                    tool_call_id=call["id"],
                )
            )

    return {"messages": tool_messages}
```

真实项目还需要处理并行、参数校验、运行时注入、Command 传播、超时、重试、权限和审计，因此通常应使用 `ToolNode`。

### 30.10.3 使用 `ToolNode`

```python
from typing import Literal
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气。"""
    return f"{city} 今天天气不错"


tools = [get_weather]
model_with_tools = model.bind_tools(tools)


def model_node(state: MessagesState) -> dict:
    response = model_with_tools.invoke(state["messages"])
    return {"messages": [response]}


def route_after_model(state: MessagesState) -> Literal["tool_node", END]:
    return "tool_node" if state["messages"][-1].tool_calls else END


builder = StateGraph(state_schema=MessagesState)
builder.add_node("model_node", model_node)
builder.add_node("tool_node", ToolNode(tools=tools))
builder.add_edge(START, "model_node")
builder.add_conditional_edges(
    "model_node",
    route_after_model,
    path_map=["tool_node", END],
)
builder.add_edge("tool_node", "model_node")

graph = builder.compile()
result = graph.invoke(
    {"messages": [HumanMessage(content="今天北京天气怎么样？")]}
)
```

### 30.10.4 `ToolRuntime`

附件中 `ToolRuntime` 的主要字段：

| 字段 | 内容 |
|---|---|
| `state` | 当前图状态，即短期记忆 |
| `context` | Runtime Context |
| `config` | RunnableConfig 与运行元数据 |
| `stream_writer` | 自定义流写入器 |
| `tool_call_id` | 当前工具调用 ID |
| `store` | 长期记忆 Store |

规范写法要求工具函数存在名为 `runtime` 且标注为 `ToolRuntime` 的参数：

```python
from langgraph.prebuilt.tool_node import ToolRuntime


@tool
def user_profile(runtime: ToolRuntime) -> dict:
    """读取当前用户档案。"""
    user_id = runtime.context.user_id
    item = runtime.store.get(("users", user_id), "profile")
    return item.value if item else {}
```

`ToolRuntime` 与节点使用的 `langgraph.runtime.Runtime` 不是同一个类型。前者额外包含工具调用专属信息。

### 30.10.5 工具更新图状态

工具可以返回 `Command(update=...)`：

```python
from langchain_core.messages import ToolMessage
from langgraph.types import Command


@tool
def get_weather(city: str, runtime: ToolRuntime) -> Command:
    """查询天气并写入状态。"""
    result = f"{city} 今天天气不错"
    tool_message = ToolMessage(
        content=result,
        tool_call_id=runtime.tool_call_id,
    )
    return Command(
        update={
            "weather_result": result,
            "messages": [tool_message],
        }
    )
```

当工具返回普通值时，`ToolNode` 会自动包装成 `ToolMessage`。当工具自行返回 `Command` 时，开发者必须确保更新中包含与原调用匹配的 `ToolMessage`。

### 30.10.6 `wrap_tool_call`

附件介绍 `ToolNode` 的同步包装器 `wrap_tool_call`，异步版本为 `awrap_tool_call`。包装器接收：

- 当前工具请求 `request`；
- 真正执行工具的回调 `execute`。

它可以：

- 重试；
- 缓存；
- 修改参数；
- 短路返回；
- 统一错误转 `ToolMessage`；
- 返回 `Command` 改变控制流。

概念示例：

```python
import time
from langchain_core.messages import ToolMessage


def wrap_tool_call(request, execute):
    max_attempts = request.runtime.context.max_attempts

    for attempt in range(1, max_attempts + 1):
        try:
            return execute(request)
        except ConnectionError as exc:
            if attempt == max_attempts:
                return ToolMessage(
                    content=f"工具重试耗尽: {exc}",
                    tool_call_id=request.runtime.tool_call_id,
                )
            time.sleep(min(2 ** (attempt - 1), 8))
```

具体构造参数应以项目锁定版本为准。

### 30.10.7 工具审批

高风险工具不要让模型直接执行：

```mermaid
flowchart LR
    LLM[LLM 生成 tool_call] --> CLASSIFY{风险等级}
    CLASSIFY -->|低风险| TOOL[ToolNode]
    CLASSIFY -->|高风险| HITL[interrupt 人工审批]
    HITL -->|批准/改参| TOOL
    HITL -->|拒绝| LLM
    TOOL --> LLM
```

审批时允许三种决定：

- `approve`：原参数执行；
- `edit`：使用人工修改后的参数；
- `reject`：生成对应 ToolMessage，告诉模型该操作被拒绝。

### 30.10.8 工具注册表

平台级系统不应在节点中写大量 `if tool_name == ...`。使用 Provider 无关注册表：

```python
from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    capability: str
    risk_level: str
    idempotent: bool
    timeout_seconds: float
    handler: Callable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> None:
        if descriptor.name in self._tools:
            raise ValueError(f"duplicate tool: {descriptor.name}")
        self._tools[descriptor.name] = descriptor

    def get(self, name: str) -> ToolDescriptor:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"unknown tool: {name}") from exc
```

注册表承担能力声明、风险分级、权限检查、超时和审计，不让应用层按模型供应商或工具来源编写分支逻辑。

### 30.10.9 ReAct 的停止条件

不能只依赖模型“自觉停止”。至少设置：

```python
class BudgetState(MessagesState):
    tool_calls_used: int
    max_tool_calls: int
    final_answer: str
```

在路由中判断：

- 无 `tool_calls`：结束；
- 超过工具次数：进入降级回答；
- 剩余超步不足：进入总结节点；
- 相同工具和相同参数重复调用：进入循环检测；
- 用户取消：进入取消清理节点。

---
## 30.11 流式执行

### 30.11.1 为什么需要流式执行

`invoke()` 等待整个图完成后返回最终结果；流式执行则在任务尚未完成时持续交付：

- 当前完整状态；
- 节点增量更新；
- LLM 消息 Token；
- Checkpoint 事件；
- 任务开始、完成、异常和中断；
- 自定义进度事件；
- 调试数据。

```mermaid
sequenceDiagram
    participant Client
    participant Graph
    participant NodeA
    participant NodeB

    Client->>Graph: stream(input)
    Graph-->>Client: 初始状态
    Graph->>NodeA: 执行
    NodeA-->>Graph: 状态更新
    Graph-->>Client: updates / values
    Graph->>NodeB: 调用模型
    NodeB-->>Graph: token chunks
    Graph-->>Client: messages
    Graph-->>Client: 最终状态
```

### 30.11.2 API 关系

| API | 同步/异步 | 用途 |
|---|---|---|
| `stream()` | 同步 | 迭代图业务数据和运行时数据 |
| `astream()` | 异步 | `stream()` 的异步版本 |
| `astream_events()` | 异步 | Runnable 标准生命周期事件 |
| `invoke()` | 同步 | 内部消费流并聚合为最终值 |
| `ainvoke()` | 异步 | 异步聚合最终值 |

### 30.11.3 输出格式版本

附件说明，从 LangGraph 1.1 起，`stream/astream` 存在两种输出格式思路：

- **v1**：形态随参数变化。单模式直接返回数据；多模式返回 `(mode, data)`；启用子图后还会包含命名空间。
- **v2**：统一封装为包含 `type`、`ns`、`data` 的结构，便于统一解析。

附件的示例主要以 v1 展开，并刻意把单个 `stream_mode` 也写成列表，使输出稳定表现为 `(mode, data)`。

### 30.11.4 七类 `stream_mode`

![Streaming Modes 与前端投影](../assets/appendix30/13-streaming-modes-and-projections.svg)

> **图 11-1　Streaming Modes 与 typed projections。** 图运行时可输出完整状态、增量更新、LLM Token、自定义进度、检查点、任务和调试信息。当前官网建议新应用评估 1.2 的 Event Streaming typed-projection API，以便按 messages、values、subgraphs、output 等投影独立消费。


| 模式 | 输出内容 | 典型用途 |
|---|---|---|
| `values` | 每个超步后的完整状态 | 状态快照、前端整体刷新 |
| `updates` | 每个节点产生的局部更新 | 节点级进度与增量 UI |
| `messages` | LLM 消息增量与元数据 | Token 流式输出 |
| `checkpoints` | 检查点更新事件 | 持久化监控、中断调试 |
| `tasks` | 任务开始与结果事件 | 任务追踪、异常分析 |
| `debug` | Checkpoint 与 Task 的调试封装 | 深度排错 |
| `custom` | 节点或工具主动写出的数据 | 自定义进度和阶段说明 |

### 30.11.5 `values`

```python
for mode, data in graph.stream(
    {"initial_state": "初始状态"},
    stream_mode=["values"],
):
    print(mode, data)
```

输出类似：

```text
values {'initial_state': '初始状态'}
values {'initial_state': '初始状态', 'node_a_output': '...'}
values {'initial_state': '初始状态', 'node_a_output': '...', 'node_b_output': '...'}
```

`values` 易于理解，但状态很大时，每个超步都传完整快照会增加网络和序列化成本。

### 30.11.6 `updates`

```python
for mode, data in graph.stream(
    input_state,
    stream_mode=["updates"],
):
    print(mode, data)
```

输出以节点名组织：

```python
("updates", {"node_a": {"node_a_output": "..."}})
("updates", {"node_b": {"node_b_output": "..."}})
```

前端通常更适合消费 `updates`，因为可以把某个节点标为完成，并只更新相关 UI 区域。

### 30.11.7 `messages`

```python
from langchain_core.messages import HumanMessage

for mode, data in graph.stream(
    {"messages": [HumanMessage(content="你好")]},
    stream_mode=["messages"],
):
    message_chunk, metadata = data
    if message_chunk.content:
        print(message_chunk.content, end="", flush=True)
```

元数据通常包含当前节点、步骤、触发通道、模型信息和检查点命名空间，可用于：

- 只展示某个 LLM 节点的 Token；
- 区分规划器与回答器；
- 将多 Agent 的输出路由到不同窗口；
- 统计不同节点的 Token 和延迟。

```python
node_name = metadata.get("langgraph_node")
if node_name == "answer_node":
    render_token(message_chunk.content)
```

### 30.11.8 `checkpoints`

该模式依赖 Checkpointer，用来观察：

- Checkpoint 创建；
- 当前 `values`；
- `next` 节点；
- Checkpoint 配置与元数据；
- 中断前后的状态变化。

适用于可视化执行时间线和恢复问题排查，不建议把完整 Checkpoint 无过滤地直接暴露给普通终端用户。

### 30.11.9 `tasks`

`tasks` 以任务为单位输出事件，通常比 `updates` 更偏运行时观测，可能包含：

- 任务 ID；
- 节点名称；
- 输入；
- 触发通道；
- 结果；
- 异常；
- 中断。

它非常适合构建 Agent APM：

```text
Graph Run
└── Superstep 3
    ├── task: search_web      success  820ms
    ├── task: query_database  failed   2.1s
    └── task: summarize       pending
```

### 30.11.10 `debug`

`debug` 用于深度调试。它通常携带更完整的步骤、时间戳、Checkpoint 和 Task 信息，数据量也最大。

建议：

- 本地调试按需开启；
- 生产环境采样开启；
- 敏感字段先脱敏；
- 不要把 debug 流作为稳定的业务协议。

### 30.11.11 `custom`

节点可以主动写出不进入 State 的进度信息：

```python
from langgraph.config import get_stream_writer


def long_running_node(state: State) -> dict:
    writer = get_stream_writer()
    writer({"stage": "download", "progress": 0.2})
    # ...
    writer({"stage": "parse", "progress": 0.6})
    # ...
    writer({"stage": "complete", "progress": 1.0})
    return {"result": "done"}
```

也可以通过 `runtime.stream_writer` 或工具的 `ToolRuntime.stream_writer` 使用，具体形式以锁定版本为准。

消费：

```python
for mode, data in graph.stream(
    input_state,
    stream_mode=["custom", "updates"],
):
    if mode == "custom":
        render_progress(data)
    elif mode == "updates":
        update_node_status(data)
```

### 30.11.12 多模式统一解析

```python
STREAM_MODES = ["messages", "updates", "custom", "tasks"]

for mode, data in graph.stream(input_state, stream_mode=STREAM_MODES):
    match mode:
        case "messages":
            message, metadata = data
            handle_message(message, metadata)
        case "updates":
            handle_update(data)
        case "custom":
            handle_custom(data)
        case "tasks":
            handle_task(data)
```

对外 API 不应直接把框架原始结构透传给前端。建议转换为稳定事件协议：

```python
class AgentEvent(TypedDict):
    schema_version: int
    event_id: str
    run_id: str
    thread_id: str
    sequence: int
    timestamp: str
    type: str
    node: str | None
    payload: dict
```

### 30.11.13 `astream_events`

`astream_events()` 更关注 Runnable 生命周期、父子调用关系、输入输出与元数据：

```python
async for event in graph.astream_events(
    input_state,
    version="v2",
):
    print(event["event"], event["name"])
```

附件说明：

- v1：Runnable 标准事件，父链信息较弱；
- v2：改进 `parent_ids`，能表达更完整父子关系；
- v3：附件将其标记为 LangGraph 1.2+ 的新协议，基于 Pregel 原始流并提供类型化投影；
- 1.1.2 环境主要使用 v1/v2，默认 v2。

### 30.11.14 流式系统的工程问题

### 背压

前端或网络消费速度低于模型输出速度时，需要：

- 有界队列；
- 批量合并小 Token；
- 慢消费者断开策略；
- 丢弃低价值 debug 事件；
- 保证最终状态事件不丢失。

### 顺序

并行节点事件会交错到达，不能按到达顺序推断业务依赖。使用 `sequence`、`superstep`、`task_id` 和 `node` 组织。

### 断线重连

SSE/WebSocket 断开后，客户端可以基于：

- `thread_id`；
- 最后收到的 `event_id`；
- 最新 Checkpoint；

恢复 UI，而不是重新执行整个任务。

### 取消

取消请求需要向图运行时和外部工具传播。仅关闭前端连接并不等于后端任务已经停止。

### 隐私

消息流、工具参数、Checkpoint 和 debug 元数据都可能包含敏感信息。事件落盘前必须经过脱敏策略。

---

## 30.12 子图与模块化工作流

### 30.12.1 什么是子图

当一个状态图被另一个状态图调用或直接作为父图节点时，前者就是子图。

```mermaid
flowchart LR
    START([START]) --> P1[父图：解析请求]
    P1 --> SG{{子图：检索与生成}}
    SG --> P2[父图：审核与输出]
    P2 --> END([END])
```

子图的价值：

- 模块化；
- 独立测试；
- 复用领域流程；
- 局部持久化；
- 隔离状态和复杂度；
- 多 Agent 角色边界。

命名节点时避免使用 Mermaid 关键字，例如 `subgraph`、`end`、`graph`、`flowchart`、`classDef`，否则拓扑渲染可能失败。

### 30.12.2 两种嵌入方式

### 方式一：在节点函数中调用子图

适合父子状态不同、需要显式转换：

```python
def call_research_subgraph(state: ParentState) -> dict:
    sub_input = {
        "query": state["user_input"],
        "max_sources": 5,
    }
    sub_result = research_graph.invoke(sub_input)
    return {
        "research_summary": sub_result["summary"],
        "sources": sub_result["sources"],
    }
```

优点：状态边界清晰；缺点：父图对内部调用包装负有更多责任。

### 方式二：把编译后的子图直接作为节点

适合父子共享兼容状态字段：

```python
parent_builder.add_node("research", research_graph)
parent_builder.add_edge(START, "research")
parent_builder.add_edge("research", END)
```

优点：拓扑结构更自然，运行时能直接识别子图；缺点：父子状态耦合更强。

### 30.12.3 状态适配层

不要为了复用子图强迫父图使用同一个超大 State。推荐显式 Adapter：

```python
class ParentState(TypedDict):
    user_input: str
    report: str


class ResearchState(TypedDict):
    query: str
    evidence: list[str]
    summary: str


def to_research_state(state: ParentState) -> ResearchState:
    return {"query": state["user_input"], "evidence": [], "summary": ""}


def from_research_state(result: ResearchState) -> dict:
    return {"report": result["summary"]}
```

这类似 Ports & Adapters：子图公开稳定输入输出契约，内部状态可独立演进。

### 30.12.4 查看子图

```python
for namespace, subgraph in parent_graph.get_subgraphs():
    print(namespace, subgraph)
```

父图检查点中，子图任务的 `state` 可能保存子图配置。调用：

```python
snapshot = parent_graph.get_state(
    parent_checkpoint_config,
    subgraphs=True,
)
```

可展开子图的最新快照。

### 30.12.5 子图 Checkpoint 命名空间

根图的 `checkpoint_ns` 通常是空字符串，子图使用非空命名空间。父图和子图检查点之间通过：

- `metadata.parents`；
- `config.configurable.checkpoint_map`；

记录父子关联。

命名空间让同一个 `thread_id` 中不同子图的状态不会混在一起。

### 30.12.6 三种子图持久化策略

![子图三种持久化模式](../assets/appendix30/14-subgraph-persistence-modes.svg)

> **图 12-1　子图持久化模式。** `checkpointer=None` 为默认的 per-invocation 模式，`True` 为跨调用保留状态的 per-thread 模式，`False` 为完全无检查点的 stateless 模式。需要中断、状态检查或 Durable Execution 时，父图仍需配置 Checkpointer。依据官方 Subgraphs 文档重绘。


附件按子图编译时的 `checkpointer` 参数区分：

| 策略 | 编译方式 | 保存检查点 | 中断恢复 | 跨调用多轮记忆 |
|---|---|---:|---:|---:|
| Per-invocation | `compile()` 或 `checkpointer=None` | 是 | 是 | 否 |
| Per-thread | `compile(checkpointer=True)` | 是 | 是 | 是 |
| Stateless | `compile(checkpointer=False)` | 否 | 否 | 否 |

### Per-invocation

每次父图调用子图形成独立调用实例。适合：

- 一次性子任务；
- 需要中断恢复，但不希望下一次调用自动继承上次历史；
- 动态 Worker。

### Per-thread

同一个 thread 下的多次调用复用子图历史。适合：

- 长期对话子 Agent；
- 同一线程中持续积累上下文的领域助手；
- 需要跨父图调用保存子图记忆。

### Stateless

纯计算、无中断、无需历史：

- 文本格式化；
- 确定性校验；
- 轻量数据转换；
- 可安全重复执行的计算。

### 30.12.7 多次调用子图的建议

附件比较了多种结构后，推荐“每个子图使用独立父图节点”：

```mermaid
flowchart LR
    START([START]) --> A[调用检索子图]
    START --> B[调用分析子图]
    A --> J[汇总]
    B --> J
    J --> END([END])
```

相比在一个父节点中循环调用多个子图，这种结构：

- 命名空间更清晰；
- 节点状态更可观测；
- 故障隔离更好；
- 容易为不同子图设置重试和权限；
- 更适合并行执行。

### 30.12.8 子图流式输出

父图流式调用传入：

```python
for chunk in parent_graph.stream(
    input_state,
    stream_mode=["updates", "messages"],
    subgraphs=True,
):
    print(chunk)
```

附件的 v1 结构可概括为：

```text
(namespace, stream_mode, data)
```

这里的 namespace 是流处理命名空间，用于区分父图和各层子图来源，不应与 Checkpoint namespace 混为一谈。

前端可以按命名空间展示：

```text
父图：正在规划
research_agent：正在检索
analysis_agent：正在分析
writer_agent：正在生成报告
```

### 30.12.9 子图动态路由到父图

子图节点可返回：

```python
from langgraph.types import Command


def sub_node(state: SubState) -> Command:
    return Command(
        update={"data": state["data"] + " -> 子图处理完成"},
        goto="parent_router",
        graph=Command.PARENT,
    )
```

要点：

- `graph=Command.PARENT` 表示目标位于父图；
- 子图自身不知道父图节点全集，不宜使用只包含父图节点名的泛型返回注解；
- 添加子图节点时可通过 `destinations` 声明潜在父图目标，帮助可视化渲染；
- `destinations` 主要影响拓扑展示，不是动态跳转本身的执行条件。

### 30.12.10 多 Agent 与子图
<!-- OFFICIAL_IMAGE:supervisor -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/langgraph-supervisor-py/main/static/img/supervisor.png" alt="LangGraph Supervisor 官方仓库架构图" width="950" />
</p>

> **官网原图 12-A　Supervisor 与专业 Agent 的 Handoff。** 图源：[`langgraph-supervisor-py`](https://github.com/langchain-ai/langgraph-supervisor-py)。该仓库当前说明：多数新项目更推荐直接用工具实现 Supervisor 模式，以便更精细地控制 Context Engineering；因此应把这张图理解为层级多 Agent 的结构示意，而不是强制使用该库。
<!-- /OFFICIAL_IMAGE -->


![多 Agent 四种常见模式](../assets/appendix30/15-multi-agent-patterns.svg)

> **图 12-2　多 Agent 模式。** 官方多 Agent 指南将常见方案归纳为 Subagents、Handoffs、Skills 与 Router；这些模式可以组合。Subagents 强调主 Agent 集中控制并把子 Agent 作为工具，Handoffs 转移控制权，Skills 按需加载能力，Router 做一次性分类与派发。


每个专业 Agent 可以封装成子图：

```mermaid
flowchart TB
    ORCH[Orchestrator]
    ORCH --> CODE[Code Agent 子图]
    ORCH --> TEST[Test Agent 子图]
    ORCH --> REVIEW[Review Agent 子图]
    CODE --> ORCH
    TEST --> ORCH
    REVIEW --> ORCH
```

每个子图拥有：

- 独立状态 Schema；
- 独立工具集合；
- 独立系统提示；
- 独立 Checkpoint 策略；
- 独立权限与预算；
- 对父图公开的输入输出契约。

不要默认让所有 Agent 共享完整消息历史。更好的做法是共享结构化任务状态与工件引用，仅在 Handoff 时传递必要上下文。

---

## 30.13 本地部署、LangSmith 与 Agent Chat UI

### 30.13.1 附件中的部署主线

附件演示了三部分：

1. 使用 `langgraph-cli` 启动本地 LangGraph 项目；
2. 对接 LangSmith Studio，查看图、状态、中断和运行链路；
3. 对接 Agent Chat UI，提供消息交互、工具审批和历史会话界面。

这一章属于“如何把 Notebook 代码变成可调试应用”的过渡。

### 30.13.2 项目结构

```text
hitl_demo/
├── .env
├── langgraph.json
└── src/
    └── hitl_demo/
        ├── __init__.py
        ├── agent.py
        └── chat_agent.py
```

### 30.13.3 `langgraph.json`

概念结构：

```json
{
  "dependencies": ["."],
  "graphs": {
    "hitl_agent": "./src/hitl_demo/agent.py:graph",
    "chat_agent": "./src/hitl_demo/chat_agent.py:graph"
  },
  "env": ".env"
}
```

字段和文件解析能力可能随 CLI 版本变化，应以项目安装版本为准。

### 30.13.4 启动本地服务

```bash
langgraph dev
```

启动后可以进行：

- 图调用；
- 中断恢复；
- 静态断点调试；
- 查看 State 与节点执行；
- 对接 Studio；
- 为前端提供本地 Agent API。

### 30.13.5 LangSmith

附件将 LangSmith 描述为面向 LangChain/LangGraph 应用的工程平台，覆盖：

- 运行链路追踪；
- 调试；
- 评估；
- 生产监控；
- Studio 图形化调试。

环境变量示例：

```dotenv
LANGSMITH_API_KEY=lsv2-...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=langgraph-tutorial
```

建议为不同环境使用不同项目：

```text
langgraph-tutorial-local
langgraph-tutorial-test
langgraph-tutorial-prod
```

Trace 元数据至少带上：

- tenant_id；
- user_id 的脱敏形式；
- thread_id；
- graph_version；
- prompt_version；
- model/provider；
- request_id；
- release commit。

### 30.13.6 Studio 调试关注点

在 Studio 中不要只看最终回答，重点观察：

- 图的实际路由是否符合设计；
- 哪个节点耗时最长；
- Tool Call 参数是否正确；
- 中断前状态是否完整；
- 恢复后节点是否被重复执行；
- 并行任务是否正确 Fan-in；
- Checkpoint 的 `next` 与 `tasks`；
- Replay/Fork 后状态变化；
- 消息列表中 AIMessage 与 ToolMessage 是否配对。

### 30.13.7 Agent Chat UI

![前端与 LangGraph 运行时概念映射](../assets/appendix30/17-frontend-runtime-mapping.svg)

> **图 13-2　前端不是只渲染一条最终消息。** 节点、状态键、Token、Checkpoints、Interrupts 与 Subgraphs 都可以映射成可解释的 UI 元素，包括节点卡片、Checkpoint 时间线、审批面板和子图树。依据官方 Frontend Overview 文档重绘。


附件展示的交互能力包括：

- 多轮对话；
- 消息流式展示；
- 工具调用审批；
- 修改工具参数后执行；
- 拒绝工具调用；
- 查看历史线程。

接入前要确保图的 State 含 `messages` 字段，并使用兼容消息合并规则。

### 30.13.8 从本地开发到生产

![从本地开发到生产部署](../assets/appendix30/16-local-to-production-deployment.svg)

> **图 13-1　本地开发到生产部署。** LangGraph CLI 负责本地开发与构建，Studio 连接 Agent Server 做可视化调试；生产环境中的 Agent Server 与后台 Worker 协作，PostgreSQL 保存 threads、runs、checkpoints 与 Store 数据，Redis 用于队列唤醒、取消、流式通信及临时元数据。依据官方 Agent Server、Data Plane 与 Deployment 文档重绘。


`langgraph dev` 适合开发调试，生产部署还需要补齐：

```mermaid
flowchart TB
    LB[API Gateway / Load Balancer]
    AUTH[认证、租户、配额]
    API[Agent API]
    RUNTIME[LangGraph Runtime Workers]
    PG[(PostgreSQL Checkpoint / Store)]
    CACHE[(Cache)]
    TOOL[Tool Gateway / Sandbox]
    OBS[Tracing / Metrics / Logs]

    LB --> AUTH --> API --> RUNTIME
    RUNTIME --> PG
    RUNTIME --> CACHE
    RUNTIME --> TOOL
    RUNTIME --> OBS
```

生产检查项：

- 无状态 API 与有状态 Checkpoint 分离；
- Worker 崩溃后可从数据库恢复；
- `thread_id` 不可由用户随意猜测并越权访问；
- 工具网络访问经过网关、沙箱或权限层；
- 长任务支持取消、超时和幂等；
- SSE/WebSocket 支持断线重连；
- Checkpoint、Store、Trace 具备保留与删除策略；
- 图版本与状态 Schema 可迁移；
- 所有密钥来自 Secret Manager，而不是镜像或代码。

---

## 30.14 六类经典运行图设计模式

附件总结了六种典型模式。选型关键不是“哪种更高级”，而是任务的动态性、可验证性和控制权应该放在哪里。

### 30.14.1 总览
<!-- OFFICIAL_IMAGE:agent_workflow -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/agent_workflow.png" alt="LangChain 官方 Workflow 与 Agent 模式总览" width="1000" />
</p>

> **官网原图 14-A　Workflow 与 Agent 模式总览。** 官方文档以“控制流由预定义代码路径还是由模型动态决定”为主轴，对 Prompt Chaining、Parallelization、Routing、Orchestrator-worker、Evaluator-optimizer 与 Agent 进行对比。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents)。
<!-- /OFFICIAL_IMAGE -->


![六类 Workflow 与 Agent 模式](../assets/appendix30/18-six-workflow-agent-patterns.svg)

> **图 14-1　六类运行图模式的控制权梯度。** 从 Prompt Chaining 到 Agent，确定性逐步降低、运行时动态性逐步提升。模式选择应围绕任务结构是否预先已知、是否需要并行、是否需要迭代评价及模型是否拥有流程控制权。


| 模式 | 图结构 | 动态性 | 核心能力 | 适合场景 |
|---|---|---:|---|---|
| Prompt Chaining | 顺序链与 Gate | 低 | 普通边、条件边 | 可拆分且步骤固定 |
| Parallelization | 固定 Fan-out/Fan-in | 低 | 并行超步、汇聚 | 独立任务并行、集成判断 |
| Routing | 条件分支 | 中 | 结构化输出、条件边 | 请求分类与专用流程 |
| Orchestrator-worker | 动态 Fan-out/Fan-in | 高 | Send、WorkerState、Reducer | 子任务数量运行时决定 |
| Evaluator-optimizer | 反馈循环 | 中 | 循环、反馈状态、限制 | 有明确质量标准的迭代 |
| Agent | 自主决策循环 | 最高 | MessagesState、ToolNode | 步骤和工具顺序未知 |

```mermaid
flowchart LR
    FIXED[步骤是否预先确定?]
    FIXED -->|完全确定| CHAIN[Prompt Chaining]
    FIXED -->|固定任务可并行| PARA[Parallelization]
    FIXED -->|按输入选择固定流程| ROUTE[Routing]
    FIXED -->|子任务数量未知| ORCH[Orchestrator-worker]
    FIXED -->|需多轮质量改进| EVAL[Evaluator-optimizer]
    FIXED -->|连步骤都由模型决定| AGENT[Agent]
```

### 30.14.2 Prompt Chaining
<!-- OFFICIAL_IMAGE:prompt_chain -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/prompt_chain.png" alt="LangChain 官方 Prompt Chaining 模式图" width="900" />
</p>

> **官网原图 14-B　Prompt Chaining。** 多个 LLM 调用按固定顺序串联，并在中间设置 Gate 进行质量或条件校验；失败路径可提前终止或进入修复流程。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents#prompt-chaining)。
<!-- /OFFICIAL_IMAGE -->


将复杂任务拆成可以独立检查的阶段：

```mermaid
flowchart LR
    A[提取需求] --> B[生成草稿]
    B --> G{质量门控}
    G -->|通过| D[最终格式化]
    G -->|不通过| C[改进]
    C --> D
```

适合：

- 翻译 → 校对 → 润色；
- 需求分析 → 生成代码 → 解释；
- 提取 → 分类 → 格式化；
- 草稿 → 合规检查 → 修订。

优点是稳定、可调试；缺点是面对未知任务数量时不灵活。

骨架：

```python
builder.add_edge(START, "draft")
builder.add_conditional_edges(
    "draft",
    quality_gate,
    {"pass": "format", "fail": "improve"},
)
builder.add_edge("improve", "format")
builder.add_edge("format", END)
```

### 30.14.3 Parallelization
<!-- OFFICIAL_IMAGE:parallelization -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/parallelization.png" alt="LangChain 官方 Parallelization 模式图" width="900" />
</p>

> **官网原图 14-C　Parallelization。** 独立的 LLM 子任务同时执行，再由 Aggregator 汇聚结果。LangGraph 中通常映射为同一超步的并行节点，或 `Send` 动态任务加 Reducer。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents#parallelization)。
<!-- /OFFICIAL_IMAGE -->


固定数量独立任务同时执行：

```mermaid
flowchart LR
    START([START]) --> FACT[事实检查]
    START --> STYLE[风格检查]
    START --> POLICY[合规检查]
    FACT --> AGG[综合判定]
    STYLE --> AGG
    POLICY --> AGG
    AGG --> END([END])
```

两种目标：

1. **任务拆分**：提升速度；
2. **多次独立判断**：投票、平均或集成，提升置信度。

固定并行节点写不同字段最简单；写同一字段时需要 Reducer。

### 30.14.4 Routing
<!-- OFFICIAL_IMAGE:routing -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/routing.png" alt="LangChain 官方 Routing 模式图" width="900" />
</p>

> **官网原图 14-D　Routing。** Router 先对输入进行分类，再选择一个或多个专用流程。工程上应优先让路由输出结构化、候选目标可枚举，并设计未知类别的兜底路径。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents#routing)。
<!-- /OFFICIAL_IMAGE -->


先让 LLM 输出受约束的结构化类别，再由纯函数路由：

```python
from typing import Literal
from pydantic import BaseModel, Field


class Route(BaseModel):
    destination: Literal["refund", "price", "recommend"] = Field(
        description="后续业务流程"
    )


router_model = model.with_structured_output(Route)


def classify(state: State) -> dict:
    result = router_model.invoke(state["user_input"])
    return {"destination": result.destination}


def route(state: State):
    return state["destination"]
```

设计原则：

- LLM 负责语义判断；
- 路由函数负责映射；
- 业务节点负责执行；
- 未识别结果必须有兜底；
- 不让模型随意生成任意节点名。

### 30.14.5 Orchestrator-worker
<!-- OFFICIAL_IMAGE:orchestrator_worker -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/worker.png" alt="LangChain 官方 Orchestrator-worker 模式图" width="900" />
</p>

> **官网原图 14-E　Orchestrator-worker。** Orchestrator 在运行时拆解任务，多个 Worker 独立处理子任务，Synthesizer 汇总结果；在 LangGraph 中常由结构化规划、`Send`、私有 WorkerState 与 Reducer 组合实现。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents#orchestrator-worker)。
<!-- /OFFICIAL_IMAGE -->


编排器先动态生成任务列表，再用 `Send` 创建多个 Worker：

```mermaid
flowchart LR
    IN[主题] --> PLAN[Orchestrator 规划章节]
    PLAN --> W1[Worker 1]
    PLAN --> W2[Worker 2]
    PLAN --> WN[Worker N]
    W1 --> SYN[Synthesizer]
    W2 --> SYN
    WN --> SYN
    SYN --> OUT[报告]
```

核心映射：

- Orchestrator：生成 Map 任务；
- Send：动态派发；
- Worker：执行 Map；
- State Reducer：合并中间结果；
- Synthesizer：执行 Reduce。

简化骨架：

```python
from operator import add
from typing import Annotated, TypedDict
from langgraph.types import Send


class State(TypedDict):
    tasks: list[dict]
    completed: Annotated[list[dict], add]
    output: str


def assign(state: State):
    return [Send("worker", task) for task in state["tasks"]]
```

它与固定 Parallelization 的主要区别：任务数量是否在运行时才确定。

### 30.14.6 Evaluator-optimizer
<!-- OFFICIAL_IMAGE:evaluator_optimizer -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/evaluator_optimizer.png" alt="LangChain 官方 Evaluator-optimizer 模式图" width="900" />
</p>

> **官网原图 14-F　Evaluator-optimizer。** Generator 产出候选结果，Evaluator 根据明确标准决定接受或返回反馈；未通过时带着反馈继续迭代。实现时必须设置迭代上限、预算与降级输出。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents#evaluator-optimizer)。
<!-- /OFFICIAL_IMAGE -->


```mermaid
flowchart LR
    G[Generator] --> E[Evaluator]
    E -->|通过| END([END])
    E -->|反馈| G
```

Evaluator 可以是：

- LLM；
- 规则；
- 编译器；
- 单元测试；
- Rubric 评分器；
- 人类审核。

状态示例：

```python
class State(TypedDict):
    draft: str
    score: float
    feedback: str
    iteration: int
    accepted: bool
```

必须设置：

- 最大迭代次数；
- 最低改进阈值；
- 总 Token/成本预算；
- 相同反馈循环检测；
- 最终降级输出。

```python
def route_after_eval(state: State):
    if state["accepted"]:
        return END
    if state["iteration"] >= 3:
        return "fallback"
    return "generator"
```

### 30.14.7 Agent
<!-- OFFICIAL_IMAGE:agent_loop -->
<p align="center">
  <img src="https://raw.githubusercontent.com/langchain-ai/docs/main/src/oss/images/agent.png" alt="LangChain 官方 Agent 工具反馈循环图" width="900" />
</p>

> **官网原图 14-G　Agent。** 模型根据环境反馈自主选择工具和下一步行动，直到产生最终输出。LangGraph 负责把这一循环显式化，并叠加状态、持久化、流式输出、HITL、预算与容错约束。图源：[`Workflows and agents`](https://docs.langchain.com/oss/python/langgraph/workflows-agents#agents)。
<!-- /OFFICIAL_IMAGE -->


Workflow 由开发者预先定义步骤；Agent 让模型根据当前消息与工具结果决定下一步行为。

```mermaid
flowchart LR
    USER[用户请求] --> LLM[LLM 决策]
    LLM -->|tool_calls| TOOL[工具]
    TOOL --> LLM
    LLM -->|final answer| END([END])
```

Agent 仍然需要开发者提供：

- 工具集合；
- 系统提示与任务边界；
- 权限；
- 预算；
- 审批规则；
- 停止条件；
- 观测与评估。

“自主决策”不等于“无限权限”。

### 30.14.8 模式组合

实际系统通常是组合，而非单选：

```mermaid
flowchart TB
    ROUTER[Routing：识别任务]
    ROUTER --> RESEARCH[Orchestrator-worker：并行研究]
    RESEARCH --> DRAFT[Prompt Chaining：生成草稿]
    DRAFT --> EVAL[Evaluator-optimizer：质量迭代]
    EVAL --> HITL[HITL：人工批准]
    HITL --> TOOL[Agent/Tool：执行发布]
```

选择原则：

- 能用确定性流程解决的，不要全部交给 Agent；
- 能在编译期确定的，不要无故动态化；
- 高风险副作用必须增加审批和幂等；
- 每个动态循环都必须有预算与退出条件。

---
## 30.15 综合实战：可恢复的研究报告 Agent

> **本章性质：工程化综合扩展。** 本章不是附件中某一个原始案例的逐字复刻，而是把前面已经讲过的 `StateGraph`、`Send`、Reducer、Checkpoint、Store、`interrupt()`、流式输出和 Evaluator-optimizer 组合成一个完整项目。

### 30.15.1 目标与边界

我们实现一个“可恢复的研究报告 Agent”，支持：

1. 用户提交研究主题与报告要求；
2. 系统读取用户长期偏好；
3. Planner 生成章节计划；
4. 人工审核计划，可批准、修改或取消；
5. 根据章节数量动态创建 Worker；
6. 多个 Worker 并行编写章节；
7. Synthesizer 汇总全文；
8. Evaluator 按 Rubric 评估；
9. 不合格时携带反馈修订，但有明确循环预算；
10. 最终发布前再次人工审批；
11. 全流程通过 Checkpointer 保存，可在失败或等待审批后继续；
12. 调用方可实时消费阶段进度、状态更新与模型消息。

系统边界如下：

```mermaid
flowchart TB
    CLIENT[Web / CLI / Desktop Client]
    API[Agent API]
    GRAPH[LangGraph Runtime]
    CHECKPOINT[(Checkpoint DB)]
    STORE[(Long-term Store)]
    MODEL[LLM Provider]
    TOOLS[Search / File / Database Tools]
    REVIEWER[Human Reviewer]
    TRACE[Trace / Metrics / Eval]

    CLIENT --> API
    API --> GRAPH
    GRAPH <--> CHECKPOINT
    GRAPH <--> STORE
    GRAPH --> MODEL
    GRAPH --> TOOLS
    GRAPH <--> REVIEWER
    GRAPH --> TRACE
```

### 30.15.2 运行图设计

```mermaid
flowchart TB
    START([START]) --> MEMORY[load_user_memory]
    MEMORY --> PLAN[plan_report]
    PLAN --> PLAN_REVIEW[review_plan]

    PLAN_REVIEW -->|批准| DISPATCH[dispatch_sections]
    PLAN_REVIEW -->|要求重做| PLAN
    PLAN_REVIEW -->|取消| END([END])

    DISPATCH -. Send N tasks .-> WORKER[write_section]
    WORKER --> SYNTH[synthesize_report]
    SYNTH --> EVAL[evaluate_report]

    EVAL -->|合格| FINAL_REVIEW[review_final_report]
    EVAL -->|不合格且预算充足| REVISE[revise_report]
    EVAL -->|预算耗尽| FINAL_REVIEW
    REVISE --> EVAL

    FINAL_REVIEW -->|批准发布| PUBLISH[publish_report]
    FINAL_REVIEW -->|退回修改| REVISE
    FINAL_REVIEW -->|取消| END
    PUBLISH --> END
```

这里有三类控制逻辑：

- **确定性工作流**：记忆加载、计划、汇总、评估、发布；
- **运行时动态并行**：章节数量由 Planner 决定，再通过 `Send` 创建 N 个 Worker 任务；
- **人机协同**：计划审核与最终审核使用 `interrupt()`。

### 30.15.3 项目目录

```text
research_agent/
├── .env
├── langgraph.json
├── pyproject.toml
├── src/
│   └── research_agent/
│       ├── __init__.py
│       ├── context.py
│       ├── models.py
│       ├── state.py
│       ├── nodes.py
│       ├── graph.py
│       └── main.py
└── tests/
    ├── test_routes.py
    ├── test_reducers.py
    ├── test_hitl.py
    └── test_graph.py
```

建议把以下内容分离：

- `state.py`：只定义状态契约；
- `models.py`：只定义结构化输出模型；
- `nodes.py`：实现节点业务逻辑；
- `graph.py`：只组装节点和边；
- `main.py`：管理数据库连接、线程配置与调用；
- `tests/`：使用可控的 Fake Model 和 Fake Tool 测试，不直接依赖线上模型。

### 30.15.4 依赖与环境变量

示例依赖：

```toml
[project]
name = "research-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "langgraph",
  "langchain-core",
  "langchain-deepseek",
  "langgraph-checkpoint-postgres",
  "psycopg[binary]",
  "pydantic",
  "python-dotenv",
  "loguru",
]
```

`.env`：

```dotenv
DEEPSEEK_API_KEY=sk-xxx
DATABASE_URL=postgresql://langgraph_user:强密码@localhost:5432/langgraph_db?sslmode=disable
```

生产环境不要把密钥写入仓库，应使用：

- Secret Manager；
- 容器 Secret；
- CI/CD 密钥注入；
- 操作系统凭证存储。

### 30.15.5 定义运行时上下文

运行时上下文只对本次调用有效，适合携带租户、用户、请求来源和权限信息。

```python
# context.py
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    user_id: str
    request_id: str
    source: str = "api"
    can_publish: bool = False
```

这些字段不应自动写入跨轮状态：

- 当前请求 ID；
- 当前鉴权结果；
- 临时数据库连接；
- 本次功能开关；
- Trace Context。

### 30.15.6 定义结构化输出模型

```python
# models.py
from typing import Literal
from pydantic import BaseModel, Field


class SectionPlan(BaseModel):
    section_id: str = Field(description="稳定且唯一的章节 ID")
    title: str = Field(description="章节标题")
    objective: str = Field(description="章节需要解决的核心问题")
    keywords: list[str] = Field(default_factory=list)


class ReportPlan(BaseModel):
    title: str
    summary: str
    sections: list[SectionPlan]


class EvaluationResult(BaseModel):
    verdict: Literal["accept", "revise"]
    score: int = Field(ge=0, le=100)
    feedback: str
    missing_points: list[str] = Field(default_factory=list)
```

结构化输出的价值不只是“格式好看”，而是让：

- 路由逻辑可类型检查；
- 章节任务可稳定派发；
- 评估结果可程序化处理；
- 非法模型输出可以被捕获并重试或降级。

### 30.15.7 定义图状态

```python
# state.py
from operator import add
from typing import Annotated, TypedDict
from langgraph.graph.message import MessagesState
from langgraph.managed import RemainingSteps


class SectionPlanDict(TypedDict):
    section_id: str
    title: str
    objective: str
    keywords: list[str]


class SectionDraft(TypedDict):
    section_id: str
    title: str
    content: str


class ResearchState(MessagesState, total=False):
    # 外部输入
    topic: str
    requirements: str

    # 用户记忆
    user_preferences: dict[str, str]

    # 计划
    report_title: str
    plan_summary: str
    sections: list[SectionPlanDict]
    plan_decision: str
    plan_feedback: str

    # 动态 Worker 产物
    completed_sections: Annotated[list[SectionDraft], add]

    # 报告与评估
    draft_report: str
    evaluation_score: int
    evaluation_feedback: str
    missing_points: list[str]
    accepted: bool
    revision_count: int

    # 最终审批与发布
    final_decision: str
    final_feedback: str
    published_uri: str
    publish_idempotency_key: str

    # 运行时托管值
    remaining_steps: RemainingSteps


class WorkerState(TypedDict):
    topic: str
    requirements: str
    section: SectionPlanDict
    user_preferences: dict[str, str]
```

### 为什么 `completed_sections` 必须有 Reducer

`Send` 会创建多个 `write_section` 任务实例，它们可能在同一个 Superstep 中并行返回：

```python
{"completed_sections": [draft_a]}
{"completed_sections": [draft_b]}
{"completed_sections": [draft_c]}
```

如果没有 Reducer，同一状态键在一个超步中出现多次写入，可能触发并发更新冲突。使用：

```python
completed_sections: Annotated[list[SectionDraft], add]
```

后，运行时会把这些局部列表合并。

需要注意，`operator.add` 只负责拼接，不保证业务顺序。因此汇总时必须根据原计划中的 `section_id` 排序，不能依赖并发完成顺序。

### 30.15.8 初始化模型

```python
# nodes.py（节选）
from langchain_deepseek import ChatDeepSeek

model = ChatDeepSeek(
    model="deepseek-v4-flash",
    extra_body={"thinking": {"type": "disabled"}},
)
```

附件示例使用以上模型名。实际项目应把 Provider、模型名、温度、超时和最大 Token 数配置化，并锁定依赖版本。

```python
from pydantic import BaseModel


def build_model(config: BaseModel):
    # 根据 Provider Registry 构建模型，避免节点内硬编码供应商分支。
    ...
```

### 30.15.9 加载长期记忆

```python
from langgraph.runtime import Runtime
from loguru import logger

from .context import RequestContext
from .state import ResearchState


def load_user_memory(
    state: ResearchState,
    runtime: Runtime[RequestContext],
) -> ResearchState:
    context = runtime.context
    store = runtime.store

    if context is None or store is None:
        logger.warning("缺少 Runtime Context 或 Store，跳过长期记忆")
        return {"user_preferences": {}}

    namespace = ("tenants", context.tenant_id, "users", context.user_id)
    item = store.get(namespace, "report_preferences")

    return {
        "user_preferences": item.value if item else {}
    }
```

命名空间必须包含租户边界。不要只使用：

```python
("users", user_id)
```

否则不同租户中相同的 `user_id` 可能发生数据串读。更安全的结构是：

```python
("tenants", tenant_id, "users", user_id, "memory")
```

### 30.15.10 生成计划

```python
from langchain.messages import HumanMessage, SystemMessage
from langgraph.types import RetryPolicy

from .models import ReportPlan

planner = model.with_structured_output(ReportPlan)


def plan_report(state: ResearchState, runtime) -> ResearchState:
    runtime.stream_writer({
        "stage": "planning",
        "message": "正在生成报告计划",
    })

    preferences = state.get("user_preferences", {})
    result = planner.invoke([
        SystemMessage(
            "你是一名研究报告规划器。"
            "请把主题拆分成互不重复、能够并行撰写的章节。"
            "section_id 必须稳定、简短且唯一。"
        ),
        HumanMessage(
            f"主题：{state['topic']}\n"
            f"要求：{state.get('requirements', '')}\n"
            f"用户偏好：{preferences}"
        ),
    ])

    return {
        "report_title": result.title,
        "plan_summary": result.summary,
        "sections": [section.model_dump() for section in result.sections],
        "plan_decision": "",
        "plan_feedback": "",
        "completed_sections": [],
    }
```

计划节点最好保持“纯生成”职责，不要同时执行文件写入、网络发布等副作用。

### 30.15.11 人工审核计划

```python
from langgraph.types import interrupt


def review_plan(state: ResearchState) -> ResearchState:
    decision = interrupt({
        "type": "plan_review",
        "title": state["report_title"],
        "summary": state["plan_summary"],
        "sections": state["sections"],
        "allowed_actions": ["approve", "revise", "cancel"],
    })

    # 恢复数据必须经过业务校验，而不能直接信任客户端。
    action = decision.get("action")
    if action not in {"approve", "revise", "cancel"}:
        action = "revise"

    return {
        "plan_decision": action,
        "plan_feedback": str(decision.get("feedback", "")),
    }
```

路由：

```python
from typing import Literal
from langgraph.graph import END


def route_after_plan_review(
    state: ResearchState,
) -> Literal["dispatch_sections", "plan_report", END]:
    if state["plan_decision"] == "approve":
        return "dispatch_sections"
    if state["plan_decision"] == "revise":
        return "plan_report"
    return END
```

更完整的实现应把 `plan_feedback` 加入下一轮 Planner Prompt，否则“要求重做”不会真正吸收人工反馈。

### 30.15.12 动态派发章节任务

`dispatch_sections` 是一个轻量锚点节点：

```python
def dispatch_sections(state: ResearchState) -> ResearchState:
    return {"completed_sections": []}
```

路由函数返回 `Send` 列表：

```python
from collections.abc import Sequence
from langgraph.types import Send


def assign_workers(state: ResearchState) -> Sequence[Send]:
    return [
        Send(
            "write_section",
            {
                "topic": state["topic"],
                "requirements": state.get("requirements", ""),
                "section": section,
                "user_preferences": state.get("user_preferences", {}),
            },
        )
        for section in state["sections"]
    ]
```

同一个 `write_section` 节点定义会被实例化为多个运行时任务。节点级拓扑仍然只有一个 Worker 节点，任务级拓扑则是：

```mermaid
flowchart LR
    D[dispatch_sections]
    D --> W1[write_section task 1]
    D --> W2[write_section task 2]
    D --> W3[write_section task N]
    W1 --> S[synthesize_report]
    W2 --> S
    W3 --> S
```

### 30.15.13 Worker 编写章节

```python
from .state import WorkerState, ResearchState


def write_section(state: WorkerState, runtime) -> ResearchState:
    section = state["section"]
    runtime.stream_writer({
        "stage": "writing_section",
        "section_id": section["section_id"],
        "title": section["title"],
    })

    response = model.invoke([
        SystemMessage(
            "你是一名严谨的研究报告作者。"
            "只编写分配给你的章节，不要重复其他章节。"
            "使用 Markdown，先结论后论据。"
            "无法确认的内容必须明确说明不确定性。"
        ),
        HumanMessage(
            f"总主题：{state['topic']}\n"
            f"总要求：{state['requirements']}\n"
            f"用户偏好：{state['user_preferences']}\n"
            f"章节标题：{section['title']}\n"
            f"章节目标：{section['objective']}\n"
            f"关键词：{section['keywords']}"
        ),
    ])

    return {
        "completed_sections": [{
            "section_id": section["section_id"],
            "title": section["title"],
            "content": response.content,
        }]
    }
```

真实研究 Agent 还应把来源证据设计成结构化状态，例如：

```python
class Evidence(TypedDict):
    source_id: str
    source_type: str
    title: str
    locator: str
    claim: str
    excerpt: str
```

让 Worker 返回“章节草稿 + 证据列表”，而不是只返回没有出处的自由文本。

### 30.15.14 汇总报告

```python
def synthesize_report(state: ResearchState, runtime) -> ResearchState:
    runtime.stream_writer({
        "stage": "synthesizing",
        "message": "正在汇总报告",
    })

    plan_order = {
        section["section_id"]: index
        for index, section in enumerate(state["sections"])
    }

    ordered = sorted(
        state.get("completed_sections", []),
        key=lambda item: plan_order.get(item["section_id"], 10**9),
    )

    body = "\n\n---\n\n".join(
        f"## {item['title']}\n\n{item['content']}"
        for item in ordered
    )

    report = (
        f"# {state['report_title']}\n\n"
        f"> {state['plan_summary']}\n\n"
        f"{body}"
    )

    return {
        "draft_report": report,
        "revision_count": state.get("revision_count", 0),
    }
```

汇总节点应执行至少四项一致性检查：

- 是否缺少计划中的章节；
- 是否存在重复 `section_id`；
- 实际章节顺序是否与计划一致；
- 是否存在 Worker 返回的未知章节。

### 30.15.15 评估报告

```python
from .models import EvaluationResult

report_evaluator = model.with_structured_output(EvaluationResult)


def evaluate_report(state: ResearchState, runtime) -> ResearchState:
    runtime.stream_writer({
        "stage": "evaluating",
        "iteration": state.get("revision_count", 0),
    })

    result = report_evaluator.invoke([
        SystemMessage(
            "你是报告质量评估器。按完整性、结构、论证、"
            "事实谨慎性、可读性五个维度评估。"
            "只有总分不低于 85 且不存在关键缺失时才能 accept。"
        ),
        HumanMessage(state["draft_report"]),
    ])

    return {
        "evaluation_score": result.score,
        "evaluation_feedback": result.feedback,
        "missing_points": result.missing_points,
        "accepted": result.verdict == "accept",
    }
```

评估器输出必须进入显式状态。不要只在日志中写“评分 80”，否则后续路由、报告和问题定位都无法可靠使用该信息。

### 30.15.16 控制评估循环

```python
from typing import Literal

MAX_REVISIONS = 3


def route_after_evaluation(
    state: ResearchState,
) -> Literal["review_final_report", "revise_report"]:
    if state.get("accepted", False):
        return "review_final_report"

    if state.get("revision_count", 0) >= MAX_REVISIONS:
        return "review_final_report"

    # remaining_steps 是运行时托管值，可作为另一道保护。
    if state.get("remaining_steps", 100) < 4:
        return "review_final_report"

    return "revise_report"
```

修订节点：

```python
def revise_report(state: ResearchState, runtime) -> ResearchState:
    next_iteration = state.get("revision_count", 0) + 1
    runtime.stream_writer({
        "stage": "revising",
        "iteration": next_iteration,
    })

    response = model.invoke([
        SystemMessage(
            "你是报告编辑。必须根据反馈修改报告，"
            "保留正确内容，不要删除必要章节。"
        ),
        HumanMessage(
            f"当前报告：\n{state['draft_report']}\n\n"
            f"评分：{state.get('evaluation_score')}\n"
            f"反馈：{state.get('evaluation_feedback')}\n"
            f"缺失点：{state.get('missing_points', [])}"
        ),
    ])

    return {
        "draft_report": response.content,
        "revision_count": next_iteration,
    }
```

生产实现建议增加“无改进检测”：

```python
if new_score <= previous_score and feedback_hash == previous_feedback_hash:
    # 连续多轮没有进步，提前退出或升级人工处理。
    ...
```

### 30.15.17 最终人工审核

```python
def review_final_report(state: ResearchState) -> ResearchState:
    decision = interrupt({
        "type": "final_report_review",
        "report": state["draft_report"],
        "score": state.get("evaluation_score"),
        "feedback": state.get("evaluation_feedback", ""),
        "allowed_actions": ["publish", "revise", "cancel"],
    })

    action = decision.get("action")
    if action not in {"publish", "revise", "cancel"}:
        action = "cancel"

    return {
        "final_decision": action,
        "final_feedback": str(decision.get("feedback", "")),
    }


def route_after_final_review(state: ResearchState):
    if state["final_decision"] == "publish":
        return "publish_report"
    if state["final_decision"] == "revise":
        return "revise_report"
    return END
```

最终审核节点把完整报告放入中断返回值，演示上很直观，但大报告可能超出前端或传输限制。生产系统更适合返回：

- 报告工件 ID；
- 预览地址；
- 内容摘要；
- Diff；
- 风险清单。

### 30.15.18 幂等发布

发布是有副作用的节点，必须幂等。

```python
import hashlib


def build_publish_key(state: ResearchState, tenant_id: str) -> str:
    raw = (
        tenant_id
        + state["report_title"]
        + state["draft_report"]
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def publish_report(
    state: ResearchState,
    runtime: Runtime[RequestContext],
) -> ResearchState:
    context = runtime.context
    if context is None or not context.can_publish:
        raise PermissionError("当前调用无发布权限")

    key = build_publish_key(state, context.tenant_id)

    # 伪代码：数据库中对 idempotency_key 建唯一索引。
    existing = find_published_by_key(key)
    if existing:
        return {
            "published_uri": existing.uri,
            "publish_idempotency_key": key,
        }

    artifact = create_report_artifact(
        tenant_id=context.tenant_id,
        title=state["report_title"],
        content=state["draft_report"],
        idempotency_key=key,
    )

    return {
        "published_uri": artifact.uri,
        "publish_idempotency_key": key,
    }
```

为什么不能只依赖“节点只运行一次”？

- 中断恢复会重新执行包含 `interrupt()` 的节点函数；
- 失败恢复可能重放尚未被确认提交的外部副作用；
- 用户可能重复点击恢复；
- 网络超时不代表服务端没有成功；
- Replay/Fork 会主动重新执行后续节点。

因此，外部写操作必须同时具备：

- 业务幂等键；
- 数据库唯一约束；
- 状态机检查；
- 可审计记录。

### 30.15.19 组装运行图

```python
# graph.py
from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from .context import RequestContext
from .state import ResearchState
from .nodes import (
    load_user_memory,
    plan_report,
    review_plan,
    route_after_plan_review,
    dispatch_sections,
    assign_workers,
    write_section,
    synthesize_report,
    evaluate_report,
    route_after_evaluation,
    revise_report,
    review_final_report,
    route_after_final_review,
    publish_report,
)


def build_graph(*, checkpointer, store):
    builder = StateGraph(
        state_schema=ResearchState,
        context_schema=RequestContext,
    )

    llm_retry = RetryPolicy(
        max_attempts=3,
        initial_interval=0.5,
        backoff_factor=2.0,
        jitter=True,
    )

    builder.add_node("load_user_memory", load_user_memory)
    builder.add_node("plan_report", plan_report, retry_policy=llm_retry)
    builder.add_node("review_plan", review_plan)
    builder.add_node("dispatch_sections", dispatch_sections)
    builder.add_node("write_section", write_section, retry_policy=llm_retry)
    builder.add_node("synthesize_report", synthesize_report)
    builder.add_node("evaluate_report", evaluate_report, retry_policy=llm_retry)
    builder.add_node("revise_report", revise_report, retry_policy=llm_retry)
    builder.add_node("review_final_report", review_final_report)
    builder.add_node("publish_report", publish_report)

    builder.add_edge(START, "load_user_memory")
    builder.add_edge("load_user_memory", "plan_report")
    builder.add_edge("plan_report", "review_plan")

    builder.add_conditional_edges(
        "review_plan",
        route_after_plan_review,
        path_map=["dispatch_sections", "plan_report", END],
    )

    builder.add_conditional_edges(
        "dispatch_sections",
        assign_workers,
        path_map=["write_section"],
    )
    builder.add_edge("write_section", "synthesize_report")
    builder.add_edge("synthesize_report", "evaluate_report")

    builder.add_conditional_edges(
        "evaluate_report",
        route_after_evaluation,
        path_map=["review_final_report", "revise_report"],
    )
    builder.add_edge("revise_report", "evaluate_report")

    builder.add_conditional_edges(
        "review_final_report",
        route_after_final_review,
        path_map=["publish_report", "revise_report", END],
    )
    builder.add_edge("publish_report", END)

    return builder.compile(
        checkpointer=checkpointer,
        store=store,
    )
```

### 一个重要的图设计细节

不要再给 `review_plan` 或 `review_final_report` 添加普通下游边。它们的后续路径已经由条件边决定；同时混用普通边与动态路由，可能使多条路径一起触发。

### 30.15.20 初始化 PostgreSQL 后端

```python
# main.py
import os
from dotenv import load_dotenv
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

from .context import RequestContext
from .graph import build_graph

load_dotenv(override=True)
DB_URL = os.environ["DATABASE_URL"]


def run():
    with PostgresSaver.from_conn_string(DB_URL) as checkpointer, \
         PostgresStore.from_conn_string(DB_URL) as store:

        # 初始化/迁移最好由独立部署步骤执行；教程中为简化而直接调用。
        checkpointer.setup()
        store.setup()

        graph = build_graph(
            checkpointer=checkpointer,
            store=store,
        )

        config = {
            "configurable": {
                "thread_id": "tenant-a:user-42:report-20260901"
            },
            "recursion_limit": 50,
        }

        context = RequestContext(
            tenant_id="tenant-a",
            user_id="user-42",
            request_id="req-001",
            can_publish=True,
        )

        result = graph.invoke(
            {
                "topic": "企业级 Agent Runtime 的设计",
                "requirements": "面向架构师，包含控制面、数据面、治理与评估",
                "revision_count": 0,
            },
            config=config,
            context=context,
            durability="async",
        )
        print(result)
```

首次调用会在 `review_plan` 暂停。返回值通常包含 `__interrupt__`。

### 30.15.21 恢复计划审核

```python
from langgraph.types import Command

plan_resume = graph.invoke(
    Command(resume={
        "action": "approve",
        "feedback": "计划通过",
    }),
    config=config,
    context=context,
    durability="async",
)
```

恢复必须满足：

- 使用相同的 `thread_id`；
- 使用同一个持久化后端；
- 传入 `Command(resume=...)`；
- 不要把新的普通输入误当成恢复数据。

如果进程已经重启，只要使用相同数据库、相同图定义和相同 `thread_id`，仍可从持久化检查点恢复。

### 30.15.22 流式消费

```python
for mode, data in graph.stream(
    {
        "topic": "企业级 Agent Runtime 的设计",
        "requirements": "面向架构师",
        "revision_count": 0,
    },
    config=config,
    context=context,
    stream_mode=["updates", "messages", "custom"],
    durability="async",
):
    if mode == "custom":
        print("进度：", data)
    elif mode == "messages":
        message_chunk, metadata = data
        print(message_chunk.content, end="", flush=True)
    elif mode == "updates":
        print("\n状态更新：", data)
```

客户端应把三种数据分开处理：

- `messages`：用于聊天正文逐 Token 展示；
- `custom`：用于“正在规划”“正在编写第 3 章”等进度提示；
- `updates`：用于调试面板、节点状态和业务进度条。

### 30.15.23 查看状态和历史

```python
latest = graph.get_state(config)
print(latest.values)
print(latest.next)
print(latest.interrupts)

history = list(graph.get_state_history(config))
for snapshot in history:
    print(
        snapshot.metadata.get("step"),
        snapshot.next,
        snapshot.config["configurable"].get("checkpoint_id"),
    )
```

运营或开发平台可以基于 `StateSnapshot` 实现：

- 会话时间线；
- 当前等待节点；
- 中断审批队列；
- 节点错误详情；
- Replay/Fork 调试入口；
- 失败后“从最新检查点继续”按钮。

### 30.15.24 失败恢复演练

假设某个 Worker 因外部服务故障失败，而其他 Worker 已完成：

1. 失败超步中的成功结果会作为中间写入被保存；
2. 修复外部服务或节点代码；
3. 重新构建相同拓扑；
4. 使用相同 Checkpointer；
5. 调用：

```python
result = graph.invoke(
    None,
    config={
        "configurable": {
            "thread_id": "tenant-a:user-42:report-20260901"
        }
    },
    context=context,
)
```

恢复时不应携带历史 `checkpoint_id`，否则语义会从“从最新失败点继续”变成“从指定历史检查点重放”。

### 30.15.25 Replay 与 Fork 在本项目中的用途

### Replay

从“章节编写前”的检查点重放，可以重新调用所有 Worker，生成另一版报告：

```python
checkpoint = next(
    snapshot
    for snapshot in graph.get_state_history(config)
    if snapshot.next == ("write_section",)
)

new_result = graph.invoke(
    None,
    config=checkpoint.config,
    context=context,
)
```

动态 `Send` 场景中的 `next` 形态可能受运行时结构影响，正式代码更适合结合 `metadata`、任务名称和业务状态筛选目标检查点，而不是只靠元组相等。

### Fork

从计划审核后的检查点修改报告要求，生成一条新的执行分支：

```python
fork_config = graph.update_state(
    config=checkpoint.config,
    values={
        "requirements": "改为面向 CTO，控制在 5000 字以内",
    },
    as_node="review_plan",
)

forked_result = graph.invoke(
    None,
    config=fork_config,
    context=context,
)
```

Fork 不会修改旧检查点，而是创建新的 checkpoint 分支。它适合：

- A/B 版本对比；
- 人工修改历史状态后重新执行；
- 调试不同路由结果；
- 从同一研究计划生成不同读者版本。

### 30.15.26 `langgraph.json`

```json
{
  "dependencies": ["."],
  "graphs": {
    "research_agent": "./src/research_agent/graph.py:graph"
  },
  "env": ".env"
}
```

若 `graph.py` 需要数据库上下文管理器才能创建图，不宜在模块顶层直接暴露一个会引用已关闭连接的实例。更稳妥的生产方案是：

- 按 LangGraph 部署环境要求使用其生命周期管理；
- 或提供由运行时托管资源的图工厂；
- 或在服务启动时创建长生命周期连接池，在服务停止时统一关闭。

### 30.15.27 生产化补强清单

这份教程代码展示的是核心闭环。进入生产环境前至少补充：

| 领域 | 必须补强的能力 |
|---|---|
| 模型 | Provider Registry、超时、限流、Fallback、结构化输出校验 |
| 工具 | 权限、参数校验、沙箱、网络出站策略、审批、审计 |
| 状态 | Schema 版本、迁移、大小上限、敏感字段分类 |
| 持久化 | 连接池、备份、加密、租户隔离、清理策略 |
| 循环 | 步数、Token、成本、时间、无进展检测 |
| 中断 | 审批人授权、超时、撤回、代理审批、并发恢复控制 |
| 发布 | 幂等、事务、补偿、工件版本、内容签名 |
| 流式 | 背压、断线续传、事件序号、心跳、客户端去重 |
| 可观测 | Trace、Metrics、Log、状态快照、工具轨迹 |
| 评估 | 离线数据集、在线采样、Rubric、回归门禁 |

---

## 30.16 测试、评估、可观测与安全

> **本章性质：工程化扩展。** 附件详细解释了节点、检查点、流式事件和中断机制；本章进一步把这些运行时能力整理为可落地的质量保障体系。

### 30.16.1 测试金字塔

![企业级 LangGraph Agent Runtime 参考架构](../assets/appendix30/19-enterprise-langgraph-runtime.svg)

> **图 16-1　企业级 LangGraph Agent Runtime 参考架构。** 该图属于工程化综合重绘，不是 LangChain 官方部署拓扑：以 Agent Gateway 统一认证、租户、配额与策略；以 Agent Server、Pregel Scheduler、Graphs/Subgraphs 和 HITL 组成运行时平面；外接模型网关、Tool Registry/MCP、Checkpointer/Store 与队列；横向接入可观测、评估、安全和运维治理。


LangGraph 应用不是只测 Prompt，也不是只测最终文本。建议分五层：

```mermaid
flowchart TB
    E2E[E2E：API/UI/恢复/审批]
    GRAPH[Graph Integration：节点与边]
    NODE[Node Unit：节点函数]
    CONTRACT[Contract：State/Tool/Structured Output]
    PURE[Pure Logic：Reducer/Router/Validator]

    PURE --> CONTRACT --> NODE --> GRAPH --> E2E
```

| 层级 | 重点 | 是否调用真实模型 |
|---|---|---|
| 纯逻辑测试 | Router、Reducer、预算、排序、校验 | 否 |
| 契约测试 | State Schema、工具参数、结构化输出 | 通常否 |
| 节点单测 | Prompt 组装、局部更新、异常映射 | Fake Model 优先 |
| 图集成测试 | 路由、并行、循环、中断、恢复 | 少量或不调用 |
| E2E | 部署、API、数据库、前端流式交互 | 可使用测试模型 |

### 30.16.2 Router 单元测试

```python
import pytest
from langgraph.graph import END


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("approve", "dispatch_sections"),
        ("revise", "plan_report"),
        ("cancel", END),
    ],
)
def test_route_after_plan_review(decision, expected):
    state = {"plan_decision": decision}
    assert route_after_plan_review(state) == expected
```

Router 应尽量是纯函数，因为：

- 易于穷举输入；
- 不消耗模型调用；
- 路由错误可快速定位；
- 不会把语义判断和图拓扑耦合在一起。

### 30.16.3 Reducer 与并发结果测试

```python
from operator import add


def test_section_reducer_appends_results():
    left = [{"section_id": "s1", "content": "A"}]
    right = [{"section_id": "s2", "content": "B"}]
    assert add(left, right) == [
        {"section_id": "s1", "content": "A"},
        {"section_id": "s2", "content": "B"},
    ]
```

还要测试：

- 重复 `section_id`；
- Worker 乱序完成；
- 某个 Worker 返回空内容；
- 并行结果中出现未知章节；
- Replay 后 Reducer 是否意外重复累计。

对于“可重放”的列表状态，简单 `add` 可能产生重复项。生产项目可定义按稳定 ID 去重的 Reducer：

```python
def merge_by_section_id(left, right):
    merged = {item["section_id"]: item for item in left}
    for item in right:
        merged[item["section_id"]] = item
    return list(merged.values())
```

该 Reducer 的覆盖语义必须写入状态契约文档，避免调用者误以为是单纯追加。

### 30.16.4 节点单元测试：注入 Fake Model

不要在节点模块内部写死全局模型。更容易测试的设计是节点工厂：

```python
def make_plan_node(planner):
    def plan_node(state, runtime):
        result = planner.invoke(...)
        return {"sections": result.sections}
    return plan_node
```

测试：

```python
class FakePlanner:
    def invoke(self, messages):
        return ReportPlan(
            title="测试报告",
            summary="摘要",
            sections=[
                SectionPlan(
                    section_id="intro",
                    title="引言",
                    objective="说明背景",
                )
            ],
        )


def test_plan_node_returns_stable_schema():
    node = make_plan_node(FakePlanner())
    result = node(
        {"topic": "LangGraph", "requirements": ""},
        FakeRuntime(),
    )
    assert result["sections"][0]["section_id"] == "intro"
```

### 30.16.5 图结构测试

#### 30.16.5.1 正常路径

断言：

```text
START
→ load_user_memory
→ plan_report
→ review_plan
→ dispatch_sections
→ write_section × N
→ synthesize_report
→ evaluate_report
→ review_final_report
→ publish_report
→ END
```

#### 30.16.5.2 分支覆盖

至少覆盖：

- 计划批准；
- 计划退回；
- 计划取消；
- 评估一次通过；
- 评估多次修订；
- 修订预算耗尽；
- 最终批准；
- 最终退回；
- 最终取消。

#### 30.16.5.3 拓扑快照

```python
def test_graph_topology_snapshot(graph):
    mermaid = graph.get_graph().draw_mermaid()
    assert "plan_report" in mermaid
    assert "review_plan" in mermaid
    assert "write_section" in mermaid
    assert "publish_report" in mermaid
```

对 Mermaid 全文做脆弱的字符串快照可能会因渲染器格式变化而频繁失败。更稳定的做法是检查：

- 关键节点是否存在；
- 关键边是否存在；
- 不允许出现的边是否不存在；
- 节点总数和关键出口集合。

### 30.16.6 HITL 测试

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


def test_plan_review_interrupt_and_resume(builder):
    checkpointer = InMemorySaver()
    graph = builder.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "hitl-test-1"}}

    first = graph.invoke(
        {"topic": "LangGraph", "requirements": "测试"},
        config=config,
    )
    assert "__interrupt__" in first

    resumed = graph.invoke(
        Command(resume={"action": "approve"}),
        config=config,
    )
    assert resumed is not None
```

HITL 测试还应验证：

- 错误 `thread_id` 无法恢复原会话；
- 非法审批动作被拒绝或降级；
- 同一个中断被重复恢复时不会重复产生副作用；
- 多个并行中断可按中断 ID 正确对应恢复数据；
- 审批人不具备权限时不能恢复。

### 30.16.7 失败恢复测试

构造同一超步中的两个并行节点：

- `node_a` 成功并增加调用计数；
- `node_b` 第一次失败，修复后成功。

恢复后断言：

- `node_a` 的调用计数不再增加；
- `node_b` 被重新执行；
- 下游汇总节点只在依赖就绪后执行；
- 最终状态使用已保存的成功结果。

这类测试比“最终结果是否存在”更重要，因为它直接验证 Durable Execution 是否避免了重复计算。

### 30.16.8 Replay/Fork 测试

### Replay 测试

- 从指定历史 `checkpoint_id` 调用；
- 断言检查点之前的节点不执行；
- 断言检查点之后的模型或工具会重新调用；
- 对非确定性节点，不要求输出文本完全一致。

### Fork 测试

- `update_state()` 返回新的 `checkpoint_id`；
- 原分支历史没有被修改；
- 新分支使用修改后的状态；
- `as_node` 不同会导致不同后继路径；
- 伪造状态不符合下游契约时应明确失败。

### 30.16.9 流式测试

不要使用 `sleep()` 猜测事件顺序。应直接消费流并检查事件：

```python
def test_custom_progress_events(graph):
    chunks = list(graph.stream(
        {"topic": "LangGraph"},
        stream_mode=["custom"],
    ))

    events = [data for mode, data in chunks if mode == "custom"]
    assert any(event.get("stage") == "planning" for event in events)
```

需要分别测试：

- `values` 是否输出超步后的完整状态；
- `updates` 是否只包含节点局部更新；
- `messages` 是否包含消息块和元数据；
- `custom` 是否输出业务进度；
- `tasks` 是否能观察异常和中断；
- 开启 `subgraphs=True` 后命名空间解析是否正确；
- 客户端断线重连时是否去重。

### 30.16.10 离线评估体系

建议把评估数据集按如下结构保存：

```json
{
  "case_id": "report-001",
  "input": {
    "topic": "企业级 Agent Runtime",
    "requirements": "必须包含权限、沙箱、可观测"
  },
  "expected": {
    "required_sections": ["权限", "沙箱", "可观测"],
    "must_not_publish": false
  },
  "rubric": {
    "completeness": 30,
    "correctness": 30,
    "structure": 20,
    "traceability": 20
  }
}
```

评估维度分为四类：

| 类型 | 例子 |
|---|---|
| 最终结果 | 正确性、完整性、格式、事实一致性 |
| 轨迹 | 是否调用正确工具、是否出现无效循环、是否越权 |
| 运行时 | 延迟、Token、成本、重试、缓存命中率 |
| 安全治理 | 敏感信息、危险工具、审批绕过、租户串读 |

### 30.16.11 节点级与全图级指标

### 节点级

- 调用次数；
- 成功率；
- 重试次数；
- P50/P95/P99 延迟；
- 输入/输出 Token；
- 缓存命中率；
- 超时率；
- 结构化输出校验失败率。

### 全图级

- 任务完成率；
- 首次通过率；
- 平均 Superstep 数；
- 平均循环次数；
- 中断率和审批等待时长；
- 失败恢复成功率；
- 用户取消率；
- 每任务总成本；
- 最终 Rubric 得分。

### 30.16.12 日志字段规范

每条结构化日志建议包含：

```json
{
  "tenant_id": "tenant-a",
  "user_id": "user-42",
  "request_id": "req-001",
  "thread_id": "report-20260901",
  "checkpoint_id": "...",
  "checkpoint_ns": "",
  "langgraph_step": 4,
  "node": "write_section",
  "task_id": "...",
  "tool_call_id": null,
  "event": "node_completed",
  "latency_ms": 1450
}
```

不得默认记录：

- 完整系统提示；
- 用户原始敏感信息；
- API Key；
- 数据库连接串；
- 工具返回中的凭证；
- 未脱敏的长文档内容。

### 30.16.13 Trace 模型

```mermaid
flowchart LR
    TRACE[Graph Run Trace]
    TRACE --> STEP1[Superstep Span]
    TRACE --> STEP2[Superstep Span]
    STEP1 --> NODE1[Node Span]
    STEP1 --> NODE2[Node Span]
    NODE1 --> MODEL[Model Span]
    NODE2 --> TOOL[Tool Span]
    TOOL --> EXT[External API Span]
```

Span 之间应关联：

- 图运行；
- 超步；
- 节点任务；
- 模型调用；
- 工具调用；
- 外部 HTTP/DB 调用；
- 中断与恢复。

LangSmith 可以用于 LangChain/LangGraph 链路调试与评估；企业平台也可将 OpenTelemetry 作为统一遥测协议，再接入内部 APM、日志和指标平台。

### 30.16.14 在线质量闭环

```mermaid
flowchart LR
    PROD[生产运行] --> TRACE[Trace/Feedback]
    TRACE --> SAMPLE[采样与脱敏]
    SAMPLE --> DATASET[评估数据集]
    DATASET --> EVAL[离线评估]
    EVAL --> RCA[根因分析]
    RCA --> OPT[Prompt/Tool/Graph/Model 优化]
    OPT --> GATE[回归门禁]
    GATE --> PROD
```

根因不要只归结为“模型不好”。常见类别包括：

- State 设计错误；
- Reducer 合并错误；
- Router 路由错误；
- 工具参数或权限错误；
- Checkpoint 恢复语义错误；
- Prompt 缺少约束；
- 数据源过期；
- 循环预算不合理；
- 并发与幂等缺陷。

### 30.16.15 安全模型

#### 30.16.15.1 威胁面

```mermaid
flowchart TB
    INPUT[不可信用户输入]
    PROMPT[Prompt Injection]
    MODEL[模型决策]
    TOOL[高权限工具]
    DATA[敏感数据]
    OUTPUT[外部副作用]

    INPUT --> PROMPT --> MODEL --> TOOL --> DATA
    TOOL --> OUTPUT
```

#### 30.16.15.2 防护原则

1. **模型不是授权主体**：模型建议调用工具，不代表它有权限执行。
2. **工具最小权限**：按租户、用户、工作区、动作和资源范围授权。
3. **高风险动作审批**：删除、付款、发布、发信、部署等动作默认 HITL。
4. **输入和参数双重校验**：模型生成参数也属于不可信输入。
5. **沙箱隔离**：代码、Shell、文件和浏览器工具应运行在受限环境。
6. **网络出站控制**：防止 SSRF、数据外传和访问内部元数据服务。
7. **状态脱敏**：Checkpoint/Store 中不应无限制保存敏感原文。
8. **审计不可抵赖**：记录谁、何时、基于什么输入批准了什么动作。

### 30.16.16 租户隔离

至少同时隔离：

- `thread_id`；
- Store namespace；
- 数据库查询条件；
- 工具凭证；
- 文件工作区；
- Trace 和评估数据；
- 缓存 Key。

仅仅把租户 ID 拼到 `thread_id` 中还不够，数据库层仍应有租户字段、访问策略或物理隔离。

### 30.16.17 预算与熔断

一个生产 Agent 至少应有五类预算：

| 预算 | 保护对象 |
|---|---|
| `recursion_limit` | 最大 Superstep 数 |
| Tool Budget | 最大工具调用次数 |
| Token Budget | 模型上下文与生成成本 |
| Time Budget | 单任务墙钟时间 |
| Cost Budget | 单任务或单租户金额上限 |

预算耗尽时不要统一抛异常，可以按业务选择：

- 返回部分结果；
- 进入人工接管；
- 切换低成本模型；
- 跳过非关键章节；
- 生成明确的降级说明；
- 保存检查点，等待后续继续。

### 30.16.18 发布门禁

上线新版本前建议同时满足：

- 单元测试通过；
- 图分支覆盖通过；
- 历史评估集无显著回退；
- 高风险工具审批测试通过；
- Checkpoint 迁移测试通过；
- 流式协议兼容；
- P95 延迟和成本在阈值内；
- 无严重安全问题；
- 具备快速回滚方案。

---

## 30.17 常见问题与排错清单

### 30.17.1 快速排错表

| 现象 | 常见原因 | 排查与修复 |
|---|---|---|
| 图编译后不知道从哪里开始 | 没有从 `START` 添加边 | 添加 `builder.add_edge(START, "node")` 或入口点 |
| 并行节点写同一字段时报错 | 字段没有 Reducer | 为字段增加 `Annotated[..., reducer]`，或让节点写不同字段 |
| 列表状态重复累计 | 使用 `operator.add` 且发生重放/重复提交 | 使用稳定 ID 去重 Reducer，或在节点中保证幂等 |
| 路由节点同时触发多个下游 | 同时配置普通边和 `Command(goto=...)`/条件边 | 同一节点尽量只保留一种路由机制 |
| `Send` 节点没有正确画在图上 | 渲染器无法推断动态目标 | 在 `add_conditional_edges` 中声明 `path_map` |
| 图无限循环 | 没有停止条件或路由始终回环 | 增加业务停止条件、`recursion_limit`、`RemainingSteps` |
| 出现 `GraphRecursionError` | 超步耗尽 | 分析循环根因；不要只盲目调大限制 |
| 节点重复调用外部 API | 重试、恢复或中断导致重执行 | 使用幂等键，把不可重复副作用放到中断之后 |
| `interrupt()` 没有暂停图 | 未配置 Checkpointer，或被 `try/except` 捕获 | 启用 Checkpointer；不要吞掉 `GraphInterrupt` |
| 恢复后从头运行 | `thread_id` 不一致或检查点后端已重建 | 复用相同 `thread_id` 和持久化后端 |
| `InMemorySaver` 历史消失 | Python 进程/对象重建 | 改用 SQLite/PostgreSQL，或保留同一对象 |
| 恢复调用没有继续 | 把普通字典当作新输入 | 使用 `Command(resume=...)` 恢复动态中断 |
| 恢复到了旧位置 | 配置中意外携带 `checkpoint_id` | 失败恢复只传 `thread_id`；Replay 才显式传历史 ID |
| Fork 后路径不正确 | `as_node` 选择错误 | 理解它表示“把 values 当作哪个节点的输出” |
| 工具调用后模型报消息不完整 | `AIMessage.tool_calls` 后缺少对应 `ToolMessage` | 每个 tool call ID 必须有匹配的工具结果消息 |
| Tool 返回 `Command` 后消息丢失 | 没有在 `Command.update` 中写入 `ToolMessage` | 显式构造并更新 `messages` |
| ToolNode 无法注入运行时 | 参数名/类型不符合约定 | 使用 `runtime: ToolRuntime` |
| 缓存一直不命中 | 输入对象不可稳定序列化，或 Key 包含波动字段 | 自定义 `key_func`，只纳入真正影响输出的字段 |
| 缓存返回旧数据 | TTL 过长或模型/Prompt 版本未纳入 Key | 缩短 TTL，把版本号放入缓存 Key |
| `draw_mermaid_png()` 失败 | 默认在线渲染服务网络不可用 | 重试、替换服务地址，或只输出 Mermaid 源码本地渲染 |
| `messages` 流解析异常 | 把消息块当纯字符串 | 按 `(AIMessageChunk, metadata)` 处理 |
| 多流模式解析错位 | v1 单模式/多模式封装不同 | 统一传列表，或明确采用 v2 `StreamPart` |
| 子图内部事件看不到 | 未设置 `subgraphs=True` | 父图流式调用时启用子图流 |
| 子图多轮记忆丢失 | 使用默认 Per-invocation | 需要跨调用历史时将子图编译为 `checkpointer=True` |
| Stateless 子图不能中断 | `checkpointer=False` 不保存状态 | 改用默认或 Per-thread 策略 |
| Pydantic 状态构造失败 | 输入字段缺失或类型不符 | 检查 Schema 与输入；必要时为可选字段设默认值 |
| 节点返回字段没有生效 | 字段未被图记录或名称拼错 | 检查 State Schema、私有 Schema 和字段名称 |
| 线上数据库检查点快速膨胀 | 状态过大、消息无限累积、无清理策略 | 摘要/裁剪消息，工件外置，设置保留与归档策略 |

### 30.17.2 `InvalidUpdateError` 的系统排查

常见根因：

1. 同一超步中多个任务写同一个未配置 Reducer 的字段；
2. 节点返回了不受支持的类型；
3. 根状态与字段状态模式混用；
4. 自定义 Reducer 返回类型不符合 State Schema；
5. 并行工具都更新了同一普通状态字段。

排查步骤：

```text
查看 tasks/debug 流
→ 确认同一 Superstep 中有哪些任务
→ 对比各任务返回的状态键
→ 找出冲突字段
→ 决定“覆盖、追加、去重、求和、最大值、版本合并”中的正确语义
→ 定义并测试 Reducer
```

不要为了消除异常而机械地给所有字段加 `operator.add`。Reducer 是业务语义，不是异常抑制器。

### 30.17.3 中断恢复重复执行

动态中断不是从 Python 函数中间的指令地址继续，而是恢复时重新运行节点函数，并让对应的 `interrupt()` 返回恢复值。因此：

```python
def approval_node(state):
    charge_credit_card()       # 危险：恢复时可能重复执行
    decision = interrupt(...)
    return {"decision": decision}
```

应改成：

```python
def approval_node(state):
    decision = interrupt(...)
    return {"decision": decision}


def charge_node(state):
    # 使用幂等键执行扣款
    ...
```

### 30.17.4 `thread_id` 设计错误

不推荐：

```text
thread_id = user_id
```

因为一个用户可能同时有多个会话和任务。建议：

```text
<tenant_id>:<user_id>:<application>:<conversation_or_task_id>
```

例如：

```text
tenant-a:user-42:research-agent:report-20260901
```

并保证：

- 不允许客户端伪造其他租户 ID；
- 长度受控；
- 不直接包含敏感信息；
- 生成规则稳定；
- 与业务任务一一对应。

### 30.17.5 状态太大

Checkpoint 保存的是执行状态，不适合直接塞入：

- 大型二进制文件；
- 完整 PDF；
- 几百 MB 的日志；
- 未裁剪的网页正文集合；
- 所有模型中间 Token。

推荐：

```text
大对象 → Object Storage / Artifact Store
State → artifact_id、摘要、哈希、元数据
```

### 30.17.6 消息历史超窗

`MessagesState` 会持续积累消息。长期会话需要：

- Token 计数；
- 滑动窗口；
- 摘要压缩；
- 重要消息保留；
- 工具结果裁剪；
- 原始历史外置；
- 压缩前后完整性校验。

不要直接删除所有旧 ToolMessage，否则后续模型可能失去关键观察结果。

### 30.17.7 路由由模型自由返回节点名

不推荐：

```python
next_node = model.invoke("返回下一节点名")
return Command(goto=next_node)
```

风险：

- 返回不存在的节点；
- Prompt Injection 影响拓扑；
- 无法静态检查；
- 渲染器不能可靠推断；
- 节点重命名会破坏 Prompt。

推荐：

```text
LLM → 业务枚举 decision
Router → decision 到节点名的受控映射
```

### 30.17.8 重试放大故障

多层同时重试会导致调用放大：

```text
API Gateway 重试 3 次
× Graph Node 重试 3 次
× Tool Wrapper 重试 3 次
= 最坏 27 次下游请求
```

需要统一重试预算，并明确哪一层负责：

- HTTP 连接级重试；
- 节点级业务重试；
- Agent 语义级重新规划。

### 30.17.9 Checkpoint Schema 迁移

当 State 字段发生变化时，历史检查点仍可能使用旧结构。建议：

```python
class State(TypedDict, total=False):
    schema_version: int
    ...
```

恢复时：

```text
读取旧状态
→ 识别 schema_version
→ 执行纯函数迁移
→ 校验新 Schema
→ 保存新检查点或拒绝恢复
```

高风险变更包括：

- 字段重命名；
- Reducer 语义变化；
- 消息类型变化；
- 节点名称变化；
- 子图命名空间变化；
- 路由枚举变化。

### 30.17.10 线上排错的推荐顺序

```text
1. 确认 thread_id / request_id
2. 查看最新 StateSnapshot
3. 查看 next / tasks / interrupts
4. 确认失败 Superstep 和节点
5. 查看节点输入与局部更新摘要
6. 查看工具调用及 tool_call_id
7. 检查 Retry/Timeout/Cache
8. 检查 Reducer 与并发写入
9. 判断应恢复、Replay 还是 Fork
10. 修复后用最小测试用例回归
```

---

## 30.18 练习题与面试题

### 30.18.1 分阶段实践练习

### 练习 1：最小顺序图

实现：

```text
START → normalize → validate → output → END
```

验收条件：

- 使用 `TypedDict`；
- 每个节点只返回局部更新；
- 能输出 Mermaid；
- 为非法输入设计明确分支。

### 练习 2：多 Schema

设计 `InputState`、`OverallState`、`OutputState`、`PrivateState`，实现：

```text
用户输入 → 内部清洗 → 私有特征 → 对外结果
```

验收条件：最终输出不能暴露内部私有字段。

### 练习 3：自定义 Reducer

实现一个按 `id` 去重并以新值覆盖旧值的 Reducer，用于合并并行检索结果。

验收条件：

- 新 ID 追加；
- 相同 ID 更新；
- 合并顺序可预测；
- 有单元测试。

### 练习 4：静态并行与 Fan-in

并行执行关键词、格式、敏感词三个检查器，等待全部完成后由汇总节点输出质量报告。

### 练习 5：动态 Map-Reduce

输入任意长度的文本列表，通过 `Send` 动态创建摘要任务，再聚合成总摘要。

验收条件：输入 1、3、100 个元素都能运行。

### 练习 6：Command 动态跳转

节点读取状态并返回 `Command(update=..., goto=...)`，实现“正常、降级、终止”三条路径。

### 练习 7：ReAct 循环

实现模型—工具—模型循环，并添加：

- 最大工具调用次数；
- 工具失败重试；
- 未知工具兜底；
- 最终答案节点。

### 练习 8：Checkpoint 多轮会话

使用相同 `thread_id` 完成三轮对话，再换一个 `thread_id` 验证会话隔离。

### 练习 9：失败恢复

构造两个并行节点，其中一个失败。修复后恢复，验证成功节点不重复执行。

### 练习 10：Replay 与 Fork

- Replay：从中间检查点重新生成后半段；
- Fork：修改历史输入，产生新执行分支；
- 对比两个分支的 `checkpoint_id` 和最终状态。

### 练习 11：HITL 审批

工具执行前触发中断，支持：

- approve；
- edit；
- reject。

编辑参数后必须重新校验权限和参数。

### 练习 12：流式 UI

同时消费 `messages`、`updates`、`custom`：

- 聊天区展示 Token；
- 侧边栏展示节点状态；
- 顶部展示业务进度。

### 练习 13：长期记忆

把用户偏好存入 Store，并按租户和用户命名空间隔离。新建不同 `thread_id` 后仍能读取偏好。

### 练习 14：子图

创建“检索子图”和“写作子图”，分别测试：

- 默认 Per-invocation；
- Per-thread；
- Stateless。

### 练习 15：完整质量闭环

实现：

```text
Generate → Evaluate → Revise → HITL → Publish
```

必须具备：递归限制、成本预算、幂等发布、离线评估用例。

### 30.18.2 核心面试题

### 1. LangGraph 与 LangChain 的定位有什么区别？

回答要点：LangChain 更偏高层 Agent 开发入口；LangGraph 是更底层的有状态编排框架和 Agent Runtime，负责节点、边、状态、持久化、流式输出、可恢复执行和 HITL。简单 Agent 可使用高层 API，复杂流程或需要底层状态控制时使用 LangGraph。

### 2. State、Node、Edge 分别是什么？

回答要点：State 是共享状态快照；Node 是读取状态并返回局部更新的执行单元；Edge 定义触发与依赖关系。三者共同描述可执行图，而不是静态流程图。

### 3. 什么是 Superstep？

回答要点：Pregel 风格运行时按超步推进。一个超步中先根据通道更新规划任务，再并行执行已激活节点，最后统一合并状态写入。并行节点基于同一轮开始时的状态快照计算，不能立即看到同超步其他节点的新值。

### 4. 为什么并行更新同一个字段需要 Reducer？

回答要点：同一超步可能出现多个状态更新，运行时需要明确合并语义。Reducer 可能是追加、去重、求和、最大值或按版本覆盖；没有 Reducer 的普通字段通常是覆盖语义，并行多写可能冲突。

### 5. `operator.add` 与 `add_messages` 有什么区别？

回答要点：`operator.add` 对列表只是拼接；`add_messages` 面向消息对象，既可追加新消息，也会根据相同消息 ID 更新已有消息，更适合对话状态。

### 6. 节点为什么只返回局部更新？

回答要点：LangGraph 把节点输出看作对 State 的 Patch。未返回字段保持不变；返回字段按 Reducer 合并或覆盖。这样可以降低节点耦合，便于并发和状态演进。

### 7. Graph API 与 Functional API 如何选择？

回答要点：复杂分支、并行、共享状态、可视化和长期维护优先 Graph API；已有过程式代码、线性流程和快速原型可用 Functional API。二者共享运行时。

### 8. 条件边与 `Command(goto=...)` 有什么区别？

回答要点：条件边把路由逻辑放在图定义侧；Command 把状态更新和下一跳内聚到节点返回值中。二者都能动态路由，但同一节点不应无故混用普通边和动态路由。

### 9. `Send` 解决什么问题？

回答要点：运行时根据数据动态创建多个任务实例，并为每个任务提供独立输入，适合动态 Fan-out 和 Map-Reduce。它不是运行时创建新的节点定义，目标节点仍需预注册。

### 10. 静态 Fan-in 的两种写法有什么差异？

回答要点：`add_edge([a, b], c)` 表示等待所有上游共同到达后触发一次；分别添加 `a→c`、`b→c` 表示两个上游独立触发，`c` 可能执行多次。

### 11. Durable Execution 依赖什么？

回答要点：依赖 Checkpointer 保存 Checkpoint，并由 `thread_id` 组织执行线。恢复时运行时利用状态快照、下一任务和中间写入继续执行，而不是简单读取一个业务变量。

### 12. `thread_id` 与 `checkpoint_id` 的职责有什么区别？

回答要点：`thread_id` 标识一条会话或持久化执行线；`checkpoint_id` 定位该执行线上的具体历史状态。失败恢复通常只传 thread_id，历史重放则显式传 checkpoint_id。

### 13. `StateSnapshot.next` 和 `tasks` 分别表示什么？

回答要点：`next` 表示从该快照继续时下一步应执行的节点名；`tasks` 是具体 PregelTask 信息，还可能包含已成功结果、异常或中断，用于观测和失败恢复。

### 14. 为什么失败恢复能避免重复执行同一超步中已成功的并行节点？

回答要点：运行时会保存任务级中间写入。即使同超步另一任务失败，成功任务的 result 仍可与检查点关联；恢复时复用已成功结果，只重试未完成任务。

### 15. Replay 与 Fork 有什么区别？

回答要点：Replay 不修改历史状态，从指定检查点重新执行后续节点；Fork 使用 `update_state()` 在历史点应用新的状态更新，创建新检查点分支。两者都不会重新执行目标检查点之前的节点。

### 16. `update_state(..., as_node=...)` 的真实含义是什么？

回答要点：不是执行该节点，而是把 `values` 当作该节点已产生的输出，交给该节点的 writers 处理，应用状态写入及可能的路由写入，再创建新检查点。因此不同 `as_node` 会改变后继路径。

### 17. 短期记忆、长期记忆、Runtime Context 如何区分？

回答要点：短期记忆在 State 中，由 Checkpointer 按 thread_id 保存；长期记忆在 Store 中，支持跨会话；Runtime Context 只对本次调用生效，不持久化，适合身份、权限、请求来源和临时依赖。

### 18. 为什么 `interrupt()` 必须结合 Checkpointer？

回答要点：中断后要保存当前位置和状态，恢复时才能找到同一执行线。没有 Checkpointer，暂停状态无法可靠恢复。

### 19. 动态中断恢复时为什么会重新执行节点？

回答要点：它不是保存 Python 调用栈和指令指针，而是基于检查点重新运行节点，并让相应 `interrupt()` 调用返回恢复值。因此中断前副作用必须幂等或拆到后续节点。

### 20. ToolNode 相比手写工具节点解决了什么？

回答要点：它封装工具查找、参数处理、并行执行、ToolMessage 对应关系、运行时注入和 Command 传播等通用逻辑，减少手写错误；复杂治理仍可通过包装器扩展。

### 21. ToolRuntime 能访问哪些信息？

回答要点：工具可通过它访问图状态、Runtime Context、RunnableConfig、Stream Writer、tool_call_id 和 Store，因此能实现状态更新、长期记忆、进度流和调用关联。

### 22. 工具返回 Command 时为什么还要写 ToolMessage？

回答要点：模型发起的每个 tool call 都要求后续消息历史中存在匹配 `tool_call_id` 的 ToolMessage。返回 Command 不会自动替开发者补齐这个协议，所以必须在 `Command.update["messages"]` 中显式加入。

### 23. LangGraph 的 `stream_mode` 有哪些？

回答要点：`values`、`updates`、`messages`、`checkpoints`、`tasks`、`debug`、`custom`。分别面向完整状态、局部更新、消息增量、持久化事件、任务观测、综合调试和业务自定义进度。

### 24. `stream/astream` 与 `astream_events` 有什么区别？

回答要点：前者主要消费图业务和 Pregel 运行数据；后者面向 Runnable 标准生命周期事件、父子调用关系和输入输出元数据，更适合组件级事件追踪。

### 25. 子图有哪两种嵌入方式？

回答要点：在父节点函数中调用子图，适合状态隔离和显式适配；把编译后的子图直接作为父图节点，适合共享或兼容状态。两种方式在状态边界和持久化命名空间上不同。

### 26. 子图三种持久化策略是什么？

回答要点：默认 Per-invocation 保存本次调用检查点并支持中断，但不跨父图多次调用加载历史；`checkpointer=True` 是 Per-thread，可跨调用保留记忆；`checkpointer=False` 是 Stateless，不保存检查点，也不支持中断恢复。

### 27. Workflow 与 Agent 最大的区别是什么？

回答要点：Workflow 的主要步骤和路径由开发者预定义；Agent 让模型根据消息和工具观察动态决定下一步。即使是 Agent，工具、权限、预算、停止条件和治理边界仍由开发者控制。

### 28. 如何避免 Agent 无限循环？

回答要点：业务停止条件、`recursion_limit`、`RemainingSteps`、最大工具调用数、Token/时间/成本预算、重复轨迹检测、无进展检测和最终兜底应组合使用。

### 29. 节点重试与 Agent 重新规划有什么区别？

回答要点：节点重试适合临时故障，通常重复同一操作；Agent 重新规划属于语义层策略改变，可能换工具或路径。若混在一起容易造成调用放大和副作用重复。

### 30. 如何把 LangGraph 做成企业级 Agent Runtime？

回答要点：在图运行时之上补齐多租户、Provider Registry、工具注册与权限、沙箱、持久化、审批、可观测、评估、预算、幂等、版本迁移、部署和运营控制面。LangGraph 负责执行核心，但企业治理需要平台层建设。

---

## 30.19 API 速查表

### 30.19.1 图构建

| 目标 | API |
|---|---|
| 创建状态图 | `StateGraph(state_schema=...)` |
| 添加节点 | `builder.add_node(name, callable, ...)` |
| 添加普通边 | `builder.add_edge(source, target)` |
| 添加多前驱同步边 | `builder.add_edge([a, b], c)` |
| 添加条件边 | `builder.add_conditional_edges(source, router, path_map=...)` |
| 添加顺序节点 | `builder.add_sequence([...])` |
| 编译 | `builder.compile(...)` |
| 起点 | `START` |
| 终点 | `END` |

### 30.19.2 图调用

| 目标 | API |
|---|---|
| 同步调用 | `graph.invoke(input, config=...)` |
| 异步调用 | `await graph.ainvoke(...)` |
| 同步流式 | `graph.stream(...)` |
| 异步流式 | `graph.astream(...)` |
| Runnable 事件流 | `graph.astream_events(...)` |
| 设置递归限制 | `config={"recursion_limit": N}` |
| 设置会话 | `config={"configurable": {"thread_id": "..."}}` |

### 30.19.3 State

| 目标 | 写法 |
|---|---|
| 轻量状态 | `TypedDict` |
| 对象属性访问 | `dataclass` |
| 强校验 | `Pydantic BaseModel` |
| 为字段声明 Reducer | `Annotated[T, reducer]` |
| 列表追加 | `Annotated[list[T], operator.add]` |
| 消息合并 | `Annotated[list[AnyMessage], add_messages]` |
| 预定义消息状态 | `MessagesState` |
| 单次绕过 Reducer | `Overwrite(value)` |
| 输入边界 | `input_schema=...` |
| 输出边界 | `output_schema=...` |
| 运行时上下文 | `context_schema=...` |

### 30.19.4 控制流

| 场景 | API |
|---|---|
| 固定下一跳 | 普通边 |
| 静态候选条件分支 | `add_conditional_edges` |
| 节点内动态跳转 | `Command(goto=...)` |
| 同时更新状态与跳转 | `Command(update=..., goto=...)` |
| 动态创建 N 个任务 | `Send(node, arg)` |
| 子图跳父图 | `Command(graph=Command.PARENT, goto=...)` |
| 延迟收尾节点 | `add_node(..., defer=True)` |

### 30.19.5 容错与缓存

| 目标 | API/配置 |
|---|---|
| 节点重试 | `retry_policy=RetryPolicy(...)` |
| 最大尝试次数 | `max_attempts`，包含首次执行 |
| 指数退避 | `initial_interval` + `backoff_factor` |
| 随机抖动 | `jitter=True` |
| 异常过滤 | `retry_on=...` |
| 节点缓存策略 | `cache_policy=CachePolicy(...)` |
| 缓存后端 | `compile(cache=InMemoryCache())` |
| TTL | `CachePolicy(ttl=...)` |
| 自定义缓存键 | `CachePolicy(key_func=...)` |
| 节点超时 | 附件标记为 1.2+ 的 `TimeoutPolicy` 能力 |
| 最终错误处理 | 附件标记为 1.2+ 的 `error_handler` 能力 |

### 30.19.6 Checkpoint 与恢复

| 目标 | API |
|---|---|
| 内存检查点 | `InMemorySaver()` |
| PostgreSQL | `PostgresSaver.from_conn_string(...)` |
| 启用检查点 | `compile(checkpointer=...)` |
| 最新快照 | `graph.get_state(config)` |
| 历史快照 | `graph.get_state_history(config)` |
| 指定历史快照 | 配置中加入 `checkpoint_id` |
| 从最新失败点继续 | `graph.invoke(None, config={thread_id})` |
| Replay | `graph.invoke(None, config=history.config)` |
| Fork | `graph.update_state(config, values, as_node=...)` |
| 持久化模式 | `durability="exit" / "async" / "sync"` |

### 30.19.7 中断

| 目标 | API |
|---|---|
| 动态中断 | `interrupt(payload)` |
| 恢复 | `Command(resume=value)` |
| 多中断恢复 | 按中断 ID 提供恢复值 |
| 节点前静态断点 | `interrupt_before=[...]` |
| 节点后静态断点 | `interrupt_after=[...]` |

中断四条硬规则：

1. 配置 Checkpointer；
2. 复用 `thread_id`；
3. 不要捕获 `interrupt()` 用于控制流的内部异常；
4. 中断前副作用必须幂等。

### 30.19.8 工具

| 目标 | API |
|---|---|
| 定义工具 | `@tool` |
| 绑定模型 | `model.bind_tools(tools)` |
| 预构建工具节点 | `ToolNode(tools=...)` |
| 工具运行时 | `runtime: ToolRuntime` |
| 工具更新图状态 | 返回 `Command(update=...)` |
| 工具前后包装 | `wrap_tool_call` / `awrap_tool_call` |
| 工具调用 ID | `runtime.tool_call_id` |
| 工具长期记忆 | `runtime.store` |
| 工具自定义流 | `runtime.stream_writer(...)` |

### 30.19.9 流式模式

| 模式 | 内容 |
|---|---|
| `values` | 每个超步后的完整状态 |
| `updates` | 节点局部更新 |
| `messages` | 模型消息增量和元数据 |
| `checkpoints` | 检查点事件 |
| `tasks` | 任务开始、结果、异常、中断 |
| `debug` | 综合调试事件 |
| `custom` | 节点/工具主动写出的业务事件 |

### 30.19.10 Store 与 Runtime Context

| 数据类型 | 存放位置 | 生命周期 |
|---|---|---|
| 节点间计算结果 | State | 当前线程，可持久化 |
| 会话消息 | State + Checkpointer | 同 `thread_id` |
| 用户长期偏好 | Store | 跨会话 |
| 当前身份和权限 | Runtime Context | 仅本次调用 |
| 大文件内容 | Artifact/Object Storage | 独立生命周期，State 只存引用 |

### 30.19.11 子图

| 目标 | 写法 |
|---|---|
| 节点内部调用子图 | `subgraph.invoke(...)` |
| 子图直接作为节点 | `builder.add_node("sub", compiled_subgraph)` |
| 查看子图 | `graph.get_subgraphs()` |
| 展开子图状态 | `get_state(..., subgraphs=True)` |
| 子图流 | 父图调用时 `subgraphs=True` |
| 默认调用级检查点 | `sub_builder.compile()` |
| 线程级记忆 | `sub_builder.compile(checkpointer=True)` |
| 无状态子图 | `sub_builder.compile(checkpointer=False)` |

### 30.19.12 六类模式选择表

| 模式 | 何时使用 | 关键能力 |
|---|---|---|
| Prompt Chaining | 步骤固定、前后依赖 | 顺序边、质量 Gate |
| Parallelization | 任务固定且互相独立 | 静态 Fan-out/Fan-in |
| Routing | 输入类型决定专用流程 | 结构化输出、条件边 |
| Orchestrator-worker | 子任务数量运行时确定 | `Send`、Reducer、WorkerState |
| Evaluator-optimizer | 有明确质量标准且需迭代 | 循环、反馈状态、预算 |
| Agent | 步骤和工具顺序无法预知 | MessagesState、ToolNode、ReAct |

### 30.19.13 最终学习检查表

### 基础

- [ ] 能解释 State、Node、Edge、Superstep；
- [ ] 能从零构建、编译、调用和可视化 StateGraph；
- [ ] 能使用 TypedDict 定义 State；
- [ ] 理解节点返回的是局部更新。

### 状态与控制流

- [ ] 能为并行状态更新设计正确 Reducer；
- [ ] 能区分普通边、条件边、Command 和 Send；
- [ ] 能实现静态/动态 Fan-out 与 Fan-in；
- [ ] 能实现有停止条件的循环。

### Runtime

- [ ] 能配置 Retry、Cache 和递归限制；
- [ ] 能解释 Checkpoint、thread_id、checkpoint_id；
- [ ] 能实现失败恢复、Replay 和 Fork；
- [ ] 能区分 State、Store、Runtime Context。

### Agent 能力

- [ ] 能实现 ToolNode 驱动的 ReAct 循环；
- [ ] 能使用 ToolRuntime 访问状态和上下文；
- [ ] 能实现工具审批；
- [ ] 能处理工具失败、重试和消息协议。

### 交互与模块化

- [ ] 能实现动态 HITL 与静态断点；
- [ ] 能消费至少三种 stream_mode；
- [ ] 能构建和持久化子图；
- [ ] 能让子图动态路由回父图。

### 生产工程

- [ ] 有多租户隔离；
- [ ] 有工具最小权限和沙箱；
- [ ] 有幂等与补偿；
- [ ] 有 Trace、Metrics 和结构化日志；
- [ ] 有离线评估集和回归门禁；
- [ ] 有 State/Checkpoint 版本迁移；
- [ ] 有 Token、步骤、时间和成本预算；
- [ ] 有故障恢复演练与回滚方案。

### 30.19.14 核心术语表

| 术语 | 说明 |
|---|---|
| Pregel | LangGraph 借鉴的图计算执行模型 |
| Superstep | 一轮计划、并行执行和统一提交的运行阶段 |
| State | 图运行的共享状态 |
| Channel | 底层承载状态更新和触发关系的通道 |
| Node | 图中的执行单元 |
| Edge | 节点之间的依赖与触发关系 |
| Reducer | 合并同一状态键多个更新的函数 |
| Checkpoint | 超步边界上的持久化状态快照 |
| Checkpointer | 保存和读取 Checkpoint 的后端抽象 |
| Thread | 一条逻辑上的持久化执行线/会话 |
| StateSnapshot | 面向开发者的检查点视图 |
| Durable Execution | 利用持久化状态在中断或失败后继续执行 |
| Replay | 从历史检查点重新执行后续步骤 |
| Fork | 修改历史状态并创建新执行分支 |
| Store | 跨会话长期记忆存储 |
| Runtime Context | 仅对当前调用有效的上下文 |
| HITL | Human-in-the-loop，人在环 |
| Send | 动态创建任务实例并传递独立输入 |
| Command | 同时表达状态更新、跳转、恢复或跨图控制 |
| ToolNode | LangGraph 预构建的工具执行节点 |
| ToolRuntime | 工具专用运行时注入对象 |
| Fan-out | 从一个上游分发到多个下游任务 |
| Fan-in | 多个上游结果汇聚到一个下游 |
| Map-Reduce | 动态拆分任务并聚合结果的模式 |
| ReAct | Reason + Action 的模型—工具循环 |

---


### 30.19.15 图表使用与再编辑

本增强版的所有新增图表同时提供 SVG 与 Graphviz DOT 源文件：

```text
assets/
├── diagrams/       # Markdown 直接引用的 SVG
├── diagrams-png/   # Office / PDF 工具兼容的 PNG 版本
└── diagrams-src/   # 可编辑的 .dot 源文件
```

重新生成某张图：

```bash
dot -Tsvg assets/diagrams-src/01-langgraph-ecosystem-stack.dot \
  -o assets/diagrams/01-langgraph-ecosystem-stack.svg
```

图表采用“官网原图在线引用 + 官方概念本地重绘”的双轨方式。官网原图不复制进压缩包，离线阅读时仍可使用 19 张本地图。具体出处、核验日期与图表对应关系见 [`SOURCES.md`](SOURCES.md)。转载或二次发布教程时，应同时保留来源说明，不应把这些重绘图标记为 LangChain 官方原图。


# 结语

掌握 LangGraph 的关键，不是记住每个 API，而是建立四层认知：

1. **图模型**：State、Node、Edge、Reducer；
2. **运行时模型**：Superstep、Channel、Task、流式事件；
3. **可靠性模型**：Checkpoint、恢复、中断、重试、幂等；
4. **工程治理模型**：权限、沙箱、预算、可观测、评估和多租户。

从简单工作流到企业级 Agent Runtime，复杂度的增长并不是“节点越来越多”，而是状态、控制流、副作用、恢复语义和治理边界越来越重要。一个可靠的 LangGraph 系统，应当既允许模型做动态决策，又把权限、成本、质量和失败边界牢牢掌握在确定性的工程控制之中。

---

> **使用提示**：本附录是"用 LangGraph 落地图执行"的操作手册，与机制章互为代码与原理——StateGraph 三要素与 Pregel（30.1）对第 18 章图执行范式、控制流原语（30.5）对第 18 章条件路由/回边、持久化与 Time Travel（30.7）对第 12 章 checkpoint、记忆与上下文（30.8）对第 5/10 章、中断与 HITL（30.9）对第 13 章与第 18 章 interrupt、工具与 ReAct（30.10）对第 4/7 章、流式（30.11）对第 3 章、多 Agent 模式对第 17–19 章与附录 17。它是附录 4 D.5"LangGraph = 第 18 章显式图执行的最流行实现"的展开；框架 API 会过期，核心运行时模型不过期（[C-53]）。