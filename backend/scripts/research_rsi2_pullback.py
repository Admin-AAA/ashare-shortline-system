"""[短线] §117 大盘/高流动性股票上升趋势中的短期超跌均值回归（Connors 式 RSI(2) 回撤）— 预注册 3 版
（/workspace/strategy-research.md §117，结果出来前写定，无网格、不调参）。

数据：lt_data.prices() 窗口（LT_START/LT_END，自动回拉 420 自然日暖身）；ac = pct_chg 逐股累乘；MA5/MA200/RSI2 由 ac 自算（不用 DB ma 列）。
U（T 收盘）：非北交所(4/8/9)、非ST(PIT)、面板内 >=250 行、amt20>=3亿、close>=3、次日有日K、ac>MA200。
RSI2：Wilder（ewm alpha=1/2, adjust=False）。
  V1 RSI2_10_H5    : U ∧ RSI2<10；horizon 5
  V2 RSI2_10_3D_H5 : V1 ∧ T,T-1,T-2 pct_chg<0；horizon 5
  V3 RSI2_5_H10    : U ∧ RSI2<5；horizon 10
出场：simulate + execution_policy_for_next_open_entry()（一字涨停/跳空>6% 不买、−5.5% 止损、利润地板 trailing、一字跌停顺延）
  + MA5 回归：持仓中首个可卖日 t（入场日 < t < 原离场日、有量、非一字跌停）ac_t > MA5_t → 截到 t 重跑规范核、t 日收盘离场
  （仿 app/services/ma_break_exit.apply_ma_break_backtest，只会提前离场）。
每日 RSI2 升序取前 3，此前 5 个交易日已选跳过。net 已含 ~0.27%，判定再扣 0.03 → avg_c30。
[信息] V1_NOSTOP / V3_NOSTOP：同选股、stop_loss.enabled=False，不参与判定。
对照：同日 U 随机同等只数、同出场，20 种子。
用法：LT_START=.. LT_END=.. python scripts/research_rsi2_pullback.py "R12,EXT,N3,W1" out.json
"""
from __future__ import annotations
import dataclasses as dc, json, os, random, sys, time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
import numpy as np
import polars as pl
import lt_data as L
from app.engine.backtest import BarInput, RecommendationInput
from app.engine.board_rules import is_one_word_limit_down_bar
from app.engine.simulation import simulate
from app.services.entry_policy import execution_policy_for_next_open_entry

c = pl.col
PH = {"R12": ("2025-10-01", "2026-10-16"), "EXT": ("2025-01-01", "2025-09-30"), "N3": ("2025-01-01", "2026-10-16"),
      "W1": ("2024-01-01", "2024-12-31"), "W0": ("2022-04-01", "2023-03-31"), "W00": ("2023-04-01", "2023-12-31")}
NSEED = 20
TOPN = 3
NOREPEAT = 5
EXTRA_COST = 0.03
AMT_MIN = 3e8
_P5 = execution_policy_for_next_open_entry()
_P10 = dc.replace(_P5, horizon_days=10)
POL = {"h5": _P5, "h10": _P10,
       "h5_nostop": dc.replace(_P5, stop_loss=dc.replace(_P5.stop_loss, enabled=False)),
       "h10_nostop": dc.replace(_P10, stop_loss=dc.replace(_P10.stop_loss, enabled=False))}
