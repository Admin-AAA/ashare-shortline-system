"""全表三窗覆盖普查 + 最后一张三窗全覆盖表 stk_limits 的板型/距涨停快筛（只读）。

目的：在 L-058~L-081 之后系统回答"是否还有冻结窗有足量数据却没筛过的数据源"。
(1) 枚举所有表，按日期列统计 冻结244/前瞻117/run3 三窗行数，标出冻结>1000 的表。
(2) stk_limits（仅 up_limit/down_limit）衍生：板型宽度 bw=(up-down)/(up+down) 归并
    ST5/main10/gemstar20/bse30；收盘距涨停 dist_up=(up_limit/close-1)*100。
结论（2026-09 跑）：冻结+三窗全覆盖且未测的只剩 stk_limits；板型 20cm-vs-10cm 差
冻结+0.31/前瞻+0.17/run3 -0.57 翻号，dist_up 是 pct_chg 变换且三窗 +0.45/-0.20/-0.02 翻号
（同号 .69/.43/.50），均不达标。冻结0表（capital_flow/concept_sector/news_announcement/
llm_review/stock_quotes 等）无法三窗；事件表（surveys/broker/pledge/repurchases/share_floats/
fina_indicator/hk_holdings）单窗或过稀，覆盖即死。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
DATE_CAND = ["trade_date", "announce_date", "ann_date", "date", "datetime", "end_date",
             "surv_date", "pub_date", "news_date", "day", "dt", "time"]
WINS = [("冻结", "2025-01-15", "2026-01-16"), ("前瞻", "2026-02-09", "2026-08-05"),
        ("run3", "2026-08-07", "2026-09-02")]


def engine():
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (B / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    return create_engine(url)


def coverage(e):
    with e.connect() as c:
        tabs = [x[0] for x in c.execute(text("SHOW TABLES")).all()]
        print(f"共 {len(tabs)} 表；三窗行数（仅列有日期列者）：")
        for t in tabs:
            cols = [x[0] for x in c.execute(text(f"SHOW COLUMNS FROM {t}")).all()]
            dc = next((d for d in DATE_CAND if d in cols), None)
            if not dc:
                continue
            try:
                cnt = [c.execute(text(f"SELECT COUNT(*) FROM {t} WHERE {dc} BETWEEN :a AND :b"),
                                {"a": a, "b": b}).scalar() for _, a, b in WINS]
            except Exception:
                continue
            flag = "  <==冻结有数据" if cnt[0] > 1000 else ""
            print(f"  {t:32s} {dc:13s} 冻结{cnt[0]:>9} 前瞻{cnt[1]:>9} run3{cnt[2]:>8}{flag}")


def stk_limits_screen(e):
    d = pd.read_pickle(OUT / "newfactor_fullmarket.pkl")[["code", "trade_date", "fwd5", "close"]].copy()
    d["code"] = d.code.astype(str); d["trade_date"] = d.trade_date.astype(str)
    with e.connect() as c:
        L = pd.read_sql(text("SELECT code,trade_date,up_limit,down_limit FROM stk_limits "
                             "WHERE trade_date BETWEEN '2025-01-01' AND '2026-09-11'"), c)
    L["code"] = L.code.astype(str); L["trade_date"] = L.trade_date.astype(str)
    L = L[(L.up_limit < 1e5) & (L.down_limit > 0.05)]
    L["bw"] = (L.up_limit - L.down_limit) / (L.up_limit + L.down_limit)
    L["board"] = L.bw.map(lambda x: "ST5" if x < .07 else ("main10" if x < .15 else
                                                           ("gemstar20" if x < .25 else "bse30")))
    m = d.merge(L, on=["code", "trade_date"], how="inner")
    m["dist_up"] = (m.up_limit / m.close - 1) * 100
    print("\n== 板型 fwd5 ==")
    for wn, a, b in WINS:
        x = m[(m.trade_date >= a) & (m.trade_date <= b) & m.fwd5.notna()]
        g = x.groupby("board").fwd5.agg(["mean", "count"])
        print(f" {wn}: " + "  ".join(f'{i}={r["mean"]:+.2f}(n{int(r["count"])})' for i, r in g.iterrows()))
    print("\n== 距涨停 dist_up 五分位 ==")
    for wn, a, b in WINS:
        x = m[(m.trade_date >= a) & (m.trade_date <= b) & m.fwd5.notna() & m.dist_up.notna()].copy()
        x["qb"] = x.groupby("trade_date").dist_up.transform(
            lambda s: pd.qcut(s, 5, labels=False, duplicates="drop"))
        g = x.groupby("qb").fwd5.mean()
        mm = x.groupby([x.trade_date.str[:7], "qb"]).fwd5.mean().unstack()
        dff = mm.iloc[:, -1] - mm.iloc[:, 0]
        same = (np.sign(dff) == np.sign(g.iloc[-1] - g.iloc[0])).mean()
        print(f" {wn}: Qhi-Qlo={g.iloc[-1]-g.iloc[0]:+.3f} 同号{same:.2f} | "
              + " ".join(f"{v:+.2f}" for v in g.values))


if __name__ == "__main__":
    e = engine(); coverage(e); stk_limits_screen(e)
