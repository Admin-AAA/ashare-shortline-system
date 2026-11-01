"""[短线研究 §376]「大额增持公告后弱反应」INBU_h5。
预注册 docs §376。INB 事件 + 首日 pct∈[-1,+2] 未涨停；按 cr 降序前5；次日开盘买持5。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/inbu_s376.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import importlib.util
from pathlib import Path as P
spec = importlib.util.spec_from_file_location("inb_s340", str(P(__file__).resolve().parent / "inb_s340.py"))
INB = importlib.util.module_from_spec(spec); spec.loader.exec_module(INB)
c = pl.col
NAME, HOLD, K, MAX_NEW = "INBU_h5", 5, 20, 5
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")


def prep():
    df = INB.prep().sort("code", "trade_date")
    df = df.with_columns(c("pct_chg").alias("pct"))
    muted = (c("pct") >= -1.0) & (c("pct") <= 2.0) & ~c("luc")
    # rebuild from raw event days (ev) with muted
    cand = (c("ev").fill_null(False) & c("liq") & muted).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("cr")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns(
        (c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("INBU"),
        pl.when(c("cand")).then(c("cr")).otherwise(-1e9).alias("sc_inbu"),
    ).drop("_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("INBU") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "pct_med": round(float(w["pct"].median()), 2),
                         "cr_med": round(float(w["cr"].median()), 2)})
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "INBU", "sc_inbu", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
