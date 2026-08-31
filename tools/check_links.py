#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外链存活检查（nightly/发行前，非 PR 阻断门禁）。

扫描全部 Markdown 的 http(s) 外链（代码块内也算——收录文档的参考资料多在文内），
并发 HEAD（失败回退 GET）探测存活。判定口径：

  - 硬失败：DNS/连接错误、超时（重试一次后仍失败）、404/410；
  - 软告警：403/405/429/999 等疑似反爬（不计失败）、301/302 视为存活；
  - 允许清单（KNOWN_FLAKY）内的域名只告警不失败。

用法：python3 tools/check_links.py [仓库根] [--limit N] [--timeout S]
退出码 0=无硬失败；1=存在硬失败。
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import sys
import urllib.request
from pathlib import Path

TIMEOUT = 12
KNOWN_FLAKY = (
    "doi.org",            # 出版商跳转常拦截脚本
    "openreview.net",
    "dl.acm.org",
    "ieee.org",
    "linkedin.com",
    "twitter.com", "x.com",
    "medium.com",
)
URL_RE = re.compile(r"https?://[^\s<>()\"'\]）】，。；！？*`]+")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) agent-book-linkcheck/1.0"}


def collect(root: Path) -> dict[str, set[str]]:
    urls: dict[str, set[str]] = {}
    for md in sorted(root.rglob("*.md")):
        s = str(md)
        if "/.git/" in s or "node_modules" in s:
            continue
        for m in URL_RE.finditer(md.read_text(encoding="utf-8", errors="ignore")):
            u = m.group(0).rstrip(".,;:")
            if _is_placeholder(u):
                continue
            urls.setdefault(u, set()).add(md.name)
    return urls


def _is_placeholder(url: str) -> bool:
    """文档示例 URL：localhost/回环/内网别名/example 域/裸协议头，不参与探测。"""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if not host or "." not in host:            # mcp_upstream、your-host、裸 https://
        return True
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"):
        return True
    if "example." in host or host.endswith((".local", ".internal", ".test", ".invalid")):
        return True
    return False


def probe(url: str, timeout: int) -> tuple[str, int | str]:
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return url, resp.status
        except urllib.error.HTTPError as e:
            if method == "GET" or e.code not in (405, 403, 400):
                return url, e.code
        except Exception as e:  # DNS/超时/连接
            if method == "GET":
                return url, f"ERR:{type(e).__name__}"
    return url, "ERR:unknown"


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("--") else Path(__file__).resolve().parents[1]
    limit = 0
    timeout = TIMEOUT
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if "--timeout" in argv:
        timeout = int(argv[argv.index("--timeout") + 1])

    urls = collect(root)
    items = sorted(urls)
    if limit:
        items = items[:limit]
    print(f"外链总数 {len(urls)}，本次探测 {len(items)}（并发 16，超时 {timeout}s）")

    hard, soft = [], []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for url, status in ex.map(lambda u: probe(u, timeout), items):
            flaky = any(d in url for d in KNOWN_FLAKY)
            if isinstance(status, int):
                if status in (404, 410) and not flaky:
                    hard.append((url, status))
                elif status >= 400:
                    soft.append((url, status))
            else:  # ERR:*
                # 网络类错误重试一次
                url2, status2 = probe(url, timeout)
                if isinstance(status2, int):
                    if status2 in (404, 410) and not flaky:
                        hard.append((url, status2))
                    elif status2 >= 400:
                        soft.append((url, status2))
                elif flaky:
                    soft.append((url, status2))
                else:
                    hard.append((url, status2))

    for url, st in sorted(soft):
        print(f"  ⚠ {st}  {url}  ← {'/'.join(sorted(urls[url])[:2])}")
    for url, st in sorted(hard):
        print(f"  ✗ {st}  {url}  ← {'/'.join(sorted(urls[url])[:2])}")
    if hard:
        print(f"✗ 外链检查：{len(hard)} 条硬失败，{len(soft)} 条软告警")
        return 1
    print(f"✓ 外链检查：无硬失败（{len(soft)} 条软告警，反爬/权限类不阻断）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
