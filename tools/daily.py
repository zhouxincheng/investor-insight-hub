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

    # 同日幂等：当天日报已归档过就跳过（重复触发不失败、不烧额度）
    _date = time.strftime("%Y%m%d")
    _existing = os.path.join(ROOT, "Reports", time.strftime("%Y"), "report_%s.html" % _date)
    if os.path.exists(_existing):
        print("今日日报已归档（%s），跳过重复运行。" % _existing)
        return 0

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

    # 1.5) 公众号采集（失败不阻断，降级为仅雪球）
    if run([PY, os.path.join(HERE, "collect_gongzhonghao.py"), "--hours", str(a.hours),
            "--out-dir", outdir, "--max-per-account", "3"]).returncode != 0:
        print("[warn] 公众号采集失败，降级为仅雪球")
    gzh_txts = sorted(glob.glob(os.path.join(outdir, "*_公众号文章_*.txt")), key=os.path.getmtime)
    gzh_txt = gzh_txts[-1] if gzh_txts else None

    # 1.6) 合并雪球 + 公众号 → 一份素材
    merged = os.path.join(outdir, "%s_合并素材.txt" % time.strftime("%Y%m%d_%H%M%S"))
    with open(merged, "w", encoding="utf-8") as f:
        f.write(open(txt, encoding="utf-8").read())
        if gzh_txt and os.path.exists(gzh_txt):
            f.write("\n\n" + open(gzh_txt, encoding="utf-8").read())
        else:
            f.write("\n\n# 公众号今日无数据（源站点无更新或采集失败）\n")
    txt = merged

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

    # 4) 可选发布（github 需走本机 Clash 代理，直连会超时）
    if a.publish:
        d = time.strftime("%Y-%m-%d")
        run(["git", "add", "Reports", "reports.json", "data", "tools"])
        run(["git", "commit", "-m", "chore: 自动采集并生成 %s 日报" % d])
        run(["git", "-c", "http.proxy=http://127.0.0.1:7890", "push"])
    print("完成：", html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
