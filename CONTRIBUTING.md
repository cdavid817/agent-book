# 贡献指南

感谢参与《企业级 Agent 从入门到专家》。本仓库是**书稿 + 可运行贯穿项目**的合体，一致性由脚本门禁保证。提交前请通读本文并本地跑通 `make verify`。

## 环境准备

```bash
make setup      # 安装锁定依赖（requirements-ci.txt）+ 可编辑装入 examples/reference-assistant
make verify     # 本地跑全部门禁；PR 通过它才有意义
```

需要 Python ≥ 3.10。依赖版本锁定在 `requirements-ci.txt`，请勿在 PR 里顺手升级。

## 章节结构约定

- 每章遵循固定六段：`## 1. 场景引入` / `## 2. 原理` / `## 3. 动手实现` / `## 4. 生产级考量` / `## 5. 常见坑` / `## 6. 面试高频问题`。
- 新增或改名章节，**必须同步 `book.yml`**（全书结构单一来源）；`tools/check_book.py` 会核验 `book.yml` 与磁盘/README/outline 一致、章号连续、六段齐全、图号连续有图注。
- 图注编号从 1 连续；跨章引用写作“第 N 章图 M”。

## 代码片段规则

- 章节里的可执行代码应从 `examples/reference-assistant/` **受控引用**，不要各存副本。
- 源码用 region 标记：`# region book:<anchor>` … `# endregion book:<anchor>`。
- Markdown 用嵌入标记（紧贴代码块上方）：`<!-- snippet: <path>#<anchor> mode=executable verified_by=<test> -->`。
- `tools/check_snippets.py` 逐字符校验片段与源码一致；漂移即失败。改源码就要改章节，反之亦然。

## 来源规则

- 凡量化结果、版本号、“当前/最新/已废弃/主流”等时效性表述，正文标 `[C-XX]`，并在 `references/sources.yaml` 登记（附录 C 是其可读视图）。
- 协议/API 结论必须官方一手来源（规范 > 官方 SDK 文档 > 官方公告 > 论文 > 官方基准 > 二手分析 > 社区），不得以二手博客为唯一来源。
- 时效性来源带 `verified_at` / `expires_at`；`tools/check_sources.py` 校验一致性与过期，并核对协议版本常量（如第 8 章 `PROTOCOL_VERSION` 与 `C-03`）。
- 发行前跑 `make check-sources-strict`：`待确认` / 过期项会阻断。

## 图表规则

三类图分工见 [`图表规范.md`](图表规范.md)：**D2** 画架构/分层（源 `diagrams/d2/*.d2` → 渲染 `assets/*.svg` → 正文 `![]()`），**Mermaid** 画时序/流程（正文内联），**Excalidraw** 画原理示意。`tools/check_diagrams.py` 校验每个 D2 源已渲染、被引用、且不含 `<foreignObject>`（否则 GitHub 不显示）。

## 测试命令

```bash
make verify            # 全部门禁（渲染/目录/交叉引用/片段/图源/来源/测试）
make test              # 只跑贯穿项目 pytest
make check-snippets    # 只查片段同步
python -m pytest -q examples/reference-assistant
```

## 提交规范

- 采用 Conventional Commits：`type(scope): 摘要`（`feat` / `fix` / `docs` / `refactor` / `test` / `ci` / `chore`）。
- 提交信息用中文摘要即可，说清“做了什么、为什么”。
- **不添加 AI 署名 / co-author trailer**。

## PR 拆分原则

- 一个 PR 一个关注点：协议修正、契约统一、单章扩写各自独立。
- PR 描述须含：验证命令与结果、影响章节、涉及来源、必要时截图（见 PR 模板）。
- 大改动先开 issue 或提案对齐范围，再动手。

## 不接受的变更类型

- 无来源的时效性数字、版本号、产品状态。
- 把伪代码/未运行代码包装成“已测试”。
- 把网络/第三方不可用当作“通过”。
- 删除现有内容却不证明已被取代、不给迁移说明。
- 绕过门禁（改 checker 让失败变通过，而非修正内容）。

## 版本敏感内容复核规则

“快照内容”（模型/产品/SDK/协议版本/价格/市场格局）必须标注核验日期与下次复核日期，并登记 `references/sources.yaml`。稳定内容（Loop、状态机、幂等、权限模型、评测与观测原则、编排原则）不设过期。复核周期默认 3 个月；过期项在发行门禁中阻断。
