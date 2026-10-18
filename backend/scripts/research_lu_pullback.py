"""[短线] §110 涨停后缩量回踩（limit-up then low-volume pullback）— 预注册 3 版
（/workspace/strategy-research.md §110，结果出来前写定，无网格、不调参）。

数据：lt_data.prices() 窗口加载（LT_START/LT_END），只用 OHLC/amount/volume/pct_chg；MA 由复权收盘 ac 自算，不读 DB ma。
涨停：pre_close = close/(1+pct/100)；lim 20%（688；300/301 且 >=2020-08-24）否则 10%；up = round(pre*(1+lim),2)；
      LU = close >= up-0.01 且 pct >= lim*100-1。
P0（T 收盘）：非北交所、非ST(PIT)、上市>=60 行、amt20>5000万、close>=3、次日有日K、T 非涨停、最近涨停 L 满足 1<=T-L<=5。
S0：pct_T<=0；ac_T<ac_L；min(al[L+1..T]) > al_L；vol_T <= 0.5*vol_L。
  LUPB_MA5    : S0 + al_T<=1.01*MA5 且 ac_T>=0.98*MA5，执行核默认出场（next-open 入场）
  LUPB_OPEN   : S0 + al_T<=1.01*ao_L，执行核默认出场
  LUPB_MA5_H5 : LUPB_MA5 选股，trailing 关闭（−5.5% 止损 + 持满 5 日收盘）
每日按 vol_T/vol_L 升序、代码升序取前 3；此前 5 个交易日内已选的代码跳过。
出场/成本：app.engine.simulation.simulate + execution_policy_for_next_open_entry()（一字涨停不买、跳空>6%不买、
  −5.5% 止损、利润地板 trailing、horizon 5、一字跌停顺延；net 已含 ~0.27%），判定再扣 0.03 → avg_c30。
对照：每个有成交的信号日从当日 P0 随机抽同等只数（同排除近 5 日已选），同出场，20 种子平均。
用法：LT_START=.. LT_END=.. python scripts/research_lu_pullback.py "<phase>,<phase>" out.json
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
from lt_research import PH
from app.engine.backtest import BarInput, RecommendationInput
from app.engine.simulation import simulate
from app.services.entry_policy import execution_policy_for_next_open_entry

c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
NSEED = 20
TOPN = 3
NOREPEAT = 5
EXTRA_COST = 0.03
P_LIVE = execution_policy_for_next_open_entry()
P_H5 = dc.replace(P_LIVE, trailing=dc.replace(P_LIVE.trailing, enabled=False))
VARIANTS = {"LUPB_MA5": ("sig_ma5", "live"), "LUPB_OPEN": ("sig_open", "live"), "LUPB_MA5_H5": ("sig_ma5", "h5")}
POL = {"live": P_LIVE, "h5": P_H5}


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    k = c("ac") / c("close")
    lim = (pl.when(c("code").str.starts_with("688")).then(0.2)
           .when(c("code").str.slice(0, 3).is_in(["300", "301"]) & (c("trade_date") >= "2020-08-24")).then(0.2)
           .otherwise(0.1))
    df = df.with_columns((c("open") * k).alias("ao"), (c("low") * k).alias("al"), lim.alias("lim"),
                         (c("close") / (1 + c("pct_chg") / 100)).alias("pre"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns((((c("pre") * (1 + c("lim"))) * 100).round(0) / 100).alias("up"))
    df = df.with_columns(((c("close") >= c("up") - 0.01) & (c("pct_chg") >= c("lim") * 100 - 1)).fill_null(False).alias("lu"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("ac").rolling_mean(5).over("code").alias("ma5"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    # last limit-up day L (ffill within code) and its values
    df = df.with_columns(
        pl.when(c("lu")).then(c("age")).forward_fill().over("code").alias("L_age"),
        pl.when(c("lu")).then(c("ac")).forward_fill().over("code").alias("ac_L"),
        pl.when(c("lu")).then(c("al")).forward_fill().over("code").alias("al_L"),
        pl.when(c("lu")).then(c("ao")).forward_fill().over("code").alias("ao_L"),
        pl.when(c("lu")).then(c("volume")).forward_fill().over("code").alias("vol_L"))
    df = df.with_columns(pl.when(c("lu")).then(pl.lit(float("inf"))).otherwise(c("al")).alias("al_x"))
    df = df.with_columns(c("al_x").cum_min().over(["code", "L_age"]).alias("minlow_since"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(td, on="trade_date", how="left")
    gap = c("age") - c("L_age")
    p0 = (~c("st") & (c("age") >= 60) & (c("amt20") > 5e7) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt"))
          & ~c("lu") & gap.is_between(1, 5)).fill_null(False)
    s0 = (p0 & (c("pct_chg") <= 0) & (c("ac") < c("ac_L")) & (c("minlow_since") > c("al_L"))
          & (c("volume") <= 0.5 * c("vol_L")) & (c("vol_L") > 0)).fill_null(False)
    sig_ma5 = (s0 & (c("al") <= 1.01 * c("ma5")) & (c("ac") >= 0.98 * c("ma5"))).fill_null(False)
    sig_open = (s0 & (c("al") <= 1.01 * c("ao_L"))).fill_null(False)
    df = df.with_columns(p0.alias("p0"), sig_ma5.alias("sig_ma5"), sig_open.alias("sig_open"),
                         (c("volume") / c("vol_L")).alias("vr"))
    cand = df.filter(c("p0")).select("code", "trade_date", "close", "vr", "sig_ma5", "sig_open")
    bars = {}
    for (code,), part in df.select("code", "trade_date", "open", "high", "low", "close", "volume") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = (part["trade_date"].to_list(), part["open"].to_numpy(), part["high"].to_numpy(),
                      part["low"].to_numpy(), part["close"].to_numpy(), part["volume"].to_numpy())
    days = td["trade_date"].to_list()
    del df
    return cand, bars, days


class Sim:
    def __init__(self, bars):
        self.bars = bars; self.idx = {}; self.cache = {}

    def __call__(self, dt, code, close, mode):
        key = (dt, code, mode)
        if key in self.cache:
            return self.cache[key]
        d, o, h, l, cl, v = self.bars[code]
        if code not in self.idx:
            self.idx[code] = {x: i for i, x in enumerate(d)}
        i = self.idx[code][dt]
        bs = tuple(BarInput(code=code, trade_date=d[j], open=float(o[j]), high=float(h[j]), low=float(l[j]),
                            close=float(cl[j]), volume=float(v[j]) if v[j] == v[j] else None,
                            tradable=bool((v[j] or 0) > 0))
                   for j in range(i + 1, min(i + 13, len(d))))
        out = None
        if bs:
            tr = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=float(close)), bs, POL[mode]).trade
            if tr is not None and tr.executed and tr.exit_date:
                out = (float(tr.net_return_pct), str(tr.exit_reason) if hasattr(tr, "exit_reason") else "")
        self.cache[key] = out
        return out


def pick_days(cand, sig, start, end):
    """-> {dt: [(code, close)]} top-3 by vr asc, code asc; skip codes picked in prior NOREPEAT trading days."""
    x = cand.filter(c(sig) & c("trade_date").is_between(pl.lit(start), pl.lit(end))).sort(["trade_date", "vr", "code"])
    return {dt: list(zip(g["code"].to_list(), g["close"].to_list()))
            for (dt,), g in x.partition_by("trade_date", as_dict=True, maintain_order=True).items()}


def run_picks(byday, days, sim, mode, limit=TOPN):
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
            s = sim(dt, code, close, mode)
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


def control(cand, picks, start, end, days, sim, mode):
    """Random from same-day P0, same count as variant's picks that day, same no-repeat rule; NSEED seeds."""
    pool = cand.filter(c("trade_date").is_between(pl.lit(start), pl.lit(end)))
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
                r = sim(dt, code, close, mode)
                if r is not None:
                    rows.append({"dt": dt, "code": code, "net": r[0]})
        res.append(rows)
    ms = [metrics(r, [d for d in days if start <= d <= end]) for r in res]
    ms = [m for m in ms if m.get("n")]
    if not ms:
        return {"n": 0}
    keys = ("n", "wr", "avg", "pf", "mdd")
    return {k: round(float(np.mean([m[k] for m in ms])), 3) for k in keys}


def main():
    phases = sys.argv[1].split(","); out = Path(sys.argv[2])
    t0 = time.time()
    cand, bars, days = prep()
    print(f"prep {time.time()-t0:.0f}s cand={cand.height}", flush=True)
    sim = Sim(bars)
    res = json.loads(out.read_text()) if out.exists() else {}
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        res[ph] = {"pool_days": int(cand.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))["trade_date"].n_unique()),
                   "pool_avg_per_day": round(cand.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b))).height / max(len(pdays), 1), 1)}
        for v, (sig, mode) in VARIANTS.items():
            byday = pick_days(cand, sig, a, b)
            rows, picks, nblock = run_picks(byday, days, sim, mode)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            m["signal_days"] = len(byday)
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            ctl = control(cand, picks, a, b, days, sim, mode)
            m["control"] = ctl
            m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            res[ph][v] = m
            print(ph, v, json.dumps({k: m[k] for k in m if k != "by_exit"}, ensure_ascii=False), flush=True)
            for r in rows:
                trades.append({"phase": ph, "variant": v, **r})
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if trades:
        pl.DataFrame(trades).write_csv(out.with_suffix("").as_posix() + "_trades_" + "_".join(p.split()[0] for p in phases) + ".csv")
    print(f"done {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
