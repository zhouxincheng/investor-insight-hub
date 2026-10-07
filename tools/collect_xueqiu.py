#!/usr/bin/env python3
"""collect_xueqiu.py — 雪球大V「近 N 小时」主贴采集（登录态 cookie 直连，不开浏览器）。

数据路验证（2026-10）：雪球对无头/自动化有 WAF 与滑块，但**登录态 + 旧端点**可稳定取数：
  GET https://xueqiu.com/statuses/user_timeline.json?user_id=<uid>&page=N
返回 JSON：{count, statuses:[{id, user_id, created_at(ms), description, title, type, ...}]}
/v4/statuses/user_timeline.json 仍被 WAF 拦，不要用。

依赖：登录态 JSON（由有头浏览器登录后用 storage_state 导出）。获取方式：
  1. 起一个可见 Chrome 登录雪球（或复用现有 9223 实例）；
  2. 运行 tools/save_xq_auth.py 把登录态存成 ~/xqauto/xq_auth.json。
采集本身只读这个文件，不需要浏览器在跑；cookie 过期后重新登录并再存一次即可。

用法:
  python collect_xueqiu.py --hours 24 --out-dir . --auth ~/xqauto/xq_auth.json
输出:
  <YYYYMMDD_HHMMSS>_雪球主贴_近24小时.txt
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS = os.path.join(HERE, "..", "data", "xueqiu_accounts.json")
DEFAULT_AUTH = os.path.join(os.path.expanduser("~"), "xqauto", "xq_auth.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def cookie_header(path):
    st = json.load(open(path, encoding="utf-8"))
    return "; ".join("%s=%s" % (c["name"], c["value"])
                     for c in st.get("cookies", [])
                     if "xueqiu" in (c.get("domain") or ""))


def fetch_timeline(uid, page, cookie, retries=3):
    """带重试的取数。雪球对连续请求会限流（返回空/非 JSON），退避重试。
    空列表或非 JSON 都视为「可能被限流」，最多 retries 次。"""
    last = None
    for i in range(retries):
        try:
            url = ("https://xueqiu.com/statuses/user_timeline.json"
                   "?user_id=%s&page=%s" % (uid, page))
            req = urllib.request.Request(url)
            req.add_header("Cookie", cookie)
            req.add_header("User-Agent", UA)
            req.add_header("X-Requested-With", "XMLHttpRequest")
            req.add_header("Referer", "https://xueqiu.com/u/%s" % uid)
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            raw = op.open(req, timeout=30).read().decode("utf-8", "ignore")
            if not raw.lstrip().startswith("{"):
                last = RuntimeError("非 JSON 响应（可能被限流）: %s" % raw[:60])
                raise last
            d = json.loads(raw)
            if "statuses" not in d:
                last = RuntimeError("响应无 statuses 字段: %s" % json.dumps(d)[:80])
                raise last
            return d
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def main():
    ap = argparse.ArgumentParser(description="雪球大V主贴采集")
    ap.add_argument("--hours", type=float, default=24, help="回看窗口（小时）")
    ap.add_argument("--out-dir", default=".", help="txt 输出目录")
    ap.add_argument("--auth", default=DEFAULT_AUTH, help="登录态 JSON")
    ap.add_argument("--max-pages", type=int, default=20, help="每账号最多翻页数")
    a = ap.parse_args()

    accounts = json.load(open(ACCOUNTS, encoding="utf-8"))
    cookie = cookie_header(a.auth)
    cutoff_ms = (time.time() - a.hours * 3600) * 1000
    out_lines = []
    total = 0
    for acc in accounts:
        uid, name = acc["id"], acc["name"]
        got = 0
        for page in range(1, a.max_pages + 1):
            try:
                d = fetch_timeline(uid, page, cookie)
            except Exception as e:
                print("[%s] 第 %s 页失败（已重试）: %s" % (name, page, repr(e)[:80]))
                break
            statuses = d.get("statuses") or []
            if not statuses:
                if page == 1:                       # 首页为空多半是被限流，重试整个账号一次
                    print("[%s] 第 1 页为空，疑似限流，退避重试一次…" % name)
                    time.sleep(8)
                    try:
                        d = fetch_timeline(uid, 1, cookie, retries=4)
                        statuses = d.get("statuses") or []
                    except Exception as e:
                        print("[%s] 重试仍失败: %s" % (name, repr(e)[:80]))
                        statuses = []
                if not statuses:
                    break
            # 注意：时间线第一项可能是置顶帖（几个月前的），不能按顺序 break；
            # 过滤整页在窗口内的帖子，只在「整页最新都比窗口旧」时才停止翻页。
            in_window = [s for s in statuses if (s.get("created_at") or 0) >= cutoff_ms]
            for s in in_window:
                ts = time.strftime("%m-%d %H:%M", time.localtime((s.get("created_at") or 0) / 1000))
                text = (s.get("description") or s.get("title") or "").replace("\n", " ").strip()
                sid = s.get("id")
                out_lines.append("[%s] %s [%s]\n%s\nhttps://xueqiu.com/%s/%s\n"
                                 % (name, ts, s.get("type"), text, uid, sid))
                got += 1
            total += len(in_window)
            newest_on_page = max((s.get("created_at") or 0) for s in statuses)
            if newest_on_page < cutoff_ms or len(statuses) < 20:
                break
            time.sleep(0.8)
        print("[%s] 主贴 %d 条" % (name, got))
        time.sleep(1.5)

    ts = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, "%s_雪球主贴_近%d小时.txt" % (ts, int(a.hours)))
    header = ("# 雪球主贴采集 · 近%d小时（截至 %s）\n"
              "# 账号 %d 个 · 主贴 %d 条\n\n" % (int(a.hours),
              time.strftime("%Y-%m-%d %H:%M:%S"), len(accounts), total))
    with open(out, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(out_lines))
    print("\n共 %d 条，已写 %s" % (total, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
