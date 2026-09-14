"""重建候选池 vs 既有候选池的**口径等价性**体检。

为什么必须先做这一步：`research_candidate_pool_rebuild.py` 用
`ProductionRuleReplayCandidateBuilder(historical_replay=True)` 生产候选池，
它和此前各轮用的候选池（W1 冻结快照 / W2·W3 存储回放）不是同一次构建。
若两者的成交量级/净收益水平不可比，就不能把"改 skill 后重建"的结果直接拿去
对照既有 canonical 基线，否则等于换了尺子还比长度。

做法：对共同可用的交易日，分别在两个候选池上跑**同一套** v4 选择 + 真实因果出场，
输出 n / 胜率 / 均净 / PF / sum 与逐日 overlap，并给出配对日 Δ 的 bootstrap CI。

只读，不改生产代码。
用法：
    cd backend && .venv/bin/python scripts/research_pool_basis_equivalence.py \
        --pool output/research/pool_W1 --baseline frozen --start 2025-01-15 --end 2025-04-17
    cd backend && .venv/bin/python scripts/research_pool_basis_equivalence.py \
        --pool output/research/pool_W3 --baseline run3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy,
    evaluate_selection_policy,
    SelectionPolicyContext,
)
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    ScoredCandidateSnapshot,
    _thaw_json,
    _simulate_card,
)
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
    attach_ivol20_pct,
    attach_ivol_dn20_pct,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    OUTPUT_COUNT,
    SCORE_LINE,
    _main_industries_from_db,
    _attach_main_industries,
)
from scripts.research_exit_policy_canonical import _bars_for, _calendar, _closes_for  # noqa: E402
import scripts.research_exit_policy_canonical as exit_harness  # noqa: E402


def _load_pool(pool_dir: Path):
    """每个 json 是一日 ScoredCandidateSnapshot（days 长度 1）。

    统一表示：{date: {"cards": [...], "ms": {...}, "bars": {code: tuple(BarInput)}}}
    """
    days = {}
    for p in sorted(pool_dir.glob("*.json")):
        payload = json.loads(p.read_text(encoding="utf-8"))
        snap = ScoredCandidateSnapshot.from_payload(payload)
        day = snap.days[0]
        days[day.signal_date] = {
            "cards": [dict(c) for c in day.cards],
            "ms": _thaw_json(day.market_snapshot),
            "bars": dict(day.outcome_bars),
            "netmap": None,
        }
    return days


def _load_baseline(kind: str):
    """既有候选池 → 同一表示。`canon_groups` 的 W2/W3 组自带 netmap（缓存回放净收益）。"""
    if kind == "frozen":
        dates, groups = frozen_groups()
    else:
        dates, groups = canon_groups("third")
    out = {}
    for dt, cards, netmap, day in groups:
        ms = _thaw_json(getattr(day, "market_snapshot", {}) or {})
        bars = dict(getattr(day, "outcome_bars", {}) or {})
        out[dt] = {
            "cards": [_thaw_json(c) for c in cards],
            "ms": ms,
            "bars": bars,
            "netmap": dict(netmap) if netmap else None,
        }
    return out


def _metrics(rows, dates):
    if not rows:
        return {"n": 0, "win": 0.0, "avg": 0.0, "pf": float("nan"), "sum_net": 0.0, "g": pd.Series(dtype=float)}
    df = pd.DataFrame(rows)
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    g = df.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0)
    return {"n": len(df), "win": float((df.net > 0).mean()), "avg": float(df.net.mean()),
            "pf": float(gp / gl) if gl > 1e-9 else float("inf"), "sum_net": float(df.net.sum()), "g": g}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="重建池目录（每日一个 json）")
    ap.add_argument("--baseline", choices=("frozen", "run3"), required=True)
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()

    pool_dir = Path(args.pool)
    if not pool_dir.is_absolute():
        pool_dir = B / pool_dir
    rebuilt = _load_pool(pool_dir)
    b_items = _load_baseline(args.baseline)
    if args.start:
        b_items = {d: v for d, v in b_items.items() if d >= args.start}
        rebuilt = {d: v for d, v in rebuilt.items() if d >= args.start}
    if args.end:
        b_items = {d: v for d, v in b_items.items() if d <= args.end}
        rebuilt = {d: v for d, v in rebuilt.items() if d <= args.end}
    common = sorted(set(b_items) & set(rebuilt))
    if not common:
        raise SystemExit("没有共同可用交易日")
    print(f"共同交易日 {len(common)}：{common[0]} ~ {common[-1]}", flush=True)

    exit_harness._POLICY = load_production_selection_policy(version=4)
    cal = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    exit_harness.bars_dates = {d: cal[idx[d] + 1: idx[d] + 1 + 9] for d in common if d in idx}
    mi = _main_industries_from_db(common)
    with SessionLocal() as s:
        iv20 = load_ivol20_pct_map(s, common)
        ivdn = load_ivol_dn20_pct_map(s, common)

    def pick_and_sim(items, tag):
        picks = {}
        for dt, item in items.items():
            ms = dict(item["ms"])
            if not ms.get("main_industries"):
                ms["main_industries"] = tuple(mi.get(dt, ()))
            cards = [dict(c) for c in item["cards"]]
            attach_ivol20_pct(cards, dt, iv20)
            attach_ivol_dn20_pct(cards, dt, ivdn)
            for c in evaluate_selection_policy(
                cards, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms),
                exit_harness._POLICY,
            ).recommendations:
                picks[(dt, str(c.get("code")))] = _thaw_json(c)
        codes = sorted({c for _d, c in picks})
        bars, closes = {}, {}
        need = {x for dt in {d for d, _c in picks} for x in exit_harness.bars_dates.get(dt, [])}
        bars.update(_bars_for(codes, need))
        closes.update(_closes_for(codes, {d for d, _c in picks}))
        rows = []
        for dt, item in items.items():
            sel = [(c, picks[(dt, c)]) for (d, c) in picks if d == dt]
            if not sel:
                continue
            if item["netmap"] is not None:  # 缓存回放净收益：直接用，不重算出场
                for code, _card in sel:
                    v = item["netmap"].get(code)
                    if v is not None:
                        rows.append({"dt": dt, "code": code, "net": float(v)})
                continue
            built = item["bars"] or {
                code: tuple(bars[(code, x)] for x in exit_harness.bars_dates[dt] if (code, x) in bars)
                for code, _card in sel
            }
            built = {k: tuple(v) for k, v in built.items() if v}
            use = ScoredCandidateDay(signal_date=dt, market_snapshot=dict(item["ms"]),
                                     cards=tuple(c for _c, c in sel), outcome_bars=built)
            for code, card in sel:
                cc = dict(card)
                if not cc.get("recommend_price"):
                    cc["recommend_price"] = closes.get((code, dt))
                tr = (_simulate_card(use, cc, ExecutionPolicy()) or {}).get("trade")
                if tr is not None and tr.executed:
                    rows.append({"dt": dt, "code": code, "net": float(tr.net_return_pct)})
        m = _metrics(rows, common)
        print(f"[{tag}] n{m['n']} 胜{m['win']*100:.1f}% 均{m['avg']:+.4f} PF{m['pf']:.3f} sum{m['sum_net']:+.2f}",
              flush=True)
        return rows, m

    b_rows, b_m = pick_and_sim({d: b_items[d] for d in common}, args.baseline)
    r_rows, r_m = pick_and_sim({d: rebuilt[d] for d in common}, "rebuilt")
    j = pd.concat([b_m["g"].rename("b"), r_m["g"].rename("r")], axis=1).fillna(0.0)
    dd = (j.r - j.b).values
    lo, hi = _bootstrap_ci(dd, draws=5000)
    print(f"\n配对日 Δcohort = {np.mean(dd):+.4f}  CI[{lo:+.4f},{hi:+.4f}]")
    print("口径等价判据：Δ 相对既有基线量级（W1 均+0.41 / W2 -0.10 / W3 -0.04）若同号且量级相近，则两池可比")


if __name__ == "__main__":
    main()
