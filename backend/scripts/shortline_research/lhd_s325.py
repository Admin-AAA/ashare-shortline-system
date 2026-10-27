"""[短线研究 §325] 追龙 LHD：首板日特征 → 连板龙头打分，T+1 开盘买得到才买（预注册 docs/strategy-research.md §325，结果出来前写定，不调参）。
只读本地缓存（kpl_list + midterm 长面板），不调 promax/tushare。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/lhd_s325.py
输出：backend/output/research/s325/{events.parquet,desc.json,trades.json}"""
import sys, json, glob
from pathlib import Path
import numpy as np
import pandas as pd
import polars as pl
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lt_data as L
from realistic import add_limits, COST
import theme_leader_s156 as S
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

c = pl.col
B = Path(__file__).resolve().parents[2]
OUT = B / "output/research/s325"
KPL = B / "data/research_cache/kpl_list"
START, END = "2019-01-01", "2026-10-16"
TOPN, THR_BUY = 3, 0.998
SEEDS = (1, 2, 3)
FEATS = ["ret5", "ret20", "ret60", "dist60", "rng60", "pct_T", "yizi", "lu_s", "reseal", "fr_close", "fr_lu", "turnover", "volratio",
         "ff", "amount", "gx120", "dsl", "theme_n", "theme_first", "mkt_lu", "mkt_up", "b20"]
LOGF = {"ff", "amount", "volratio", "turnover", "fr_close", "fr_lu", "mkt_lu", "dsl", "theme_n"}


def sec(x):
    return c(x).str.slice(0, 2).cast(pl.Int32, strict=False) * 3600 + c(x).str.slice(3, 2).cast(pl.Int32, strict=False) * 60 \
        + c(x).str.slice(6, 2).cast(pl.Int32, strict=False)


def load_kpl2():
    k = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "lu_time", "last_time", "status", "theme", "limit_order",
                                                  "lu_limit_order", "free_float", "turnover_rate"])
                   for f in sorted(glob.glob(str(KPL / "kpl_*.parquet")))], how="diagonal_relaxed")
    k = k.with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("trade_date"))
    return k.unique(["code", "trade_date"])


