#!/usr/bin/env bash
# 渲染 diagrams/d2/*.d2 → assets/*.svg
# D2 默认字体不含中文，必须用 --font-regular 指定一个「单体 TTF/OTF」中文字体（.ttc 不被接受）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CJK="${D2_CJK_FONT:-$(fc-list :lang=zh file 2>/dev/null | grep -oE '/[^:]+\.(ttf|otf)' | head -1)}"
[ -n "$CJK" ] || { echo "未找到中文 TTF/OTF 字体；设 D2_CJK_FONT=/path/to/cjk.ttf"; exit 1; }
echo "使用中文字体: $CJK"
mkdir -p "$ROOT/assets"
for f in "$ROOT"/diagrams/d2/*.d2; do
  out="$ROOT/assets/$(basename "${f%.d2}").svg"
  d2 --font-regular "$CJK" "$f" "$out"
  echo "  ✓ $(basename "$out")"
done
