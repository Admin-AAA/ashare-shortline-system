"""[短线] §125 NR7 / 波动收敛后放量突破（上升趋势中的高流动性股）— 预注册 N7_h3 / N7_h5 / N7_big_h5
（/workspace/strategy-research.md §125，2026-10-19 11:26 CST 结果出来前写定，无网格、不调参）。

复权：ac = pct_chg 逐股累乘；f = ac/close；ah = high*f、al = low*f；rng = ah - al。
信号（T 收盘）：NR7 = rng_{T-1} > 0 且 = min(rng_{T-7..T-1})、T-1 有量；ac_T > ah_{T-1}；
  volume_T >= 1.5 * mean(volume_{T-20..T-1})；ac_T > MA20 > MA60（ac 自算）；T 非涨停（pct < lim-0.2）。
股池 U：非北交所、非ST(PIT)、面板内>=60 行、amt20 > 1e8、close >= 3、次日有日K。
  N7_h3 / N7_h5：每日按 volume_T/avg20 降序（平手代码升序）取前 3，T+1 开盘买，horizon 3 / 5。
  N7_big_h5：同上但限 T 日流通市值（周频 basic circ_mv 按价格折算）全市场前 300，horizon 5。
执行：simulate + execution_policy_for_next_open_entry()（一字涨停/跳空>6% 不买、-5.5% 止损、利润地板 trailing、一字跌停顺延）。
net 已含 ~0.27%，判定再扣 0.03 → avg_c30。此前 5 个交易日已选跳过。
对照：同日从 U ∧ 上升趋势 ∧ T 非涨停（big 再限市值前 300）随机同等只数、同出场，20 种子。
只读本地 parquet（lt_data），不调用 promax / 网关，不连库。
用法：LT_START=.. LT_END=.. python scripts/research_nr7_breakout.py "R12,N3,EXT,W1" out.json
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
PH = {"R12": ("2025-10-01", "2026-10-16"), "N3": ("2023-10-01", "2026-10-16"), "EXT": ("2025-01-01", "2025-09-30"),
      "W1": ("2024-01-01", "2024-12-31"), "W0": ("2022-04-01", "2023-03-31"), "W00": ("2023-04-01", "2023-12-31"),
      "P19": ("2019-01-01", "2019-12-31"), "P20": ("2020-01-01", "2020-12-31"), "P21": ("2021-01-01", "2021-12-31")}
NSEED = 20
TOPN = 3
NOREPEAT = 5
EXTRA_COST = 0.03
MIN_AGE = 60
MIN_AMT20 = 1e8
VOL_MULT = 1.5
MV_TOP = 300
_P = execution_policy_for_next_open_entry()
POL = {"h3": dc.replace(_P, horizon_days=3), "h5": dc.replace(_P, horizon_days=5)}
VARIANTS = {"N7_h3": ("h3", False), "N7_h5": ("h5", False), "N7_big_h5": ("h5", True)}


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date") \
        .select("code", "trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg")
    df = df.with_columns((1 + c("pct_chg").fill_null(0) / 100).cum_prod().over("code").alias("ac"))
    df = df.with_columns((c("ac") / c("close")).alias("_f"))
    df = df.with_columns((c("high") * c("_f")).alias("ah"), ((c("high") - c("low")) * c("_f")).alias("rng"))
    o = lambda e: e.over("code")  # noqa: E731
    df = df.with_columns(
        o(pl.int_range(pl.len())).alias("age"),
        o(c("amount").rolling_mean(20)).alias("amt20"),
        o(c("volume").shift(1).rolling_mean(20)).alias("vavg20"),
        o(c("ac").rolling_mean(20)).alias("ma20"),
        o(c("ac").rolling_mean(60)).alias("ma60"),
        o(c("rng").shift(1)).alias("rng1"),
        o(c("rng").rolling_min(7).shift(1)).alias("rmin7"),
        o(c("ah").shift(1)).alias("ah1"),
        o(c("volume").shift(1)).alias("vol1"),
        o(c("trade_date").shift(-1)).alias("nxt"),
    ).drop("_f")
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False))
    # 流通市值：周频 basic 快照 circ_mv × close_T / 快照日 close
    bs = L.basic().select("code", "trade_date", "circ_mv").filter(c("circ_mv") > 0)
    bs = bs.join(df.select("code", "trade_date", c("close").alias("close_b")), on=["code", "trade_date"], how="inner") \
        .rename({"trade_date": "bdate"}).sort("bdate")
    df = df.join_asof(bs, left_on="trade_date", right_on="bdate", by="code", strategy="backward") \
        .with_columns((c("circ_mv") * c("close") / c("close_b")).alias("fmv")).drop("circ_mv", "close_b", "bdate")
    days = sorted(df["trade_date"].unique().to_list())
    nxtmap = pl.DataFrame({"trade_date": days, "mkt_nxt": days[1:] + [None]})
    df = df.join(nxtmap, on="trade_date", how="left").sort("code", "trade_date")
    big = c("code").str.starts_with("688") | ((c("code").str.starts_with("300") | c("code").str.starts_with("301"))
                                            & (c("trade_date") >= "2020-08-24"))
    lim = pl.when(big).then(20.0).when(c("st")).then(5.0).otherwise(10.0)
    mkt = (~c("st") & (c("age") >= MIN_AGE) & (c("volume") > 0) & c("fmv").is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(mkt).then(c("fmv")).otherwise(None).rank("ordinal", descending=True).over("trade_date").alias("mv_rank"))
    u = (~c("st") & (c("age") >= MIN_AGE) & (c("amt20") > MIN_AMT20) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt"))).fill_null(False)
    trend = ((c("ac") > c("ma20")) & (c("ma20") > c("ma60")) & (c("pct_chg") < lim - 0.2)).fill_null(False)
    sig = ((c("rng1") > 0) & (c("rng1") <= c("rmin7") + 1e-12) & (c("vol1") > 0) & (c("ac") > c("ah1"))
           & (c("volume") >= VOL_MULT * c("vavg20"))).fill_null(False)
    cand = df.filter(u & trend).with_columns(sig.alias("sig"), (c("volume") / c("vavg20")).alias("volr")) \
        .select("code", "trade_date", "close", "volr", "sig", "mv_rank")
    codes = cand["code"].unique().to_list()
    raw = df.filter(c("code").is_in(codes)).select("code", "trade_date", "open", "high", "low", "close", "volume").sort("code", "trade_date")
    bars = {}
    for (code,), part in raw.partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = tuple(part[k].to_list() if k == "trade_date" else part[k].to_numpy()
                           for k in ("trade_date", "open", "high", "low", "close", "volume"))
    del df, raw
    return cand, bars, days


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
                            tradable=bool((v[j] or 0) > 0)) for j in range(i + 1, min(i + 21, len(d))))
        out = None
        if bs:
            tr = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=float(close)), bs, self.pol).trade
            if tr is not None and tr.executed and tr.exit_date:
                out = (float(tr.net_return_pct), str(tr.exit_reason or ""))
        self.cache[key] = out
        return out


def _win(cand, start, end, bigonly):
    x = cand.filter(c("trade_date").is_between(pl.lit(start), pl.lit(end)))
    return x.filter(c("mv_rank") <= MV_TOP) if bigonly else x


def pick_days(cand, start, end, bigonly):
    x = _win(cand, start, end, bigonly).filter(c("sig")).with_columns((-c("volr")).alias("_k")).sort(["trade_date", "_k", "code"])
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


def control(cand, picks, start, end, days, sim, bigonly):
    pool = _win(cand, start, end, bigonly)
    pdts = set(dt for dt in picks if picks[dt])
    pool = pool.filter(c("trade_date").is_in(list(pdts)))
    P = {dt: sorted(zip(g["code"].to_list(), g["close"].to_list()))
         for (dt,), g in pool.partition_by("trade_date", as_dict=True, maintain_order=True).items()}
    pos = {d: i for i, d in enumerate(days)}
    pdays = [d for d in days if start <= d <= end]
    ms = []
    for s in range(NSEED):
        rng = random.Random(1000 + s); last = {}; rows = []
        for dt in sorted(picks):
            k = len(picks[dt])
            if not k:
                continue
            avail = [x for x in P.get(dt, []) if not (x[0] in last and pos[dt] - pos[last[x[0]]] <= NOREPEAT)]
            for code, close in rng.sample(avail, min(k, len(avail))):
                last[code] = dt
                r = sim(dt, code, close)
                if r is not None:
                    rows.append({"dt": dt, "code": code, "net": r[0]})
        m = metrics(rows, pdays)
        if m.get("n"):
            ms.append(m)
    if not ms:
        return {"n": 0}
    return {k: round(float(np.mean([m[k] for m in ms])), 3) for k in ("n", "wr", "avg", "pf", "mdd")}


def main():
    phases = sys.argv[1].split(","); out = Path(sys.argv[2])
    t0 = time.time()
    cand, bars, days = prep()
    print(f"prep {time.time()-t0:.0f}s cand={cand.height} sig={int(cand['sig'].sum())} codes={len(bars)}", flush=True)
    sims = {k: Sim(bars, p) for k, p in POL.items()}
    res = json.loads(out.read_text()) if out.exists() else {}
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        w_all = _win(cand, a, b, False)
        res[ph] = {"trading_days": len(pdays),
                   "pool_per_day": round(w_all.height / max(len(pdays), 1), 1),
                   "sig_per_day": round(int(w_all["sig"].sum()) / max(len(pdays), 1), 2),
                   "sig_per_day_big": round(int(_win(cand, a, b, True)["sig"].sum()) / max(len(pdays), 1), 2)}
        print(ph, json.dumps(res[ph]), flush=True)
        for v, (pk, bigonly) in VARIANTS.items():
            sim = sims[pk]
            byday = pick_days(cand, a, b, bigonly)
            rows, picks, nblock = run_picks(byday, days, sim)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            m["signal_days"] = len(byday)
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            yr = defaultdict(list)
            for r in rows:
                yr[r["dt"][:4]].append(r["net"])
            m["by_year"] = {k: [len(x), round(float(np.mean(x)) - EXTRA_COST, 2)] for k, x in sorted(yr.items())}
            ctl = control(cand, picks, a, b, days, sim, bigonly)
            m["control"] = ctl
            m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            res[ph][v] = m
            print(ph, v, json.dumps({k: m[k] for k in m if k not in ("by_exit",)}, ensure_ascii=False), flush=True)
            print("   exits", json.dumps(m["by_exit"], ensure_ascii=False), flush=True)
            for r in rows:
                trades.append({"phase": ph, "variant": v, **r})
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if trades:
        pl.DataFrame(trades).write_csv(out.with_suffix("").as_posix() + "_trades_" + "_".join(phases) + ".csv")
    print(f"done {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