# variant -> (signal column, policy key, decision?)
VARIANTS = {"V1_RSI2_10_H5": ("sig1", "h5", True), "V2_RSI2_10_3D_H5": ("sig2", "h5", True), "V3_RSI2_5_H10": ("sig3", "h10", True),
            "V1_NOSTOP": ("sig1", "h5_nostop", False), "V3_NOSTOP": ("sig3", "h10_nostop", False)}


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    df = df.with_columns(pl.int_range(pl.len()).over("code").alias("age"),
                         (1 + c("pct_chg") / 100).cum_prod().over("code").alias("ac"))
    d = c("ac").diff().over("code")
    df = df.with_columns(pl.when(d > 0).then(d).otherwise(0.0).alias("_up"), pl.when(d < 0).then(-d).otherwise(0.0).alias("_dn"))
    df = df.with_columns(c("_up").ewm_mean(alpha=0.5, adjust=False).over("code").alias("_ua"),
                         c("_dn").ewm_mean(alpha=0.5, adjust=False).over("code").alias("_da"),
                         c("ac").rolling_mean(5).over("code").alias("ma5"),
                         c("ac").rolling_mean(200).over("code").alias("ma200"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"),
                         (c("pct_chg") < 0).cast(pl.Int32).rolling_sum(3).over("code").alias("dn3"))
    df = df.with_columns(pl.when(c("_ua") + c("_da") > 0).then(100 * c("_ua") / (c("_ua") + c("_da"))).otherwise(100.0).alias("rsi2")) \
        .drop("_up", "_dn", "_ua", "_da")
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(td, on="trade_date", how="left")
    u = (~c("st") & (c("age") >= 250) & (c("amt20") >= AMT_MIN) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt"))
         & c("ma200").is_not_null() & (c("ac") > c("ma200"))).fill_null(False)
    df = df.with_columns(u.alias("U"))
    df = df.with_columns((c("U") & (c("rsi2") < 10)).fill_null(False).alias("sig1"),
                         (c("U") & (c("rsi2") < 5)).fill_null(False).alias("sig3"))
    df = df.with_columns((c("sig1") & (c("dn3") == 3)).fill_null(False).alias("sig2"))
    cand = df.filter(c("U")).select("code", "trade_date", "close", "rsi2", "sig1", "sig2", "sig3")
    codes = set(cand["code"].unique().to_list())
    bars = {}
    for (code,), part in df.filter(c("code").is_in(list(codes))).select(
            "code", "trade_date", "open", "high", "low", "close", "volume", "pct_chg", "ac", "ma5") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = tuple(part[k].to_list() if k == "trade_date" else part[k].to_numpy()
                           for k in ("trade_date", "open", "high", "low", "close", "volume", "pct_chg", "ac", "ma5"))
    days = td["trade_date"].to_list()
    del df
    return cand, bars, days


class Sim:
    def __init__(self, bars, pol):
        self.bars = bars; self.pol = pol; self.idx = {}; self.cache = {}; self.stats = defaultdict(int)

    def _run(self, code, dt, close, bs):
        tr = simulate(RecommendationInput(code=code, signal_date=dt, signal_price=float(close)), bs, self.pol).trade
        return tr if (tr is not None and tr.executed and tr.exit_date) else None

    def __call__(self, dt, code, close):
        key = (dt, code)
        if key in self.cache:
            return self.cache[key]
        d, o, h, l, cl, v, pc, ac, m5 = self.bars[code]
        if code not in self.idx:
            self.idx[code] = {x: i for i, x in enumerate(d)}
        ix = self.idx[code]; i = ix[dt]
        rng = range(i + 1, min(i + 21, len(d)))
        bs = tuple(BarInput(code=code, trade_date=d[j], open=float(o[j]), high=float(h[j]), low=float(l[j]),
                            close=float(cl[j]), volume=float(v[j]) if v[j] == v[j] else None,
                            tradable=bool((v[j] or 0) > 0)) for j in rng)
        out = None
        tr = self._run(code, dt, close, bs) if bs else None
        if tr is not None:
            reason = str(tr.exit_reason or "")
            e, x = ix.get(str(tr.entry_date)[:10]), ix.get(str(tr.exit_date)[:10])
            t = None
            if e is not None and x is not None:
                for j in range(e + 1, x):
                    if not (v[j] > 0) or not (m5[j] == m5[j]):
                        continue
                    if is_one_word_limit_down_bar(code, SimpleNamespace(open=o[j], high=h[j], low=l[j], close=cl[j], pct_chg=pc[j])):
                        continue
                    if ac[j] > m5[j]:
                        t = j; break
            if t is not None:
                cut = tuple(b for b in bs if b.trade_date <= d[t])
                tr2 = self._run(code, dt, close, cut)
                if tr2 is not None and str(tr2.exit_date)[:10] == d[t]:
                    tr = tr2
                    reason = "ma5_reclaim_close" if str(tr2.exit_reason or "").startswith("horizon_close") else str(tr2.exit_reason)
                    self.stats["ma5_cut"] += 1
                else:
                    self.stats["ma5_cut_rejected"] += 1
            out = (float(tr.net_return_pct), reason)
        self.cache[key] = out
        return out


def pick_days(cand, sig, start, end):
    x = cand.filter(c(sig) & c("trade_date").is_between(pl.lit(start), pl.lit(end))).sort(["trade_date", "rsi2", "code"])
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


def control(cand, picks, start, end, days, sim):
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
    sims = {k: Sim(bars, p) for k, p in POL.items()}
    res = json.loads(out.read_text()) if out.exists() else {}
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        win = cand.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))
        nd = max(len(pdays), 1)
        res[ph] = {"pool_U_avg_per_day": round(win.height / nd, 1),
                   "pool_sig1_per_day": round(win.filter(c("sig1")).height / nd, 2),
                   "pool_sig2_per_day": round(win.filter(c("sig2")).height / nd, 2),
                   "pool_sig3_per_day": round(win.filter(c("sig3")).height / nd, 2)}
        print(ph, res[ph], flush=True)
        for v, (sig, pk, decision) in VARIANTS.items():
            sim = sims[pk]
            byday = pick_days(cand, sig, a, b)
            rows, picks, nblock = run_picks(byday, days, sim)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            m["signal_days"] = len(byday)
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            if decision:
                ctl = control(cand, picks, a, b, days, sim)
                m["control"] = ctl
                m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            res[ph][v] = m
            print(ph, v, json.dumps({k: m[k] for k in m if k != "by_exit"}, ensure_ascii=False), flush=True)
            print("   exits", json.dumps(m["by_exit"], ensure_ascii=False), flush=True)
            for r in rows:
                trades.append({"phase": ph, "variant": v, **r})
        res.setdefault("_sim_stats", {}).update({k: dict(s.stats) for k, s in sims.items()})
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if trades:
        pl.DataFrame(trades).write_csv(out.with_suffix("").as_posix() + "_trades_" + "_".join(phases) + ".csv")
    print(f"done {time.time()-t0:.0f}s", {k: dict(s.stats) for k, s in sims.items()}, flush=True)


if __name__ == "__main__":
    main()
