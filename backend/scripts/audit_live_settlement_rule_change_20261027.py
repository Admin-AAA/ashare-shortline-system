"""只读审计（2026-10-27）：按新规则，10-14 以来单策略实盘已结算记录哪些会变；并核对实盘结算路径与回测路径的差异。

新规则 = PR #15（fix/live-entry-confirmation）：
  A. 盘中买入确认只做入场日检查（窗口按入场方式、可成交、非一字涨停、开盘跳空 ≤6%）；
  B. 板块相对强度龙头结算改为回测规则（backtest_open_window_v1：窗口内第一个可买日开盘买）。

对每条记录给出三列：
  stored    ：库里现在的结算（含执行状态投影：入场前取消/过期 → missed）
  settle_new：新结算规则下的行情模拟（_simulate_outcome，不叠加执行状态——新确认逻辑下这些都会被确认买入，见 19 只回归）
  backtest  ：§282 回测路径（PE._simulate_card，同一根 K 线）
样本含 10-14/10-15 的单技能补跑 run（它们带着 preset_formal_opening 的 preset_id，classify_era 已按技能数判 single）。
回测卡片带上封存快照（exit_strategy_id 等），否则逐策略出场微调 / 破买入日最低价出场不生效（第一版的假差异来源）。
末尾再输出 19 只回归样本「确认买入日/价 vs 结算成交日/价」对照。
只读：不写库、不调外部数据源。用法：cd backend && .venv/bin/python scripts/audit_live_settlement_rule_change_20261027.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import select  # noqa: E402

import app.services.backtest.policy_experiment as PE  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.recommendation import RecommendationExecutionState  # noqa: E402
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402
from app.services.evidence.validation import canonical_live_forward_run_ids, catchup_formal_run_ids  # noqa: E402
from app.services.live_trade_breakdown import classify_era  # noqa: E402
from app.services.outcome_service import ENTRY_LOOKAHEAD_DAYS, _load_outcome_bars, bounded_outcome_bars  # noqa: E402
from app.services.outcome_simulation import _simulate_outcome  # noqa: E402
from scripts.research_exit_resim import Day, fetch_bars, variant  # noqa: E402

SINCE = "2026-10-14"


def _fmt(d, p):
    return f"{d or '—'} {round(float(p), 2) if p else '—'}"


def main() -> None:
    out = []
    with SessionLocal() as db:
        run_ids = tuple(canonical_live_forward_run_ids(db)) + tuple(catchup_formal_run_ids(db))
        rows = db.execute(
            select(Recommendation, RecommendationRun, RecommendationOutcome, RecommendationExecutionState)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .outerjoin(RecommendationExecutionState,
                       RecommendationExecutionState.recommendation_id == Recommendation.recommendation_id)
            .where(Recommendation.run_id.in_(run_ids), Recommendation.trade_date >= SINCE)
            .order_by(Recommendation.trade_date, Recommendation.code)
        ).all()
        rows = [r for r in rows if classify_era(r[1].preset_id, r[1].selected_skills_json) == "single"]
        recs = [r[0] for r in rows]
        bars_by_code, _ = _load_outcome_bars(db, recs)
        need = defaultdict(set)
        for rec in recs:
            need[str(rec.trade_date)].add(str(rec.code))
        rbars = fetch_bars(db, need)
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    for rec, run, outcome, state in rows:
        horizon = int(outcome.horizon_days or 5)
        bounded = bounded_outcome_bars(rec, bars_by_code.get(str(rec.code), []), horizon,
                                       entry_lookahead_days=ENTRY_LOOKAHEAD_DAYS)
        sim = _simulate_outcome(rec, bounded, [str(b.trade_date) for b in bounded], horizon_days=horizon,
                                ma_context_bars=bars_by_code.get(str(rec.code), [])) or {}
        snap = json.loads(rec.feature_snapshot_json or "{}")
        day = Day(); day.signal_date = str(rec.trade_date)
        day.outcome_bars = {str(rec.code): tuple(rbars[str(rec.trade_date)].get(str(rec.code), ())[:lim])}
        # 回测卡片带上封存快照（exit_strategy_id / strategy_signals），否则逐策略出场微调与破买入日最低价出场不生效
        card = {**snap, "code": rec.code, "recommend_price": rec.recommend_price, "bucket": rec.bucket,
                "entry_mode": snap.get("entry_mode")}
        tr = (PE._simulate_card(day, card, ep) or {}).get("trade")
        bt = {"entry": str(tr.entry_date) if tr and tr.executed else None, "entry_price": getattr(tr, "entry_price", None) if tr and tr.executed else None,
              "exit": str(tr.exit_date) if tr and tr.exit_date else None,
              "net": round(float(tr.net_return_pct), 2) if tr and tr.executed and tr.exit_date else None}
        row = {
            "signal": str(rec.trade_date), "strategy": str(rec.bucket), "code": str(rec.code), "name": rec.name,
            "stored": {"label": outcome.outcome_label, "entry": _fmt(outcome.entry_date, outcome.entry_price),
                       "exit": _fmt(outcome.exit_date, outcome.exit_price), "ret": outcome.executed_return,
                       "reason": outcome.exit_reason, "entry_policy": outcome.entry_policy_id},
            "settle_new": {"entry": _fmt(sim.get("entry_date"), sim.get("entry_price")),
                           "exit": _fmt(sim.get("exit_date"), sim.get("exit_price")),
                           "ret": sim.get("executed_return"), "reason": sim.get("exit_reason"),
                           "entry_policy": sim.get("entry_policy_id"), "label": sim.get("outcome_label")},
            "backtest": bt,
            "state": getattr(state, "latest_decision_code", None),
        }
        row["changes"] = [
            k for k, a, b in (
                ("成交与否", outcome.outcome_label == "missed", sim.get("outcome_label") == "missed"),
                ("入场", row["stored"]["entry"], row["settle_new"]["entry"]),
                ("出场", row["stored"]["exit"], row["settle_new"]["exit"]),
                ("入场规则", outcome.entry_policy_id, sim.get("entry_policy_id")),
            ) if a != b
        ]
        row["settle_vs_backtest_entry_same"] = (sim.get("entry_date") == bt["entry"]) if sim.get("entry_date") or bt["entry"] else True
        out.append(row)
        print(json.dumps(row, ensure_ascii=False, default=str))
    changed = [r for r in out if r["changes"]]
    print(f"\n共 {len(out)} 条单策略已结算记录（{SINCE} 起），按新规则会变 {len(changed)} 条", flush=True)


def nineteen_table() -> None:
    """19 只回归样本：确认买入日/价（入场日检查，按真实日 K）对照 结算成交日/价（_resolve_entry，同一组日 K）。"""
    from tests.test_entry_confirmation_regression_19 import CASES

    from app.models.tables import DailyKbar
    from app.services.entry_day_checks import check_entry_bar
    from app.services.entry_window_state import open_days_after
    from app.services.outcome_simulation import _rec_is_st, _resolve_entry

    print("\n19 只对照：确认买入日/价 vs 结算成交日/价", flush=True)
    with SessionLocal() as db:
        for signal_date, code, name, _price, _ohlc in CASES:
            recs = db.scalars(select(Recommendation).where(
                Recommendation.code == code, Recommendation.trade_date == signal_date)).all()
            rec = recs[0]
            snap = json.loads(rec.feature_snapshot_json or "{}")
            days = open_days_after(db, signal_date, "2026-12-31")[:2]
            bars = [db.scalar(select(DailyKbar).where(DailyKbar.code == code, DailyKbar.trade_date == d)) for d in days]
            bars = [b for b in bars if b is not None]
            confirm = ("—", None)
            for b in bars:  # 确认：按窗口逐日入场日检查，第一日不挡就当日开盘买
                chk = check_entry_bar(code, b, float(rec.recommend_price), session_closed=True,
                                      is_st=_rec_is_st(rec, snap))
                if not chk.blocked:
                    confirm = (str(b.trade_date), round(float(b.open), 2))
                    break
            entry = _resolve_entry(rec, snap, bars, 5)
            settle = (entry.get("date") or "—", round(float(entry["price"]), 2) if entry.get("price") else None)
            same = "一致" if confirm == settle else "不一致"
            print(f"{signal_date} {code} {name} {rec.bucket} | 确认 {confirm[0]} {confirm[1]} | 结算 {settle[0]} {settle[1]} | {same}",
                  flush=True)


if __name__ == "__main__":
    main()
    nineteen_table()
