# reference-assistant · 贯穿项目参考实现

《企业级 Agent 从入门到专家（2026 版）》的可运行贯穿项目。**教学参考实现，不是生产框架**——每处教学简化都在对应章节标注了补齐路标。

本目录当前落地的是 **Task Group 1：统一事件契约**——把第 12、14、15、16 章之间的事件字段漂移收敛到唯一契约包，并用合同测试锁死。

## 结构

```text
src/assistant/
├─ contracts/          # 唯一契约（第 12 章）
│  ├─ event.py         #   AgentEvent 外壳 + 载荷校验 + 生产者工厂
│  ├─ usage.py         #   TokenUsage（保留供应商原始分类，前向兼容）
│  ├─ run.py           #   领域枚举（SessionStatus）与术语表
│  ├─ tool.py          #   tool_call/tool_result 按 call_id 配对，检出孤儿
│  └─ artifact.py      #   执行产物（带指针不带大对象）
├─ runtime/events.py   # EventBus 生产者：校验 + seq 逆序/重复检测（第 12 章）
├─ obs/otel_bridge.py  # 消费者：事件 → OTel Span/指标（第 14 章）
└─ cost/ledger.py      # 消费者：turn_end → per-session 成本（第 16 章）
tests/
├─ test_event_contract.py   # TG1 §6.7 九条合同测试
└─ test_consumers.py        # OTel 与台账消费同一事件流；并发不串线
```

## 契约要点（消费者不得再猜字段）

- `turn_end.payload.usage` **嵌套**保存 token 分类，`model` 必填；
- `tool_call` / `tool_result` 通过**同一个 `call_id`** 关联；
- `session_end.payload.status` 取有限枚举（`ok/error/aborted/cancelled`）；
- 可选字段前向兼容；破坏性变更提升 `schema_version`，不支持则 fail-fast。

第 14 章（OTel）与第 16 章（成本台账）是同一 `turn_end` 事件的两个投影。

## 运行

```bash
cd examples/reference-assistant
python -m pip install -e ".[dev]"    # 或仅 pip install pytest opentelemetry-sdk
pytest -q                            # 一条命令跑测试（合同 + 消费者）
python tools/check_snippets.py       # 一条命令验证：章节片段与源码零漂移
```

`obs/otel_bridge.py` 需要 `opentelemetry-sdk`（`.[obs]` 附加依赖）；契约与台账仅用标准库。

## 片段同步（Task Group 3 · 8.4）

章节代码不再各存一份，而是从本工程受控引用。源码用 region 标记：

```python
# region book:ch12-event-envelope
...
# endregion book:ch12-event-envelope
```

Markdown 用嵌入标记（放在代码块正上方）：

```html
<!-- snippet: examples/reference-assistant/src/assistant/contracts/event.py#ch12-event-envelope mode=executable verified_by=... -->
```

`tools/check_snippets.py` 校验：目标文件存在、region 唯一、Markdown 片段与源码逐字符一致、anchor 缺失即失败。**源码漂移 → 检查失败**（接入 CI 后即 CI 失败）。已接入的片段：`第 12 章 · AgentEvent 事件外壳`。
