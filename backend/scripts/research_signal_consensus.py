"""L-081 探索：单卡【策略共识数 / 信号分离散度】三窗同向性（只读，手写 cohort 仅探索）。
假设：被更多独立策略同时命中(n_signals)、信号分越一致(dispersion 小)的卡质量越高。
- 冻结：holdout_244d.json 每卡 strategy_signals=[{runtime,score}]（匿名但可计数/算离散）。
- 前瞻/第三窗：canonical run 逐策略行，按(dt,code)聚合 n_signals、分数离散。
合并到三窗 v2 选中（含净收益），看 n_signals / dispersion 与 net 的分层关系是否三窗同向单调。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
FROZEN = B / "output" / "frozen_snapshots" / "holdout_244d.json"
RUN_FWD = "btr_fa625f3c41c147edbee3edb7d3adede5"
RUN3 = "btr_2bec6fed6c344eaa819e533b8220a0d6"


def _db():
    for line in open(B / ".env"):
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))
    raise RuntimeError("no db url")


def frozen_consensus():
    fz = json.load(open(FROZEN))
    rows = []
    for day in fz["days"]:
        dt = str(day["signal_date"])
        for c in day["cards"]:
            sc = [float(s.get("score") or 0) for s in (c.get("strategy_signals") or [])]
            rows.append({"dt": dt, "code": str(c.get("code")), "n_sig": len(sc),
                         "sig_spread": (max(sc) - min(sc)) if sc else np.nan})
    return pd.DataFrame(rows)


def run_consensus(run_id):
    q = text("""SELECT a.signal_date dt, a.code,
                      JSON_EXTRACT(a.signal_json,'$.score') score,
                      JSON_EXTRACT(t.trade_json,'$.net_return_pct') net
               FROM backtest_candidates a JOIN backtest_trades t
               ON a.backtest_id=t.backtest_id AND a.signal_date=t.signal_date AND a.code=t.code
               WHERE a.backtest_id=:r""")
    with _db().connect() as c:
        df = pd.DataFrame(c.execute(q, {"r": run_id}).mappings().all())
    df["score"] = df.score.astype(float); df["net"] = df.net.astype(float)
    df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
    g = df.groupby(["dt", "code"]).agg(
        n_sig=("score", "size"),
        sig_spread=("score", lambda x: x.max() - x.min()),
        net=("net", "first")).reset_index()
    return g


def layer(df, col, edges=None, q=False):
    d = df.dropna(subset=[col]).copy()
    d["bin"] = pd.qcut(d[col], 3, duplicates="drop") if q else pd.cut(d[col], edges)
    return d.groupby("bin", observed=True).net.agg(
        n="size", avg="mean", win=lambda x: (x > 0).mean(),
        ndays=lambda x: d.loc[x.index, "dt"].nunique()).round(3)


def corr(df, col):
    return df.dropna(subset=[col])[[col, "net"]].corr().iloc[0, 1]


def main():
    fz = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl"); fz["dt"] = fz.dt.astype(str)
    fwd = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl"); fwd["dt"] = fwd.dt.astype(str)
    r3 = pd.read_pickle(OUT / "run3_v2_selected.pkl"); r3["dt"] = r3.dt.astype(str)
    fz = fz.merge(frozen_consensus(), on=["dt", "code"], how="left")
    cf = run_consensus(RUN_FWD)[["dt", "code", "n_sig", "sig_spread"]]
    c3 = run_consensus(RUN3)[["dt", "code", "n_sig", "sig_spread"]]
    fwd = fwd.merge(cf, on=["dt", "code"], how="left")
    r3 = r3.merge(c3, on=["dt", "code"], how="left")
    for w, d in {"FROZEN": fz, "FORWARD": fwd, "RUN3": r3}.items():
        print(f"\n===== {w} n={len(d)} n_sig分布={d.n_sig.value_counts().sort_index().to_dict()} =====")
        print("corr n_sig=%.4f  corr spread=%.4f" % (corr(d, "n_sig"), corr(d, "sig_spread")))
        print("-- n_sig 分层 --"); print(layer(d, "n_sig", edges=[-0.1, 0.9, 1.9, 2.9, 100]).to_string())
        print("-- sig_spread 三分位 --"); print(layer(d, "sig_spread", q=True).to_string())
        d.to_pickle(OUT / f"consensus_{w.lower()}.pkl")


if __name__ == "__main__":
    main()
