"""§327 追龙预测性分析：首板日及之前可知的特征 能否区分「后面连板≥3」(Y=1) 和「首板就断」(Y=0, L=1)。
规则见 docs/strategy-research.md §327（预注册，不得改）。只读本地缓存与已落盘分钟线。
用法（cd backend）：
  PYTHONPATH=. .venv/bin/python scripts/shortline_research/lhd_s327_predict.py tier1   # 全市场 2020-2026 日线+limit_list_d
  PYTHONPATH=. .venv/bin/python scripts/shortline_research/lhd_s327_predict.py tier2   # 230 只分钟样本
  PYTHONPATH=. .venv/bin/python scripts/shortline_research/lhd_s327_predict.py tier3   # 小石全市场 3 个月（描述性）
输出 output/research/s327/*.json"""
from __future__ import annotations
import json, sys, glob, gc, bisect
from pathlib import Path
import numpy as np

B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm")); sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = B / "output/research/s327"
EVENTS = B / "output/research/s325/events.parquet"
LLD = B / "data/research_cache/limit_list_d"
EPS = 0.005
FEATS22 = ["ret5", "ret20", "ret60", "dist60", "rng60", "pct_T", "yizi", "lu_s", "reseal", "fr_close", "fr_lu", "turnover", "volratio",
           "ff", "amount", "gx120", "dsl", "theme_n", "theme_first", "mkt_lu", "mkt_up", "b20"]
LOGF = {"ff", "amount", "volratio", "turnover", "fr_close", "fr_lu", "mkt_lu", "dsl", "theme_n"}
LLD_F = ["open_times", "first_time", "last_time"]
MIN_T = ["m_touch", "m_seal", "m_open_n", "m_vol30", "m_vol_pre", "m_vol_post", "m_gap", "m_dd_pre", "m_ret10", "m_low"]
MIN_P = ["m1_pos", "m1_amp", "m1_vol30", "m1_tail", "m1_upper", "m_vol30_ratio"]
MIN_F = MIN_T + MIN_P


# ---------------- 纯函数（有单测） ----------------
def trade_minute_index(tm):
    """分钟-of-day(9:30=570) → 交易分钟序号 0..240（午休折叠）。"""
    tm = np.asarray(tm).astype(np.int64)
    rel = tm - 570
    return np.where(tm <= 690, rel, rel - 90)


def day_stats(tm, o, h, l, c, v, u, prev_close):
    """T 日分钟派生特征。u=涨停价；prev_close=昨收。"""
    idx = trade_minute_index(tm)
    n = len(idx)
    out = {k: np.nan for k in MIN_T}
    if n < 30 or not prev_close:
        return out
    touch = np.where(h >= u - EPS)[0]
    sealed = c >= u - EPS
    tot = float(v.sum()) or np.nan
    t0 = int(touch[0]) if len(touch) else n - 1
    uns = np.where(~sealed)[0]
    s0 = (int(uns[-1]) + 1) if len(uns) else 0
    s0 = min(s0, n - 1)
    out["m_touch"] = float(idx[t0]) if len(touch) else 240.0
    out["m_seal"] = float(idx[s0]) if sealed[-1] else 240.0
    out["m_open_n"] = float(sum(1 for i in range(t0 + 1, n) if sealed[i - 1] and not sealed[i])) if len(touch) else 0.0
    out["m_vol30"] = float(v[idx <= 30].sum() / tot)
    out["m_vol_pre"] = float(v[:t0].sum() / tot)
    out["m_vol_post"] = float(v[s0:].sum() / tot)
    out["m_gap"] = float(o[0] / prev_close - 1)
    run_hi = np.maximum.accumulate(h[: t0 + 1])
    out["m_dd_pre"] = float((c[: t0 + 1] / run_hi - 1).min())
    k10 = np.where(tm <= 600)[0]
    out["m_ret10"] = float(c[k10[-1]] / prev_close - 1) if len(k10) else np.nan
    out["m_low"] = float(l.min() / prev_close - 1)
    return out


