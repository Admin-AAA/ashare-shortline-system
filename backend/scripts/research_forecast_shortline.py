"""[短线] §119 业绩预告大幅预增 / 扭亏 短持有事件策略 — 预注册 3 版
（/workspace/strategy-research.md §119，结果出来前写定，无网格、不调参）。

事件：data/midterm/forecast/<code>.parquet；每个 (ts_code,end_date) 取 ann_date 最早一行 = 首次预告；
      该行 first_ann_date < ann_date（原始首发缺失、此行是修正版）→ 整条剔除。
PIT：T = <=ann_date 的最后一个交易日（假定收盘后披露），T+1 开盘买。
base（T 收盘）：非北交所(4/8/9)、非ST(PIT)、面板内 >=60 行、amt20>5000万、close>=3、次日有日K。
  SF_big_h3  : base ∧ type==预增 ∧ p_change_min>=50；horizon 3；排序 p_change_min 降序
  SF_big_h5  : 同上；horizon 5
  SF_turn_h5 : base ∧ type==扭亏；horizon 5；排序 net_profit_min 降序（空值最后）
每日前 3，平手代码升序；此前 5 个交易日已选跳过（不递补）。
出场：simulate + execution_policy_for_next_open_entry()（一字涨停/跳空>6% 不买、−5.5% 止损、利润地板 trailing、一字跌停顺延）。
net 已含 ~0.27%，判定再扣 0.03 → avg_c30。
[信息] *_HOLD：同选股，关止损+关 trailing，持满 horizon 收盘卖。
主对照：每笔成交 → 当日 base（剔除当日任何首次预告公告股）同总市值五分位随机 1 只（总市值取 basic_<year> 周度快照 join_asof backward），同出场，20 种子平均。
[信息] cohort：每个有成交的信号日从 base 随机 ≤100 只同出场取均值（按笔加权）。
用法：LT_START=.. LT_END=.. python scripts/research_forecast_shortline.py "R12,N3,EXT,W1" out.json
"""
from __future__ import annotations
import dataclasses as dc, glob, json, os, random, sys, time
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
NCOH = 100
TOPN = 3
NOREPEAT = 5
EXTRA_COST = 0.03
AMT_MIN = 5e7
_P5 = execution_policy_for_next_open_entry()
_P3 = dc.replace(_P5, horizon_days=3)


def _hold(p):
    return dc.replace(p, stop_loss=dc.replace(p.stop_loss, enabled=False), trailing=dc.replace(p.trailing, enabled=False))


POL = {"h3": _P3, "h5": _P5, "h3_hold": _hold(_P3), "h5_hold": _hold(_P5)}
# variant -> (signal col, score col, policy key, decision?)
VARIANTS = {"SF_big_h3": ("big", "sc_big", "h3", True), "SF_big_h5": ("big", "sc_big", "h5", True),
            "SF_turn_h5": ("turn", "sc_turn", "h5", True),
            "SF_big_h3_HOLD": ("big", "sc_big", "h3_hold", False), "SF_big_h5_HOLD": ("big", "sc_big", "h5_hold", False),
            "SF_turn_h5_HOLD": ("turn", "sc_turn", "h5_hold", False)}


