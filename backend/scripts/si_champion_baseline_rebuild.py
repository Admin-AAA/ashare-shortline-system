"""Self-improve champion baseline rebuild — MEASUREMENT_FIX (L-001/L-002 口径重建).

背景（本轮 PRECHECK 事实）：
  main 在 8 月下旬~9 月落了一串因果/统计口径修复：
    d3ddab2/e4bd796 修复并迁移「策略入场 + 未成交收益」口径
    ef11a54        对齐 outcome accuracy 语义
    511b8fe        未完成 horizon 的 outcome 保持 provisional
  同时 evidence scope 收紧了 LIVE_FORWARD 判定（run_mode/status/provenance 全套），
  历史上大量 formal run 被重新归类为 historical_reconstruction。

  结果是 self_check 反复输出「同口径 champion 待重放（旧历史基线已失效，不计算 Δ）」，
  而 state.canonical_production 仍挂着 545a8b9 时点的快照
  （n=55 / WR63.6% / avg+2.039% / PF2.93）。该快照按 L-001/L-002 已不可用作 champion。

本脚本做的是「champion 自身的同口径重建」，不是 challenger，也绝不调参：
  1. 在同一份 DB、同一份当前 main 代码下，重放未修改的 production 退出状态机
     （_simulate_outcome, exit_resolver=None, horizon=5, 生产止损）。
  2. 分别对三个 population 给出四核心指标：
       A) LIVE_FORMAL_STRICT  —— canonical 前瞻口径（official_sample_conditions）
       B) FORMAL_ALL_LEGACY   —— 旧 champion 快照的宽口径（全部 formal completed rec_*）
       C) LEGACY_MINUS_STRICT —— B 相对 A 的差集，即「旧口径多算进来的样本」
  3. 按 §2 要求披露：前后样本数、交易日数、净胜率、平均净收益、PF、最大回撤、
     标签翻转矩阵，以及等权组合资金曲线（abstain 计现金）。
  4. 覆盖率 = 可重放笔数 / 选中笔数（缺口如实报告，不作否决）。

诚实性：
  - 标签 live_forward_rebuild / champion_replay；不声称提高收益。
  - 只读，不写任何生产表；结果落 output/champion_baseline_<date>.json。

用法：
  backend/.venv/bin/python backend/scripts/si_champion_baseline_rebuild.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import (  # noqa: E402
    DailyKbar,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
)
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402
from app.services.outcome_simulation import (  # noqa: E402
    DEFAULT_STOP_LOSS_PCT,
    _simulate_outcome,
)

NET_COST_PCT = 0.07          # 与 self_check / 既有反事实脚本一致的往返成本
HORIZON = 5                  # production 默认结算 horizon
SCORE_LINE_FLOOR = 72.0      # production clean 地板
OUTPUT_DIR = BACKEND_ROOT.parent / "output"

STRICT_CONDITIONS = official_sample_conditions()
LEGACY_CONDITIONS = (
    RecommendationRun.run_mode == "formal",
    RecommendationRun.status == "completed",
    Recommendation.recommendation_id.like("rec_%"),
)


def _board(code: str) -> str:
    c = str(code or "")
    return "20cm" if c.startswith(("300", "301", "688", "689")) else "10cm"


def _selected(db, conditions: tuple, score_floor: float | None) -> list[Recommendation]:
    q = (
        select(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*conditions)
    )
    if score_floor is not None:
        q = q.where(Recommendation.final_score >= score_floor)
    return list(db.scalars(q.order_by(Recommendation.trade_date.asc())).all())


def _replay(db, recs: list[Recommendation]) -> dict[str, dict]:
    """用当前生产代码因果重放每笔推荐（production exit, horizon=5）。"""
    codes = sorted({str(r.code) for r in recs})
    bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
    if codes:
        for bar in db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code.in_(codes))
            .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
        ).all():
            bars_by_code[str(bar.code)].append(bar)

    out: dict[str, dict] = {}
    for rec in recs:
        bars = bars_by_code.get(str(rec.code)) or []
        if not bars:
            continue
        dates = [str(b.trade_date) for b in bars]
        sim = _simulate_outcome(rec, bars, dates, HORIZON, exit_resolver=None)
        if not sim or not sim.get("entry_triggered"):
            continue
        label = sim.get("outcome_label")
        if label not in ("success", "failed"):
            continue
        gross = float(sim.get("executed_return") or 0.0)
        out[str(rec.recommendation_id)] = {
            "code": str(rec.code),
            "board": _board(rec.code),
            "bucket": str(rec.bucket or ""),
            "trade_date": str(rec.trade_date),
            "score": float(rec.final_score or 0.0),
            "gross": round(gross, 4),
            "net": round(gross - NET_COST_PCT, 4),
            "label": label,
            "exit_reason": str(sim.get("exit_reason") or ""),
        }
    return out


def _load_stored(db, rec_ids: set[str]) -> dict[str, dict]:
    if not rec_ids:
        return {}
    rows = db.scalars(
        select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id.in_(sorted(rec_ids))
        )
    ).all()
    out: dict[str, dict] = {}
    for o in rows:
        label = o.outcome_label
        if label not in ("success", "failed"):
            continue
        gross = float(o.executed_return or 0.0)
        out[str(o.recommendation_id)] = {
            "net": round(gross - NET_COST_PCT, 4),
            "label": label,
        }
    return out


def _metrics(records: list[dict]) -> dict:
    rets = [float(r["net"]) for r in records]
    if not rets:
        return {"n": 0, "trade_days": 0}
    wins = sum(1 for x in rets if x > 0)
    gw = sum(x for x in rets if x > 0)
    gl = -sum(x for x in rets if x < 0)
    return {
        "n": len(rets),
        "trade_days": len({r["trade_date"] for r in records}),
        "win_rate_pct": round(wins / len(rets) * 100, 1),
        "avg_net_pct": round(sum(rets) / len(rets), 3),
        "sum_net_pct": round(sum(rets), 2),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "avg_win_pct": round(gw / max(1, wins), 2),
        "avg_loss_pct": round(-gl / max(1, len(rets) - wins), 2),
        "max_loss_pct": round(min(rets), 2),
    }


def _equity_curve(records: list[dict]) -> dict:
    """等权组合资金曲线：abstain（无票日）计现金，按 trade_date 聚合日收益。

    说明：horizon=5 存在持仓重叠，无法在无仓位记录的情况下重建真实仓位，
    因此这里用「等权、按信号日聚合」的口径，并在输出中显式标注。
    """
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_date[r["trade_date"]].append(float(r["net"]))
    if not by_date:
        return {"method": "equal_weight_by_signal_date", "cum_net_pct": 0.0, "max_drawdown_pct": 0.0}
    equity = 100.0
    peak = 100.0
    mdd = 0.0
    daily = []
    for td in sorted(by_date):
        day_ret = sum(by_date[td]) / len(by_date[td])
        equity *= 1 + day_ret / 100.0
        peak = max(peak, equity)
        mdd = min(mdd, (equity - peak) / peak * 100.0)
        daily.append({"trade_date": td, "n": len(by_date[td]), "day_net_pct": round(day_ret, 3)})
    return {
        "method": "equal_weight_by_signal_date (abstain/cash, overlapping horizons not netted)",
        "cum_net_pct": round(equity - 100.0, 2),
        "max_drawdown_pct": round(mdd, 2),
        "daily": daily,
    }


def _breakdown(records: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[str(r.get(key) or "?")].append(r)
    return {k: _metrics(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def _label_flip(a: dict[str, dict], b: dict[str, dict]) -> dict:
    flips = defaultdict(int)
    for rid in sorted(set(a) & set(b)):
        flips[(a[rid]["label"], b[rid]["label"])] += 1
    return {
        "success_success": flips[("success", "success")],
        "success_failed": flips[("success", "failed")],
        "failed_success": flips[("failed", "success")],
        "failed_failed": flips[("failed", "failed")],
    }


def _population_block(db, tag: str, conditions: tuple, score_floor: float | None) -> dict:
    selected = _selected(db, conditions, score_floor)
    replayed = _replay(db, selected)
    stored = _load_stored(db, set(replayed))
    common = sorted(set(replayed) & set(stored))
    recs = [replayed[rid] for rid in common]
    block = {
        "tag": tag,
        "score_floor": score_floor,
        "selected_n": len(selected),
        "replayable_n": len(replayed),
        "stored_settled_n": len(common),
        "coverage_pct": round(len(replayed) / len(selected) * 100, 1) if selected else 0.0,
        "metrics": _metrics(recs),
        "stored_metrics": _metrics([{"net": stored[rid]["net"], "trade_date": replayed[rid]["trade_date"]} for rid in common]),
        "equity_curve": _equity_curve(recs),
        "board_breakdown": _breakdown(recs, "board"),
        "bucket_breakdown": _breakdown(recs, "bucket"),
        "exit_reason_breakdown": _breakdown(recs, "exit_reason"),
        "label_flip_replay_vs_stored": _label_flip({rid: replayed[rid] for rid in common}, stored),
        "detail": [
            {
                "recommendation_id": rid,
                "code": replayed[rid]["code"],
                "trade_date": replayed[rid]["trade_date"],
                "score": replayed[rid]["score"],
                "net_pct": replayed[rid]["net"],
                "label": replayed[rid]["label"],
                "exit_reason": replayed[rid]["exit_reason"],
            }
            for rid in common
        ],
    }
    return block


def _legacy_minus_strict(db, score_floor: float | None) -> dict:
    strict = _replay(db, _selected(db, STRICT_CONDITIONS, score_floor))
    legacy = _replay(db, _selected(db, LEGACY_CONDITIONS, score_floor))
    diff = {k: v for k, v in legacy.items() if k not in strict}
    treated_as_strict = {k: v for k, v in strict.items() if k in legacy}
    return {
        "tag": "LEGACY_MINUS_STRICT (旧口径多算进来的样本)",
        "score_floor": score_floor,
        "legacy_n": len(legacy),
        "strict_n": len(strict),
        "strict_replayable_n": len(treated_as_strict),
        "diff_n": len(diff),
        "diff_metrics": _metrics(list(diff.values())),
        "strict_metrics": _metrics(list(strict.values())),
        "diff_date_span": sorted({v["trade_date"] for v in diff.values()}),
    }


def main() -> None:
    db = SessionLocal()
    try:
        print(f"[config] net_cost={NET_COST_PCT}% horizon={HORIZON} "
              f"production_stop={DEFAULT_STOP_LOSS_PCT}% score_floor={SCORE_LINE_FLOOR}")
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "kind": "champion_baseline_rebuild",
            "net_cost_pct": NET_COST_PCT,
            "horizon": HORIZON,
            "production_stop_loss_pct": DEFAULT_STOP_LOSS_PCT,
            "populations": [],
            "scope_audit": [],
        }

        for floor in (None, SCORE_LINE_FLOOR):
            label = "all_scores" if floor is None else f"clean>={floor:g}"
            a = _population_block(db, f"LIVE_FORMAL_STRICT/{label}", STRICT_CONDITIONS, floor)
            b = _population_block(db, f"FORMAL_ALL_LEGACY/{label}", LEGACY_CONDITIONS, floor)
            c = _legacy_minus_strict(db, floor)
            payload["populations"].extend([a, b])
            payload["scope_audit"].append(c)

            print(f"\n=== {label} ===")
            for blk in (a, b):
                m = blk["metrics"]
                print(f"  {blk['tag']}: selected={blk['selected_n']} replayable={blk['replayable_n']} "
                      f"settled={blk['stored_settled_n']} coverage={blk['coverage_pct']}%")
                print(f"    metrics={m}")
                print(f"    equity={ {k: v for k, v in blk['equity_curve'].items() if k != 'daily'} }")
                print(f"    boards={blk['board_breakdown']}")
            print(f"  LEGACY-STRICT diff_n={c['diff_n']} diff_metrics={c['diff_metrics']}")
            print(f"    strict_metrics={c['strict_metrics']}")

        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"champion_baseline_{datetime.now().strftime('%Y%m%d')}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\n[persist] -> {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
