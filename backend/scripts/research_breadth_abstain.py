"""假设：市场广度状态择时门控（abstain），双窗口 canonical 选中日 cohort 检验（只读）。

不做仓位管理：只决定"当天出不出票"（分数线/门控层）。把每个出票日按大盘广度
(up_ratio=up/sample, lu_rate=lu/sample, ld 跌停数, regime, sentiment) 分桶，
看 v2 选中日 cohort 等权均值是否存在【两窗口同向为负、且有足够天数】的可 abstain 状态。
单窗口/单日聚集一律视为 L-058 过拟合，不落地。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots

OUT = B / "output" / "research"


def frozen_day_breadth():
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    rows = []
    for d in snap.days:
        s = d.market_snapshot or {}
        n = int(s.get("sample_count") or 0); up = int(s.get("up_count") or 0)
        lu = int(s.get("limit_up_count") or 0); ld = int(s.get("limit_down_count") or 0)
        rows.append({"dt": d.signal_date, "n": n, "up_ratio": up / n if n else np.nan,
                     "lu_rate": lu / n if n else np.nan, "ld": ld,
                     "base": _market_base(s), "sent": _market_sentiment(s)})
    return pd.DataFrame(rows).set_index("dt")


def daily_cohort(selected: pd.DataFrame, dates) -> pd.Series:
    return selected.groupby("dt").net.mean().reindex(dates)


def bin_table(df, col, edges, labels):
    df = df.copy()
    df["bin"] = pd.cut(df[col], edges, labels=labels, right=False)
    out = []
    for b, g in df.groupby("bin", observed=True):
        if not len(g):
            continue
        out.append({"bin": str(b), "days": len(g), "ticket_days": int(g.cohort.notna().sum()),
                    "cohort_avg": round(g.cohort.mean(), 4),
                    "win_day": round((g.cohort > 0).mean(), 3)})
    return pd.DataFrame(out)


def main():
    fz_sel = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl")
    fw_sel = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl")
    fz_br = frozen_day_breadth()
    fz_co = daily_cohort(fz_sel, sorted(fz_br.index)).fillna(0.0)  # 空日记0
    fz = fz_br.join(fz_co.rename("cohort")).fillna({"cohort": 0.0})

    fw = build_forward_canonical(); dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates); have = [d for d in dates if d in snaps]
    rows = []
    for d in have:
        s = snaps[d]; n = int(s.get("sample_count") or 0)
        rows.append({"dt": d, "up_ratio": s["up_count"] / n, "lu_rate": s["limit_up_count"] / n,
                     "ld": s["limit_down_count"], "base": _market_base(s), "sent": _market_sentiment(s)})
    fw_br = pd.DataFrame(rows).set_index("dt")
    fw_co = daily_cohort(fw_sel, have).fillna(0.0)
    fwdf = fw_br.join(fw_co.rename("cohort")).fillna({"cohort": 0.0})

    # 特殊日
    print("2026-07-16 前瞻广度:", fw_br.loc["2026-07-16"].to_dict() if "2026-07-16" in fw_br.index else "无")

    for col, edges, labels in [
        ("up_ratio", [0, .25, .35, .50, .65, 1.01], ["<.25", ".25-.35", ".35-.50", ".50-.65", ">=.65"]),
        ("ld", [-1, 10, 30, 60, 100, 100000], ["<10", "10-30", "30-60", "60-100", ">100"]),
        ("lu_rate", [-.01, .001, .005, .012, .02, 1], ["<.001", ".001-.005", ".005-.012", ".012-.02", ">=.02"])]:
        print("\n===== 按", col, "分桶（v2 选中日 cohort 等权，空日记0）=====")
        tf = bin_table(fz, col, edges, labels); tw = bin_table(fwdf, col, edges, labels)
        j = tf.merge(tw, on="bin", how="outer", suffixes=("_fz", "_fw")).fillna("–")
        print(j.to_string(index=False))

    # regime×sentiment 日级（对照）
    print("\n===== regime·sentiment 日出票 cohort =====")
    for name, d in [("frozen", fz), ("forward", fwdf)]:
        print(f"--{name}--")
        for k, g in d.groupby(d.base + "·" + d.sent):
            print(f"  {k:9s} days{len(g):>3} cohort{g.cohort.mean():+.4f} winDay{(g.cohort>0).mean():.2f}")


if __name__ == "__main__":
    main()