def first_forecasts() -> pl.DataFrame:
    cols = ["ts_code", "ann_date", "end_date", "type", "first_ann_date", "p_change_min", "net_profit_min"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "forecast" / "*.parquet"))):
        try:
            x = pl.read_parquet(f)
        except Exception:
            continue
        if x.height == 0 or not {"ts_code", "ann_date", "end_date", "type"} <= set(x.columns):
            continue
        for k in cols:
            if k not in x.columns:
                x = x.with_columns(pl.lit(None).alias(k))
        parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date", "type", "first_ann_date")
                               else c(k).cast(pl.Float64, strict=False) for k in cols]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.with_columns(c("ann_date").str.strip_chars()).filter(c("ann_date").str.len_chars() == 8)
    d = d.sort(["ann_date", "ts_code"]).unique(["ts_code", "end_date"], keep="first", maintain_order=True)
    n0 = d.height
    d = d.filter(~(c("first_ann_date").is_not_null() & (c("first_ann_date").str.len_chars() == 8) & (c("first_ann_date") < c("ann_date"))))
    print(f"first forecasts {n0} -> {d.height} after first_ann_date check", flush=True)
    return d.with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("ann_date").alias("ann"))


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    df = df.with_columns(pl.int_range(pl.len()).over("code").alias("age"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(td, on="trade_date", how="left")
    base = (~c("st") & (c("age") >= 60) & (c("amt20") > AMT_MIN) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt"))).fill_null(False)
    df = df.with_columns(base.alias("base"))
    # total_mv quintile within base per day
    lo, hi = df["trade_date"].min(), df["trade_date"].max()
    fs = [f for f in sorted(L.D.glob("basic_[0-9]*.parquet")) if lo[:4] <= f.stem[-4:] <= hi[:4]]
    bs = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "total_mv"]) for f in fs], how="diagonal_relaxed") \
        .select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("trade_date"), c("total_mv").cast(pl.Float64)) \
        .unique(["code", "trade_date"])
    # basic_<year> 是周度快照 → 按代码 join_asof（backward，只用 <=T 的最近一期）
    bs = bs.drop_nulls("total_mv").sort("trade_date")
    pool = df.filter(c("base")).select("code", "trade_date", "close").sort("trade_date") \
        .join_asof(bs, on="trade_date", by="code", strategy="backward").sort("code", "trade_date")
    pool = pool.with_columns(pl.when(c("total_mv").is_not_null())
                             .then(((c("total_mv").rank("ordinal").over("trade_date") - 1) * 5
                                    / c("total_mv").count().over("trade_date")).floor().cast(pl.Int32))
                             .otherwise(None).alias("q"))
    # events -> T = last trading day <= ann
    ev = first_forecasts()
    tdl = td.select(c("trade_date").alias("T"))
    ev = ev.sort("ann").join_asof(tdl.sort("T"), left_on="ann", right_on="T", strategy="backward").drop_nulls("T")
    ev = ev.with_columns(((c("type") == "预增") & (c("p_change_min") >= 50)).fill_null(False).alias("big"),
                         (c("type") == "扭亏").fill_null(False).alias("turn"))
    evd = ev.group_by(["code", "T"]).agg(c("big").any(), c("turn").any(),
                                          pl.when(c("big")).then(c("p_change_min")).otherwise(None).max().alias("sc_big"),
                                          pl.when(c("turn")).then(c("net_profit_min")).otherwise(None).max().alias("sc_turn"),
                                          c("ann").max().alias("ann")).rename({"T": "trade_date"})
    pool = pool.join(evd, on=["code", "trade_date"], how="left").with_columns(
        c("ann").is_not_null().alias("anyfc"), c("big").fill_null(False), c("turn").fill_null(False))
    codes = set(pool["code"].unique().to_list())
    bars = {}
    for (code,), part in df.filter(c("code").is_in(list(codes))).select("code", "trade_date", "open", "high", "low", "close", "volume") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = tuple(part[k].to_list() if k == "trade_date" else part[k].to_numpy()
                           for k in ("trade_date", "open", "high", "low", "close", "volume"))
    days = td["trade_date"].to_list()
    del df
    return pool, bars, days


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
        rng = range(i + 1, min(i + 16, len(d)))
        bs = tuple(BarInput(code=code, trade_date=d[j], open=float(o[j]), high=float(h[j]), low=float(l[j]),
                            close=float(cl[j]), volume=float(v[j]) if v[j] == v[j] else None,
                            tradable=bool((v[j] or 0) > 0)) for j in rng)
        out = None
        if bs:
            tr = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=float(close)), bs, self.pol).trade
            if tr is not None and tr.executed and tr.exit_date:
                gap = 100 * (float(o[i + 1]) / float(cl[i]) - 1) if i + 1 < len(d) else None
                out = (float(tr.net_return_pct), str(tr.exit_reason or ""), gap)
        self.cache[key] = out
        return out


def pick_days(pool, sig, score, start, end):
    x = pool.filter(c(sig) & c("trade_date").is_between(pl.lit(start), pl.lit(end))) \
        .sort(["trade_date", score, "code"], descending=[False, True, False], nulls_last=True)
    return {dt: list(zip(g["code"].to_list(), g["close"].to_list()))
            for (dt,), g in x.partition_by("trade_date", as_dict=True, maintain_order=True).items()}