def panel(k):
    df = L.prices(START, END)
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    kf = c("ac") / c("close")
    df = df.with_columns((c("open") * kf).alias("ao"), (c("low") * kf).alias("al"), (c("high") * kf).alias("ah"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    df = df.with_columns(pl.coalesce(c("up_limit"), pc * (1 + band)).alias("uplim"))
    lu = k.select("code", "trade_date").with_columns(pl.lit(1).alias("is_lu"))
    df = df.join(lu, on=["code", "trade_date"], how="left").with_columns(c("is_lu").fill_null(0))
    return df


def features(df, k):
    g = c("code")
    df = df.with_columns(
        (c("ac").shift(1) / c("ac").shift(6) - 1).over(g).alias("ret5"),
        (c("ac").shift(1) / c("ac").shift(21) - 1).over(g).alias("ret20"),
        (c("ac").shift(1) / c("ac").shift(61) - 1).over(g).alias("ret60"),
        c("ah").shift(1).rolling_max(60).over(g).alias("max60"),
        c("al").shift(1).rolling_min(60).over(g).alias("min60"),
        (c("volume") / c("volume").shift(1).rolling_mean(20).over(g)).alias("volratio"),
        c("is_lu").shift(1).rolling_sum(120, min_samples=1).over(g).fill_null(0).alias("gx120"),
        c("is_lu").shift(1).over(g).alias("prev_lu"),
        pl.when(c("is_lu") == 1).then(c("age")).otherwise(None).forward_fill().shift(1).over(g).alias("last_lu_age"),
        ((c("open") >= c("uplim") - 0.005) & (c("high") == c("low"))).alias("yizi"),
    )
    df = df.with_columns((c("ac").shift(1).over(g) / c("max60") - 1).alias("dist60"),
                         ((c("max60") - c("min60")) / c("min60")).alias("rng60"),
                         (c("age") - c("last_lu_age")).alias("dsl"))
    # 连板数 L：从该行起连续涨停的行数
    df = df.with_columns((c("is_lu") != c("is_lu").shift(1).over(g)).fill_null(True).cum_sum().over(g).alias("rid"))
    df = df.with_columns(pl.len().over(g, "rid").alias("rlen"), pl.int_range(pl.len()).over(g, "rid").alias("rpos"))
    df = df.with_columns((c("rlen") - c("rpos")).alias("L"))
    # 市场上涨家数占比
    mk = df.group_by("trade_date").agg((c("pct_chg") > 0).mean().alias("mkt_up"))
    df = df.join(mk, on="trade_date", how="left")
    # kpl 日级
    mlu = k.group_by("trade_date").agg(pl.len().alias("mkt_lu"))
    th = k.with_columns(c("theme").fill_null("").str.split("、").alias("th")).explode("th").with_columns(c("th").str.strip_chars())
    th = th.filter((c("th").str.len_chars() > 0) & ~pl.any_horizontal([c("th").str.contains(b, literal=True) for b in S.BAD]))
    th = th.with_columns(sec("lu_time").alias("lus"))
    th = th.with_columns(pl.len().over("trade_date", "th").alias("n"),
                         pl.struct(a=c("lus"), b=c("code")).rank("ordinal").over("trade_date", "th").alias("rk"))
    th = th.sort(["trade_date", "code", "n"], descending=[False, False, True]).group_by("trade_date", "code", maintain_order=True).first()
    th = th.select("trade_date", "code", c("n").alias("theme_n"), (c("rk") == 1).cast(pl.Int32).alias("theme_first"))
    ev = df.filter((c("is_lu") == 1) & (c("prev_lu").fill_null(0) == 0))
    kk = k.select("code", "trade_date", sec("lu_time").alias("lu_s"), sec("last_time").alias("last_s"), "status",
                  (c("limit_order") / c("free_float")).alias("fr_close"), (c("lu_limit_order") / c("free_float")).alias("fr_lu"),
                  c("turnover_rate").alias("turnover"), (c("free_float") / 1e8).alias("ff"))
    ev = ev.join(kk, on=["code", "trade_date"], how="left").join(th, on=["code", "trade_date"], how="left").join(mlu, on="trade_date", how="left")
    ev = ev.with_columns(c("theme_n").fill_null(0), c("theme_first").fill_null(0),
                         ((c("last_s") - c("lu_s")) > 60).cast(pl.Int32).alias("reseal"),
                         c("pct_chg").alias("pct_T"), (c("amount") / 1e8).alias("amount"),
                         (c("code").str.starts_with("3") | c("code").str.starts_with("688")).cast(pl.Int32).alias("b20"),
                         c("yizi").cast(pl.Int32))
    uni = (~c("st")) & (c("age") >= 60) & (c("amount") >= 0.5) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) \
        & (c("status") != "新上市未开板").fill_null(True)
    ev = ev.filter(uni)
    return df, ev


def sim(a, i0, n, elc=True):
    """T 行 i0；e=i0+1 开盘买。返回 (net_pct, exit_idx, hold) 或 None（买不进）。"""
    e = i0 + 1; N = len(a["trade_date"])
    if e >= N - 1: return None
    if a["open"][e] >= a["uplim"][e] * THR_BUY: return None
    px = a["ao"][e]; low_e = a["al"][e]
    for i in range(e, N - 1):
        if elc and i > e and a["ac"][i] < low_e:
            j = i + 1
            while j < N - 1 and a["lock_down"][j]: j += 1
            return (a["ao"][j] / px - 1 - COST) * 100, j, j - e
        if i >= e + n:
            j = i
            if a["lock_down"][j]:
                j = i + 1
                while j < N - 1 and a["lock_down"][j]: j += 1
                return (a["ao"][j] / px - 1 - COST) * 100, j, j - e
            return (a["ac"][j] / px - 1 - COST) * 100, j, j - e
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    k = load_kpl2()
    df = panel(k)
    df, ev = features(df, k)
    ev = ev.select("code", "trade_date", "age", "L", "pct_T", *[f for f in FEATS if f != "pct_T"], "uplim", "status").to_pandas()
    ev[FEATS] = ev[FEATS].replace([np.inf, -np.inf], np.nan)
    ev["year"] = ev["trade_date"].str[:4].astype(int)
    print("events", len(ev), flush=True)
    # 数组缓存
    cols = ["trade_date", "open", "ao", "ac", "al", "uplim", "lock_down"]
    sub = df.select("code", *cols)
    arr = {code: {col: p[col].to_numpy() for col in cols} for (code,), p in sub.partition_by("code", as_dict=True, maintain_order=True).items()}
    idx = {code: {d: i for i, d in enumerate(a["trade_date"])} for code, a in arr.items()}
    del df, sub
    # 次日开盘能否买进（事件级）+ 一字
    ev["i0"] = [idx[cd][d] for cd, d in zip(ev["code"], ev["trade_date"])]
    ev["buyable"] = [sim(arr[cd], i, 2) is not None for cd, i in zip(ev["code"], ev["i0"])]
    ev["nextopen_lu"] = [(arr[cd]["open"][i + 1] >= arr[cd]["uplim"][i + 1] * THR_BUY) if i + 1 < len(arr[cd]["open"]) else None for cd, i in zip(ev["code"], ev["i0"])]
    ev["cls"] = np.where(ev["L"] >= 4, "LEAD4", np.where(ev["L"] == 3, "LEAD3", np.where(ev["L"] == 2, "TWO", "ONE")))
    ev["lead3"] = (ev["L"] >= 3).astype(int)
    ev["observable"] = ev["trade_date"] <= "2026-10-09"
    ev.drop(columns=["status"]).to_parquet(OUT / "events.parquet")

    # ---------- 描述统计 ----------
    obs = ev[ev["observable"]]
    desc = {"n_events": int(len(obs)), "by_class": obs["cls"].value_counts().to_dict(),
            "nextopen_limitup_share": {cl: round(float(g["nextopen_lu"].astype(float).mean()), 4) for cl, g in obs.groupby("cls")},
            "buyable_share": {cl: round(float(g["buyable"].mean()), 4) for cl, g in obs.groupby("cls")},
            "yizi_T_share": {cl: round(float(g["yizi"].mean()), 4) for cl, g in obs.groupby("cls")},
            "by_class_board": {f"{b}|{cl}": int(len(g)) for (b, cl), g in obs.groupby(["b20", "cls"])},
            "feat": {}, "auc_lead3_vs_one": {}, "auc_by_board": {}}
    for f in FEATS:
        desc["feat"][f] = {cl: {"mean": round(float(g[f].mean()), 4), "median": round(float(g[f].median()), 4)} for cl, g in obs.groupby("cls")}
        x = obs[obs["cls"].isin(["ONE", "LEAD3", "LEAD4"]) & obs[f].notna()]
        y = (x["cls"] != "ONE").astype(int)
        if y.nunique() == 2:
            desc["auc_lead3_vs_one"][f] = round(float(roc_auc_score(y, x[f])), 4)
        for b in (0, 1):
            xb = x[x["b20"] == b]; yb = (xb["cls"] != "ONE").astype(int)
            if yb.nunique() == 2:
                desc["auc_by_board"].setdefault(f, {})[str(b)] = round(float(roc_auc_score(yb, xb[f])), 4)
    # 逐年连板率
    desc["lead3_rate_by_year"] = {int(y): round(float(g["lead3"].mean()), 4) for y, g in obs.groupby("year")}
    (OUT / "desc.json").write_text(json.dumps(desc, ensure_ascii=False, indent=1))
    print("desc done", {k_: desc[k_] for k_ in ("n_events", "by_class", "buyable_share", "nextopen_limitup_share")}, flush=True)

    # ---------- V1 滚动模型 ----------
    ev["p"] = np.nan; ev["p_thr"] = np.nan
    X = ev[FEATS].copy()
    for f in LOGF: X[f] = np.log1p(X[f].clip(lower=0))
    for y in range(2021, 2027):
        tr = ev[(ev["trade_date"] < f"{y-1}-12-20") & ev["observable"]].index
        te = ev[ev["year"] == y].index
        if len(te) == 0 or len(tr) < 500: continue
        med = X.loc[tr].median(); mu = X.loc[tr].mean(); sd = X.loc[tr].std().replace(0, 1)
        def prep(ix):
            z = ((X.loc[ix].fillna(med) - mu) / sd).clip(-3, 3)
            return z
        m = LogisticRegression(C=1.0, max_iter=500).fit(prep(tr), ev.loc[tr, "lead3"])
        ptr = m.predict_proba(prep(tr))[:, 1]
        ev.loc[te, "p"] = m.predict_proba(prep(te))[:, 1]; ev.loc[te, "p_thr"] = np.quantile(ptr, 0.9)
        print("fold", y, "train", len(tr), "test", len(te), "coef", {f: round(float(w), 2) for f, w in zip(FEATS, m.coef_[0])}, flush=True)
    oos = ev[ev["p"].notna() & ev["observable"]]
    desc["model_oos_auc"] = round(float(roc_auc_score(oos["lead3"], oos["p"])), 4)
    desc["model_oos_auc_by_year"] = {int(y): round(float(roc_auc_score(g["lead3"], g["p"])), 4) for y, g in oos.groupby("year") if g["lead3"].nunique() == 2}
    (OUT / "desc.json").write_text(json.dumps(desc, ensure_ascii=False, indent=1))
    print("model auc", desc["model_oos_auc"], desc["model_oos_auc_by_year"], flush=True)

    # ---------- 变体选股 ----------
    ev["fr_c"] = ev["fr_close"].fillna(-1)
    ev["lus"] = ev["lu_s"].fillna(99999)
    ev["h"] = {}.get(0, 0)
    base = ev[ev["trade_date"] >= "2021-01-01"].copy()
    def top(d, key, asc=False):
        d = d.sort_values(["trade_date"] + key[0], ascending=[True] + key[1])
        return d.groupby("trade_date").head(TOPN)
    SEL = {}
    SEL["V1"] = top(base[base["p"].notna() & (base["p"] >= base["p_thr"])], (["p"], [False]))
    SEL["V2"] = top(base[(base["yizi"] == 0) & (base["lu_s"] <= 36000) & (base["reseal"] == 0)], (["fr_c"], [False]))
    SEL["V3"] = top(base[(base["dist60"] <= -0.20) & (base["volratio"] >= 2) & (base["ff"] <= 80) & (base["yizi"] == 0)], (["fr_c"], [False]))
    SEL["V4"] = top(base[(base["gx120"] >= 2) & (base["ret5"] <= 0.10) & (base["yizi"] == 0)], (["gx120", "fr_c"], [False, False]))
    for s in SEEDS:
        b2 = base.copy(); b2["h"] = pd.util.hash_pandas_object(b2["code"] + b2["trade_date"] + str(s), index=False).astype("uint64") % 10**9
        SEL[f"CTRL{s}"] = top(b2, (["h"], [True]))
    SEL["ALL"] = base  # 全部首板（不限3只）
    trades = {}
    for v, d in SEL.items():
        trades[v] = {}
        sig = len(d)
        for ex_key, (n, elc) in {"n2_elc": (2, True), "n1_elc": (1, True), "n3_elc": (3, True), "n2_fix": (2, False)}.items():
            rows = []
            for cd, i0, T, L_, cls in zip(d["code"], d["i0"], d["trade_date"], d["L"], d["cls"]):
                a = arr[cd]; r = sim(a, int(i0), n, elc)
                if r is None: continue
                net, j, hold = r
                rows.append({"sid": v, "dt": T, "code": cd, "net": float(net), "en": a["trade_date"][int(i0) + 1].replace("-", ""),
                             "ex": a["trade_date"][j].replace("-", ""), "hold": int(hold), "L": int(L_)})
            trades[v][ex_key] = rows
        print(v, "signals", sig, "bought", len(trades[v]["n2_elc"]), "buyable%", round(100 * len(trades[v]["n2_elc"]) / max(sig, 1), 1), flush=True)
    (OUT / "trades.json").write_text(json.dumps(trades, ensure_ascii=False))
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
