"""§114 核对：实盘 app/services/midterm_block_trade.py 与研究 lt_research25（BT_prem_h20）逐日对比。
1) 事件层（liq 前）：每个交易日 d，live 经网关拉 block_trade(d) + DB daily_kbars/daily_basic 算出的达标事件
   vs 研究（缓存 parquet + lt_data 行情/周度 basic 快照）同一天的达标事件；
2) 成交层：研究 R12 成交 CSV 中信号日（入场前一交易日）在区间内的成交，是否都在 live 候选里、排名。
用法：PYTHONPATH=. python scripts/midterm/validate_block_trade_live.py 2026-09-01 2026-10-16"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import polars as pl
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import midterm_block_trade as M
import lt_data as L
import lt_research25 as R
c = pl.col

s, e = sys.argv[1], sys.argv[2]
db = SessionLocal()
days = [r[0] for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date>=:a "
                                      "AND trade_date<=:b ORDER BY trade_date"), {"a": "2026-06-01", "b": e})]
# 研究事件（与 lt_research.build 同样的 basic join_asof，再走 lt_research25.bt_features）
px = L.prices(s, e)
b = L.basic(s, e).sort("trade_date")
px = px.sort("trade_date").join_asof(b.rename({"trade_date": "bd"}).with_columns(c("bd").alias("trade_date")),
                                     on="trade_date", by="code", strategy="backward").filter(c("bd").is_not_null())
f = R.bt_features(px).filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)) & c("bt_amt").is_not_null())
ev = f.filter((c("bt_prem") >= 0) & (c("bt_ratio") >= R.RATIO_TH))
research = {d: set(g["code"].to_list()) for (d,), g in ev.group_by("trade_date")}
tr = pl.read_csv(Path(__file__).resolve().parents[2] / "output/research/sprint4/lt25_block_trade_BT_prem_h20_R12_trades.csv",
                 schema_overrides={"code": pl.Utf8})
prev = {days[i + 1]: days[i] for i in range(len(days) - 1)}
tr = tr.with_columns(c("entry").cast(pl.Utf8).map_elements(lambda x: prev.get(x), return_dtype=pl.Utf8).alias("sig"))
out, ok_ev, ok_tr, n_tr, near = [], 0, 0, 0, 0
for d in [x for x in days if s <= x <= e]:
    rows = M.fetch_block_trades(d)
    agg = M.aggregate(rows or [])
    live_ev = set(M.events(agg, M._close_and_fmv(db, d, sorted(agg))))
    res_ev = research.get(d, set())
    cand = M.block_trade_candidates(db, d, rows=rows) or []
    rank = [x["code"] for x in cand]
    t = tr.filter(c("sig") == d)["code"].to_list()
    hit = [x for x in t if x in rank]
    ok_ev += live_ev == res_ev; n_tr += len(t); ok_tr += len(hit)
    diff = sorted(live_ev ^ res_ev)
    dd = f.filter((c("trade_date") == d) & c("code").is_in(diff)).select("code", "bt_prem", "bt_ratio").to_dicts() if diff else []
    out.append({"as_of": d, "n_rows": len(rows or []), "live_events": sorted(live_ev), "research_events": sorted(res_ev),
                "match": live_ev == res_ev, "diff_detail_research": dd, "live_candidates": rank, "research_trades": t,
                "trade_ranks": [rank.index(x) for x in hit]})
    print(d, len(rows or []), "events match" if live_ev == res_ev else
          f"DIFF live-only {sorted(live_ev - res_ev)} research-only {sorted(res_ev - live_ev)} {dd}",
          "| cand", rank[:6], "| research trades", t, flush=True)
print(f"event days matched {ok_ev}/{len(out)}; research trades found in live candidates {ok_tr}/{n_tr}")
json.dump(out, open("/tmp/validate_block_trade_live.json", "w"), indent=1, ensure_ascii=False)
