"""Self-improve champion replay — MEASUREMENT_FIX (L-002 污染重放协议).

背景：
  self_check 在 D 项反复标注「同口径 champion 待重放（旧历史基线已失效，不计算 Δ）」。
  07-13 止盈价重算 + 07-21 RSI/max_dd 失真列重算等口径变化后，旧回合报告的 champion
  基线（如 clean≥72 净WR79.8%/PLR3.21）已不可信；按 L-002，必须先以当前代码重放未修改
  的 production 策略，建立无污染 champion，之后任何 challenger 比较才有效。

本脚本做的是「champion 自身的重放」，不是 challenger：
  - 冻结生产单路径因果退出状态机（_simulate_outcome，exit_resolver=None），
    champion 止损 -5.5% 对两个板块均生效（与 board-stop 反事实的 champion 档一致）。
  - horizon=5（与 production 默认结算 horizon 一致；board-stop 用 3 做相对配对，
    本次建立绝对基线必须用 5 才能对齐存储 outcome）。
  - 在 official_sample_conditions() 口径下重放 clean≥72 与全量 formal 推荐。
  - 与存储 recommendation_outcomes 逐笔对齐，报告前后样本数、净胜率、平均净收益、
    PF、最大亏损、标签翻转矩阵（L-002 强制披露项）。

诚实性：
  - 标签 backtest/champion_replay；不声称提高收益（MEASUREMENT_FIX 仅证明测量能力）。
  - 仅读取，不写任何生产表；结果落 output/champion_baseline_*.json 供未来轮次计算 Δ。

用法：
  backend/.venv/bin/python backend/scripts/si_champion_replay.py
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402
from app.services.outcome_simulation import DEFAULT_STOP_LOSS_PCT, _simulate_outcome  # noqa: E402

SCORE_LINE_FLOOR = 72.0          # clean≥72 生产地板
NET_COST_PCT = 0.07              # 与 self_check / board-stop 一致（扣 0.07%/笔）
CHAMPION_STOP = DEFAULT_STOP_LOSS_PCT   # -5.5，两板块均生效
HORIZON = 5                      # 与 production 默认结算 horizon 对齐
OUTPUT_DIR = BACKEND_ROOT.parent / "output"


def _board(code: str) -> str:
    c = str(code or "")
    return "20cm" if c.startswith(("300", "301", "688", "689")) else "10cm"


def _replay_population(db, score_floor: float | None) -> dict[str, dict]:
    """重放 official sample（可选 score>=floor）的 champion outcome。"""
    q = (
        select(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*official_sample_conditions())
    )
    if score_floor is not None:
        q = q.where(Recommendation.final_score >= score_floor)
    recs = db.scalars(q.order_by(Recommendation.trade_date.asc())).all()

    codes = sorted({str(r.code) for r in recs})
    bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
    for bar in db.scalars(
        select(DailyKbar).where(DailyKbar.code.in_(codes)).order_by(
            DailyKbar.code.asc(), DailyKbar.trade_date.asc()
        )
    ).all():
        bars_by_code[str(bar.code)].append(bar)

    out: dict[str, dict] = {}
    for rec in recs:
        code = str(rec.code)
        bars = bars_by_code.get(code) or []
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
            "code": code,
            "board": _board(code),
            "trade_date": str(rec.trade_date),
            "gross": gross,
            "net": round(gross - NET_COST_PCT, 4),
            "label": label,
            "exit_reason": sim.get("exit_reason"),
        }
    return out


def _load_stored(db, rec_ids: set[str]) -> dict[str, dict]:
    if not rec_ids:
        return {}
    rows = db.scalars(
        select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id.in_(rec_ids)
        )
    ).all()
    out: dict[str, dict] = {}
    for o in rows:
        label = o.outcome_label
        if label not in ("success", "failed"):
            continue
        out[str(o.recommendation_id)] = {
            "gross": float(o.executed_return or 0.0),
            "net": round(float(o.executed_return or 0.0) - NET_COST_PCT, 4),
            "label": label,
            "horizon_days": int(o.horizon_days or 5),
        }
    return out


def _metrics(records: list[dict]) -> dict:
    rets = [r["net"] for r in records]
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    gw = sum(x for x in rets if x > 0)
    gl = -sum(x for x in rets if x < 0)
    return {
        "n": len(rets),
        "win_rate": round(wins / len(rets) * 100, 1),
        "avg_net": round(sum(rets) / len(rets), 3),
        "sum_net": round(sum(rets), 1),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_loss": round(min(rets), 2),
    }


def _paired_bootstrap(champ_net: dict, stored_net: dict, td_map: dict, seed: int = 20260730, n_boot: int = 10000):
    """按 trade_date 分组的 paired cluster bootstrap，主判定量=replay-stored 平均单笔净收益差。"""
    common = sorted(set(champ_net) & set(stored_net))
    if len(common) < 2:
        return {"paired_n": len(common)}
    by_date: dict[str, list[float]] = defaultdict(list)
    diffs = []
    for k in common:
        d = td_map[k]
        by_date[d].append(champ_net[k] - stored_net[k])
        diffs.append(champ_net[k] - stored_net[k])
    dates_sorted = sorted(by_date)
    clusters = [by_date[d] for d in dates_sorted]
    mean_diff = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        samp = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        flat = [x for c in samp for x in c]
        boots.append(sum(flat) / len(flat))
    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[int(0.975 * n_boot)]
    return {
        "paired_n": len(common),
        "n_dates": len(dates_sorted),
        "mean_diff": round(mean_diff, 3),
        "ci95": [round(lo, 3), round(hi, 3)],
        "ci_excludes_zero": bool(lo > 0 or hi < 0),
    }


def _analyze(db, tag: str, score_floor: float | None) -> dict:
    replayed = _replay_population(db, score_floor)
    stored = _load_stored(db, set(replayed))
    common = sorted(set(replayed) & set(stored))
    td_map = {rid: replayed[rid]["trade_date"] for rid in replayed}

    replay_recs = [replayed[rid] for rid in common]
    stored_recs = [{"net": stored[rid]["net"]} for rid in common]
    flip = defaultdict(int)
    for rid in common:
        flip[(stored[rid]["label"], replayed[rid]["label"])] += 1

    before = _metrics(stored_recs)
    after = _metrics(replay_recs)
    champ_net = {rid: replayed[rid]["net"] for rid in common}
    stored_net = {rid: stored[rid]["net"] for rid in common}
    boot = _paired_bootstrap(champ_net, stored_net, td_map)

    # board breakdown (after)
    boards = defaultdict(list)
    for rid in common:
        boards[replayed[rid]["board"]].append(replayed[rid]["net"])
    board_metrics = {b: _metrics([{"net": x} for x in v]) for b, v in boards.items()}

    print(f"\n=== {tag} (horizon={HORIZON}, champion_stop={CHAMPION_STOP}%) ===")
    print(f"  replayed n={len(replayed)} | stored-aligned n={len(common)}")
    print(f"  BEFORE (stored outcome): {before}")
    print(f"  AFTER  (current-code replay): {after}")
    print(f"  board breakdown (after): {board_metrics}")
    print(f"  label flip matrix (stored→replay): "
          f"success→success={flip[('success','success')]}, "
          f"success→failed={flip[('success','failed')]}, "
          f"failed→success={flip[('failed','success')]}, "
          f"failed→failed={flip[('failed','failed')]}")
    print(f"  paired bootstrap (replay-stored) net diff: {boot}")
    return {
        "tag": tag,
        "horizon": HORIZON,
        "champion_stop": CHAMPION_STOP,
        "replayed_n": len(replayed),
        "aligned_n": len(common),
        "before": before,
        "after": after,
        "board_after": board_metrics,
        "label_flip": {
            "success_success": flip[("success", "success")],
            "success_failed": flip[("success", "failed")],
            "failed_success": flip[("failed", "success")],
            "failed_failed": flip[("failed", "failed")],
        },
        "paired_bootstrap": boot,
    }


def main() -> None:
    db = SessionLocal()
    try:
        print(f"[config] score_floor={SCORE_LINE_FLOOR} net_cost={NET_COST_PCT}% "
              f"champion_stop={CHAMPION_STOP}% horizon={HORIZON}")
        results = []
        results.append(_analyze(db, "clean>=72 formal (production champion population)", SCORE_LINE_FLOOR))
        results.append(_analyze(db, "full official formal (all scores)", None))

        baseline = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "code_version_note": "current _simulate_outcome, horizon=5, champion stop -5.5% both boards",
            "net_cost_pct": NET_COST_PCT,
            "populations": results,
        }
        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"champion_baseline_{datetime.now().strftime('%Y%m%d')}.json"
        out_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
        print(f"\n[persist] champion baseline -> {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
