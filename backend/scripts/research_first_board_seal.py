"""[短线] §115 首板封板质量（早封、未炸板、封单大的首板 → T+1 开盘买）— 预注册 3 版
（/workspace/strategy-research.md §115，结果出来前写定，无网格、不调参）。

数据：lt_data.prices() 窗口（LT_START/LT_END）+ limit_list_d 缓存 output/research/sprint4/limit_list/（统一网关，scripts/fetch_limit_list_cache.py）。
涨停 LU：同 §113（stk_limits.up_limit 优先，§110 兜底）。
FB（T 收盘）：非北交所、非ST(PIT)、上市>=60 行、amt20>5000万、close>=3、次日有日K、limit_list_d limit=='U' 且 limit_times==1、
    价格口径 LU_T、T-10..T-1 无 LU、high_T>low_T（非一字）、fd_amount>0、float_mv>0。
  FBQ_H3     : FB ∧ first_time<=100000 ∧ open_times==0 ∧ fd_amount/float_mv>=1%；按 seal_fmv 降序取前 3；horizon 3
  FBQ_H5     : 同选股；horizon 5
  FBQ_AMT_H3 : FB ∧ first_time<=113000 ∧ open_times==0 ∧ fd_amount/amount>=1.0；按 seal_amt 降序取前 3；horizon 3
此前 5 个交易日已选跳过。出场/成本：simulate + execution_policy_for_next_open_entry()（一字涨停/跳空>6% 不买、−5.5% 止损、
  利润地板 trailing、一字跌停顺延；net 已含 ~0.27%），判定再扣 0.03 → avg_c30。
对照（主）：同日 FB 池随机同等只数、同 horizon，20 种子。
用法：LT_START=.. LT_END=.. python scripts/research_first_board_seal.py "R12,EXT,N3" out.json
"""
from __future__ import annotations
import dataclasses as dc, json, os, random, sys, time
from collections import defaultdict
from pathlib import Path
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
import numpy as np
import polars as pl
import lt_data as L
from app.engine.backtest import BarInput, RecommendationInput
from app.engine.simulation import simulate
from app.services.entry_policy import execution_policy_for_next_open_entry

c = pl.col
PH = {"R12": ("2025-10-01", "2026-10-16"), "EXT": ("2025-01-01", "2025-09-30"), "N3": ("2025-01-01", "2026-10-16"),
      "W1": ("2024-01-01", "2024-12-31"), "N3L": ("2024-01-01", "2026-10-16"),
      "W0": ("2022-04-01", "2023-03-31"), "W00": ("2023-04-01", "2023-12-31")}
NSEED = 20
TOPN = 3
NOREPEAT = 5
EXTRA_COST = 0.03
LL_DIR = B / "output/research/sprint4/limit_list"
POL = {3: dc.replace(execution_policy_for_next_open_entry(), horizon_days=3), 5: execution_policy_for_next_open_entry()}
# variant -> (signal column, rank column, horizon)
VARIANTS = {"FBQ_H3": ("sig_q", "seal_fmv", 3), "FBQ_H5": ("sig_q", "seal_fmv", 5), "FBQ_AMT_H3": ("sig_amt", "seal_amt", 3)}


