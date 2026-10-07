#!/usr/bin/env python3
"""daily.py — 一键跑整条链路：采集 → 生成 → 归档 → 重建索引（可选 git 发布）。

本机 Hermes 每天跑一次：
  python tools/daily.py --hours 24 [--publish]

前置：已登录雪球并导出登录态（tools/save_xq_auth.py），DeepSeek key 在 ~/xqauto/gen_config.json。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def run(args, cwd=ROOT):
    print(">>", " ".join(os.path.basename(a) if os.path.isabs(a) and os.path.dirname(a) == HERE else a for a in args), flush=True)
    return subprocess.run(args, cwd=cwd)


def main():
    ap = argparse.ArgumentParser(description="雪球观点日报：采集→生成→归档→索引")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "collect_out"))
    ap.add_argument("--publish", action="store_true", help="工作区干净时 git commit + push")
    a = ap.parse_args()

    outdir = os.path.abspath(a.out_dir)
    os.makedirs(outdir, exist_ok=True)

    # 1) 采集
    if run([PY, os.path.join(HERE, "collect_xueqiu.py"), "--hours", str(a.hours),
            "--out-dir", outdir]).returncode != 0:
        return 1
    txts = sorted(glob.glob(os.path.join(outdir, "*_雪球主贴_*.txt")), key=os.path.getmtime)
    if not txts:
        print("无采集结果，中止。")
        return 2
    txt = txts[-1]

    # 2) 生成
    date = time.strftime("%Y%m%d")
    html = os.path.join(outdir, "report_%s.html" % date)
    if run([PY, os.path.join(HERE, "generate_report.py"), "--txt", txt, "--out", html]).returncode != 0:
        return 3

    # 3) 归档 + 重建索引（放进 Inbox，交给 import-inbox --write）
    inbox = os.path.join(ROOT, "Inbox")
    os.makedirs(inbox, exist_ok=True)
    shutil.copy(html, os.path.join(inbox, os.path.basename(html)))
    if run([PY, os.path.join(HERE, "build_reports_index.py"), "--root", ROOT,
            "--import-inbox", "--write"]).returncode != 0:
        return 4

    # 4) 可选发布
    if a.publish:
        d = time.strftime("%Y-%m-%d")
        run(["git", "add", "Reports", "reports.json", "data", "tools", "collect_out"])
        run(["git", "commit", "-m", "chore: 自动采集并生成 %s 日报" % d])
        run(["git", "push"])
    print("完成：", html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
