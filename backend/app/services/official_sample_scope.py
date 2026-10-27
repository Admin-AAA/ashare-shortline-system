"""Canonical eligibility rules for user-facing recommendation samples.

Callers remain responsible for their projection and date/filter clauses.  This
module owns the facts that make a recommendation eligible to be described as a
completed forward sample or an explicitly authorized historical reconstruction.

The historical-reconstruction branch is not defined here: its single source of
truth is ``app.services.evidence.selectors``, re-exported below so existing
callers keep working without a second copy of the expression.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, false, or_

from app.models.tables import Recommendation, RecommendationRun
from app.services.evidence.selectors import (
    OFFICIAL_RECOMMENDATION_PREFIX,
    historical_reconstruction_run_conditions,
    live_forward_run_conditions,
)


LIVE_FORWARD_SCOPE = "live_forward"
HISTORICAL_REPLAY_SCOPE = "historical_replay"
METRICS_SAMPLE_SCOPES = (LIVE_FORWARD_SCOPE, HISTORICAL_REPLAY_SCOPE)


def evaluation_scope_for_run(
    *,
    run_mode: str,
    trade_date_override: str | None,
    matrix_run: bool = False,
    generated_at: datetime | None = None,
) -> str:
    """Classify a newly requested run without inferring from its display name.

    带 trade_date_override 的 formal run（安全网补跑等）：在该信号日入场日开盘（09:15）前生成的，
    与正常日更同口径算实盘（live_forward），之后生成的才是回放。判定统一走
    midterm_live_seal.generated_before_entry_open（2026-10-27）。矩阵批跑始终是回放。

    generated_at 在排队/开跑时只能给「开始时刻」，结果是暂定的；任务完成时 finalize_override_scope
    按真实生成时刻（完成时刻与最后一条推荐写入时刻取较晚者）定稿，开盘后才完成的改回回放。
    """
    normalized_mode = str(run_mode or "formal").lower()
    if normalized_mode == "experiment":
        return "experiment"
    if normalized_mode == "backtest":
        return "backtest"
    if matrix_run:
        return "historical_replay"
    if trade_date_override:
        if normalized_mode == "formal" and generated_at is not None:
            from app.services.midterm_live_seal import generated_before_entry_open

            try:
                if generated_before_entry_open(str(trade_date_override), generated_at):
                    return LIVE_FORWARD_SCOPE
            except Exception:  # noqa: BLE001 - 判定不了时保守记为回放
                pass
        return "historical_replay"
    return LIVE_FORWARD_SCOPE


def _override_of(run) -> str | None:
    import json

    try:
        request = json.loads(getattr(run, "request_json", None) or getattr(run, "params_json", None) or "{}")
    except (TypeError, ValueError):
        return None
    value = request.get("trade_date_override") if isinstance(request, dict) else None
    return str(value) if value else None


def finalize_override_scope(run, generated_at: datetime | None) -> bool:
    """完成时定稿：带 override 的 formal run 若暂定为 live_forward、但真实生成时刻不在入场日开盘前
    （或没有完成时刻）→ 改回 historical_replay。返回是否改了。正常日更（无 override）不在这里改。"""
    if str(getattr(run, "run_mode", "") or "").lower() != "formal":
        return False
    if str(getattr(run, "evaluation_scope", "") or "") != LIVE_FORWARD_SCOPE:
        return False
    override = _override_of(run)
    if not override:
        return False
    from app.services.midterm_live_seal import generated_before_entry_open

    try:
        live = generated_at is not None and generated_before_entry_open(override, generated_at)
    except Exception:  # noqa: BLE001 - 判定不了时保守记为回放
        live = False
    if live:
        return False
    run.evaluation_scope = HISTORICAL_REPLAY_SCOPE
    return True


def is_reconstruction_marked(run) -> bool:
    """显式历史重建（replay_all_days 预留了 batch / 证据类）：口径归重建适配器管，这里一律不动。"""
    return bool(getattr(run, "reconstruction_batch_id", None)) or (
        str(getattr(run, "evidence_class", "") or "") == "historical_reconstruction"
    )


NOT_LIVE_TERMINAL_STATUSES = frozenset({"failed", "interrupted", "cancelled"})


def scope_on_transition(run, request, target_status: str, now: datetime) -> str | None:
    """任务状态迁移时带 override 的 formal 补跑应写入的 evaluation_scope；None = 不改。

    - 进入失败 / 中断 / 取消：定稿为不算实盘（historical_replay）。永远完成不了的补跑不能一直挂着「暂定实盘」。
    - 重新开跑（RUNNING，含中断后恢复）：按此刻重新暂定（入场日开盘前 → live_forward）；完成时再按真实生成时刻定稿。
    正常日更（无 override）、矩阵批跑、显式历史重建都不在这里改。
    """
    mode = getattr(getattr(request, "run_mode", None), "value", getattr(request, "run_mode", None))
    if str(mode or "").lower() != "formal":
        return None
    override = getattr(request, "trade_date_override", None)
    if not override or bool(getattr(request, "matrix_run", False)) or is_reconstruction_marked(run):
        return None
    current = str(getattr(run, "evaluation_scope", "") or "")
    status = str(getattr(target_status, "value", target_status) or "")
    if status in NOT_LIVE_TERMINAL_STATUSES:
        return HISTORICAL_REPLAY_SCOPE if current == LIVE_FORWARD_SCOPE else None
    if status == "running":
        tentative = evaluation_scope_for_run(run_mode="formal", trade_date_override=str(override),
                                             generated_at=now)
        return tentative if tentative != current else None
    return None


def official_sample_conditions() -> tuple:
    """Canonical LIVE_FORWARD eligibility for current demo metrics."""
    return (
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        *live_forward_run_conditions(),
        Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
    )


def historical_reconstruction_sample_conditions() -> tuple:
    """Backward-compatible alias; the canonical definition lives in selectors.

    Kept so existing imports of this name resolve to the same expression owned
    by ``app.services.evidence.selectors``.
    """
    return historical_reconstruction_run_conditions()


def recommendation_history_conditions(
    *,
    live_run_ids: tuple[str, ...] | list[str] = (),
    historical_run_ids: tuple[str, ...] | list[str] = (),
    catchup_run_ids: tuple[str, ...] | list[str] = (),
) -> tuple:
    """Return only explicitly validated LIVE or reconstruction run rows.

    ``catchup_run_ids`` (display-only) admits next-morning catch-up formal runs.
    """

    live_branch = false()
    if live_run_ids:
        live_branch = and_(
            *official_sample_conditions(),
            RecommendationRun.run_id.in_(tuple(live_run_ids)),
        )
    historical_branch = false()
    if historical_run_ids:
        historical_branch = and_(
            *historical_reconstruction_run_conditions(),
            RecommendationRun.run_id.in_(tuple(historical_run_ids)),
        )
    catchup_branch = false()
    if catchup_run_ids:
        catchup_branch = and_(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
            Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
            RecommendationRun.run_id.in_(tuple(catchup_run_ids)),
        )
    return (or_(live_branch, historical_branch, catchup_branch),)


__all__ = [
    "HISTORICAL_REPLAY_SCOPE",
    "LIVE_FORWARD_SCOPE",
    "METRICS_SAMPLE_SCOPES",
    "OFFICIAL_RECOMMENDATION_PREFIX",
    "evaluation_scope_for_run",
    "finalize_override_scope",
    "is_reconstruction_marked",
    "scope_on_transition",
    "historical_reconstruction_sample_conditions",
    "official_sample_conditions",
    "recommendation_history_conditions",
]
