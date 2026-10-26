"""§335 可检验性核对（预注册写定）：现役 4 短线 §282 BASE 逐笔里，信号日命中 §332「低波股 z5≤−2.5」条件的笔数。
条件与 lvz_fdv_s332.py 完全相同（候选 cand_lvz，不限每日前 5）。命中 0 → 跳过；12m 命中 <10 → 不可检验，跳过。
用法：cd backend/scripts/midterm && LT_START=2022-01-01 LT_END=2026-10-26 PYTHONPATH=../..:. python s335_overlap.py"""
import json, os, sys
import polars as pl
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../shortline_research"))
from lvz_fdv_s332 import prep
c = pl.col
B = os.path.join(os.path.dirname(__file__), "../../output/research")
base = json.load(open(os.path.join(B, "s282/short_trades_base.json")))
df = prep().select("code", "trade_date", "cand_lvz", "lv", "vrk", "z5")
out = {}
for w, rows in base.items():
    t = pl.DataFrame({"code": [r["code"] for r in rows], "trade_date": [r["dt"] for r in rows],
                      "sid": [r["sid"] for r in rows], "net": [r["net"] for r in rows]})
    j = t.join(df, on=["code", "trade_date"], how="left")
    hit = j.filter(c("cand_lvz").fill_null(False))
    out[w] = {"trades": t.height, "matched_rows": int(j["lv"].is_not_null().sum()), "hits": hit.height,
              "hits_by_sid": dict(hit.group_by("sid").len().iter_rows()),
              "in_lowvol30": int((j["vrk"] <= 0.30).fill_null(False).sum()),
              "z5_le_-2.5_any_vol": int((j["z5"] <= -2.5).fill_null(False).sum()),
              "hit_avg_net": round(float(hit["net"].mean()), 3) if hit.height else None}
    print(w, out[w], flush=True)
od = os.path.join(B, "s335"); os.makedirs(od, exist_ok=True)
json.dump(out, open(os.path.join(od, "overlap.json"), "w"), ensure_ascii=False, indent=1)
tot = sum(v["hits"] for v in out.values())
print("§335 判定：", "命中 0 → 否决没有作用，跳过" if tot == 0 else ("12m 命中 <10 → 不可检验，跳过" if out["12m"]["hits"] < 10 else "可检验，另行按 §276 框架跑"))
