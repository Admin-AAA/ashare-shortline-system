"""[短线] §113 龙头首阴（连板后首个非涨停收跌日，T+1 开盘低吸）— 预注册 3 版
（/workspace/strategy-research.md §113，结果出来前写定，无网格、不调参）。

数据：lt_data.prices() 窗口加载（LT_START/LT_END）；复权 ac = pct_chg 累乘，ao/al = open/low × ac/close；ST PIT。
涨停 LU：stk_limits.up_limit 有值 → close >= up_limit-0.005；否则 §110 兜底（lim 20%: 688 / 300,301 且 >=2020-08-24，
        否则 10%；up=round(pre*(1+lim),2)；close>=up-0.01 且 pct>=lim*100-1）。
streak_t = 截至 t 连续 LU 天数。
P0_SY（T 收盘）：非北交所、非ST、上市>=60 行、amt20>5000万、close>=3、次日有日K、streak_{T-1}>=2、T 非 LU、ac_T<ac_{T-1}。
  SY2_H3     : P0_SY
  SY3_H3     : P0_SY ∧ streak_{T-1}>=3
  SY2_LOW_H3 : P0_SY ∧ al_T >= al_{T-1}
每日按 streak_{T-1} 降序、amount_T 降序、代码升序取前 3；此前 5 个交易日内已选的代码跳过。
出场/成本：simulate + execution_policy_for_next_open_entry() 的 horizon_days=3（一字涨停/跳空>6% 不买、−5.5% 止损、
  利润地板 trailing、一字跌停顺延；net 已含 ~0.27%），判定再扣 0.03 → avg_c30。
对照（主）：同日 §110 P0（近 5 日涨停、T 非涨停、同过滤）随机同等只数，20 种子；[信息] 对照：同日 P0_SY 随机。
用法：LT_START=.. LT_END=.. python scripts/research_first_yin.py "R12,EXT,N3" out.json
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
P_H3 = dc.replace(execution_policy_for_next_open_entry(), horizon_days=3)
VARIANTS = {"SY2_H3": "sig2", "SY3_H3": "sig3", "SY2_LOW_H3": "siglow"}


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    lim_tab = pl.read_parquet(L.D / "stk_limits.parquet", columns=["code", "trade_date", "up_limit"])
    df = df.join(lim_tab, on=["code", "trade_date"], how="left")
    del lim_tab
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    k = c("ac") / c("close")
    lim = (pl.when(c("code").str.starts_with("688")).then(0.2)
           .when(c("code").str.slice(0, 3).is_in(["300", "301"]) & (c("trade_date") >= "2020-08-24")).then(0.2)
           .otherwise(0.1))
    df = df.with_columns((c("low") * k).alias("al"), lim.alias("lim"),
                         (c("close") / (1 + c("pct_chg") / 100)).alias("pre"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns((((c("pre") * (1 + c("lim"))) * 100).round(0) / 100).alias("up"))
    lu_fb = (c("close") >= c("up") - 0.01) & (c("pct_chg") >= c("lim") * 100 - 1)
    lu = pl.when(c("up_limit").is_not_null() & (c("up_limit") > 0)).then(c("close") >= c("up_limit") - 0.005).otherwise(lu_fb)
    df = df.with_columns(lu.fill_null(False).alias("lu"),
                         pl.when(c("up_limit").is_not_null() & (c("up_limit") > 0)).then(1).otherwise(0).alias("off_lim"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    df = df.with_columns((~c("lu")).cast(pl.Int32).cum_sum().over("code").alias("grp"))
    df = df.with_columns(c("lu").cast(pl.Int32).cum_sum().over(["code", "grp"]).alias("streak"))
    df = df.with_columns(c("streak").shift(1).over("code").fill_null(0).alias("streak_prev"),
                         c("ac").shift(1).over("code").alias("ac_prev"),
                         c("al").shift(1).over("code").alias("al_prev"),
                         pl.when(c("lu")).then(c("age")).forward_fill().over("code").alias("L_age"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(td, on="trade_date", how="left")
    gap = c("age") - c("L_age")
    base = (~c("st") & (c("age") >= 60) & (c("amt20") > 5e7) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt"))
            & ~c("lu")).fill_null(False)
    p0 = (base & gap.is_between(1, 5)).fill_null(False)  # §110 大池（主对照）
    p0sy = (base & (c("streak_prev") >= 2) & (c("ac") < c("ac_prev"))).fill_null(False)
    df = df.with_columns(p0.alias("p0"), p0sy.alias("p0sy"), p0sy.alias("sig2"),
                         (p0sy & (c("streak_prev") >= 3)).alias("sig3"),
                         (p0sy & (c("al") >= c("al_prev"))).fill_null(False).alias("siglow"))
    cand = df.filter(c("p0")).select("code", "trade_date", "close", "amount", "streak_prev", "off_lim", "p0",
                                     "p0sy", "sig2", "sig3", "siglow")
    codes = set(cand["code"].unique().to_list())
    bars = {}
    for (code,), part in df.filter(c("code").is_in(list(codes))).select("code", "trade_date", "open", "high", "low", "close", "volume") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = (part["trade_date"].to_list(), part["open"].to_numpy(), part["high"].to_numpy(),
                      part["low"].to_numpy(), part["close"].to_numpy(), part["volume"].to_numpy())
    days = td["trade_date"].to_list()
    del df
    return cand, bars, days


class Sim:
    def __init__(self, bars):
        self.bars = bars; self.idx = {}; self.cache = {}

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
                   for j in range(i + 1, min(i + 13, len(d))))
        out = None
        if bs:
            tr = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=float(close)), bs, P_H3).trade
            if tr is not None and tr.executed and tr.exit_date:
                out = (float(tr.net_return_pct), str(tr.exit_reason) if hasattr(tr, "exit_reason") else "")
        self.cache[key] = out
        return out


def pick_days(cand, sig, start, end):
    x = cand.filter(c(sig) & c("trade_date").is_between(pl.lit(start), pl.lit(end))) \
        .sort(["trade_date", "streak_prev", "amount", "code"], descending=[False, True, True, False])
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
    cand, bars, days = prep()
    print(f"prep {time.time()-t0:.0f}s cand={cand.height} codes={len(bars)}", flush=True)
    sim = Sim(bars)
    res = json.loads(out.read_text()) if out.exists() else {}
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        win = cand.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))
        sy = win.filter(c("p0sy"))
        res[ph] = {"pool_p0_avg_per_day": round(win.height / max(len(pdays), 1), 1),
                   "pool_sy_avg_per_day": round(sy.height / max(len(pdays), 1), 2),
                   "pool_sy3_avg_per_day": round(win.filter(c("sig3")).height / max(len(pdays), 1), 2),
                   "pool_sylow_avg_per_day": round(win.filter(c("siglow")).height / max(len(pdays), 1), 2),
                   "sy_official_limit_share": round(float(sy["off_lim"].mean() or 0), 3) if sy.height else None}
        for v, sig in VARIANTS.items():
            byday = pick_days(cand, sig, a, b)
            rows, picks, nblock = run_picks(byday, days, sim)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            m["signal_days"] = len(byday)
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            ctl = control(cand, "p0", picks, a, b, days, sim)
            ctl2 = control(cand, "p0sy", picks, a, b, days, sim)
            m["control"] = ctl; m["control_sy_info"] = ctl2
            m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            m["excess_vs_sy_info"] = round(m.get("avg", 0) - ctl2.get("avg", 0), 3) if m.get("n") and ctl2.get("n") else None
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
