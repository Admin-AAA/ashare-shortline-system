"""§RV 参考：10-16 起实盘日用线上同一取数函数（只读：tushare + SELECT，不落库）拿全部候选，
按原排序 / 候选排序各取前 5，次日开盘买（开盘涨停剔除），盯市到 END 收盘（复权）。不参与判定。
用法（backend 目录）：PYTHONPATH=. .venv/bin/python scripts/midterm/rv1030_live.py"""
import json, os, statistics as st
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.midterm_service import earnings_accel_candidates
from app.services.midterm_forecast import forecast_beat_candidates
END = "2026-10-29"
OUT = os.path.join(os.path.dirname(__file__), "../../output/research/rv1030/live_resim.json")
db = SessionLocal()
days = [str(r[0])[:10] for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date>='2026-10-16' AND trade_date<=:e ORDER BY 1"), {"e": END})]


def bar(code, d):
    return db.execute(text("SELECT open, close, pct_chg, adj_factor, high FROM daily_kbars WHERE code=:c AND trade_date=:d"), {"c": code, "d": d}).first()


res = {"EA": {}, "FA": {}}
for i, d in enumerate(days[:-1]):
    nd = days[i + 1]
    for S, fn in (("EA", lambda: earnings_accel_candidates(db, d)), ("FA", lambda: forecast_beat_candidates(db, d, store=False))):
        cands = fn()
        if not cands: res[S][d] = {"n_cand": 0 if cands == [] else None}; continue
        rows = []
        for x in cands:
            b0, b1, bl = bar(x["code"], d), bar(x["code"], nd), bar(x["code"], END)
            if not (b0 and b1 and bl): continue
            band = 0.195 if x["code"][0] in "3" or x["code"].startswith("688") else 0.095
            lu = float(b1[0]) >= float(b0[1]) * (1 + band)
            ret = (float(bl[1]) * float(bl[3] or 1)) / (float(b1[0]) * float(b1[3] or 1)) - 1 - 0.003
            rows.append({"code": x["code"], "name": x.get("name"), "score": x["score"], "mv": x["total_mv"], "d0": float(b0[2] or 0), "lu": lu, "ret": ret})
        n = len(rows)
        rk = lambda key, rev: {r["code"]: k for k, r in enumerate(sorted(rows, key=lambda r: (-(r[key]) if rev else r[key], r["code"])))}
        r_d0, r_mv = rk("d0", True), rk("mv", False)
        for r in rows: r["cb"] = (n - r_d0[r["code"]]) / n + (n - r_mv[r["code"]]) / n
        out = {"n_cand": n}
        for v, key, rev in (("base", "score", True), ("d0", "d0", True), ("mv", "mv", False), ("combo", "cb", True)):
            top = sorted(rows, key=lambda r: (-(r[key]) if rev else r[key], r["code"]))[:5]
            bought = [r for r in top if not r["lu"]]
            out[v] = {"picks": [(r["code"], r["name"], round(100 * r["ret"], 2)) for r in top], "rets": [r["ret"] for r in bought]}
        out["all"] = [r["ret"] for r in rows if not r["lu"]]
        res[S][d] = out
        print(S, d, n, {v: round(100 * st.mean(out[v]["rets"]), 2) if out[v]["rets"] else None for v in ("base", "d0", "mv", "combo")},
              "all", round(100 * st.mean(out["all"]), 2) if out["all"] else None, flush=True)
json.dump(res, open(OUT, "w"), indent=1, ensure_ascii=False, default=str)
for S in res:
    for v in ("base", "d0", "mv", "combo", "all"):
        rs = [x for d in res[S].values() if d.get("n_cand") for x in (d[v]["rets"] if v != "all" else d["all"])]
        if rs: print(f"SUM {S} {v}: n={len(rs)} avg={100*st.mean(rs):+.2f} win={100*sum(r>0 for r in rs)/len(rs):.1f}")
