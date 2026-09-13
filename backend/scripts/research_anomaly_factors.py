"""横截面异象族全市场三窗口 strictly-prior 预筛（L-064/L-065 铁律：先全市场，再看选中池）。

L-061~L-079 已证伪价格/量比/位置/资金流/规模/换手/PE/封板/龙虎榜/两融/竞价/分时/新鲜度，
但 backtest_factor_oos 是【布尔形态】库，从未系统检验【连续型横截面异象族】（彩票 MAX、
低波动/低特质波动、偏度、Amihud、个股跳空倾向）。本脚本全市场三窗口严格分诊，毛口径
T+1 open→T+h close 仅用于快速杀因子，幸存者再上生产同构因果 harness。

用法：
  python research_anomaly_factors.py build     # 构建并缓存面板（float32、裁掉 warmup）
  python research_anomaly_factors.py screen    # 从缓存读面板做三窗口分诊
"""
from __future__ import annotations
import sys, json, warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import text

warnings.filterwarnings("ignore")
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402

OUT = B / "output" / "research"
OUT.mkdir(parents=True, exist_ok=True)
PANEL = OUT / "anomaly_panel.pkl"

WINDOWS = {
    "W1_frozen": ("2025-01-15", "2026-01-16"),
    "W2_forward": ("2026-02-09", "2026-08-05"),
    "W3_third": ("2026-08-07", "2026-09-02"),
}
KEEP_FROM = "2025-01-06"          # 滚动窗最长 60/250，仅保留窗口前少量行
LOAD_FROM = "2024-06-01"
F32 = lambda x: x.astype(np.float32)


def build_panel() -> pd.DataFrame:
    with SessionLocal() as s:
        k = pd.DataFrame(s.execute(text(
            """SELECT code, trade_date, open, high, low, close, volume, amount,
                      pct_chg, turnover_rate, upper_shadow_count20,
                      chip_cost_15, chip_cost_50, chip_cost_85, chip_weight_avg, chip_winner_rate
               FROM daily_kbars
               WHERE trade_date >= :s AND close > 0 AND high > 0 AND low > 0
                 AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'
                 AND code NOT IN (SELECT code FROM stocks WHERE name LIKE '%ST%')"""),
            {"s": LOAD_FROM}).mappings().all())
        idx = pd.DataFrame(s.execute(text(
            "SELECT ts_code, trade_date, pct_chg FROM index_daily WHERE ts_code='000905.SH'")
        ).mappings().all())
    fcols = ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover_rate",
             "chip_cost_15", "chip_cost_50", "chip_cost_85", "chip_weight_avg", "chip_winner_rate"]
    for c in fcols:
        k[c] = k[c].astype(np.float32)
    k["upper_shadow_count20"] = k["upper_shadow_count20"].astype(np.float32)
    mkt = idx.assign(mkt=(idx.pct_chg.astype(float) / 100).astype(np.float32))[["trade_date", "mkt"]]
    k = k.merge(mkt, on="trade_date", how="left").sort_values(["code", "trade_date"]).reset_index(drop=True)

    out_parts = []
    for code, blk in k.groupby("code", sort=False):
        blk = blk.reset_index(drop=True)
        o, h, l, cl = blk.open, blk.high, blk.low, blk.close
        prev = cl.shift(1)
        r = (blk.pct_chg / 100.0).astype(np.float32)
        gap = (o / prev - 1.0).astype(np.float32)
        amt_yi = (blk.amount / 1e5).astype(np.float32)
        m = blk.mkt

        def R(s_, w, fn, mp=None):
            return getattr(s_.rolling(w, min_periods=mp or int(w * 0.8)), fn)().astype(np.float32)

        d = pd.DataFrame({"code": code, "trade_date": blk.trade_date.values})
        d["ret20"] = cl.pct_change(20).astype(np.float32)
        d["ret60"] = cl.pct_change(60).astype(np.float32)
        d["vol20"] = R(r, 20, "std"); d["vol60"] = R(r, 60, "std")
        d["max1_20"] = R(r, 20, "max"); d["max1_60"] = R(r, 60, "max")
        m1 = R(r, 20, "mean"); m2 = R(r ** 2, 20, "mean"); m3 = R(r ** 3, 20, "mean")
        var = (m2 - m1 * m1).clip(lower=1e-12)
        d["skew20"] = ((m3 - 3 * m1 * m2 + 2 * m1 ** 3) / var ** 1.5).astype(np.float32)
        amih = (r.abs() / amt_yi.where(amt_yi > 0)).replace([np.inf, -np.inf], np.nan).astype(np.float32)
        d["amihud20"] = R(amih, 20, "mean")
        d["downvol20"] = R(r.where(r < 0), 20, "std")
        d["gapdn_freq20"] = R((gap < -0.01).astype(np.float32), 20, "mean")
        d["gapdn_min20"] = R(gap, 20, "min")
        d["gapup_freq20"] = R((gap > 0.02).astype(np.float32), 20, "mean")
        turn20 = R(blk.turnover_rate, 20, "mean")
        d["turn_t_vs_20"] = (blk.turnover_rate / turn20.where(turn20 > 0)).astype(np.float32)
        hi250 = cl.rolling(250, min_periods=120).max()
        d["dist_hi250"] = (cl / hi250 - 1.0).astype(np.float32)
        d["amp20"] = R(((h - l) / prev).astype(np.float32), 20, "mean")
        cov = r.rolling(20, min_periods=16).cov(m)
        mvar = m.rolling(20, min_periods=16).var()
        beta = (cov / mvar.where(mvar > 0)).astype(np.float32)
        d["beta20"] = beta
        resid = (r - beta * m).astype(np.float32)
        d["ivol20"] = resid.rolling(20, min_periods=16).std().astype(np.float32)
        d["upper_shadow_count20"] = blk.upper_shadow_count20.values

        c15, c50, c85, wac = blk.chip_cost_15, blk.chip_cost_50, blk.chip_cost_85, blk.chip_weight_avg
        ok = (c50 > 0) & (c15 > 0) & (c85 > 0) & (wac > 0)
        d["c_winner"] = blk.chip_winner_rate.where(blk.chip_winner_rate > 0).astype(np.float32)
        d["c_conc"] = ((c85 - c15) / c50).where(ok).astype(np.float32)
        d["c_vs_wac"] = (cl / wac - 1.0).where(ok).astype(np.float32)
        d["c_asym"] = (((c85 - c50) - (c50 - c15)) / c50).where(ok).astype(np.float32)

        entry = o.shift(-1)
        d["entry"] = entry.astype(np.float32)
        for hh in (2, 3, 5):
            d[f"fwd{hh}"] = ((cl.shift(-hh) / entry - 1.0) * 100.0).astype(np.float32)
        out_parts.append(d[d.trade_date >= KEEP_FROM])
    k = pd.concat(out_parts, ignore_index=True)
    k = k[k.entry > 0]
    k.to_pickle(PANEL)
    print(f"saved {PANEL} rows={len(k):,} cols={k.shape[1]} mem={k.memory_usage(deep=True).sum()/1e9:.2f}GB")
    return k


