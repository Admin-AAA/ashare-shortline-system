"""三窗"整策略剔除"生产同构消融（单次重放 × 多臂复用，只读、零 DB 写）。

动机：research_strategy_exclude_canonical / research_strategy_ablate_ci 每个目标策略都要把
最贵的"universe + 特征 + 逐 skill 规则扫描"重建一遍（实测 ~37min/臂·窗），13 个策略 × 3 窗
≈ 24h。本脚本把重放与聚合拆开：每个交易日只做一次最贵的部分，把 prelim_entries（每只股票
每个 skill 的规则信号）留在内存，随后每个剔除臂只重跑廉价的 gate + aggregate。

语义（与生产的等价性）：
- 剔除某 skill = selected_skills 去掉它 + 该 skill 的 rule_signal 置空（对应 prefilter 并集里
  它不再贡献候选），prelim_score 按过滤后信号重算。生产里 `prefilter_skill_ids` 决定每个
  skill 给哪些股票打分，因此单只股票只剩被剔除 skill 的信号时会自然落选，与真剔除一致。
- 选择/出场一律用**未压缩的全量卡**：aggregate 内部已跑真实 evaluate_selection_policy
  （DEFAULT_PRODUCTION_SELECTION_POLICY = production_selection v4），且 ivol20/ivol_dn20 分位
  在压缩前就附在卡上。走 _pre_policy_card 压缩后的卡会丢这两个字段 → v3/v4 风险否决静默失效
  （L-112(b) 陷阱），故本脚本不用压缩卡做选择。
- 出场用真实因果 _simulate_card（sellable=tradable），与 published 出场策略一致。

前置断言（L-112a）：A 臂（全启用）三窗指标必须与既有 canonical 记录同量级，不一致先修基线。

用法：
  cd backend && .venv/bin/python scripts/research_strategy_ablation_fast.py \
      --windows W1,W2,W3 --out output/research/per_strategy/ablate_fast.json
输出：JSON（每窗 A 臂指标 + 每个 skill 的剔除 Δ/CI/逐月同号/独持卡/递补卡）+ log。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import event, select

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY  # noqa: E402
from app.models.tables import DailyKbar  # noqa: E402
from app.services.backtest.candidate_snapshot_builder import (  # noqa: E402
    ProductionRuleReplayCandidateBuilder,
    _reject_write_statement,
)
from app.services.backtest.policy_experiment import _simulate_card  # noqa: E402
from app.services.backtest.stock_metadata import StockMetadataHistoryProvider  # noqa: E402
from app.services.runs.skill_gate import build_skill_rule_gate  # noqa: E402
from app.services.runs.stages.adapters import ProductionRecommendationAssembly  # noqa: E402
from app.services.workbench_service import _prelim_rule_score  # noqa: E402

WINDOWS = {
    "W1": ("2025-01-15", "2026-01-16"),
    "W2": ("2026-01-17", "2026-07-30"),
    "W3": ("2026-08-01", "2026-09-13"),
}
PARAMS = {
    "data_source": "local_pool",
    "market_scope": "all",
    "market_scope_value": None,
    "score_line": 72.0,
    "output_count": 10,
    "risk_template": "balanced",
    "hide_recent_repeats": False,
    "disable_agent_review": True,
    "skill_review_limit": 0,
}
OUT = B / "output" / "research" / "per_strategy"


def _install_pct_cache(db, trade_dates) -> None:
    """把 ivol 分位 map 预加载一次并按日切片，避免每个臂每天都重查全市场。"""
    from app.services import risk_features as rf

    ivol_all = rf.load_ivol20_pct_map(db, trade_dates)
    dn_all = rf.load_ivol_dn20_pct_map(db, trade_dates)
    by_date_ivol: dict[str, dict] = defaultdict(dict)
    by_date_dn: dict[str, dict] = defaultdict(dict)
    for (d, _code), v in ivol_all.items():
        by_date_ivol[d][(d, _code)] = v
    for (d, _code), v in dn_all.items():
        by_date_dn[d][(d, _code)] = v
    real_ivol, real_dn = rf.load_ivol20_pct_map, rf.load_ivol_dn20_pct_map

    def cached_ivol(session, dates):  # noqa: ANN001
        want = sorted({str(d) for d in dates})
        if len(want) == 1 and want[0] in by_date_ivol:
            return dict(by_date_ivol[want[0]])
        return real_ivol(session, dates)

    def cached_dn(session, dates):  # noqa: ANN001
        want = sorted({str(d) for d in dates})
        if len(want) == 1 and want[0] in by_date_dn:
            return dict(by_date_dn[want[0]])
        return real_dn(session, dates)

    rf.load_ivol20_pct_map = cached_ivol
    rf.load_ivol_dn20_pct_map = cached_dn
    print(f"[cache] ivol 分位预加载 {len(ivol_all)} / {len(dn_all)} 条", flush=True)


def _provenance(card: dict) -> dict:
    sigs = [s for s in (card.get("strategy_signals") or []) if isinstance(s, dict) and not s.get("hard_veto")]
    scores = [float(s.get("score", 0) or 0) for s in sigs]
    ids = [str(s.get("strategy_id") or s.get("strategy_name") or "?") for s in sigs]
    dom = ids[scores.index(max(scores))] if scores else "none"
    return {"n_sig": len(sigs), "dominant": dom, "ids": "|".join(sorted(ids))}


def _filtered_entries(entries, skill_ids: set[str], snapshot: dict):
    out = []
    for entry in entries:
        signals = {k: v for k, v in (entry.get("rule_signals") or {}).items() if k in skill_ids}
        out.append(
            {
                "prelim_score": _prelim_rule_score(entry["stock"], signals, dict(snapshot)),
                "context": entry.get("context"),
                "rule_signals": signals,
                "stock": entry["stock"],
            }
        )
    out.sort(key=lambda item: float(item.get("prelim_score") or 0), reverse=True)
    return out


def run_window(db, window: str, targets: list[str], policy_note: str) -> dict:
    start, end = WINDOWS[window]
    # 必须用注册表 current（= 已发布的出场策略 v4）。ExecutionPolicy() 是历史 v1 默认值
    # （止盈 6% 开、止损 4.5%、trailing 2.0%），与 published 出场策略不一致。
    ep = DEFAULT_EXECUTION_POLICY
    settings = get_settings()
    builder = ProductionRuleReplayCandidateBuilder(
        db, stock_metadata_provider=StockMetadataHistoryProvider(db), historical_replay=True
    )
    selected_all = list(builder._selected_skills(None))  # noqa: SLF001
    all_ids = sorted(str(s["skill_id"]) for s in selected_all)
    arms = ["__all__"] + [t for t in targets if t in all_ids]
    trade_dates = [
        str(v)
        for v in db.scalars(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
            .distinct()
            .order_by(DailyKbar.trade_date)
        ).all()
    ]
    print(f"\n===== {window} {start}~{end} 交易日{len(trade_dates)} 启用skill{len(all_ids)} "
          f"臂{len(arms)} ({policy_note}) =====", flush=True)
    _install_pct_cache(db, trade_dates)
    trades: dict[str, list] = {a: [] for a in arms}
    prov_rows: list[dict] = []
    triggered = defaultdict(set)
    t0 = time.time()
    for di, signal_date in enumerate(trade_dates, 1):
        from app.services.backtest.historical_universe import load_historical_universe

        stocks, snapshot = load_historical_universe(
            db, signal_date, stock_metadata_provider=builder._stock_metadata_provider  # noqa: SLF001
        )
        stocks = [dict(s) for s in stocks]
        snapshot = dict(snapshot)
        snapshot["trade_date"] = str(snapshot.get("trade_date") or signal_date)
        snapshot["strategy_versions"] = {
            str(item["skill_id"]): str(item["version_id"]) for item in selected_all
        }
        prelim_entries: list = []
        scoped = builder._scope.apply_scope(stocks, PARAMS)  # noqa: SLF001
        prefiltered, audit = builder._scope.apply_prefilter(  # noqa: SLF001
            scoped, selected_all, PARAMS, snapshot
        )
        if prefiltered:
            _features, contexts = builder._features.build(prefiltered, snapshot)  # noqa: SLF001
            prelim_entries, _cand, _g, _p = builder._strategy.evaluate(  # noqa: SLF001
                research_contexts=contexts,
                selected_skills=selected_all,
                snapshot=snapshot,
                params=PARAMS,
                prefilter_audit=audit,
            )
            prelim_entries = list(prelim_entries)
            for entry in prelim_entries:
                for sid, sig in (entry.get("rule_signals") or {}).items():
                    if sig:
                        triggered[sid].add(str(entry["stock"].get("code") or ""))

        cards_by_arm: dict[str, list] = {}
        codes_union: set[str] = set()
        for arm in arms:
            if arm == "__all__":
                skill_ids = set(all_ids)
            else:
                skill_ids = set(all_ids) - {arm}
            skills_arm = [s for s in selected_all if str(s["skill_id"]) in skill_ids]
            entries_arm = _filtered_entries(prelim_entries, skill_ids, snapshot) if prelim_entries else []
            gate = build_skill_rule_gate(entries_arm, skills_arm, settings) if entries_arm else {"skill_candidates": []}
            candidate_results = tuple(
                {
                    **dict(entry),
                    "review_skill_ids": [],
                    "agent_review_disabled": True,
                    "skill_reviews": [],
                }
                for entry in gate.get("skill_candidates") or []
            )
            assembly = ProductionRecommendationAssembly(db, f"backtest-rule-replay-{signal_date}")
            formal_cards, scored_cards, _updates = assembly.aggregate(
                candidate_results=candidate_results,
                selected_skills=skills_arm,
                snapshot=snapshot,
                params=PARAMS,
            )
            full_by_code = {str(c.get("code")): c for c in scored_cards}
            cards_by_arm[arm] = [full_by_code.get(str(c.get("code")), dict(c)) for c in formal_cards]
            codes_union |= {str(c.get("code")) for c in cards_by_arm[arm]}

        bars = builder._outcome_bars(signal_date, sorted(codes_union), ep)  # noqa: SLF001

        class _Day:
            pass

        day = _Day()
        day.signal_date = signal_date
        day.outcome_bars = bars
        day.market_snapshot = snapshot
        for arm, cards in cards_by_arm.items():
            for card in cards:
                tr = (_simulate_card(day, card, ep) or {}).get("trade")
                if not (tr and tr.executed):
                    continue
                trades[arm].append(
                    {
                        "dt": signal_date,
                        "code": str(card.get("code")),
                        "net": float(tr.net_return_pct),
                        "exit": str(getattr(tr, "exit_reason", "")),
                    }
                )
                if arm == "__all__":
                    row = {"dt": signal_date, "code": str(card.get("code")),
                           "net": float(tr.net_return_pct), "exit": str(getattr(tr, "exit_reason", ""))}
                    row.update(_provenance(card))
                    prov_rows.append(row)
        if di % 10 == 0 or di == len(trade_dates):
            el = time.time() - t0
            print(f"  [{window}] {di}/{len(trade_dates)} 天 {el/60:.1f}min "
                  f"A={len(trades['__all__'])}笔", flush=True)

    print(f"[{window}] 出票日/触发skill覆盖：", flush=True)
    for sid in all_ids:
        print(f"    {sid:36s} 触发股票数(全历史累计)={len(triggered[sid])}", flush=True)

    def metrics(rows):
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        net = df.net
        daily = df.groupby("dt").net.mean()
        wins, losses = net[net > 0], net[net < 0]
        return {
            "n": int(len(df)),
            "wr": float((net > 0).mean()),
            "avg": float(net.mean()),
            "sum": float(net.sum()),
            "pf": float(wins.sum() / max(abs(losses.sum()), 1e-9)),
            "mdd": float((daily.cumsum() - daily.cumsum().cummax()).min()),
            "cohort": float(daily.mean()),
            "days": int(df.dt.nunique()),
        }

    def boot_ci(delta: np.ndarray, draws=4000, seed=7):
        rng = np.random.default_rng(seed)
        n = len(delta)
        means = np.array([delta[rng.integers(0, n, n)].mean() for _ in range(draws)])
        return float(delta.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))

    res = {"window": window, "start": start, "end": end, "A": metrics(trades["__all__"]),
           "triggered_codes": {k: len(v) for k, v in triggered.items()}, "arms": {}}
    df_a = pd.DataFrame(trades["__all__"])
    days_idx = sorted(set(df_a.dt)) if not df_a.empty else []
    da = df_a.groupby("dt").net.mean().reindex(days_idx).fillna(0.0) if days_idx else pd.Series(dtype=float)
    for t in arms:
        if t == "__all__":
            continue
        df_b = pd.DataFrame(trades[t])
        db_ = (df_b.groupby("dt").net.mean() if not df_b.empty else pd.Series(dtype=float))
        db_ = db_.reindex(days_idx).fillna(0.0)
        dlt = (db_ - da)
        md, lo, hi = boot_ci(dlt.values)
        monthly = dlt.groupby(dlt.index.str[:7]).mean() if len(dlt) else pd.Series(dtype=float)
        pos, neg = int((monthly > 0).sum()), int((monthly < 0).sum())
        ka = set(zip(df_a.dt, df_a.code))
        kb = set(zip(df_b.dt, df_b.code)) if not df_b.empty else set()
        oa = df_a[[(d, c) in ka - kb for d, c in zip(df_a.dt, df_a.code)]] if not df_a.empty else pd.DataFrame()
        ob = df_b[[(d, c) in kb - ka for d, c in zip(df_b.dt, df_b.code)]] if not df_b.empty else pd.DataFrame()
        rec = {
            "B": metrics(trades[t]),
            "delta_cohort": md, "ci_lo": lo, "ci_hi": hi,
            "days_pos": int((dlt > 0).sum()), "days_neg": int((dlt < 0).sum()),
            "monthly_pos_ratio": pos / max(pos + neg, 1),
            "monthly": {k: round(float(v), 4) for k, v in monthly.items()},
            "only_A_cards": int(len(oa)), "only_A_avg": float(oa.net.mean()) if len(oa) else None,
            "repl_B_cards": int(len(ob)), "repl_B_avg": float(ob.net.mean()) if len(ob) else None,
        }
        res["arms"][t] = rec
        print(f"  >> 剔除 {t:34s} Δ{md:+.4f} CI[{lo:+.4f},{hi:+.4f}] "
              f"日+{rec['days_pos']}/-{rec['days_neg']} 月同号{rec['monthly_pos_ratio']:.2f} "
              f"独持{rec['only_A_cards']}张均{rec['only_A_avg']} 递补{rec['repl_B_cards']}张均{rec['repl_B_avg']} "
              f"| B均{rec['B']['avg'] if rec['B'] else None}", flush=True)
    if prov_rows:
        res["provenance"] = prov_rows
    print(f"[{window}] A臂 n={res['A']['n']} WR={res['A']['wr']*100:.1f}% avg={res['A']['avg']:+.4f} "
          f"PF={res['A']['pf']:.3f} sum={res['A']['sum']:+.2f} MDD={res['A']['mdd']:+.2f}", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="W1,W2,W3")
    ap.add_argument("--targets", default="all")
    ap.add_argument("--out", default=None)
    ap.add_argument("--policy-note", default="production_selection v4 + execution policy v4")
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else OUT / "ablate_fast.json"
    from app.services.factors.registry import bootstrap_default_factor_registry

    bootstrap_default_factor_registry()
    db = SessionLocal()
    connection = db.connection()
    event.listen(connection, "before_cursor_execute", _reject_write_statement)
    results = {}
    try:
        for window in [w.strip() for w in args.windows.split(",") if w.strip()]:
            builder_targets: list[str] = []
            if args.targets == "all":
                selected_all = list(
                    ProductionRuleReplayCandidateBuilder(
                        db, stock_metadata_provider=StockMetadataHistoryProvider(db), historical_replay=True
                    )._selected_skills(None)  # noqa: SLF001
                )
                builder_targets = sorted(str(s["skill_id"]) for s in selected_all)
            else:
                builder_targets = [t.strip() for t in args.targets.split(",") if t.strip()]
            res = run_window(db, window, builder_targets, args.policy_note)
            results[window] = res
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1))
            print(f"[saved] {out_path}", flush=True)
    finally:
        db.close()
    print("ABLATE_FAST_DONE", flush=True)


if __name__ == "__main__":
    main()