def run_picks(byday, days, sim):
    pos = {d: i for i, d in enumerate(days)}
    last = {}; rows = []; nblock = 0
    for dt in sorted(byday):
        got = []
        for code, close in byday[dt]:
            if len(got) >= TOPN:
                break
            if code in last and pos[dt] - pos[last[code]] <= NOREPEAT:
                continue
            got.append((code, close))
        for code, close in got:
            last[code] = dt
            s = sim(dt, code, close)
            if s is None:
                nblock += 1; continue
            rows.append({"dt": dt, "code": code, "net": s[0], "why": s[1], "gap": s[2]})
    return rows, nblock


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
    gaps = [r["gap"] for r in rows if r.get("gap") is not None]
    return {"n": int(len(net)), "wr": round(wr * 100, 1), "avg": round(float(net.mean()), 3),
            "avg_c30": round(float(net.mean()) - EXTRA_COST, 3),
            "pf": round(float(w.sum() / max(-lo.sum(), 1e-9)), 2), "sum": round(float(net.sum()), 1),
            "mdd": round(mdd, 1), "max_lose_streak": best, "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "payoff": round(aw / -al, 2) if al < 0 else None,
            "exp_c30": round(wr * aw + (1 - wr) * al - EXTRA_COST, 3), "days": len(daily),
            "entry_gap_avg": round(float(np.mean(gaps)), 2) if gaps else None}


def controls(pool, rows, start, end, days, sim):
    win = pool.filter(c("trade_date").is_between(pl.lit(start), pl.lit(end)))
    dts = sorted({r["dt"] for r in rows})
    sub = win.filter(c("trade_date").is_in(dts))
    byq = defaultdict(list); allp = defaultdict(list)
    for dt, code, close, q, anyfc in zip(sub["trade_date"].to_list(), sub["code"].to_list(), sub["close"].to_list(),
                                         sub["q"].to_list(), sub["anyfc"].to_list()):
        allp[dt].append((code, close))
        if not anyfc and q is not None:
            byq[(dt, q)].append((code, close))
    qmap = {(dt, code): q for dt, code, q in zip(sub["trade_date"].to_list(), sub["code"].to_list(), sub["q"].to_list())}
    pdays = [d for d in days if start <= d <= end]
    ms = []
    for s in range(NSEED):
        rng = random.Random(1000 + s); cr = []
        for r in rows:
            cands = byq.get((r["dt"], qmap.get((r["dt"], r["code"]))), [])
            if not cands:
                continue
            code, close = rng.choice(sorted(cands))
            x = sim(r["dt"], code, close)
            if x is not None:
                cr.append({"dt": r["dt"], "code": code, "net": x[0]})
        m = metrics(cr, pdays)
        if m.get("n"):
            ms.append(m)
    ctl = {k: round(float(np.mean([m[k] for m in ms])), 3) for k in ("n", "wr", "avg", "pf", "mdd")} if ms else {"n": 0}
    rng = random.Random(7); dmean = {}
    for dt in dts:
        P = sorted(allp.get(dt, []))
        smp = rng.sample(P, min(NCOH, len(P)))
        vals = [x[0] for x in (sim(dt, code, close) for code, close in smp) if x is not None]
        if vals:
            dmean[dt] = float(np.mean(vals))
    cv = [dmean[r["dt"]] for r in rows if r["dt"] in dmean]
    coh = {"n_days": len(dmean), "avg": round(float(np.mean(cv)), 3) if cv else None}
    return ctl, coh


def main():
    phases = sys.argv[1].split(","); out = Path(sys.argv[2])
    t0 = time.time()
    pool, bars, days = prep()
    print(f"prep {time.time()-t0:.0f}s pool={pool.height} codes={len(bars)}", flush=True)
    sims = {k: Sim(bars, p) for k, p in POL.items()}
    res = json.loads(out.read_text()) if out.exists() else {}
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        win = pool.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))
        nd = max(len(pdays), 1)
        res[ph] = {"base_per_day": round(win.height / nd, 1), "big_events": int(win["big"].sum()), "turn_events": int(win["turn"].sum()),
                   "big_days": int(win.filter(c("big"))["trade_date"].n_unique()), "turn_days": int(win.filter(c("turn"))["trade_date"].n_unique())}
        print(ph, res[ph], flush=True)
        for v, (sig, sc, pk, decision) in VARIANTS.items():
            sim = sims[pk]
            rows, nblock = run_picks(pick_days(pool, sig, sc, a, b), days, sim)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            yr = defaultdict(list)
            for r in rows:
                yr[r["dt"][:4]].append(r["net"])
            m["by_year"] = {k: [len(x), round(float(np.mean(x)) - EXTRA_COST, 3), round(100 * float((np.array(x) > 0).mean()), 1)] for k, x in sorted(yr.items())}
            if decision and rows:
                ctl, coh = controls(pool, rows, a, b, days, sim)
                m["control"] = ctl; m["cohort"] = coh
                m["excess_vs_control"] = round(m["avg"] - ctl["avg"], 3) if ctl.get("n") else None
                m["excess_vs_cohort"] = round(m["avg"] - coh["avg"], 3) if coh.get("avg") is not None else None
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
