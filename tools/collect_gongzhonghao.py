#!/usr/bin/env python3
"""collect_gongzhonghao.py — 公众号文章采集（第三方 Hugo 镜像站）。

数据源：一组公众号 Hugo 静态镜像站（fugay.com / ucaihong.com / ...），每站对应一个公众号，
结构一致：
- RSS index.xml：标题 + AI 摘要 + 链接 + pubDate（**无全文**）
- 文章页 /YYYY/MM/DD-<slug>/：<main> 里是正文；第一个 <blockquote> 是 AI 摘要（跳过），
  首个 <img> 是封面图（跳过），其余 <p> 是全文正文。

用法:
  python collect_gongzhonghao.py --hours 24 --out-dir . --max-per-account 3
输出:
  <YYYYMMDD_HHMMSS>_公众号文章_近24小时.txt
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS = os.path.join(HERE, "..", "data", "gongzhonghao_accounts.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def fetch(url, retries=2, timeout=40):
    """带重试+退避的取数。镜像站偶发超时/断连，最多 retries 次。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", UA)
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return op.open(req, timeout=timeout).read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def rss_items(base):
    """拉 index.xml，返回 [{title, link, ts}]，按 pubDate 解析 ts。"""
    xml = fetch(base + "/index.xml")
    items = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        it = m.group(1)
        title = re.search(r"<title>(.*?)</title>", it, re.S)
        link = re.search(r"<link>(.*?)</link>", it, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        if not (title and link):
            continue
        ts = 0
        if pub:
            try:
                ts = parsedate_to_datetime(pub.group(1).strip()).timestamp()
            except Exception:
                ts = 0
        items.append({"title": html.unescape(title.group(1).strip()),
                      "link": link.group(1).strip(), "ts": ts})
    return items


def article_text(link):
    """抓文章页，抽 <main> 里的全文正文（去摘要 blockquote、去封面图）。"""
    h = fetch(link)
    m = re.search(r"<main>(.*?)</main>", h, re.S)
    if not m:
        m = re.search(r"<article>(.*?)</article>", h, re.S)  # 兜底：无 <main> 用 <article>
    if not m:
        return ""
    body = m.group(1)
    body = re.sub(r"<blockquote>.*?</blockquote>", "", body, flags=re.S)  # AI 摘要，跳过
    body = re.sub(r"<img[^>]*>", "", body)                               # 封面图
    body = re.sub(r"<[^>]+>", "\n", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return body


def collect_account(acc, cutoff, max_per):
    """单站采集：RSS 发现 → 文章页全文。返回 (name, blocks, err)。"""
    name = acc["name"]
    base = "https://www." + acc["domain"]
    try:
        items = rss_items(base)
    except Exception as e:
        return name, [], "RSS失败: %s" % repr(e)[:60]
    recent = [it for it in items if it["ts"] >= cutoff]
    recent.sort(key=lambda it: -it["ts"])
    recent = recent[:max_per]
    blocks = []
    for it in recent:
        try:
            txt = article_text(it["link"])
        except Exception:
            txt = ""
        date = time.strftime("%Y-%m-%d", time.localtime(it["ts"])) if it["ts"] else "?"
        blocks.append("【公众号·%s】 %s\n标题：%s\n%s\n来源：%s\n"
                      % (name, date, it["title"], txt, it["link"]))
        time.sleep(1.0)  # 同站文章间小间隔
    return name, blocks, None


def main():
    ap = argparse.ArgumentParser(description="公众号文章采集（Hugo 镜像站，并发）")
    ap.add_argument("--hours", type=float, default=24, help="回看窗口（小时）")
    ap.add_argument("--out-dir", default=".", help="txt 输出目录")
    ap.add_argument("--max-per-account", type=int, default=3, help="每号每天最多篇数")
    a = ap.parse_args()

    accounts = [x for x in json.load(open(ACCOUNTS, encoding="utf-8"))
                if x.get("enabled", True)]
    cutoff = time.time() - a.hours * 3600

    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(collect_account, acc, cutoff, a.max_per_account): acc
                for acc in accounts}
        for fut in as_completed(futs):
            name, blocks, err = fut.result()
            results[name] = (blocks, err)

    out_lines = []
    total = 0
    for acc in accounts:  # 按配置顺序输出
        name = acc["name"]
        blocks, err = results.get(name, ([], "未完成"))
        if err:
            print("[%s] %s" % (name, err))
        else:
            print("[%s] 文章 %d 篇" % (name, len(blocks)))
            out_lines.extend(blocks)
            total += len(blocks)

    ts = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, "%s_公众号文章_近%d小时.txt" % (ts, int(a.hours)))
    header = ("# 公众号文章采集 · 近%d小时（截至 %s）\n"
              "# 公众号 %d 个 · 文章 %d 篇\n\n"
              % (int(a.hours), time.strftime("%Y-%m-%d %H:%M:%S"), len(accounts), total))
    with open(out, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(out_lines))
    print("\n共 %d 篇，已写 %s" % (total, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
