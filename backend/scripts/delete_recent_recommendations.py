"""删除最近3个月的推荐记录及所有关联数据。

按外键依赖顺序删除：
1. 子表（outcome_versions, outcomes, execution_states, lock_signals, lock_monitors）
2. 关联表（agent_decision_logs, api_call_logs, collection_seals, classification_audits, review_notes, watch_items）
3. 主表（recommendations, recommendation_runs）
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text

START_DATE = "2026-06-17"


def in_clause(items: list[str], prefix: str = "id") -> tuple[str, dict]:
    """构建 IN 子句和参数字典。"""
    if not items:
        return "IN (NULL)", {}
    placeholders = ", ".join(f":{prefix}_{i}" for i in range(len(items)))
    params = {f"{prefix}_{i}": v for i, v in enumerate(items)}
    return f"IN ({placeholders})", params


def main() -> None:
    from app.core.config import get_settings
    settings = get_settings()
    engine = create_engine(settings.database_url)

    with engine.begin() as conn:
        # 1. 找到目标 run_id
        runs = conn.execute(
            text("SELECT run_id FROM recommendation_runs WHERE trade_date >= :start"),
            {"start": START_DATE},
        ).fetchall()
        run_ids = [r.run_id for r in runs]
        print(f"目标运行记录: {len(run_ids)} 条")

        if not run_ids:
            print("没有需要删除的记录")
            return

        # 找到目标 recommendation_id
        run_in, run_params = in_clause(run_ids, "rid")
        recs = conn.execute(
            text(f"SELECT recommendation_id FROM recommendations WHERE run_id {run_in}"),
            run_params,
        ).fetchall()
        rec_ids = [r.recommendation_id for r in recs]
        print(f"目标推荐记录: {len(rec_ids)} 条")

        rec_in, rec_params = in_clause(rec_ids, "recid")

        # 2. 删除子表数据
        # recommendation_outcome_versions
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM recommendation_outcome_versions WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 recommendation_outcome_versions: {n}")

        # recommendation_outcomes
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM recommendation_outcomes WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 recommendation_outcomes: {n}")

        # recommendation_execution_states
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM recommendation_execution_states WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 recommendation_execution_states: {n}")

        # intraday_lock_signals (通过 monitor_id 关联)
        if rec_ids:
            monitor_ids = conn.execute(
                text(f"SELECT monitor_id FROM intraday_lock_monitors WHERE recommendation_id {rec_in}"),
                rec_params,
            ).fetchall()
            mids = [m.monitor_id for m in monitor_ids]
            if mids:
                mon_in, mon_params = in_clause(mids, "mid")
                n = conn.execute(
                    text(f"DELETE FROM intraday_lock_signals WHERE monitor_id {mon_in}"),
                    mon_params,
                ).rowcount
                print(f"  删除 intraday_lock_signals: {n}")

            # intraday_lock_monitors
            n = conn.execute(
                text(f"DELETE FROM intraday_lock_monitors WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 intraday_lock_monitors: {n}")

        # 3. 删除关联表
        # agent_decision_logs (by run_id)
        n = conn.execute(
            text(f"DELETE FROM agent_decision_logs WHERE run_id {run_in}"),
            run_params,
        ).rowcount
        print(f"  删除 agent_decision_logs (by run): {n}")

        # agent_decision_logs (by recommendation_id)
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM agent_decision_logs WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 agent_decision_logs (by rec): {n}")

        # api_call_logs
        n = conn.execute(
            text(f"DELETE FROM api_call_logs WHERE run_id {run_in}"),
            run_params,
        ).rowcount
        print(f"  删除 api_call_logs: {n}")

        # recommendation_collection_seals
        n = conn.execute(
            text(f"DELETE FROM recommendation_collection_seals WHERE run_id {run_in}"),
            run_params,
        ).rowcount
        print(f"  删除 recommendation_collection_seals: {n}")

        # recommendation_evidence_classification_audits
        n = conn.execute(
            text(f"DELETE FROM recommendation_evidence_classification_audits WHERE run_id {run_in}"),
            run_params,
        ).rowcount
        print(f"  删除 recommendation_evidence_classification_audits: {n}")

        # review_notes
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM review_notes WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 review_notes: {n}")

        # watch_items (by run_id)
        n = conn.execute(
            text(f"DELETE FROM watch_items WHERE source_run_id {run_in}"),
            run_params,
        ).rowcount
        print(f"  删除 watch_items (by run): {n}")

        # watch_items (by recommendation_id)
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM watch_items WHERE source_recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 watch_items (by rec): {n}")

        # 4. 删除主表
        if rec_ids:
            n = conn.execute(
                text(f"DELETE FROM recommendations WHERE recommendation_id {rec_in}"),
                rec_params,
            ).rowcount
            print(f"  删除 recommendations: {n}")

        n = conn.execute(
            text(f"DELETE FROM recommendation_runs WHERE run_id {run_in}"),
            run_params,
        ).rowcount
        print(f"  删除 recommendation_runs: {n}")

        print("\n删除完成！")

        # 验证
        remaining = conn.execute(
            text("SELECT COUNT(*) as cnt FROM recommendations WHERE trade_date >= :start"),
            {"start": START_DATE},
        ).fetchone()
        print(f"验证 - 剩余推荐记录(>= {START_DATE}): {remaining.cnt}")

        remaining_runs = conn.execute(
            text("SELECT COUNT(*) as cnt FROM recommendation_runs WHERE trade_date >= :start"),
            {"start": START_DATE},
        ).fetchone()
        print(f"验证 - 剩余运行记录(>= {START_DATE}): {remaining_runs.cnt}")


if __name__ == "__main__":
    main()