# (label, col, 预期 Q5-Q1 符号：-1=因子值越大未来越差)
FACTORS = [
    ("max1_20 最大单日涨幅(彩票MAX)", "max1_20", -1),
    ("max1_60 60日最大单日涨幅", "max1_60", -1),
    ("skew20 收益偏度(彩票)", "skew20", -1),
    ("vol20 已实现波动(低波)", "vol20", -1),
    ("vol60 60日波动", "vol60", -1),
    ("ivol20 特质波动(低IVOL)", "ivol20", -1),
    ("downvol20 下行波动", "downvol20", -1),
    ("beta20 市场beta(低beta)", "beta20", -1),
    ("amp20 平均振幅", "amp20", -1),
    ("amihud20 非流动性", "amihud20", +1),
    ("gapdn_freq20 近月跳空低开频率", "gapdn_freq20", -1),
    ("gapdn_min20 近月最差跳空", "gapdn_min20", +1),
    ("gapup_freq20 近月跳空高开频率", "gapup_freq20", -1),
    ("dist_hi250 距250日高", "dist_hi250", -1),
    ("ret20 20日动量", "ret20", -1),
    ("ret60 60日动量", "ret60", -1),
    ("turn_t_vs_20 当日/20日换手", "turn_t_vs_20", -1),
    ("upper_shadow20 上影计数", "upper_shadow_count20", -1),
    ("c_winner 获利盘比例", "c_winner", -1),
    ("c_conc 成本离散度(小=单峰)", "c_conc", -1),
    ("c_vs_wac 现价/平均成本", "c_vs_wac", -1),
    ("c_asym 成本不对称", "c_asym", -1),
]


