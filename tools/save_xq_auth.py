#!/usr/bin/env python3
"""save_xq_auth.py — 把已登录的雪球浏览器登录态导出成 JSON（供 collect_xueqiu.py 直连）。

用法: python save_xq_auth.py [--cdp http://localhost:9223] [--out ~/xqauto/xq_auth.json]
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "xqauto", "xq_auth.json")


def main():
    ap = argparse.ArgumentParser(description="导出雪球登录态")
    ap.add_argument("--cdp", default="http://localhost:9223")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(a.cdp)
        ctx = b.contexts[0] if b.contexts else b.new_context()
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        ctx.storage_state(path=a.out)
        names = [c["name"] for c in ctx.cookies() if "xueqiu" in (c.get("domain") or "")]
        print(json.dumps({"saved": a.out, "cookies": len(names), "names": names},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
