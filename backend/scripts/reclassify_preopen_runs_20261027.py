"""列出（并在用户批准后改判）「入场日开盘前生成、却没记成实盘」的短线 formal run（2026-10-27）。

规则（与 midterm_live_seal.generated_before_entry_open 同一函数）：信号日 D 当天到入场日（下一个开市日）09:15 之前
生成的推荐算实盘 live_forward；之后生成的才算回放。安全网补跑、凌晨补跑、正常日更一律按生成时间判。

默认只预览，不写库：
  cd backend && .venv/bin/python scripts/reclassify_preopen_runs_20261027.py [--days 30]
用户批准后写库（写前自动备份到 data/backups/reclassify_preopen_20261027/）：
  cd backend && .venv/bin/python scripts/reclassify_preopen_runs_20261027.py --write
撤销：
  cd backend && .venv/bin/python scripts/reclassify_preopen_runs_20261027.py --restore <备份文件> --write

改判对象：run_mode=formal、status=completed、不是历史重建批次（evidence_class != historical_reconstruction、无 batch）、
不是矩阵批跑，真实生成时刻（完成时刻 finished_at 与最后一条推荐写入时刻取较晚者，库内 UTC；
没有完成时刻的不算）在入场日 09:15 前，且当前不是规范的实盘记录。
同一信号日同一策略已经有规范实盘批次的，跳过（不制造重复实盘）。
写库只改 run 行的口径字段：evaluation_scope=live_forward，并用该 run 自己记录的代码版本重新生成证据身份；
推荐、结算、执行状态都不动。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import Recommendation, RecommendationRun  # noqa: E402
from app.services.evidence.identity import EvidenceClass  # noqa: E402
from app.services.evidence.provenance_writer import apply_formal_run_provenance, run_generated_at  # noqa: E402
from app.services.evidence.validation import canonical_live_forward_run_ids  # noqa: E402
from app.services.midterm_live_seal import generated_before_entry_open  # noqa: E402
from app.services.standalone_strategies import run_skill_ids  # noqa: E402

BACKUP_DIR = B / "data" / "backups" / "reclassify_preopen_20261027"
FIELDS = (
    "evaluation_scope", "evidence_class", "actual_generated_at", "data_as_of", "source_snapshot_id",
    "evidence_identity_hash", "strategy_policy_fingerprint", "execution_policy_fingerprint", "code_revision",
    "provenance_schema_version", "classification_reason",
)


def _recorded_revision(run) -> str | None:
    """run 自己执行时的代码版本：已写入的 code_revision，其次执行审计里的 application.revision。"""
    if run.code_revision and str(run.code_revision).lower() not in {"unknown", "none", "n/a"}:
        return str(run.code_revision)
    try:
        audit = (json.loads(run.result_summary_json or "{}").get("execution") or {})
        rev = (audit.get("application") or {}).get("revision")
    except (TypeError, ValueError, AttributeError):
        rev = None
    return str(rev) if rev else None


def _is_matrix(run) -> bool:
    try:
        return bool(json.loads(run.request_json or "{}").get("matrix_run"))
    except (TypeError, ValueError):
        return False


def find_candidates(db, days: int) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    live = set(canonical_live_forward_run_ids(db, date_from=since))
    runs = db.scalars(
        select(RecommendationRun).where(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
            RecommendationRun.trade_date >= since,
        ).order_by(RecommendationRun.trade_date, RecommendationRun.started_at)
    ).all()
    live_keys = set()
    for run in runs:
        if run.run_id in live:
            ids = run_skill_ids(run.selected_skills_json)
            live_keys.add((str(run.trade_date), ids[0] if len(ids) == 1 else "multi"))
    out = []
    for run in runs:
        if run.run_id in live:
            continue
        if run.evidence_class == EvidenceClass.HISTORICAL_RECONSTRUCTION.value or run.reconstruction_batch_id:
            continue
        if _is_matrix(run):
            continue
        last_rec = db.scalar(select(func.max(Recommendation.recommended_at)).where(Recommendation.run_id == run.run_id))
        generated = run_generated_at(run, last_rec)
        if generated is None or not generated_before_entry_open(str(run.trade_date), generated):
            continue
        ids = run_skill_ids(run.selected_skills_json)
        skill = ids[0] if len(ids) == 1 else "multi"
        n_recs = db.scalar(select(func.count()).select_from(Recommendation).where(Recommendation.run_id == run.run_id))
        if (str(run.trade_date), skill) in live_keys:
            action = "跳过：同日同策略已有实盘批次"
        elif not _recorded_revision(run):
            action = "跳过：找不到该 run 自己的代码版本"
        else:
            action = "改判为实盘"
        out.append({
            "run_id": run.run_id, "trade_date": str(run.trade_date), "strategy": skill,
            "generated_at_utc": generated.replace(tzinfo=None).isoformat(), "recommendations": int(n_recs or 0),
            "now": f"{run.evaluation_scope}/{run.evidence_class}", "action": action,
        })
    return out


def backup(db, run_ids: list[str]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"backup_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    rows = []
    for rid in run_ids:
        run = db.get(RecommendationRun, rid)
        rows.append({"run_id": rid, **{f: (getattr(run, f).isoformat() if isinstance(getattr(run, f), datetime)
                                           else getattr(run, f)) for f in FIELDS}})
    path.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "runs": rows},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def restore(db, path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for row in payload["runs"]:
        run = db.get(RecommendationRun, row["run_id"])
        if run is None:
            continue
        for f in FIELDS:
            v = row.get(f)
            if f == "actual_generated_at" and v:
                v = datetime.fromisoformat(v)
            setattr(run, f, v)
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="往回看多少个自然日（默认 30）")
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
            print(f"已恢复 {n} 个 run")
            return
        cands = find_candidates(db, args.days)
        print(f"近 {args.days} 天入场日开盘前生成、但没记成实盘的 formal run：{len(cands)} 个")
        print("信号日 | 策略 | 生成完成(UTC) | 推荐数 | 现在 scope/class | 处理")
        for c in cands:
            print(f"{c['trade_date']} | {c['strategy']} | {c['generated_at_utc']} | {c['recommendations']} | {c['now']} | {c['action']}")
        todo = [c["run_id"] for c in cands if c["action"] == "改判为实盘"]
        print(f"\n将改判 {len(todo)} 个 run（{sum(c['recommendations'] for c in cands if c['action'] == '改判为实盘')} 条推荐）")
        if not args.write:
            print("只预览，未写库。用户批准后加 --write 执行（写前自动备份）。")
            return
        path = backup(db, todo)
        print(f"已备份到 {path}")
        done = 0
        for rid in todo:
            run = db.get(RecommendationRun, rid)
            run.evaluation_scope = "live_forward"
            run.evidence_identity_hash = None  # 让写入器按新规则重新生成身份
            last_rec = db.scalar(select(func.max(Recommendation.recommended_at)).where(Recommendation.run_id == rid))
            identity = apply_formal_run_provenance(run, code_revision=_recorded_revision(run),
                                                   latest_recommendation_at=last_rec)
            if identity is not None and identity.evidence_class is EvidenceClass.LIVE_FORWARD:
                done += 1
            else:
                print(f"  {rid} 证据不完整，未能记成实盘（已按写入器结果保留）")
        db.commit()
        print(f"已改判 {done} 个。撤销：.venv/bin/python scripts/reclassify_preopen_runs_20261027.py --restore {path} --write")


if __name__ == "__main__":
    main()