def load_ll():
    a = os.environ.get("LT_START", "2000-01-01").replace("-", ""); b = os.environ.get("LT_END", "2099-12-31").replace("-", "")
    fs = [f for f in sorted(LL_DIR.glob("*.parquet")) if a <= f.stem <= b]
    parts = []
    for f in fs:
        x = pl.read_parquet(f)
        if x.height:
            parts.append(x.select(pl.col("ts_code").cast(pl.Utf8), pl.col("trade_date").cast(pl.Utf8), pl.col("limit").cast(pl.Utf8),
                                  pl.col("limit_times").cast(pl.Float64), pl.col("first_time").cast(pl.Utf8),
                                  pl.col("open_times").cast(pl.Float64), pl.col("fd_amount").cast(pl.Float64),
                                  pl.col("float_mv").cast(pl.Float64), pl.col("amount").cast(pl.Float64).alias("ll_amount")))
    ll = pl.concat(parts).filter(c("limit") == "U")
    ll = ll.with_columns(c("ts_code").str.slice(0, 6).alias("code"),
                         (c("trade_date").str.slice(0, 4) + "-" + c("trade_date").str.slice(4, 2) + "-" + c("trade_date").str.slice(6, 2)).alias("trade_date"),
                         c("first_time").str.replace(r"\.0$", "").cast(pl.Int64, strict=False).alias("ft"))
    return ll.select("code", "trade_date", "limit_times", "ft", "open_times", "fd_amount", "float_mv", "ll_amount") \
        .unique(["code", "trade_date"], keep="first"), len(fs)


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    lim_tab = pl.read_parquet(L.D / "stk_limits.parquet", columns=["code", "trade_date", "up_limit"])
    df = df.join(lim_tab, on=["code", "trade_date"], how="left")
    del lim_tab
    lim = (pl.when(c("code").str.starts_with("688")).then(0.2)
           .when(c("code").str.slice(0, 3).is_in(["300", "301"]) & (c("trade_date") >= "2020-08-24")).then(0.2)
           .otherwise(0.1))
    df = df.with_columns(lim.alias("lim"), (c("close") / (1 + c("pct_chg") / 100)).alias("pre"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns((((c("pre") * (1 + c("lim"))) * 100).round(0) / 100).alias("up"))
    lu_fb = (c("close") >= c("up") - 0.01) & (c("pct_chg") >= c("lim") * 100 - 1)
    lu = pl.when(c("up_limit").is_not_null() & (c("up_limit") > 0)).then(c("close") >= c("up_limit") - 0.005).otherwise(lu_fb)
    df = df.with_columns(lu.fill_null(False).alias("lu"),
                         pl.when(c("up_limit").is_not_null() & (c("up_limit") > 0)).then(1).otherwise(0).alias("off_lim"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    df = df.with_columns(c("lu").cast(pl.Int32).shift(1).rolling_sum(10).over("code").alias("lu_prev10"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(td, on="trade_date", how="left")
    ll, nfiles = load_ll()
    print(f"limit_list files={nfiles} U rows={ll.height}", flush=True)
    df = df.join(ll, on=["code", "trade_date"], how="left")
    base = (~c("st") & (c("age") >= 60) & (c("amt20") > 5e7) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt"))).fill_null(False)
    fb = (base & c("lu") & c("limit_times").is_not_null() & (c("limit_times") == 1) & (c("lu_prev10") == 0)
          & (c("high") > c("low")) & (c("fd_amount") > 0) & (c("float_mv") > 0)).fill_null(False)
    df = df.with_columns(fb.alias("fb"), (c("fd_amount") / c("float_mv")).alias("seal_fmv"),
                         (c("fd_amount") / c("amount")).alias("seal_amt"))
    df = df.with_columns((c("fb") & (c("ft") <= 100000) & (c("open_times") == 0) & (c("seal_fmv") >= 0.01)).fill_null(False).alias("sig_q"),
                         (c("fb") & (c("ft") <= 113000) & (c("open_times") == 0) & (c("seal_amt") >= 1.0)).fill_null(False).alias("sig_amt"))
    # 数据一致性：limit_list U 行中价格口径非涨停的占比（信息）
    chk = df.filter(c("limit_times").is_not_null() & base)
    info = {"ll_U_in_base": chk.height, "ll_U_not_price_lu": int(chk.filter(~c("lu")).height)}
    cand = df.filter(c("fb")).select("code", "trade_date", "close", "amount", "off_lim", "fb", "sig_q", "sig_amt",
                                     "seal_fmv", "seal_amt", "ft", "open_times")
    codes = set(cand["code"].unique().to_list())
    bars = {}
    for (code,), part in df.filter(c("code").is_in(list(codes))).select("code", "trade_date", "open", "high", "low", "close", "volume") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = (part["trade_date"].to_list(), part["open"].to_numpy(), part["high"].to_numpy(),
                      part["low"].to_numpy(), part["close"].to_numpy(), part["volume"].to_numpy())
    days = td["trade_date"].to_list()
    del df
    return cand, bars, days, info


class Sim:
    def __init__(self, bars, pol):
        self.bars = bars; self.pol = pol; self.idx = {}; self.cache = {}

    def __call__(self, dt, code, close):
        key = (dt, code)
        if key in self.cache:
            return self.cache[key]
        d, o, h, l, cl, v = self.bars[code]
        if code not in self.idx:
            self.idx[code] = {x: i for i, x in enumerate(d)}
        i = self.idx[code][dt]
        bs = tuple(BarInput(code=code, trade_date=d[j], open=float(o[j]), high=float(h[j]), low=float(l[j]),
                            close=float(cl[j]), volume=float(v[j]) if v[j] == v[j] else None,
                            tradable=bool((v[j] or 0) > 0))
                   for j in range(i + 1, min(i + 15, len(d))))
        out = None
        if bs:
            tr = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=float(close)), bs, self.pol).trade
            if tr is not None and tr.executed and tr.exit_date:
                out = (float(tr.net_return_pct), str(tr.exit_reason) if hasattr(tr, "exit_reason") else "")
        self.cache[key] = out
        return out


def pick_days(cand, sig, rank, start, end):
    x = cand.filter(c(sig) & c("trade_date").is_between(pl.lit(start), pl.lit(end))) \
        .sort(["trade_date", rank, "code"], descending=[False, True, False])
    return {dt: list(zip(g["code"].to_list(), g["close"].to_list()))
            for (dt,), g in x.partition_by("trade_date", as_dict=True, maintain_order=True).items()}


def run_picks(byday, days, sim, limit=TOPN):
    pos = {d: i for i, d in enumerate(days)}
    last = {}; rows = []; nblock = 0; picks = {}
    for dt in sorted(byday):
        got = []
        for code, close in byday[dt]:
            if len(got) >= limit:
                break
            if code in last and pos[dt] - pos[last[code]] <= NOREPEAT:
                continue
            got.append((code, close))
        picks[dt] = [g[0] for g in got]
        for code, close in got:
            last[code] = dt
            s = sim(dt, code, close)
            if s is None:
                nblock += 1; continue
            rows.append({"dt": dt, "code": code, "net": s[0], "why": s[1]})
    return rows, picks, nblock


def metrics(rows, pdays):
    if not rows:
        return {"n": 0}
    net = np.array([r["net"] for r in rows])
    w, lo = net[net > 0], net[net < 0]
    daily = defaultdict(list)
    for r in rows:
        daily[r["dt"]].append(r["net"])
    cum = np.cumsum([np.mean(daily[d]) if d in daily else 0.0 for d in pdays])
    mdd = float((cum - np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]).min()) if len(cum) else 0.0
    streak = best = 0
    for r in sorted(rows, key=lambda r: (r["dt"], r["code"])):
        streak = streak + 1 if r["net"] < 0 else 0; best = max(best, streak)
    wr = float((net > 0).mean()); aw = float(w.mean()) if len(w) else 0.0; al = float(lo.mean()) if len(lo) else 0.0
    return {"n": int(len(net)), "wr": round(wr * 100, 1), "avg": round(float(net.mean()), 3),
            "avg_c30": round(float(net.mean()) - EXTRA_COST, 3),
            "pf": round(float(w.sum() / max(-lo.sum(), 1e-9)), 2), "sum": round(float(net.sum()), 1),
            "mdd": round(mdd, 1), "max_lose_streak": best, "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "exp_c30": round(wr * aw + (1 - wr) * al - EXTRA_COST, 3), "days": len(daily)}


def control(cand, flag, picks, start, end, days, sim):
    pool = cand.filter(c(flag) & c("trade_date").is_between(pl.lit(start), pl.lit(end)))
    P = {dt: list(zip(g["code"].to_list(), g["close"].to_list()))
         for (dt,), g in pool.partition_by("trade_date", as_dict=True, maintain_order=True).items()}
    pos = {d: i for i, d in enumerate(days)}
    res = []
    for s in range(NSEED):
        rng = random.Random(1000 + s); last = {}; rows = []
        for dt in sorted(picks):
            k = len(picks[dt])
            if not k:
                continue
            avail = [x for x in P.get(dt, []) if not (x[0] in last and pos[dt] - pos[last[x[0]]] <= NOREPEAT)]
            avail.sort()
            for code, close in rng.sample(avail, min(k, len(avail))):
                last[code] = dt
                r = sim(dt, code, close)
                if r is not None:
                    rows.append({"dt": dt, "code": code, "net": r[0]})
        res.append(rows)
    ms = [metrics(r, [d for d in days if start <= d <= end]) for r in res]
    ms = [m for m in ms if m.get("n")]
    if not ms:
        return {"n": 0}
    return {k: round(float(np.mean([m[k] for m in ms])), 3) for k in ("n", "wr", "avg", "pf", "mdd")}


def main():
    phases = sys.argv[1].split(","); out = Path(sys.argv[2])
    t0 = time.time()
    cand, bars, days, info = prep()
    print(f"prep {time.time()-t0:.0f}s cand={cand.height} codes={len(bars)} {info}", flush=True)
    sims = {h: Sim(bars, p) for h, p in POL.items()}
    res = json.loads(out.read_text()) if out.exists() else {}
    res.setdefault("_info", {}).update({k: v for k, v in info.items()})
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        win = cand.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))
        res[ph] = {"pool_fb_avg_per_day": round(win.height / max(len(pdays), 1), 2),
                   "pool_q_avg_per_day": round(win.filter(c("sig_q")).height / max(len(pdays), 1), 2),
                   "pool_amt_avg_per_day": round(win.filter(c("sig_amt")).height / max(len(pdays), 1), 2),
                   "fb_official_limit_share": round(float(win["off_lim"].mean() or 0), 3) if win.height else None}
        for v, (sig, rank, h) in VARIANTS.items():
            sim = sims[h]
            byday = pick_days(cand, sig, rank, a, b)
            rows, picks, nblock = run_picks(byday, days, sim)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            m["signal_days"] = len(byday)
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            ctl = control(cand, "fb", picks, a, b, days, sim)
            m["control"] = ctl
            m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            res[ph][v] = m
            print(ph, v, json.dumps({k: m[k] for k in m if k != "by_exit"}, ensure_ascii=False), flush=True)
            for r in rows:
                trades.append({"phase": ph, "variant": v, **r})
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if trades:
        pl.DataFrame(trades).write_csv(out.with_suffix("").as_posix() + "_trades_" + "_".join(phases) + ".csv")
    print(f"done {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