def prev_day_stats(tm, o, h, l, c, v):
    """T−1 日形态 + 开盘30分钟绝对成交量。"""
    idx = trade_minute_index(tm)
    out = {k: np.nan for k in MIN_P if k != "m_vol30_ratio"}
    out["_v30abs"] = np.nan
    if len(idx) < 30:
        return out
    hi, lo = float(h.max()), float(l.min())
    rng = hi - lo
    tot = float(v.sum()) or np.nan
    out["m1_pos"] = float((c[-1] - lo) / rng) if rng > 0 else 0.5
    out["m1_amp"] = float(rng / lo) if lo > 0 else np.nan
    out["m1_vol30"] = float(v[idx <= 30].sum() / tot)
    out["m1_tail"] = float(v[idx >= 211].sum() / tot)
    out["m1_upper"] = float((hi - max(o[0], c[-1])) / rng) if rng > 0 else 0.0
    out["_v30abs"] = float(v[idx <= 30].sum())
    return out


def auc(y, s):
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y); s = np.asarray(s)
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, s))


def boot_ci(y, s, n=1000, seed=0):
    y = np.asarray(y); s = np.asarray(s)
    rng = np.random.default_rng(seed)
    r = []
    for _ in range(n):
        ix = rng.integers(0, len(y), len(y))
        if len(np.unique(y[ix])) < 2:
            continue
        r.append(auc(y[ix], s[ix]))
    return [round(float(np.percentile(r, 2.5)), 4), round(float(np.percentile(r, 97.5)), 4)] if r else [None, None]


def hit_table(y, s, quants=(0.9, 0.8)):
    """打分最高 10%/20% 档里 Y=1 的比例、基准、提升倍数、召回。"""
    y = np.asarray(y); s = np.asarray(s)
    base = float(y.mean()) if len(y) else float("nan")
    out = {"n": int(len(y)), "pos": int(y.sum()), "base": round(base, 4)}
    for q in quants:
        thr = np.quantile(s, q)
        m = s >= thr
        hit = float(y[m].mean()) if m.any() else float("nan")
        out[f"top{int(round((1 - q) * 100))}"] = {"n": int(m.sum()), "hit": round(hit, 4), "lift": round(hit / base, 2) if base > 0 else None,
                                                 "recall": round(float(y[m].sum() / max(y.sum(), 1)), 3)}
    return out


def split_trades(ev_rows, cond):
    return [r for r in ev_rows if cond(r)]


# ---------------- 通用：模型滚动 ----------------
def make_xy(ev, feats):
    X = ev[feats].copy()
    for f in feats:
        if f in LOGF:
            X[f] = np.log1p(X[f].clip(lower=0))
    return X


def rolling_oos(ev, feats, model_kind, test_years, min_train=300):
    """ev 需含列 trade_date(str)、year、Y(0/1/nan)、观测可用。返回 (score Series, coef or None)。"""
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    X = make_xy(ev, feats)
    score = np.full(len(ev), np.nan)
    thr_by_year = {}
    for y in test_years:
        tr = np.where((ev["trade_date"].values < f"{y-1}-12-23") & ev["Y"].notna().values)[0]
        te = np.where(ev["year"].values == y)[0]
        if len(te) == 0 or len(tr) < min_train or len(np.unique(ev["Y"].values[tr])) < 2:
            continue
        if model_kind == "lr":
            med = X.iloc[tr].median(); mu = X.iloc[tr].mean(); sd = X.iloc[tr].std().replace(0, 1)
            prep = lambda ix: ((X.iloc[ix].fillna(med) - mu) / sd).clip(-3, 3).fillna(0)
            m = LogisticRegression(C=1.0, max_iter=500).fit(prep(tr), ev["Y"].values[tr])
            score[te] = m.predict_proba(prep(te))[:, 1]
            thr_by_year[y] = float(np.quantile(m.predict_proba(prep(tr))[:, 1], 0.9))
        else:
            m = HistGradientBoostingClassifier(max_depth=3, max_iter=100, learning_rate=0.05, random_state=0).fit(X.iloc[tr], ev["Y"].values[tr])
            score[te] = m.predict_proba(X.iloc[te])[:, 1]
            thr_by_year[y] = float(np.quantile(m.predict_proba(X.iloc[tr])[:, 1], 0.9))
    return score, thr_by_year


def eval_scores(ev, score, label):
    ok = ~np.isnan(score) & ev["Y"].notna().values
    if ok.sum() == 0:
        return {"label": label, "note": "无样本外分数"}
    y = ev["Y"].values[ok].astype(int); s = score[ok]; yr = ev["year"].values[ok]
    res = {"label": label, "pooled_auc": round(auc(y, s), 4), "ci95": boot_ci(y, s), "by_year": {}}
    for yy in sorted(set(yr)):
        m = yr == yy
        res["by_year"][int(yy)] = {"auc": round(auc(y[m], s[m]), 4), "n": int(m.sum()), "pos": int(y[m].sum())}
    res["hit"] = hit_table(y, s)
    return res