def screen_factor(df, col, exp_sign, horizon="fwd5"):
    out = {}
    for wname, (a, b) in WINDOWS.items():
        d = df.loc[(df.trade_date >= a) & (df.trade_date <= b), ["trade_date", col, horizon]].dropna()
        if len(d) < 5000 or d.trade_date.nunique() < 15:
            out[wname] = None
            continue
        # 向量化日截面五分位：日内 rank first → 分箱
        rk = d.groupby("trade_date")[col].rank(method="first")
        cnt = d.groupby("trade_date")[col].transform("size")
        d["q"] = np.clip(((rk - 1) // (cnt / 5)).astype(int), 0, 4)
        daily = d.groupby(["trade_date", "q"])[horizon].mean().unstack("q")
        qm = daily.mean()
        cs = sorted(daily.columns)
        spread = (daily[cs[-1]] - daily[cs[0]]).dropna()
        monthly = spread.groupby(spread.index.str[:7]).mean()
        sgn = 1.0 if spread.mean() >= 0 else -1.0
        samesign = float((np.sign(monthly) == sgn).mean())
        # 向量化日截面 Spearman（秩的 Pearson）
        rc = d.groupby("trade_date")[col].rank()
        rh = d.groupby("trade_date")[horizon].rank()
        # 每日 spearman = cov(rc,rh)/(std rc*std rh)
        t = pd.DataFrame({"d": d.trade_date.values, "rc": rc.values, "rh": rh.values})
        tg = t.groupby("d")
        num = tg.apply(lambda x: ((x.rc - x.rc.mean()) * (x.rh - x.rh.mean())).mean())
        den = tg.rc.std() * tg.rh.std()
        ics = (num / den).dropna()
        vals = [float(qm.get(q, np.nan)) for q in cs]
        # 左尾/右尾率：每分位 fwd5<=-5%（大亏，生产止损源）与 >=+5%（大赢）占比
        tail_bad = d.groupby("q")[horizon].apply(lambda s: float((s <= -5.0).mean())).reindex(cs).tolist()
        tail_good = d.groupby("q")[horizon].apply(lambda s: float((s >= 5.0).mean())).reindex(cs).tolist()
        mono = np.mean([1.0 if (vals[i] - vals[i - 1]) * exp_sign <= 0 else 0.0 for i in range(1, len(vals))])
        out[wname] = dict(n=int(len(d)), days=int(d.trade_date.nunique()), qmeans=vals,
                          spread=float(spread.mean()), samesign=samesign,
                          ic=float(ics.mean()), ic_ir=float(ics.mean() / (ics.std() + 1e-12)),
                          mono=float(mono), nmonths=int(len(monthly)),
                          tail_bad=[float(x) for x in tail_bad], tail_good=[float(x) for x in tail_good])
    return out


def screen():
    df = pd.read_pickle(PANEL)
    print(f"loaded rows={len(df):,} dates {df.trade_date.min()}..{df.trade_date.max()}")
    summary = []
    for label, col, exp in FACTORS:
        r = screen_factor(df, col, exp)
        print(f"\n### {label}  期望Q5-Q1={'+' if exp>0 else '-'}")
        rec = {"factor": label, "col": col, "exp": exp}
        for wn in WINDOWS:
            x = r.get(wn)
            rec[wn] = x
            if x is None:
                print(f"  {wn}: 样本/覆盖不足"); continue
            qs = " ".join(f"Q{i+1}={v:+.3f}" for i, v in enumerate(x["qmeans"]))
            tb = " ".join(f"{v*100:.1f}" for v in x["tail_bad"])
            tg = " ".join(f"{v*100:.1f}" for v in x["tail_good"])
            print(f"  {wn}: n={x['n']:>7,}/d{x['days']:>3} {qs}")
            print(f"        Q5-Q1={x['spread']:+.4f} 同号{x['samesign']:.2f}({x['nmonths']}m) "
                  f"IC={x['ic']:+.4f} ICIR={x['ic_ir']:+.3f} 单调度{x['mono']:.2f}")
            print(f"        大亏率(≤-5%) Q1..Q5: {tb} | 大赢率(≥+5%): {tg}")
        summary.append(rec)
    print("\n\n========== 三窗口一致性汇总（fwd5）==========")
    print(f"{'factor':<32}{'W1':>10}{'W2':>10}{'W3':>10}  一致 同号率  IC均")
    out_rows = []
    for rec in summary:
        sp = {w: (rec[w]["spread"] if rec[w] else None) for w in WINDOWS}
        ics = [rec[w]["ic"] for w in WINDOWS if rec[w]]
        ss = [rec[w]["samesign"] for w in WINDOWS if rec[w]]
        valid = [sp[w] for w in WINDOWS if sp[w] is not None]
        consist = bool(valid) and len({np.sign(v) for v in valid}) == 1 and np.sign(valid[0]) == np.sign(rec["exp"])
        avgss = float(np.mean(ss)) if ss else None
        avgic = float(np.mean(ics)) if ics else None
        f = lambda v: f"{v:>+10.4f}" if v is not None else f"{'-':>10}"
        print(f"{rec['factor']:<32}{f(sp['W1_frozen'])}{f(sp['W2_forward'])}{f(sp['W3_third'])}  "
              f"{'★' if consist else ' '}   {avgss if avgss is None else round(avgss,2)}  {avgic if avgic is None else round(avgic,4)}")
        out_rows.append({"factor": rec["factor"], "col": rec["col"], "spreads": sp,
                         "consistent": consist, "avg_samesign": avgss, "avg_ic": avgic,
                         "detail": {w: rec[w] for w in WINDOWS}})
    (OUT / "anomaly_factor_screen.json").write_text(json.dumps(
        out_rows, ensure_ascii=False, indent=2,
        default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print("\nsaved output/research/anomaly_factor_screen.json")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("build", "all"):
        build_panel()
    if mode in ("screen", "all"):
        screen()
