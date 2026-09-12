"""信号新鲜度（重复/连续入选）三窗口检验（只读）。
对每笔 v2 选中交易，统计信号日前 k 个交易日该股出现在 score>=70 候选池的天数 prev_k。
机制：持续多日仍在候选池=陈旧/延伸信号，首次出现=新鲜突破。三窗口（冻结/原前瞻/第三窗）
必须同向且非单日聚集、canonical 才考虑外科否决。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json
OUT = B / "output" / "research"
RUN_FW = "btr_fa625f3c41c147edbee3edb7d3adede5"
RUN_R3 = "btr_2bec6fed6c344eaa819e533b8220a0d6"


def db():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))


def run_cand(run):
    q = text("""SELECT signal_date dt, code, JSON_EXTRACT(signal_json,'$.score') sc
                FROM backtest_candidates WHERE backtest_id=:r""")
    d = pd.read_sql(q, db(), params={"r": run})
    d["dt"] = d.dt.astype(str); d["code"] = d.code.astype(str); d["sc"] = d.sc.astype(float)
    return d[d.sc >= 70][["dt", "code"]]


def frozen_cand():
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    rows = []
    for d in snap.days:
        for c in _thaw_json(d.cards):
            try: sc = float(c.get("final_score") or 0)
            except Exception: sc = 0
            if sc >= 70: rows.append((d.signal_date, str(c.get("code"))))
    return pd.DataFrame(rows, columns=["dt", "code"])


def attach_prev(selected: pd.DataFrame, cand: pd.DataFrame, ks=(1, 2, 3, 5)):
    alld = sorted(cand.dt.unique())
    didx = {d: i for i, d in enumerate(alld)}
    bycode = {c: sorted(g.dt.unique()) for c, g in cand.groupby("code")}
    import bisect
    out = selected.copy()
    for k in ks: out[f"prev{k}"] = 0
    for i, r in out.iterrows():
        cur = r["dt"]
        idx = didx.get(cur)
        if idx is None: continue
        prior = set(alld[max(0, idx - 5):idx])
        appear = bycode.get(r["code"], [])
        lo = bisect.bisect_left(appear, alld[max(0, idx-5)])
        ap = set(appear[lo:bisect.bisect_left(appear, cur)])
        for k in ks:
            wk = set(alld[max(0, idx - k):idx])
            out.at[i, f"prev{k}"] = len(ap & wk)
    return out


def show(name, df):
    print(f"\n===== {name}  n={len(df)} =====")
    for k in (1, 2, 3, 5):
        col = f"prev{k}"
        t = df.groupby(col).net.agg(n="size", avg="mean", win=lambda x: (x > 0).mean())
        fresh = df[df[col] == 0].net; stale = df[df[col] >= 1].net
        print(f" prev{k}: 新鲜(0)n{len(fresh)}均{fresh.mean():+.3f}/胜{(fresh>0).mean():.2f} | "
              f"重复(>=1)n{len(stale)}均{stale.mean():+.3f}/胜{(stale>0).mean():.2f}")
    # detailed prev3 buckets + day spread
    t = df.groupby("prev3").net.agg(n="size", avg="mean", win=lambda x: (x > 0).mean())
    print(t.round(3).to_string())


def main():
    fz_sel = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl")
    fw_sel = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl")
    r3_sel = pd.read_pickle(OUT / "run3_v2_selected.pkl")
    fz_c = frozen_cand(); fw_c = run_cand(RUN_FW); r3_c = run_cand(RUN_R3)
    # forward+r3 share a continuous candidate history: merge their candidate sets
    fr_c = pd.concat([fw_c, r3_c]).drop_duplicates()
    fz_a = attach_prev(fz_sel, fz_c)
    fw_a = attach_prev(fw_sel, fr_c)
    r3_a = attach_prev(r3_sel, fr_c)
    show("FROZEN", fz_a); show("FORWARD", fw_a); show("RUN3", r3_a)
    fz_a.to_pickle(OUT / "freshness_frozen.pkl"); fw_a.to_pickle(OUT / "freshness_forward.pkl"); r3_a.to_pickle(OUT / "freshness_run3.pkl")


if __name__ == "__main__":
    main()
