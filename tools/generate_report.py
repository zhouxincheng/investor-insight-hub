#!/usr/bin/env python3
"""generate_report.py — 把采集 txt 生成结构化日报 HTML（DeepSeek 提取 + 固定模板渲染）。

设计：HTML 骨架与样式由本脚本固定（确定性、可被 build_reports_index.py 稳定解析），
LLM 只做语义层——把帖子分类（明确交易/方向观点/仅讨论）、写摘要、抽股票/大V/行业/标签，
以 JSON 返回；脚本再把 JSON 渲染进模板。这样排版不随模型漂移，元数据通过 <head> 里的
report:* meta 直接交给索引器。

用法:
  python generate_report.py --txt <采集txt> --out <输出html> [--config ~/xqauto/gen_config.json]
"""
import argparse
import json
import os
import re
import sys
import urllib.request

DEFAULT_CFG = os.path.join(os.path.expanduser("~"), "xqauto", "gen_config.json")

SYSTEM_PROMPT = """你是投资观点日报的编辑。给你一段雪球大V在近24小时内的发帖（每条含 [名字 时间 type] 正文 链接），
输出**严格 JSON**（不要 markdown 代码块、不要任何解释），字段如下：

{
  "date": "YYYY-MM-DD",           // 采集日期
  "title": "大V每日观点：YYYY年MM月DD日",
  "summary": "本期最重要的观点、动作和风险摘要，≤180字",
  "core_conclusions": [            // 当日核心结论，2-5 条
    {"level": "good|warn|risk|", "headline": "一句结论", "body": "展开，含大V名字与关键标的"}
  ],
  "signals": [                     // 重点交易与推荐信号
    {"tag": "buy|sell|view|disc", "title": "信号名", "body": "说明",
     "influencer": "大V名", "time": "MM-DD HH:MM"}
  ],
  "market_table": [                // 市场与行业判断（≤8 行）
    {"industry": "行业/主题", "tag": "buy|sell|view|disc", "conclusion": "结论", "sources": "大V名"}
  ],
  "discussion": [                  // 仅讨论，不作为交易信号
    {"title": "话题", "body": "说明"}
  ],
  "tags": ["...", "..."],
  "industries": ["...", "..."],
  "stocks": ["紫金矿业", "腾讯控股"],
  "influencers": ["雪月霜", "但斌"]
}

分类口径（务必遵守）：
- buy：清楚披露本人已发生的买入/加仓；sell：本人卖出/减仓；
- view：有清晰看多/看空/目标价/行业选择，但无本人成交；
- disc：转发、方法论、历史状态、假设或不完整材料。
- 转发（type=2 / 提到「转发」）不算本人交易，一律归 view 或 disc；
- 组合清仓但不给发生时间，不计当日卖出；
- 只依据帖子里明确写的内容，不要脑补成交，不要补推目标价或估值结论。
- influencers 只列真正发帖/被引用的大V名，用「、」连接；stocks 只列帖子里点名且与结论相关的标的。"""


def call_deepseek(cfg, user_text, max_tokens=4000):
    body = json.dumps({
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }).encode()
    url = cfg["deepseek_base_url"] + "/chat/completions"
    req = urllib.request.Request(url, data=body)
    req.add_header("Authorization", "Bearer " + cfg["deepseek_api_key"])
    req.add_header("Content-Type", "application/json")
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        r = op.open(req, timeout=120)
    except Exception:
        op = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}))
        r = op.open(req, timeout=120)
    d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"]