def uni_auc(ev, feats, test_years):
    """单特征 AUC（全样本；方向=AUC>0.5 为正相关），及分年方向一致性。"""
    d = ev[ev["Y"].notna()]
    out = {}
    for f in feats:
        x = d[f]
        ok = x.notna()
        if ok.sum() < 100 or d["Y"][ok].nunique() < 2:
            continue
        a = auc(d["Y"][ok].astype(int), x[ok])
        yrs = {}
        for yy in test_years:
            g = d[(d["year"] == yy) & ok]
            if len(g) > 50 and g["Y"].nunique() == 2:
                yrs[int(yy)] = round(auc(g["Y"].astype(int), g[f]), 3)
        same = sum(1 for v in yrs.values() if (v - 0.5) * (a - 0.5) > 0)
        out[f] = {"auc": round(a, 4), "n": int(ok.sum()), "years": yrs, "same_dir_years": f"{same}/{len(yrs)}",
                  "has_power": bool(abs(a - 0.5) >= 0.05 and len(yrs) > 0 and same / len(yrs) >= 0.7)}
    return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]["auc"] - 0.5)))


# ---------------- 数据 ----------------
def load_events(min_date="2019-01-01"):
    import polars as pl
    ev = pl.read_parquet(EVENTS).filter(pl.col("observable") & (pl.col("trade_date") >= min_date)).to_pandas()
    ev["year"] = ev["trade_date"].str[:4].astype(int)
    ev["Y"] = np.where(ev["L"] >= 3, 1.0, np.where(ev["L"] == 1, 0.0, np.nan))
    ev["Y2"] = np.where(ev["L"] >= 2, 1.0, 0.0)
    return ev


def attach_lld(ev):
    import pandas as pd
    fs = sorted(glob.glob(str(LLD / "limit_list_d_*.parquet")))
    if not fs:
        for f in LLD_F:
            ev[f] = np.nan
        return ev, {}
    d = pd.concat([pd.read_parquet(f) for f in fs])
    d = d[d["limit"] == "U"].copy()
    d["code"] = d["ts_code"].str[:6]
    d["trade_date"] = d["trade_date"].str[:4] + "-" + d["trade_date"].str[4:6] + "-" + d["trade_date"].str[6:8]
    def sec(x):
        x = str(x).zfill(6)
        return int(x[:2]) * 3600 + int(x[2:4]) * 60 + int(x[4:6])
    d["first_time"] = d["first_time"].map(sec); d["last_time"] = d["last_time"].map(sec)
    d = d.drop_duplicates(["code", "trade_date"])[["code", "trade_date", "open_times", "first_time", "last_time"]]
    ev = ev.merge(d, on=["code", "trade_date"], how="left")
    cov = {int(y): round(float(g["open_times"].notna().mean()), 3) for y, g in ev.groupby("year")}
    return ev, cov


def next_returns(ev):
    """Tier1 盈亏：T+1 开盘<涨停×0.998 才买，开盘×1.001 买、T+3 收盘卖、扣 0.3%。用复权价算收益。"""
    import polars as pl
    import lt_data as LD
    c = pl.col
    df = LD.prices("2019-01-01", "2026-10-16").select("code", "trade_date", "open", "close", "pct_chg").sort("code", "trade_date")
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    df = df.with_columns((c("open") * c("ac") / c("close")).alias("ao"))
    df = df.with_columns(c("open").shift(-1).over("code").alias("o1"), c("ao").shift(-1).over("code").alias("ao1"),
                         c("ac").shift(-3).over("code").alias("ac3"))
    d = df.select("code", "trade_date", "o1", "ao1", "ac3").to_pandas()
    ev = ev.merge(d, on=["code", "trade_date"], how="left")
    ev["buyable1"] = ev["o1"] < ev["uplim"] * 0.998
    ev["net_h2"] = (ev["ac3"] / (ev["ao1"] * 1.001) - 1) * 100 - 0.3
    return ev


