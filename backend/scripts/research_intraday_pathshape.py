"""日内路径形态新维度三窗筛查（L-074 的 12 个分时特征之外的 4 个新构造，只读不落地）。

与 L-074/L-079 的切割：L-074 测的是水平/分段收益类（gap/am_ret/pm_ret/am_minus_pm/振幅/rvol/
日内mdd/上午量占比/VWAP上方量占比/close_off_high/尾盘30分收益/量价相关）。本轮测【路径形态/量能
方向分布】类新构造：trend_eff(趋势效率)、vol_dir_imb(量能方向不对称)、intraday_skew(日内已实现偏度)、
close_vol_share(尾盘量占比)。均为分钟级衍生、信号日收盘前可得、无前视。
三窗口：冻结(15min原生)/前瞻(5min重采样15min)/第三(run3, 5min)。缺 bar 的卡丢弃并报匹配率。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text

WT = Path(__file__).resolve().parents[1]           # worktree backend
MAIN = Path("/Users/wangao/Desktop/ashare-shortline-system/backend")
sys.path.insert(0, str(WT))
from app.engine.production_policy import load_production_selection_policy  # noqa: E402
from app.engine.aggregator import _market_base, _market_sentiment          # noqa: E402
from scripts.research_third_window import load_run3, build_market_snapshots, select, D0, D1  # noqa: E402

OUT = WT / "output" / "research"; OUT.mkdir(parents=True, exist_ok=True)
CACHE = MAIN / "output" / "research"


def _engine():
    url = [l.split("=", 1)[1].strip().strip('"').strip("'")
           for l in (WT / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    return create_engine(url)


def _bars_to_15(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    df = df[["o", "h", "l", "c", "v"]].reset_index(drop=True)
    if freq == "15min":
        return df
    df["bucket"] = df.index // 3
    g = df.groupby("bucket")
    return pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(),
                         "c": g.c.last(), "v": g.v.sum()}).reset_index(drop=True)


def _fetch_bars(eng, pairs: pd.DataFrame, freq: str) -> dict:
    result = {}
    dates = sorted(pairs.dt.unique())
    for m in sorted({str(d)[:7] for d in dates}):
        md = [d for d in dates if str(d).startswith(m)]
        codes = sorted(pairs[pairs.dt.isin(md)].code.unique().tolist())
        if not codes:
            continue
        sql = text("""SELECT trade_date, code, trade_time, open, high, low, close, volume
                      FROM minute_kbars WHERE freq=:f AND trade_date BETWEEN :d0 AND :d1
                      AND code IN :codes ORDER BY code, trade_time""")
        df = pd.read_sql(sql, eng, params={"f": freq, "d0": min(md), "d1": max(md),
                                           "codes": tuple(codes)})
        df.columns = ["dt", "code", "tt", "o", "h", "l", "c", "v"]
        df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
        for (dt, code), sub in df.groupby(["dt", "code"], sort=False):
            result[(dt, code)] = _bars_to_15(sub.drop(columns=["dt", "code", "tt"]), freq)
    return result


def _features(bars: pd.DataFrame) -> dict:
    c = bars.c.values.astype(float); v = bars.v.values.astype(float)
    o = bars.o.values.astype(float)
    n = len(c)
    if n < 8:
        return {}
    dc = np.diff(c)
    sdc = dc.sum()
    tot_v = v.sum()
    if tot_v <= 0:
        return {}
    te_den = np.abs(dc).sum()
    trend_eff = abs(c[-1] - o[0]) / te_den if te_den > 0 else np.nan
    up_v = v[1:][dc > 0].sum(); dn_v = v[1:][dc < 0].sum()
    vdi = (up_v - dn_v) / tot_v
    br = dc / c[:-1]
    skew = float(pd.Series(br).skew()) if len(br) >= 5 and br.std() > 0 else np.nan
    cvs = v[-2:].sum() / tot_v
    return {"trend_eff": trend_eff, "vol_dir_imb": vdi,
            "intraday_skew": skew, "close_vol_share": cvs}


def main():
    eng = _engine()
    frames = []
    for win, path, freq in [
            ("frozen", CACHE / "canon_frozen_v2_selected.pkl", "15min"),
            ("forward", CACHE / "canon_forward_v2_selected.pkl", "5min")]:
        sel = pd.read_pickle(path)
        sel["dt"] = sel.dt.astype(str); sel["code"] = sel.code.astype(str)
        bars = _fetch_bars(eng, sel[["dt", "code"]], freq)
        rows, miss = [], 0
        for r in sel.itertuples():
            b = bars.get((r.dt, r.code))
            if b is None or len(b) < 8:
                miss += 1; continue
            f = _features(b)
            if not f:
                miss += 1; continue
            f.update({"win": win, "dt": r.dt, "code": r.code, "net": float(r.net)})
            rows.append(f)
        print(f"{win}: selected={len(sel)} matched={len(rows)} miss={miss} "
              f"({len(rows)/len(sel)*100:.1f}%) freq={freq}", flush=True)
        frames.append(pd.DataFrame(rows))
    # 第三窗：v2 生产同构选择
    fw = load_run3()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    fw = fw[fw.dt.isin(snaps)]
    v2 = load_production_selection_policy(version=2)
    s3 = select(fw, snaps, v2)
    s3["dt"] = s3.dt.astype(str); s3["code"] = s3.code.astype(str)
    bars = _fetch_bars(eng, s3[["dt", "code"]], "5min")
    rows, miss = [], 0
    for r in s3.itertuples():
        b = bars.get((r.dt, r.code))
        if b is None or len(b) < 8:
            miss += 1; continue
        f = _features(b)
        if not f:
            miss += 1; continue
        f.update({"win": "run3", "dt": r.dt, "code": r.code, "net": float(r.net)})
        rows.append(f)
    print(f"run3: selected={len(s3)} matched={len(rows)} miss={miss} "
          f"({len(rows)/max(len(s3),1)*100:.1f}%) freq=5min", flush=True)
    frames.append(pd.DataFrame(rows))
    panel = pd.concat(frames, ignore_index=True)
    panel.to_pickle(OUT / "intraday_pathshape_panel.pkl")

    feats = ["trend_eff", "vol_dir_imb", "intraday_skew", "close_vol_share"]
    res = {}
    for w, g in panel.groupby("win"):
        res[w] = {"n": int(len(g))}
        for f in feats:
            sub = g[[f, "net"]].dropna()
            if len(sub) < 30:
                res[w][f] = {"n": int(len(sub)), "note": "样本不足"}
                continue
            rho = sub[f].corr(sub.net, method="spearman")
            q = pd.qcut(sub[f], 5, labels=False, duplicates="drop")
            qm = sub.groupby(q).net.agg(["mean", "count"])
            tail = sub.groupby(q).net.apply(lambda s: (s <= -5).mean())
            res[w][f] = {"n": int(len(sub)), "spearman": round(float(rho), 4),
                         "q_mean": [round(float(x), 3) for x in qm["mean"]],
                         "q_tail_le5": [round(float(x), 3) for x in tail],
                         "q_n": [int(x) for x in qm["count"]]}
    (OUT / "intraday_pathshape_screen.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2))
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