def parse_json(text):
    """从模型输出里稳健取出第一个 JSON 对象。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TAG_LABEL = {"buy": "明确买入", "sell": "明确卖出", "view": "方向观点", "disc": "仅讨论"}

TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="report:date" content="{date}">
<meta name="report:title" content="{title}">
<meta name="report:summary" content="{summary}">
<meta name="report:tags" content="{tags}">
<meta name="report:industries" content="{industries}">
<meta name="report:stocks" content="{stocks}">
<meta name="report:influencers" content="{influencers}">
<meta name="report:sources" content="雪球">
<style>
:root{{--bg:#f5f7fb;--card:#fff;--ink:#172033;--muted:#667085;--blue:#2457c5;--green:#13795b;--red:#b42318;--amber:#9a6700}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 18px 60px}}.hero{{background:linear-gradient(135deg,#183b82,#3478d4);color:#fff;border-radius:18px;padding:30px 32px}}
h1{{margin:0 0 8px;font-size:30px}}.subtitle{{opacity:.88}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:22px}}
.stat{{background:#ffffff1a;border:1px solid #ffffff2b;border-radius:12px;padding:12px 14px}}.stat b{{display:block;font-size:24px}}.stat span{{font-size:12px;opacity:.85}}
section{{background:var(--card);border:1px solid #e6eaf0;border-radius:14px;margin-top:18px;padding:22px 24px}}h2{{margin:0 0 14px;font-size:21px;border-left:4px solid var(--blue);padding-left:10px}}
.callout{{background:#f0f5ff;border-left:4px solid var(--blue);padding:13px 16px;border-radius:8px;margin:10px 0 4px}}
.warn{{background:#fff8e6;border-left-color:#d99a00}}.riskbox{{background:#fff2f0;border-left-color:var(--red)}}.good{{background:#eefaf5;border-left-color:var(--green)}}
table{{width:100%;border-collapse:collapse;margin-top:10px}}th,td{{border-bottom:1px solid #e6eaf0;padding:11px 9px;vertical-align:top;text-align:left}}th{{background:#f7f9fc}}
.tag{{display:inline-block;border-radius:999px;padding:2px 8px;margin-right:5px;font-size:12px;font-weight:600}}
.buy{{color:var(--green);background:#e7f6ef}}.sell{{color:var(--red);background:#ffebe8}}.view{{color:var(--blue);background:#eaf0ff}}.disc{{color:var(--amber);background:#fff4d6}}
.discussion{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px}}.discussion-item{{padding:13px 0;border-bottom:1px solid #e6eaf0}}
.footer{{color:var(--muted);font-size:12px;margin-top:18px;text-align:center}}.note{{color:var(--muted);font-size:13px}}
</style></head><body><div class="wrap">
<header class="hero">
<div class="subtitle">投资观点监测日报</div><h1>{title}</h1>
<div class="subtitle">统计区间：严格近24小时</div>
<div class="grid" aria-label="采集统计">
<div class="stat"><b>{main_count}</b><span>雪球主贴</span></div>
<div class="stat"><b>{active_count}</b><span>活跃大V</span></div>
<div class="stat"><b>{buy_count}</b><span>明确买卖/仓位调整</span></div>
<div class="stat"><b>{stock_count}</b><span>涉及标的</span></div>
</div></header>
<section><h2>当日核心结论</h2>{core_html}</section>
<section><h2>重点交易与推荐信号</h2>{signals_html}</section>
<section><h2>市场与行业判断</h2>{table_html}</section>
<section><h2>仅讨论，不作为交易信号</h2>{disc_html}</section>
<div class="legend">{legend_html}</div>
<div class="footer">数据源：{src_name} · 本文为信息整理，不构成投资建议</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="生成日报 HTML")
    ap.add_argument("--txt", required=True, help="采集 txt")
    ap.add_argument("--out", required=True, help="输出 html 路径")
    ap.add_argument("--config", default=DEFAULT_CFG)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    txt = open(a.txt, encoding="utf-8").read()
    if len(txt) > 50000:
        txt = txt[:50000]  # 控 token
    src_name = os.path.basename(a.txt)

    content = call_deepseek(cfg, txt)
    d = parse_json(content)
    if d is None:
        print("模型未返回 JSON，原始输出前 400 字：", content[:400])
        return 2

    # 统计：按「帖子块」计数（每块以 [名字 开头），不能用 URL 出现次数——正文里也可能含链接
    main_count = sum(1 for l in txt.splitlines() if l.lstrip().startswith("["))
    infs = d.get("influencers") or []
    tags = "，".join(d.get("tags") or [])
    inds = "，".join(d.get("industries") or [])
    stocks = ";".join(d.get("stocks") or [])
    inf_str = "，".join(infs)

    def callout_html():
        out = []
        for c in (d.get("core_conclusions") or []):
            lvl = c.get("level") or ""
            out.append('<div class="callout %s"><strong>%s</strong>%s</div>'
                       % (esc(lvl), esc(c.get("headline")), esc(c.get("body"))))
        return "\n".join(out) or '<p class="note">无。</p>'

    def signals_html():
        out = []
        for s in (d.get("signals") or []):
            out.append('<div class="callout"><span class="tag %s">%s</span><strong>%s</strong> %s'
                       '<span class="note"> %s %s</span></div>'
                       % (esc(s.get("tag")), TAG_LABEL.get(s.get("tag"), ""),
                          esc(s.get("title")), esc(s.get("body")),
                          esc(s.get("influencer")), esc(s.get("time"))))
        return "\n".join(out) or '<p class="note">无。</p>'

    def table_html():
        rows = ["<table><thead><tr><th>行业/主题</th><th>方向</th><th>结论</th><th>主要来源</th></tr></thead><tbody>"]
        for r in (d.get("market_table") or []):
            rows.append('<tr><td>%s</td><td><span class="tag %s">%s</span></td><td>%s</td><td>%s</td></tr>'
                        % (esc(r.get("industry")), esc(r.get("tag")), TAG_LABEL.get(r.get("tag"), ""),
                           esc(r.get("conclusion")), esc(r.get("sources"))))
        rows.append("</tbody></table>")
        return "\n".join(rows) if len(rows) > 2 else '<p class="note">无。</p>'

    def disc_html():
        out = []
        for r in (d.get("discussion") or []):
            out.append('<div class="discussion-item"><strong>%s</strong>%s</div>'
                       % (esc(r.get("title")), esc(r.get("body"))))
        return ('<div class="discussion">' + "".join(out) + "</div>") if out else '<p class="note">无。</p>'

    legend_html = ("<span class='tag buy'>明确交易</span>清楚披露本人已发生的买卖/加减仓 · "
                   "<span class='tag view'>方向观点</span>有明确看多/看空但无成交 · "
                   "<span class='tag disc'>仅讨论</span>转发/方法论/历史状态/假设")

    buy_count = sum(1 for s in (d.get("signals") or []) if s.get("tag") in ("buy", "sell"))
    html = TEMPLATE.format(
        title=esc(d.get("title")), date=esc(d.get("date")), summary=esc(d.get("summary")),
        tags=esc(tags), industries=esc(inds), stocks=esc(stocks), influencers=esc(inf_str),
        main_count=main_count, active_count=len(infs), buy_count=buy_count,
        stock_count=len(d.get("stocks") or []),
        core_html=callout_html(), signals_html=signals_html(), table_html=table_html(),
        disc_html=disc_html(), legend_html=legend_html, src_name=esc(src_name))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成 %s（%d 字节，主贴 %d，大V %d）" % (a.out, len(html), main_count, len(infs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
