"""§109 核对：实盘 app/services/midterm_incentive.py 与研究 lt_research23（INC_opt_h20）逐日对比。
1) 事件层（liq 前）：每个交易日 as_of，live 拉巨潮 (上一交易日, as_of] 的首次含期权草案 vs 研究缓存事件映射到 sd=as_of 的集合；
2) 成交层：研究 R12 成交 CSV 中信号日（入场前一交易日）在区间内的成交，是否都在 live 候选里，且 live 排名靠前。
用法：PYTHONPATH=. python scripts/midterm/validate_incentive_live.py 2026-09-01 2026-10-16"""
import sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import polars as pl
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import midterm_incentive as M
import lt_research23 as R

s, e = sys.argv[1], sys.argv[2]
db = SessionLocal()
days = [r[0] for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date>=:a "
                                      "AND trade_date<=:b ORDER BY trade_date"), {"a": "2018-01-01", "b": e})]
td = pl.DataFrame({"trade_date": days})
ev = R.load_events().with_columns(c_ann=pl.col("d").dt.strftime("%Y-%m-%d")).rename({"c_ann": "ann"}).sort("ann")
ev = ev.join_asof(td.rename({"trade_date": "sd"}).with_columns(pl.col("sd").alias("ann2")), left_on="ann", right_on="ann2",
                  strategy="forward").drop_nulls("sd").filter(pl.col("opt"))
research = {d: set(g["code"].to_list()) for (d,), g in ev.group_by("sd")}
tr = pl.read_csv(Path(__file__).resolve().parents[2] / "output/research/sprint4/lt23_incentive_INC_opt_h20_R12_trades.csv",
                 schema_overrides={"code": pl.Utf8})
nxt = {days[i]: days[i + 1] for i in range(len(days) - 1)}
prev = {v: k for k, v in nxt.items()}
tr = tr.with_columns(pl.col("entry").cast(pl.Utf8).map_elements(lambda x: prev.get(x), return_dtype=pl.Utf8).alias("sig"))
out, ok_ev, ok_tr, n_tr = [], 0, 0, 0
for d in [x for x in days if s <= x <= e]:
    dates = [r[0] for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
                                           "ORDER BY trade_date DESC LIMIT 251"), {"d": d})]
    d0 = dt.date.fromisoformat(dates[1]) + dt.timedelta(days=1)
    st, en = d0.strftime("%Y%m%d"), d.replace("-", "")
    rows = M.fetch_range((d0 - dt.timedelta(days=1)).isoformat(), d)
    codes = sorted({str(r["code"]) for r in rows if M.is_draft(str(r["title"]))})
    live_ev = {k for k, v in M.first_events(rows, st, en, M._history_dates(codes, st)).items() if v["opt"]}
    res_ev = research.get(d, set())
    cand = M.incentive_candidates(db, d, store=False) or []
    rank = [x["code"] for x in cand]
    t = tr.filter(pl.col("sig") == d)["code"].to_list()
    hit = [c for c in t if c in rank]
    ok_ev += live_ev == res_ev; n_tr += len(t); ok_tr += len(hit)
    out.append({"as_of": d, "live_events": sorted(live_ev), "research_events": sorted(res_ev), "match": live_ev == res_ev,
                "live_candidates": rank, "research_trades": t, "trade_ranks": [rank.index(c) for c in hit]})
    print(d, "events match" if live_ev == res_ev else f"DIFF live-only {sorted(live_ev - res_ev)} research-only {sorted(res_ev - live_ev)}",
          "| cand", rank[:6], "| research trades", t, flush=True)
print(f"event days matched {ok_ev}/{len(out)}; research trades found in live candidates {ok_tr}/{n_tr}")
json.dump(out, open("/tmp/validate_incentive_live.json", "w"), indent=1, ensure_ascii=False)
