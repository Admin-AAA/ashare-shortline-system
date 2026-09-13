"""stock_fina_indicator point-in-time 基本面轴预筛（L-082 唯一未做因子级测试的基本面表，只读）。

问题：L-082 只按行数判 stock_fina_indicator "单窗或过稀"，未做 point-in-time 因子测试。
本轮：(1) 逐月统计 PIT 可用覆盖率（每个信号日有 ann_date<=trade_date 的最新财报的股票占比）；
(2) 在覆盖段做全市场日截面五分位 strictly-prior 快筛（L-064 铁律：全市场先行），
    因子 = 最新已公告财报的 netprofit_yoy / or_yoy / roe，以及"新鲜公告事件"
    (ann_date 距信号日 <=10 个交易日 vs 无新鲜公告)。
口径：T 收盘已知 -> T+1 开盘买 -> T+5 收盘卖，毛口径，与 research_newfactor_fullmarket.py 一致。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]
OUT = B / "output/research"
CACHE = OUT / "newfactor_fullmarket.pkl"
FZ = ("2025-01-15", "2026-01-16"); FW = ("2026-02-09", "2026-08-05"); R3 = ("2026-08-07", "2026-09-02")


def engine():
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (B / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    return create_engine(url)


def universe_panel():
    """复用 L-064/L-082 的可交易全市场面板（含 fwd5）。缓存缺失时从 research_newfactor_fullmarket 重建。"""
    if CACHE.exists():
        return pd.read_pickle(CACHE)
    sys.path.insert(0, str(B))
    from scripts.research_newfactor_fullmarket import build
    return build()


def main():
    e = engine()
    m = universe_panel()
    m = m[m.fwd5.notna()].copy()
    m["code"] = m.code.astype(str)
    m["td_int"] = pd.to_datetime(m.trade_date).dt.strftime("%Y%m%d").astype(int)
    m["td_dt"] = pd.to_datetime(m.trade_date)

    with e.connect() as c:
        fi = pd.read_sql(text("SELECT code, end_date, ann_date, roe, grossprofit_margin, "
                              "netprofit_margin, netprofit_yoy, or_yoy FROM stock_fina_indicator"), c)
    fi["code"] = fi.code.astype(str)
    fi["ann_int"] = pd.to_numeric(fi.ann_date, errors="coerce")
    fi = fi.dropna(subset=["ann_int"])
    fi["ann_int"] = fi.ann_int.astype(int)
    fi = fi.sort_values(["code", "ann_int", "end_date"])
    # 同一 ann_date 多期披露取 end_date 最新一期
    fi = fi.drop_duplicates(subset=["code", "ann_int"], keep="last")

    # PIT merge_asof: 每个信号日取最近一次已公告财报
    fi_a = fi.sort_values("ann_int")
    rows = []
    for code, g in m.groupby("code"):
        f = fi_a[fi_a.code == code]
        if not len(f):
            continue
        gg = g.sort_values("td_int")
        mm = pd.merge_asof(gg, f[["ann_int", "end_date", "roe", "netprofit_yoy", "or_yoy",
                                  "grossprofit_margin"]], left_on="td_int", right_on="ann_int",
                           direction="backward", allow_exact_matches=True)
        rows.append(mm)
    pit = pd.concat(rows, ignore_index=True)
    pit["has_fi"] = pit.ann_int.notna()
    ann_int_i = pit.ann_int.dropna().astype(int).astype(str)
    pit["ann_dt"] = pd.to_datetime(ann_int_i, format="%Y%m%d", errors="coerce").reindex(pit.index)
    pit["ann_age_days"] = (pit.td_dt - pit.ann_dt).dt.days

    print("== (1) PIT 覆盖率：有最新已公告财报的股票占比，按月 ==")
    cov = pit.groupby(pit.trade_date.str[:7]).agg(fc=("has_fi", "mean"), n=("has_fi", "size"))
    for mo, r in cov.iterrows():
        print(f"  {mo}: {r['fc']:.2%} (n={int(r['n'])})")

    # 事件新鲜度：ann_age_days <= 12 (约10个交易日) 且有财报
    pit["fresh"] = pit.has_fi & (pit.ann_age_days <= 12)

    print("\n== (2) 覆盖段全市场日截面五分位（T+1open→T+5close 毛均收益%），strictly-prior ==")
    for col, label in [("netprofit_yoy", "净利同比 netprofit_yoy"), ("or_yoy", "营收同比 or_yoy"),
                       ("roe", "ROE")]:
        for wn, a, b in [("冻结(仅覆盖段)", FZ[0], FZ[1]), ("前瞻", FW[0], FW[1]), ("run3", R3[0], R3[1])]:
            x = pit[(pit.trade_date >= a) & (pit.trade_date <= b)].dropna(subset=[col]).copy()
            if x.trade_date.nunique() < 10:
                print(f"  {label} {wn}: 覆盖日不足 ({x.trade_date.nunique()}天), 跳过")
                continue
            x["q"] = x.groupby("trade_date")[col].transform(
                lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) if s.notna().sum() >= 5 else np.nan)
            daily = x.groupby(["trade_date", "q"]).fwd5.mean().unstack("q")
            qm = daily.mean()
            spr = (daily[qm.index[-1]] - daily[qm.index[0]]).dropna()
            mon = spr.groupby(spr.index.str[:7]).mean().to_numpy(dtype=float)
            sgn = 1.0 if float(spr.mean()) > 0 else -1.0
            samesign = float((np.sign(mon) == sgn).mean()) if len(mon) else float("nan")
            rho = x[[col, "fwd5"]].corr(method="spearman").iloc[0, 1]
            print(f"  {label} {wn}: " + " ".join(f"Q{int(q)}={qm[q]:+.3f}" for q in qm.index)
                  + f" | Q5-Q1={float(spr.mean()):+.4f} 逐月同号{samesign:.2f} rho={rho:+.3f}")

    print("\n== (3) 新鲜公告事件：财报公告后 <=12 自然日 vs 其余（同为有财报覆盖股票内对照） ==")
    for wn, a, b in [("冻结(仅覆盖段)", FZ[0], FZ[1]), ("前瞻", FW[0], FW[1]), ("run3", R3[0], R3[1])]:
        x = pit[(pit.trade_date >= a) & (pit.trade_date <= b) & pit.has_fi]
        g = x.groupby("fresh").fwd5.agg(["mean", "count"])
        if True in g.index and False in g.index:
            d = g.loc[True, "mean"] - g.loc[False, "mean"]
            print(f"  {wn}: fresh={g.loc[True,'mean']:+.3f}(n={int(g.loc[True,'count'])}) "
                  f"stale={g.loc[False,'mean']:+.3f}(n={int(g.loc[False,'count'])}) diff={d:+.3f}")
        else:
            print(f"  {wn}: 事件样本不足 {g.to_dict()}")

    OUT.mkdir(parents=True, exist_ok=True)
    pit[["code", "trade_date", "ann_int", "end_date", "roe", "netprofit_yoy", "or_yoy",
         "has_fi", "ann_age_days", "fresh", "fwd5"]].to_pickle(OUT / "fina_pit_panel.pkl")
    print("\nsaved:", OUT / "fina_pit_panel.pkl")


if __name__ == "__main__":
    main()
