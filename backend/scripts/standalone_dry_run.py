"""单策略独立运行 dry-run：对某个历史交易日，每个策略各跑一次完整 formal 流水线，
全部写操作包在一个外层事务里，结束时整体回滚——数据库不留任何 run/推荐。

用法：.venv/bin/python scripts/standalone_dry_run.py --trade-date 2026-10-13 [--skills a,b]
（关闭 LLM 审查；只读本地池；不触发上游刷新）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import engine  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade-date", required=True)
    ap.add_argument("--skills", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from app.services import scheduler_service as sched
    from app.services.workbench_service import run_workbench

    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint")
    report = []
    try:
        base = {
            "preset_id": "preset_formal_opening",
            "data_source": "local_pool",
            "risk_template": "balanced",
            "skip_aux_refresh": True,
            "disable_agent_review": True,
            "trade_date_override": args.trade_date,
        }
        if args.skills:
            base["standalone_skill_ids"] = [s for s in args.skills.split(",") if s]
        payloads = sched._standalone_run_payloads(db, base)
        for p in payloads:
            p.pop("standalone_skill_ids", None)
            sid = p["single_skill_id"]
            try:
                res = asyncio.run(run_workbench(db, p))
                run = res.get("run") or {}
                recs = res.get("recommendations") or []
                picks = [
                    {"code": r.get("code"), "name": r.get("name"), "final_score": r.get("final_score")}
                    for r in recs
                ]
                from app.models.tables import RecommendationRun

                rr = db.get(RecommendationRun, run.get("run_id"))
                summary = json.loads(rr.result_summary_json or "{}") if rr else {}
                audit = json.loads(rr.filter_audit_json or "{}") if rr else {}
                row = {"skill_id": sid, "status": run.get("status"), "score_line": p["score_line"],
                       "output_count": p["output_count"], "n": len(picks), "picks": picks,
                       "universe": getattr(rr, "universe_count", None),
                       "skill_candidates": summary.get("skill_rule_candidate_count"),
                       "no_opportunity_reason": summary.get("no_opportunity_reason"),
                       "empty_reason": audit.get("empty_reason"),
                       "reason_counts": audit.get("reason_counts")}
            except Exception as exc:  # noqa: BLE001
                row = {"skill_id": sid, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}
            report.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        db.close()
        outer.rollback()
        conn.close()
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