def money_check(ev, score, thr_by_year):
    """Top10%（阈值取训练集）买得到的笔数与每笔。"""
    import pandas as pd
    ev = ev.copy(); ev["score"] = score
    ev["top"] = [(s >= thr_by_year.get(y, np.inf)) if not np.isnan(s) else False for s, y in zip(ev["score"], ev["year"])]
    t = ev[ev["top"]]
    b = t[t["buyable1"] & t["net_h2"].notna()]
    return {"top_n": int(len(t)), "top_pos_rate": round(float((t["L"] >= 3).mean()), 4) if len(t) else None,
            "top_buyable_n": int(t["buyable1"].sum()), "top_buyable_share": round(float(t["buyable1"].mean()), 4) if len(t) else None,
            "buy_n": int(len(b)), "buy_avg": round(float(b["net_h2"].mean()), 3) if len(b) else None,
            "buy_median": round(float(b["net_h2"].median()), 3) if len(b) else None,
            "all_first_board_buyable_avg": round(float(ev[ev["buyable1"] & ev["net_h2"].notna()]["net_h2"].mean()), 3)}


# ---------------- Tier 1 ----------------
def tier1():
    OUT.mkdir(parents=True, exist_ok=True)
    ev = load_events("2020-01-01")
    ev, cov = attach_lld(ev)
    ev = next_returns(ev)
    ty = [2022, 2023, 2024, 2025, 2026]
    res = {"n_events": int(len(ev)), "n_Y": int(ev["Y"].notna().sum()), "pos": int((ev["Y"] == 1).sum()), "lld_coverage_by_year": cov}
    res["uni_auc_22"] = uni_auc(ev, FEATS22, ty)
    res["uni_auc_lld"] = uni_auc(ev[ev["open_times"].notna()], LLD_F, ty) if ev["open_times"].notna().any() else {}
    for tag, feats in (("base22", FEATS22), ("plus_lld", FEATS22 + LLD_F)):
        sub = ev
        if tag == "plus_lld":
            late = ev[ev["year"] >= 2022]
            if late["open_times"].notna().mean() < 0.9:
                res[tag] = f"limit_list_d 对 2022–2026 事件覆盖不足（{late['open_times'].notna().mean():.0%}），未评估"
                continue
            sub = ev[ev["open_times"].notna()].reset_index(drop=True)
            if len(sub) < 2000:
                res[tag] = "limit_list_d 覆盖不足，未评估"
                continue
        for mk in ("lr", "hgb"):
            sc, thr = rolling_oos(sub, feats, mk, ty)
            r = eval_scores(sub, sc, f"{tag}_{mk}")
            r["money_top10"] = money_check(sub, sc, thr)
            res[f"{tag}_{mk}"] = r
            print(tag, mk, r["pooled_auc"], r["ci95"], r["by_year"], flush=True)
    # 次要标签 Y2
    ev2 = ev.copy(); ev2["Y"] = ev2["Y2"]
    sc, thr = rolling_oos(ev2, FEATS22, "lr", ty)
    res["secondary_Y2_lr_base22"] = eval_scores(ev2, sc, "Y2_lr")
    (OUT / "tier1.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("tier1 done")


# ---------------- Tier 2 ----------------
def build_minute_feats_230():
    import polars as pl
    import os
    import lhd_s326_pilot as P
    ev = load_events("2019-01-01")
    codes = sorted(os.path.basename(p) for p in glob.glob(str(P.MIN / "*")))
    ev = ev[ev["code"].isin(codes)].reset_index(drop=True)
    rows = []
    for ci, code in enumerate(codes):
        e = ev[ev["code"] == code]
        if len(e) == 0:
            continue
        tds = P.code_trading_days(code)
        need = set(); plan = []
        for _, r in e.iterrows():
            t = r["trade_date"].replace("-", "")
            i = bisect.bisect_left(tds, t)
            if i == 0 or i >= len(tds) or tds[i] != t:
                continue
            plan.append((r, t, tds[i - 1])); need.update((t, tds[i - 1]))
        days = load_days_tm(code, need)
        for r, t, tp in plan:
            if t not in days or tp not in days:
                continue
            dT, dP = days[t], days[tp]
            pc = float(dP["close"][-1])
            f = day_stats(dT["tm"], dT["open"], dT["high"], dT["low"], dT["close"], dT["vol"], float(r["uplim"]), pc)
            g = prev_day_stats(dP["tm"], dP["open"], dP["high"], dP["low"], dP["close"], dP["vol"])
            f.update({k: v for k, v in g.items() if not k.startswith("_")})
            v30 = float(dT["vol"][trade_minute_index(dT["tm"]) <= 30].sum())
            f["m_vol30_ratio"] = v30 / g["_v30abs"] if g["_v30abs"] and not np.isnan(g["_v30abs"]) else np.nan
            f["code"] = code; f["trade_date"] = r["trade_date"]
            rows.append(f)
        del days; gc.collect()
        if ci % 40 == 0:
            print("minute feats", ci, len(rows), flush=True)
    import pandas as pd
    mf = pd.DataFrame(rows)
    return ev.merge(mf, on=["code", "trade_date"], how="left")


def load_days_tm(code, dates):
    """与 lhd_s326_pilot.load_code_days 相同口径（base 优先、update 补缺），另带分钟-of-day 数组 tm。"""
    import polars as pl
    import lhd_s326_pilot as P
    days = {}

    def take(df):
        if df.height == 0:
            return
        if df.schema["trade_time"] == pl.String:
            df = df.with_columns(pl.col("trade_time").str.to_datetime())
        df = df.with_columns(pl.col("trade_date").cast(pl.Date).dt.strftime("%Y%m%d").alias("d"),
                             (pl.col("trade_time").dt.hour().cast(pl.Int32) * 60 + pl.col("trade_time").dt.minute().cast(pl.Int32)).alias("tm")).sort("trade_time")
        for (d,), g in df.group_by(["d"], maintain_order=True):
            if d in dates and d not in days:
                days[d] = {k: g[k].to_numpy() for k in ("open", "high", "low", "close", "vol", "tm")}
    bp = P.MIN / code / "base/base.parquet"
    if bp.exists():
        lf = pl.scan_parquet(bp).select(["open", "high", "low", "close", "vol", "trade_date", "trade_time"])
        lf = lf.filter(pl.col("trade_date").cast(pl.Date).dt.strftime("%Y%m%d").is_in(list(dates)))
        take(lf.collect())
    for up in sorted(glob.glob(str(P.MIN / code / "*/update.parquet"))):
        u = pl.read_parquet(up)
        ren = {"date": "trade_date", "datetime": "trade_time", "volume": "vol"}
        u = u.rename({a: b for a, b in ren.items() if a in u.columns and b not in u.columns})
        u = P._to_date(u.select(["open", "high", "low", "close", "vol", "trade_date", "trade_time"]))
        take(u)
    return days


def tier2():
    import pandas as pd
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "tier2_events.parquet"
    if cache.exists():
        ev = pd.read_parquet(cache)
    else:
        ev = build_minute_feats_230()
        ev.to_parquet(cache)
    ev, cov = attach_lld(ev)
    ev = next_returns(ev)
    ty = [2022, 2023, 2024, 2025, 2026]
    ev = ev.reset_index(drop=True)
    res = {"n_events": int(len(ev)), "n_Y": int(ev["Y"].notna().sum()), "pos": int((ev["Y"] == 1).sum()),
           "minute_feat_coverage": round(float(ev["m_touch"].notna().mean()), 3), "lld_coverage_by_year": cov}
    res["uni_auc_minute"] = uni_auc(ev, MIN_F, ty)
    res["uni_auc_22"] = uni_auc(ev, FEATS22, ty)
    feats_sets = {"base22": FEATS22, "base22+minute": FEATS22 + MIN_F, "minute_only": MIN_F}
    if ev["open_times"].notna().mean() > 0.8:
        feats_sets["base22+lld"] = FEATS22 + LLD_F
        feats_sets["base22+lld+minute"] = FEATS22 + LLD_F + MIN_F
    scores = {}
    for tag, feats in feats_sets.items():
        sc, thr = rolling_oos(ev, feats, "lr", ty, min_train=300)
        scores[tag] = (sc, thr)
        r = eval_scores(ev, sc, tag)
        r["money_top10"] = money_check(ev, sc, thr)
        res[tag] = r
        print(tag, r["pooled_auc"], r["ci95"], flush=True)
    # 增量：同一批有分数的样本上，含分钟 vs 不含分钟的 AUC 差及 bootstrap CI
    a, b = scores["base22"][0], scores["base22+minute"][0]
    ok = ~np.isnan(a) & ~np.isnan(b) & ev["Y"].notna().values
    y = ev["Y"].values[ok].astype(int); sa, sb = a[ok], b[ok]
    rng = np.random.default_rng(0); d = []
    for _ in range(1000):
        ix = rng.integers(0, len(y), len(y))
        if len(np.unique(y[ix])) < 2:
            continue
        d.append(auc(y[ix], sb[ix]) - auc(y[ix], sa[ix]))
    res["minute_increment"] = {"auc_base": round(auc(y, sa), 4), "auc_plus_minute": round(auc(y, sb), 4), "delta": round(auc(y, sb) - auc(y, sa), 4),
                               "delta_ci95": [round(float(np.percentile(d, 2.5)), 4), round(float(np.percentile(d, 97.5)), 4)], "n": int(ok.sum()), "pos": int(y.sum())}
    # §326 保守成交口径的 Top10% 每笔
    em = {(r["code"], r["T"]): r for r in json.loads((B / "output/research/s326/events_minute.json").read_text())}
    sc, thr = scores["base22+minute"]
    top = [(i, r) for i, r in ev.iterrows() if not np.isnan(sc[i]) and sc[i] >= thr.get(r["year"], np.inf)]
    nets = []
    for i, r in top:
        m = em.get((r["code"], r["trade_date"].replace("-", "")))
        if not m:
            continue
        for tag in ("evt", "ctl"):
            x = m.get(f"{tag}_h2")
            if isinstance(x, dict):
                nets.append(x["net"]); break
    res["conservative_fill_top10_base22+minute"] = {"top_n": len(top), "filled_n": len(nets), "avg": round(float(np.mean(nets)), 3) if nets else None,
                                                    "median": round(float(np.median(nets)), 3) if nets else None}
    (OUT / "tier2.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("tier2 done")


# ---------------- Tier 3 ----------------
def tier3():
    import polars as pl
    import pandas as pd
    import sync_xiaoshi_min_all as S
    OUT.mkdir(parents=True, exist_ok=True)
    ev = load_events("2026-07-14")
    ev = ev[ev["trade_date"] <= "2026-10-09"].reset_index(drop=True)
    codes = sorted(ev["code"].unique())
    rows = []
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        df = S.load_minutes(chunk)
        if df.height == 0:
            continue
        df = df.with_columns(pl.col("ts_code").str.slice(0, 6).alias("code"), pl.col("trade_date").dt.strftime("%Y-%m-%d").alias("d"),
                             (pl.col("trade_time").dt.hour().cast(pl.Int32) * 60 + pl.col("trade_time").dt.minute().cast(pl.Int32)).alias("tm"))
        by = {}
        for (code, d), g in df.sort("trade_time").group_by(["code", "d"], maintain_order=True):
            by[(code, d)] = {k: g[k].to_numpy() for k in ("open", "high", "low", "close", "vol", "tm")}
        for _, r in ev[ev["code"].isin(chunk)].iterrows():
            dT = by.get((r["code"], r["trade_date"]))
            days = sorted(d for (c_, d) in by if c_ == r["code"] and d < r["trade_date"])
            if dT is None or not days:
                continue
            dP = by[(r["code"], days[-1])]
            f = day_stats(dT["tm"], dT["open"], dT["high"], dT["low"], dT["close"], dT["vol"], float(r["uplim"]), float(dP["close"][-1]))
            g = prev_day_stats(dP["tm"], dP["open"], dP["high"], dP["low"], dP["close"], dP["vol"])
            f.update({k: v for k, v in g.items() if not k.startswith("_")})
            v30 = float(dT["vol"][trade_minute_index(dT["tm"]) <= 30].sum())
            f["m_vol30_ratio"] = v30 / g["_v30abs"] if g["_v30abs"] and not np.isnan(g["_v30abs"]) else np.nan
            f["code"] = r["code"]; f["trade_date"] = r["trade_date"]
            rows.append(f)
        del df; gc.collect()
        print("tier3", i, len(rows), flush=True)
    mf = pd.DataFrame(rows)
    ev = ev.merge(mf, on=["code", "trade_date"], how="left")
    res = {"n_events": int(len(ev)), "n_Y": int(ev["Y"].notna().sum()), "pos": int((ev["Y"] == 1).sum()),
           "minute_feat_coverage": round(float(ev["m_touch"].notna().mean()), 3) if len(ev) else 0,
           "uni_auc_minute": uni_auc(ev, MIN_F, []) if ev["Y"].notna().sum() > 100 else {}}
    res["note"] = "描述性；龙头样本<30 不下结论" if res["pos"] < 30 else "描述性"
    (OUT / "tier3.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("tier3 done", res["n_events"], res["pos"])


if __name__ == "__main__":
    {"tier1": tier1, "tier2": tier2, "tier3": tier3}[sys.argv[1]]()
