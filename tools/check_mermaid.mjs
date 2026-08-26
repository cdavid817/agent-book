// 校验全书 Markdown 中的 ```mermaid 块能否被 mermaid.js 解析（GitHub 渲染的近似口径）。
// 依赖（不入库，本地/CI 按需安装）：npm install --no-save mermaid jsdom
// 运行：node tools/check_mermaid.mjs   （在仓库根目录）
// 背景：check-diagrams 只覆盖 D2 图源；mermaid 的三类高频翻车点——
//   ① sequenceDiagram 文本中的半角分号 ";"（被当作语句终止符）
//   ② stateDiagram-v2 的 class 语句不接受非 ASCII 状态 ID（需 ASCII id + "id: 中文标签"）
//   ③ xychart 轴分类值含非 ASCII 时必须加引号
// 均在 2026-08 的审读中实际发生过（第 8/10/18 章与附录 A），本脚本即其回归门禁。
import { readFileSync, readdirSync, statSync } from "fs";
import { join, relative } from "path";
import { JSDOM } from "jsdom";

const ROOT = process.cwd();
const SKIP = new Set(["node_modules", ".git", "assets", "diagrams"]);

function* mdFiles(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (!SKIP.has(name)) yield* mdFiles(p);
    } else if (name.endsWith(".md")) {
      yield p;
    }
  }
}

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.DOMPurify = { sanitize: (x) => x, addHook: () => {} };
const mermaid = (await import("mermaid")).default;
mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

let total = 0, failed = 0;
for (const file of mdFiles(ROOT)) {
  const text = readFileSync(file, "utf8");
  const blocks = [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)];
  for (let i = 0; i < blocks.length; i++) {
    total++;
    try {
      await mermaid.parse(blocks[i][1]);
    } catch (e) {
      failed++;
      const msg = String(e.message || e).split("\n").slice(0, 3).join(" | ");
      console.log(`✗ ${relative(ROOT, file)} #${i + 1}: ${msg}`);
    }
  }
}
if (failed) {
  console.log(`✗ Mermaid 校验失败：${total} 块中 ${failed} 块无法解析`);
  process.exit(1);
}
console.log(`✓ Mermaid 校验通过：${total} 块全部可解析`);
