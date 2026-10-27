"""[短线] §123 市场恐慌日次日高流动性低吸（market-level panic rebound）— 预注册 P_big_h3 / P_big_h5
（/workspace/strategy-research.md §123，2026-10-19 10:56 CST 结果出来前写定，无网格、不调参）。

恐慌日（T 收盘可知）：全市场 = 非北交所、volume>0、面板内行号>=5；跌停 = pct_chg <= -(lim-0.2)，
  lim = 20（688；300/301 自 2020-08-24）/ 5（ST, PIT）/ 10；等权 = clip(pct_chg,±20) 均值；
  panic = 跌停占比 >= 5% 或 等权 <= -3%。
股池 L：非北交所、非ST(PIT)、面板内>=250 行、close>=3、次日有日K，amt20 排名前 300。
  P_big_h3 : 恐慌日 L 内 pct_chg 升序前 5，T+1 开盘买，horizon 3
  P_big_h5 : 同上 horizon 5
执行：simulate + execution_policy_for_next_open_entry()（一字涨停/跳空>6% 不买、−5.5% 止损、利润地板 trailing、一字跌停顺延）。
net 已含 ~0.27%，判定再扣 0.03 → avg_c30。此前 5 个交易日已选跳过。
对照：同日 L 随机同等只数、同出场，20 种子。[信息] 恐慌日 L 全体均值；非恐慌日同规则（L 内跌最多前 5）。
只读本地 parquet（lt_data），不调用 promax / 网关。
用法：LT_START=.. LT_END=.. python scripts/research_panic_rebound.py "R12,N3,EXT,W1" out.json
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
TOPN = 5
NOREPEAT = 5
EXTRA_COST = 0.03
LIQ_TOP = 300
LD_SHARE = 0.05
EQW_MAX = -3.0
_P = execution_policy_for_next_open_entry()
POL = {"h3": dc.replace(_P, horizon_days=3), "h5": dc.replace(_P, horizon_days=5)}
VARIANTS = {"P_big_h3": "h3", "P_big_h5": "h5"}


def prep():
    df = L.prices().filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"])).sort("code", "trade_date")
    df = df.with_columns(pl.int_range(pl.len()).over("code").alias("age"),
                         c("amount").rolling_mean(20).over("code").alias("amt20"),
                         c("trade_date").shift(-1).over("code").alias("nxt"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward") \
        .with_columns(c("st").fill_null(False)).sort("code", "trade_date")
    big = c("code").str.starts_with("688") | ((c("code").str.starts_with("300") | c("code").str.starts_with("301"))
                                            & (c("trade_date") >= "2020-08-24"))
    lim = pl.when(big).then(20.0).when(c("st")).then(5.0).otherwise(10.0)
    act = (c("volume") > 0) & (c("age") >= 5)
    df = df.with_columns(act.fill_null(False).alias("_act"),
                         (act & (c("pct_chg") <= -(lim - 0.2))).fill_null(False).alias("_ld"))
    cal = df.filter(c("_act")).group_by("trade_date").agg(
        pl.len().alias("n_mkt"), c("_ld").sum().alias("n_ld"),
        c("pct_chg").clip(-20, 20).mean().alias("eqw")).sort("trade_date")
    cal = cal.with_columns((c("n_ld") / c("n_mkt")).alias("ld_share"))
    cal = cal.with_columns(((c("ld_share") >= LD_SHARE) | (c("eqw") <= EQW_MAX)).alias("panic"),
                           c("trade_date").shift(-1).alias("mkt_nxt"))
    df = df.join(cal.select("trade_date", "mkt_nxt", "panic"), on="trade_date", how="left")
    u = (~c("st") & (c("age") >= 250) & (c("close") >= 3) & (c("nxt") == c("mkt_nxt")) & c("amt20").is_not_null()).fill_null(False)
    cand = df.filter(u).with_columns(c("amt20").rank("ordinal", descending=True).over("trade_date").alias("liq_rank")) \
        .filter(c("liq_rank") <= LIQ_TOP).select("code", "trade_date", "close", "pct_chg", "amt20", "panic")
    codes = set(cand["code"].unique().to_list())
    bars = {}
    for (code,), part in df.filter(c("code").is_in(list(codes))).select(
            "code", "trade_date", "open", "high", "low", "close", "volume") \
            .partition_by("code", as_dict=True, maintain_order=True).items():
        bars[code] = tuple(part[k].to_list() if k == "trade_date" else part[k].to_numpy()
                           for k in ("trade_date", "open", "high", "low", "close", "volume"))
    days = cal["trade_date"].to_list()
    ldchk = None
    try:  # 2024+ 核对：规则法跌停 vs stk_limits.down_limit（只看计数，不看收益）
        sl = pl.read_parquet(L.D / "stk_limits.parquet").filter(c("trade_date") >= "2024-01-01")
        if sl.height:
            j = df.filter(c("_act") & (c("trade_date") >= "2024-01-01")).join(sl, on=["code", "trade_date"], how="inner") \
                .with_columns((c("close") <= c("down_limit") + 0.005).alias("_ld2"))
            g = j.group_by("trade_date").agg(c("_ld").sum().alias("rule"), c("_ld2").sum().alias("ref"), pl.len().alias("n"))
            ldchk = {"days": g.height, "rule_total": int(g["rule"].sum()), "ref_total": int(g["ref"].sum()),
                     "row_agree": round(float((j["_ld"] == j["_ld2"]).mean()), 5),
                     "max_abs_day_diff": int((g["rule"] - g["ref"]).abs().max())}
    except Exception as exc:  # noqa: BLE001
        ldchk = {"error": str(exc)}
    del df
    return cand, bars, days, cal, ldchk


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


def pick_days(cand, start, end, panic=True):
    x = cand.filter((c("panic") == panic) & c("trade_date").is_between(pl.lit(start), pl.lit(end))).sort(["trade_date", "pct_chg", "code"])
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
    pool = cand.filter(c("trade_date").is_between(pl.lit(start), pl.lit(end)) & c("panic"))
    P = {dt: list(zip(g["code"].to_list(), g["close"].to_list()))
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
            avail = sorted(x for x in P.get(dt, []) if not (x[0] in last and pos[dt] - pos[last[x[0]]] <= NOREPEAT))
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


def pool_all(cand, start, end, sim):
    x = cand.filter(c("trade_date").is_between(pl.lit(start), pl.lit(end)) & c("panic"))
    nets = [r[0] for r in (sim(dt, code, cl) for code, dt, cl in x.select("code", "trade_date", "close").iter_rows()) if r is not None]
    return {"n": len(nets), "avg": round(float(np.mean(nets)), 3) if nets else None,
            "wr": round(float(np.mean(np.array(nets) > 0)) * 100, 1) if nets else None}


def main():
    phases = sys.argv[1].split(","); out = Path(sys.argv[2])
    t0 = time.time()
    cand, bars, days, cal, ldchk = prep()
    print(f"prep {time.time()-t0:.0f}s cand={cand.height} codes={len(bars)} ldchk={ldchk}", flush=True)
    sims = {k: Sim(bars, p) for k, p in POL.items()}
    res = json.loads(out.read_text()) if out.exists() else {}
    res["_ld_check_vs_stk_limits"] = ldchk
    trades = []
    for ph in phases:
        a, b = PH[ph]
        pdays = [d for d in days if a <= d <= b]
        pc = cal.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)) & c("panic"))
        res[ph] = {"trading_days": len(pdays), "panic_days": pc.height,
                   "panic_list": [[r["trade_date"], round(r["ld_share"] * 100, 2), round(r["eqw"], 2)] for r in pc.iter_rows(named=True)]}
        print(ph, "panic days", pc.height, res[ph]["panic_list"], flush=True)
        byday = pick_days(cand, a, b, True)
        np_byday = pick_days(cand, a, b, False)
        for v, pk in VARIANTS.items():
            sim = sims[pk]
            rows, picks, nblock = run_picks(byday, days, sim)
            m = metrics(rows, pdays)
            m["not_filled"] = nblock
            m["signal_days"] = len(byday)
            why = defaultdict(list)
            for r in rows:
                why[r["why"]].append(r["net"])
            m["by_exit"] = {k: [len(x), round(float(np.mean(x)), 2)] for k, x in why.items()}
            m["by_day"] = {dt: round(float(np.mean([r["net"] for r in rows if r["dt"] == dt])), 2) for dt in sorted({r["dt"] for r in rows})}
            ctl = control(cand, picks, a, b, days, sim)
            m["control"] = ctl
            m["excess_vs_control"] = round(m.get("avg", 0) - ctl.get("avg", 0), 3) if m.get("n") and ctl.get("n") else None
            m["info_pool_all_panic"] = pool_all(cand, a, b, sim)
            r2, _, nb2 = run_picks(np_byday, days, sim)
            m["info_nonpanic_same_rule"] = {**{k: v2 for k, v2 in metrics(r2, pdays).items() if k in ("n", "wr", "avg", "avg_c30", "pf")}, "not_filled": nb2}
            res[ph][v] = m
            print(ph, v, json.dumps({k: m[k] for k in m if k not in ("by_exit", "by_day")}, ensure_ascii=False), flush=True)
            print("   exits", json.dumps(m["by_exit"], ensure_ascii=False), flush=True)
            for r in rows:
                trades.append({"phase": ph, "variant": v, **r})
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    res["_panic_calendar"] = {r["trade_date"]: [round(r["ld_share"] * 100, 2), round(r["eqw"], 2)]
                              for r in cal.filter(c("panic")).iter_rows(named=True)}
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if trades:
        pl.DataFrame(trades).write_csv(out.with_suffix("").as_posix() + "_trades_" + "_".join(phases) + ".csv")
    print(f"done {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
