# Excalidraw 示意图 brief — 注意力直觉（附录 1.1 / 图 1-1 的手绘升级版）

目标：用手绘感示意「生成每个新 token 时回看全部上下文并加权」的直觉，比现有 Mermaid 三框图更"讲原理"。

建议画法：
- 一行 token 方块（如 8 个），最右是"正在生成的 token"，用高亮色（#DD6E42）标出。
- 从"正在生成的 token"向左发射多条粗细不一的箭头连到每个旧 token——线越粗=注意力权重越大；刻意让头部与尾部的线更粗、中部更细（呼应 lost-in-the-middle）。
- 右侧手写三条小注：① 两两相连→O(n²)→窗口有限；② 回看的中间结果可缓存→KV Cache；③ 注意力偏头尾→lost in the middle。
- 配色：token 块 #C0D6DF，当前 token #DD6E42，注记文字深灰。
导出：assets/a1-attention.svg；正文以 ![]() 替换或并列现有图 A-1。
