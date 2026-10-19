"""[短线] §112 龙虎榜机构专用净买入 独立短线策略 — 预注册 3 版
（/workspace/strategy-research.md §112，结果出来前写定，无网格、不调参）。

数据：top_inst 缓存 output/research/sprint4/top_inst/YYYYMMDD.parquet（§101，2022-03-28~2023-11-03 + 2025-01-13~2026-10-16）。
  同 §101 去重：(code, institution, round(buy,4), round(sell,4))；机构 = "机构专用"；inst_net_T = 机构行 net_buy_yi 之和（只用 T 日）；
  n_inst_buy_T = 去重后 net_buy_yi>0 的机构行数。
行情：lt_data.prices() 窗口加载（预热改 150 天），只保留龙虎榜出现过的代码；fmv_T = 周度 circ_mv(万元)×close_T/快照日 close；
  inst_ratio = inst_net_T*1e4/fmv_T。
P0_LHB（T 收盘）：T 日上榜（任意席位）、非北交所、非ST(PIT)、面板内>=60 行、amt20>5000万、close>=3、次日有日K、fmv 可得。
  INST_H3  : P0 ∧ inst_net>0，inst_ratio 降序、代码升序前 3；执行核 horizon 3（其余同线上）
  INST_H5  : 同选股；执行核默认（horizon 5 + 利润地板 trailing）
  INST2_H5 : P0 ∧ inst_net>0 ∧ n_inst_buy>=2 ∧ T 非一字涨停；同排序前 3；执行核默认
此前 5 个交易日内已选的代码跳过（不递补）；T+1 一字涨停 / 跳空>6% 由执行核判不成交（不递补）。
出场/成本：app.engine.simulation.simulate + execution_policy_for_next_open_entry()；判定再扣 0.03 → avg_c30。
对照：每个有选股的信号日从当日 P0_LHB 随机抽同等只数（同排除近 5 日已选），同出场，20 种子平均（ctl）；
  信息项 ctl_inst：从 P0_LHB ∧ inst_net>0 池随机抽。
用法：LT_START=.. LT_END=.. python scripts/research_lhb_inst_standalone.py "<phase>,<phase>" out.json
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
_orig_range = L._range
L._range = lambda start=None, end=None, warmup_days=420: _orig_range(start, end, 150)
PH = {"R12 2025-10~2026-10": ("2025-10-01", "2026-10-16"), "EXT 2025-01-13~09": ("2025-01-13", "2025-09-30"),
      "N3 2025-01-13~2026-10-16": ("2025-01-13", "2026-10-16"),
      "W0 2022-04~2023-03": ("2022-04-01", "2023-03-31"), "W00 2023-04~11-03": ("2023-04-01", "2023-11-03")}
CACHE = B / "output/research/sprint4/top_inst"
INST = "机构专用"
NSEED = 20
TOPN = 3
NOREPEAT = 5
EXTRA_COST = 0.03
P_LIVE = execution_policy_for_next_open_entry()
P_H3 = dc.replace(P_LIVE, horizon_days=3)
POL = {"live": P_LIVE, "h3": P_H3}
VARIANTS = {"INST_H3": ("sig_a", "h3"), "INST_H5": ("sig_a", "live"), "INST2_H5": ("sig_c", "live")}


def load_lhb(start, end):
    a, b = start.replace("-", ""), end.replace("-", "")
    parts = []
    for f in sorted(CACHE.glob("*.parquet")):
        if not (a <= f.stem <= b):
            continue
        d = pl.read_parquet(f, columns=["code", "trade_date", "institution", "buy_amount_yi", "sell_amount_yi", "net_buy_yi"])
        if d.is_empty():
            continue
        d = d.with_columns(c("code").cast(pl.Utf8).str.zfill(6), pl.lit(f"{f.stem[:4]}-{f.stem[4:6]}-{f.stem[6:]}").alias("trade_date"),
                           c("buy_amount_yi").round(4).alias("_b"), c("sell_amount_yi").round(4).alias("_s")) \
            .unique(["code", "institution", "_b", "_s"])
        ins = c("institution") == INST
        parts.append(d.group_by("code", "trade_date").agg(
            c("net_buy_yi").filter(ins).sum().alias("inst_net"),
            (ins & (c("net_buy_yi") > 0)).sum().alias("n_inst_buy"),
            ins.sum().alias("n_inst_rows")))
    return pl.concat(parts)


def prep(start, end):
    lhb = load_lhb(start, end)
    codes = lhb["code"].unique()
    df = L.prices().filter(c("code").is_in(codes) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    lim = (pl.when(c("code").str.starts_with("688")).then(0.2)
           .when(c("code").str.slice(0, 3).is_in(["300", "301"]) & (c("trade_date") >= "2020-08-24")).then(0.2)
           .otherwise(0.1))
    df = df.with_columns(lim.alias("lim"), (c("close") / (1 + c("pct_chg") / 100)).alias("pre"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns((((c("pre") * (1 + c("lim"))) * 100).round(0) / 100).alias("up"))
    lu = ((c("close") >= c("up") - 0.01) & (c("pct_chg") >= c("lim") * 100 - 1)).fill_null(False)
    yizi = (lu & ((c("high") - c("low")).abs() < 0.005) & ((c("open") - c("close")).abs() < 0.005)).fill_null(False)
    df = df.with_columns(lu.alias("lu"), yizi.alias("yizi"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    y0, y1 = int(df["trade_date"].min()[:4]), int(df["trade_date"].max()[:4])
    bs = pl.concat([pl.read_parquet(L.D / f"basic_{y}.parquet", columns=["ts_code", "trade_date", "circ_mv"])
                    for y in range(y0, y1 + 1) if (L.D / f"basic_{y}.parquet").exists()], how="diagonal_relaxed")
    bs = bs.select(c("ts_code").str.slice(0, 6).alias("code"),
                   c("trade_date").cast(pl.Utf8).pipe(lambda s: pl.concat_str([s.str.slice(0, 4), s.str.slice(4, 2), s.str.slice(6, 2)], separator="-")).alias("trade_date"),
                   "circ_mv").unique(["code", "trade_date"])
    df = df.join(bs, on=["code", "trade_date"], how="left")
    df = df.with_columns((c("circ_mv") / c("close")).forward_fill().over("code").alias("_k"))
    df = df.with_columns((c("_k") * c("close")).alias("fmv"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    allday = pl.scan_parquet(L.CACHE if hasattr(L, "CACHE") else B / "data/cache/daily_kbar.parquet") \
        .filter(c("code") == "000001").select(c("trade_date").cast(pl.Utf8)).collect()
    td = pl.concat([df.select("trade_date"), allday]).unique().sort("trade_date") \
        .with_columns(c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(td, on="trade_date", how="left")
    df = df.join(lhb, on=["code", "trade_date"], how="left")
    p0 = (c("inst_net").is_not_null() & ~c("st") & (c("age") >= 60) & (c("amt20") > 5e7) & (c("close") >= 3)
          & (c("nxt") == c("mkt_nxt")) & c("fmv").is_not_null() & (c("fmv") > 0)).fill_null(False)
    sig_a = (p0 & (c("inst_net") > 0)).fill_null(False)
    sig_c = (sig_a & (c("n_inst_buy") >= 2) & ~c("yizi")).fill_null(False)
    df = df.with_columns(p0.alias("p0"), sig_a.alias("sig_a"), sig_c.alias("sig_c"),
                         (c("inst_net") * 1e4 / c("fmv")).alias("ratio"))
    cand = df.filter(c("p0")).select("code", "trade_date", "close", "ratio", "inst_net", "n_inst_buy", "yizi", "sig_a", "sig_c")
    bars = {}
    for (code,), part in df.select("code", "trade_date", "open", "high", "low", "close", "volume") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = (part["trade_date"].to_list(), part["open"].to_numpy(), part["high"].to_numpy(),
                      part["low"].to_numpy(), part["close"].to_numpy(), part["volume"].to_numpy())
    days = td["trade_date"].to_list()
    lhb_days = lhb["trade_date"].unique().sort().to_list()
    del df
    return cand, bars, days, lhb_days


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
    x = cand.filter(c(sig) & c("trade_date").is_between(pl.lit(start), pl.lit(end))) \
        .sort(["trade_date", "ratio", "code"], descending=[False, True, False])
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


def control(pool_df, picks, start, end, days, sim, mode):
    pool = pool_df.filter(c("trade_date").is_between(pl.lit(start), pl.lit(end)))
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
    return {k: round(float(np.mean([m[k] for m in ms])), 3) for k in ("n", "wr", "avg", "pf", "mdd")}


def main():
    phases = sys.argv[1].split(","); out = Path(sys.argv[2])
    t0 = time.time()
    a0 = min(PH[p][0] for p in phases); b0 = max(PH[p][1] for p in phases)
    cand, bars, days, lhb_days = prep(a0, b0)
    print(f"prep {time.time()-t0:.0f}s cand={cand.height} lhb_days={len(lhb_days)} {lhb_days[0]}..{lhb_days[-1]}", flush=True)
    sim = Sim(bars)
    res = json.loads(out.read_text()) if out.exists() else {}
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        sub = cand.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)))
        res[ph] = {"pool_days": int(sub["trade_date"].n_unique()), "lhb_days": sum(1 for d in lhb_days if a <= d <= b),
                   "mkt_days": len(pdays),
                   "pool_avg_per_day": round(sub.height / max(sub["trade_date"].n_unique(), 1), 1),
                   "inst_pos_avg_per_day": round(sub.filter(c("sig_a")).height / max(sub["trade_date"].n_unique(), 1), 1),
                   "sig_c_avg_per_day": round(sub.filter(c("sig_c")).height / max(sub["trade_date"].n_unique(), 1), 1)}
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
            ctl2 = control(cand.filter(c("sig_a")), picks, a, b, days, sim, mode)
            m["control"] = ctl; m["control_inst_pos_info"] = ctl2
            m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            m["excess_vs_inst_pos_info"] = round(m.get("avg", 0) - ctl2.get("avg", 0), 3) if m.get("n") and ctl2.get("n") else None
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
