"""显式重算（2026-10-27）：10-14 以来单策略实盘已结算记录，按新结算规则 r20261027 + 新盘中确认规则重算。

只在用户批准后用。默认只预览（不写库）；加 --write 才写，写前自动备份到 data/backups/resettle_20261027/。
  预览：cd backend && .venv/bin/python scripts/resettle_single_since_20261014.py
  写库：cd backend && .venv/bin/python scripts/resettle_single_since_20261014.py --write
  撤销：cd backend && .venv/bin/python scripts/resettle_single_since_20261014.py --restore <备份文件> --write

口径：
- 样本：canonical live_forward + 凌晨补跑的正式 run，单技能 run，推荐日 ≥ 2026-10-14，已结算（success/failed/missed），
  且 rule_version 还不是当前版本。
- 新确认规则（app/services/resettle_confirmation.py）：按这只推荐自己的入场窗口逐日做入场日检查
  （可成交、非一字涨停、开盘 ≤ 推荐价 +6%），有一天通过就视为「确认买入」。不写执行状态表（实盘确认记录保持原样）；
  结算记 label_source=resettle_20261027，此后自动重算/补结算都按同一规则推出执行资格，不会被投影回旧结论。
- 持有期没走完的（provisional）照样写入；之后每日补结算按当前版本 + 新确认规则走完。
- 结算：recompute_stale_outcomes(upgrade_rule_version=True)，结果记为当前版本；outcome 版本表追加新版本，旧版本保留。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import inspect, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402
from app.services import outcome_service as svc  # noqa: E402
from app.services.evidence.validation import canonical_live_forward_run_ids, catchup_formal_run_ids  # noqa: E402
from app.services.execution_outcome_policy import apply_execution_outcome_policy  # noqa: E402
from app.services.live_trade_breakdown import classify_era  # noqa: E402
from app.services.outcome_simulation import _simulate_outcome  # noqa: E402
from app.services.resettle_confirmation import RESETTLE_LABEL_SOURCE as LABEL_SOURCE  # noqa: E402
from app.services.resettle_confirmation import derived_execution_state, new_rule_confirmation  # noqa: E402

SINCE = "2026-10-14"
SETTLED = ("success", "failed", "missed")
BACKUP_DIR = B / "data" / "backups" / "resettle_20261027"


def select_targets(db) -> list[Recommendation]:
    run_ids = tuple(set(canonical_live_forward_run_ids(db)) | set(catchup_formal_run_ids(db)))
    rows = db.execute(
        select(Recommendation, RecommendationRun, RecommendationOutcome)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(Recommendation.run_id.in_(run_ids), Recommendation.trade_date >= SINCE,
               RecommendationOutcome.outcome_label.in_(SETTLED),
               RecommendationOutcome.rule_version != svc.OUTCOME_RULE_VERSION)
        .order_by(Recommendation.trade_date, Recommendation.code)
    ).all()
    return [rec for rec, run, _o in rows if classify_era(run.preset_id, run.selected_skills_json) == "single"]


def _row_dict(obj) -> dict:
    out = {}
    for col in inspect(obj).mapper.column_attrs:
        v = getattr(obj, col.key)
        out[col.key] = v.isoformat() if isinstance(v, datetime) else v
    return out


def backup(db, recs: list[Recommendation]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"backup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rule_version_target": svc.OUTCOME_RULE_VERSION,
        "outcomes": [_row_dict(db.get(RecommendationOutcome, r.recommendation_id)) for r in recs],
        "recommendation_status": {r.recommendation_id: r.status for r in recs},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path


def restore(db, path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cols = {c.key: c for c in inspect(RecommendationOutcome).mapper.column_attrs}
    n = 0
    for row in payload["outcomes"]:
        o = db.get(RecommendationOutcome, row["recommendation_id"])
        if o is None:
            continue
        for k, v in row.items():
            if k == "recommendation_id" or k not in cols:
                continue
            col_type = cols[k].columns[0].type
            if v is not None and getattr(col_type, "python_type", None) is datetime:
                v = datetime.fromisoformat(v)
            setattr(o, k, v)
        n += 1
    for rid, status in payload["recommendation_status"].items():
        rec = db.get(Recommendation, rid)
        if rec is not None:
            rec.status = status
    return n


def preview(db, recs: list[Recommendation]) -> dict[str, object]:
    bars_by_code, _ = svc._load_outcome_bars(db, recs)
    states = svc._load_execution_states(db, recs)
    overrides: dict[str, object] = {}
    print(f"共 {len(recs)} 条（推荐日 ≥ {SINCE}，单策略，已结算，版本 ≠ {svc.OUTCOME_RULE_VERSION}）")
    print("推荐日 | 代码 名称 | 策略 | 新确认 | 现在库里 | 新规则重算后")
    for rec in recs:
        o = db.get(RecommendationOutcome, rec.recommendation_id)
        cday, cprice, _ok = new_rule_confirmation(db, rec)
        state = derived_execution_state(db, rec, states.get(str(rec.recommendation_id)))
        overrides[str(rec.recommendation_id)] = state
        horizon = int(o.horizon_days or 5)
        bounded = svc.bounded_outcome_bars(rec, bars_by_code.get(str(rec.code), []), horizon,
                                           entry_lookahead_days=svc.ENTRY_LOOKAHEAD_DAYS)
        sim = _simulate_outcome(rec, bounded, [str(b.trade_date) for b in bounded], horizon_days=horizon,
                                ma_context_bars=bars_by_code.get(str(rec.code), []))
        new = svc._apply_settlement_gate(apply_execution_outcome_policy(sim, state), bounded) if sim else {}
        new_ret = new.get("executed_return")
        print(f"{rec.trade_date} | {rec.code} {rec.name} | {rec.bucket} | "
              f"{(cday or '—')} {cprice if cprice else ''} | "
              f"{o.outcome_label} {o.exit_reason} {o.executed_return} | "
              f"{new.get('outcome_label')} {new.get('entry_date')} {new.get('entry_price')} → "
              f"{new.get('exit_date')} {new.get('exit_price')} {new_ret} {new.get('exit_reason')}")
    return overrides


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="真的写库（默认只预览）")
    ap.add_argument("--restore", type=Path, help="从备份文件恢复（需同时加 --write）")
    args = ap.parse_args()
    with SessionLocal() as db:
        if args.restore:
            if not args.write:
                print(f"预览：将从 {args.restore} 恢复；加 --write 才执行")
                return
            n = restore(db, args.restore)
            db.commit()
            svc.invalidate_outcome_metrics_cache()
            print(f"已恢复 {n} 条")
            return
        recs = select_targets(db)
        overrides = preview(db, recs)
        if not args.write:
            print("\n只预览，未写库。用户批准后加 --write 执行（写前自动备份）。")
            return
        path = backup(db, recs)
        print(f"\n已备份到 {path}")
        result = svc.recompute_stale_outcomes(
            db, recommendation_ids=[r.recommendation_id for r in recs], label_source=LABEL_SOURCE,
            upgrade_rule_version=True, execution_state_overrides=overrides,
        )
        print("重算结果：", result)
        print(f"撤销：.venv/bin/python scripts/resettle_single_since_20261014.py --restore {path} --write")


if __name__ == "__main__":
    main()
