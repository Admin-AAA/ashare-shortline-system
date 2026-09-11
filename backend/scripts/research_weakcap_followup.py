"""weakcap 60->72 的稳健性/覆盖率/可叠加杠杆检验（canonical 口径）。

1) champion(60) vs cap72：逐日/逐月日cohort、正收益月占比、连续空仓、弱市产出
2) 在 cap72 基础上叠加其它单规则，看 VAL+OOS 是否继续改善（避免过拟合，只留双窗口都好的）
只读。
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY as CHAMP  # noqa: E402
from app.engine.aggregator import _market_base, _market_sentiment  # noqa: E402
from app.services.backtest.policy_experiment import (  # noqa: E402
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
)

SNAP = BACKEND / "output" / "frozen_snapshots" / "holdout_244d.json"
WINDOWS = ("train", "validation", "out_of_sample")


def spec_of(snapshot, ep):
    s = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec) \
        if False else None
    from app.services.backtest.policy_experiment import CanonicalPolicyExperimentSpec
    s = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    object.__setattr__(s, "candidate_snapshot", snapshot)
    object.__setattr__(s, "score_line", 72.0)
    object.__setattr__(s, "output_count", int(CHAMP.daily_output_limit))
    object.__setattr__(s, "execution_policy", ep)
    return s


def selected_frame(snapshot, records):
    sel, oc = records["selected_keys"], records["outcomes"]
    mbse = {d.signal_date: (_market_base(d.market_snapshot), _market_sentiment(d.market_snapshot))
            for d in snapshot.days}
    rows = []
    for key in sel:
        date, code = key.split(":", 1)
        tr = (oc.get(key) or {}).get("trade")
        if tr is None or not getattr(tr, "executed", False):
            continue
        mb, se = mbse.get(date, ("?", "?"))
        rows.append({"date": date, "code": code, "mb": mb, "se": se, "net": float(tr.net_return_pct)})
    return pd.DataFrame(rows)


def max_consec(labels):
    best = cur = 0
    for v in labels:
        cur = cur + 1 if not v else 0
        best = max(best, cur)
    return best


def main():
    payload = json.loads(SNAP.read_text(encoding="utf-8"))
    snapshot = ScoredCandidateSnapshot.from_payload(payload)
    ep = ExecutionPolicy()
    all_dates = [d.signal_date for d in snapshot.days]
    split = _purged_split(tuple(all_dates), purge_days=ep.entry_lookahead_days + ep.horizon_days)
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = spec_of(snapshot, ep)

    def run(pol):
        _pub, rec = runner._run_arm(spec, pol, split)
        return selected_frame(snapshot, rec)

    cap72 = CHAMP.with_rule_patch("weak_regime_score_cap", 72.0)
    f_champ, f_72 = run(CHAMP), run(cap72)

    # 1) 逐日 cohort（含空仓日=0）与逐月
    all_d = pd.Series(sorted(set(all_dates)))
    def daily(f):
        return f.groupby("date").net.mean().reindex(all_d).fillna(0.0)
    d_c, d_7 = daily(f_champ), daily(f_72)
    print("=== 覆盖率 / 逐日（空仓日记0）===")
    for name, d, f in [("champ60", d_c, f_champ), ("cap72", d_7, f_72)]:
        active = f.groupby("date").size()
        weak_days = {dt for dt in (d.signal_date for d in snapshot.days)}
        print(f"{name}: 选中笔数={len(f)} 有出票日={f.date.nunique()}/{all_dates.__len__()} "
              f"空仓日={(d == 0).sum()} 最长连续空仓={max_consec((d != 0).tolist())} "
              f"日均(含空)={d.mean():.4f}%")
    # 逐月均值与正收益月
    mon = pd.DataFrame({"champ": d_c, "cap72": d_7})
    mon["ym"] = mon.index.str[:7]
    mg = mon.groupby("ym")[["champ", "cap72"]].mean()
    mg["cap72_better"] = mg.cap72 > mg.champ
    print("\n=== 逐月日cohort均值 ===")
    print(mg.round(3).to_string())
    print(f"\ncap72 优于 champ 的月份: {mg.cap72_better.sum()}/{len(mg)}")
    print(f"cap72 为正的月份: {(mg.cap72>0).sum()}/{len(mg)}; champ 为正: {(mg.champ>0).sum()}/{len(mg)}")

    # 2) 叠加杠杆（都在 cap72 之上）
    print("\n=== 在 cap72 上叠加单规则（日cohort: train/val/oos/all）===")
    combos = [
        ("cap72 base", cap72),
        ("+强牛高潮72", cap72.with_rule_patch("regime_sentiment_line_overrides",
            [["强牛·高潮", 72], ["强牛·活跃", 64], ["偏多·活跃", 71.4], ["震荡·活跃", 72]])),
        ("+清override", cap72.with_rule_patch("regime_sentiment_line_overrides", [])),
        ("+climax_div3", cap72.with_rule_patch("climax_output_divisor", 3)),
        ("+out_limit6", cap72.with_rule_patch("daily_output_limit", 6)),
        ("+out_limit14", cap72.with_rule_patch("daily_output_limit", 14)),
        ("+climax_extra12", cap72.with_rule_patch("climax_nonweak_extra", 12.0)),
    ]
    rows = []
    for name, pol in combos:
        f = run(pol)
        r = {"name": name, "n": len(f)}
        for w in ("train", "validation", "out_of_sample"):
            ss = set(split[w]); ff = f[f.date.isin(ss)]
            dd = ff.groupby("date").net.mean()
            r[w + "_dc"] = round(dd.mean(), 4) if len(dd) else None
            r[w + "_n"] = len(ff)
        r["all_dc"] = round(f.groupby("date").net.mean().mean(), 4)
        rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
