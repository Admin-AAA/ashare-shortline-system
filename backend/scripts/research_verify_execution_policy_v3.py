"""闭环验证：用**已发布**的 execution_policy 默认（registry current）重算三窗。

为什么需要：扫描脚本 `research_exit_sweep_three_window.py` 里的候选是
`dataclasses.replace` 出来的临时对象。发布后必须证明**落地 artifact 本身**
（`DEFAULT_EXECUTION_POLICY`，即 registry current）能复现那些数字——
这正是 L-111 要求的"指标必须指回可复现命令/artifact"。

断言：三窗 n / WR / avg / PF / sum 与扫描记录一致（容差内），否则非零退出。

只读。
用法：cd backend && .venv/bin/python scripts/research_verify_execution_policy_v3.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY  # noqa: E402
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    _thaw_json,
    _simulate_card,
)
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: F401,E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    _main_industries_from_db,
    _attach_main_industries,
)
from scripts.research_exit_policy_canonical import (  # noqa: E402
    _bars_for,
    _calendar,
    _closes_for,
    _selected,
)
import scripts.research_exit_policy_canonical as exit_harness  # noqa: E402

BAR_HORIZON = 9
# 扫描记录（v4 = v3 + max_entry_gap_up_pct 6% + trailing.minimum_lock_gain_pct 2.5%；
# 见 exit_sweep_gap_honest.jsonl 的 mge6_ml2.5_priostop_cap15 与
# research_exit_robustness_check.py 的稳健性结论：相对 v3 逐月同号 0.82）
EXPECTED = {
    "W1": {"n": 981, "win": 0.576, "avg": 0.6448, "pf": 1.374, "sum": 632.6},
    "W2": {"n": 693, "win": 0.574, "avg": 0.0468, "pf": 1.023, "sum": 32.4},
    "W3": {"n": 94, "win": 0.553, "avg": 0.2737, "pf": 1.161, "sum": 25.7},
}


def main() -> None:
    print(f"published policy: {DEFAULT_EXECUTION_POLICY.policy_id}@"
          f"{DEFAULT_EXECUTION_POLICY.version} fp={DEFAULT_EXECUTION_POLICY.fingerprint()[:12]}…")
    print(f"  take_profit.enabled={DEFAULT_EXECUTION_POLICY.take_profit.enabled} "
          f"stop_loss={DEFAULT_EXECUTION_POLICY.stop_loss.default_loss_pct} "
          f"trailing.activation={DEFAULT_EXECUTION_POLICY.trailing.activation_gain_pct}\n")
    exit_harness._POLICY = __import__(
        "app.engine.production_policy", fromlist=["load_production_selection_policy"]
    ).load_production_selection_policy(version=4)
    cal = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    mi = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi)
    with SessionLocal() as s:
        iv20 = load_ivol20_pct_map(s, all_dates)
        ivdn = load_ivol_dn20_pct_map(s, all_dates)
    picks = {w: _selected(g, iv20, ivdn) for w, _d, g in windows}
    bars, closes = {}, {}
    for w, _d, _g in windows:
        codes = sorted({c for _dt, c in picks[w]})
        need = {d for dt in {x[0] for x in picks[w]} for d in cal[idx[dt] + 1: idx[dt] + 1 + BAR_HORIZON]}
        bars.update(_bars_for(codes, need))
        closes.update(_closes_for(codes, {d for d, _c in picks[w]}))

    ok = True
    for w, dates, groups in windows:
        rows = []
        for dt, _cards, _nm, day in groups:
            sel = [(c, picks[w][(dt, c)]) for (d, c) in picks[w] if d == dt]
            if not sel:
                continue
            built = {code: tuple(bars[(code, x)] for x in cal[idx[dt] + 1: idx[dt] + 1 + BAR_HORIZON]
                                 if (code, x) in bars) for code, _card in sel}
            built = {k: v for k, v in built.items() if v}
            cards = []
            for code, card in sel:
                cc = dict(card)
                if not cc.get("recommend_price"):
                    cc["recommend_price"] = closes.get((code, dt))
                cards.append(cc)
            day2 = ScoredCandidateDay(signal_date=dt,
                                      market_snapshot=_thaw_json(day.market_snapshot),
                                      cards=tuple(cards), outcome_bars=built)
            for card in day2.cards:
                tr = (_simulate_card(day2, card, DEFAULT_EXECUTION_POLICY) or {}).get("trade")
                if tr is not None and tr.executed:
                    rows.append({"dt": dt, "net": float(tr.net_return_pct)})
        df = pd.DataFrame(rows)
        gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
        got = {"n": len(df), "win": float((df.net > 0).mean()), "avg": float(df.net.mean()),
               "pf": float(gp / gl) if gl > 1e-9 else float("inf"), "sum": float(df.net.sum())}
        exp = EXPECTED[w]
        line = (f"[{w}] n={got['n']} WR={got['win']*100:.1f}% avg={got['avg']:+.4f} "
                f"PF={got['pf']:.3f} sum={got['sum']:+.1f}")
        match = (got["n"] == exp["n"] and abs(got["win"] - exp["win"]) < 0.002
                 and abs(got["avg"] - exp["avg"]) < 0.005 and abs(got["pf"] - exp["pf"]) < 0.005
                 and abs(got["sum"] - exp["sum"]) < 1.0)
        print(f"{line}  {'✓ 与扫描一致' if match else '✗ 与扫描不一致 → ' + str(exp)}")
        ok = ok and match
    print("\n结论：" + ("已发布 artifact 复现扫描数字 ✓" if ok else "不一致 ✗（发布物与扫描不符）"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
