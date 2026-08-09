"""Version-bound six-month performance profiles for one installed strategy."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.backtest import (
    BacktestRequest,
    DateRange,
    ExecutionPolicy,
    Trade,
    canonical_fingerprint,
)
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY
from app.engine.simulation import compute_metrics
from app.engine.strategy import build_registry_from_skills, validate_backtest_parameter_set
from app.models.tables import (
    BacktestDataSnapshot,
    BacktestRunV2,
    BacktestTrade,
    DailyKbar,
)
from app.services.backtest.research_repository import ResearchBacktestRepository
from app.services.backtest.research_runner import (
    CANDIDATE_EVIDENCE_ACTIONABLE,
    RESEARCH_INPUT_SCHEMA_VERSION,
    ResearchBacktestRunner,
)
from app.services.registry.skills import sync_skills


STRATEGY_PROFILE_EXPERIMENT_ID = "strategy-backtest-profile:v1"
STRATEGY_PROFILE_WINDOW_DAYS = 180
STRATEGY_PROFILE_WINDOW_OPTIONS = (30, 180)
STRATEGY_PROFILE_SCHEMA_VERSION = 1
STRATEGY_PROFILE_PORTFOLIO_MODEL = "capped_daily_exit_cohort_v1"


@dataclass(frozen=True)
class StrategyBacktestProfileTarget:
    strategy_id: str
    strategy_version: str
    date_from: str
    date_to: str
    parameter_set: Mapping[str, Any]
    execution_policy: ExecutionPolicy
    data_requirement_fingerprint: str = ""

    def request(self) -> BacktestRequest:
        return BacktestRequest(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            date_selector=DateRange(self.date_from, self.date_to),
            execution_policy=self.execution_policy,
            parameter_set=self.parameter_set,
            experiment_id=STRATEGY_PROFILE_EXPERIMENT_ID,
        )

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.request().semantic_payload())


class StrategyBacktestProfiles:
    """Resolve, refresh and read exact current-version canonical profiles."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def current_targets(
        self,
        *,
        as_of: str | None = None,
    ) -> tuple[StrategyBacktestProfileTarget, ...]:
        policy = DEFAULT_EXECUTION_POLICY
        signal_end = self._latest_fully_settleable_signal_date(policy, as_of=as_of)
        signal_start = (
            date.fromisoformat(signal_end) - timedelta(days=STRATEGY_PROFILE_WINDOW_DAYS - 1)
        ).isoformat()
        skills = [
            skill
            for skill in sync_skills(self._db)
            if skill.enabled and skill.executable
        ]
        registry = build_registry_from_skills(skills)
        targets = []
        for skill in sorted(skills, key=lambda item: item.skill_id):
            descriptor = registry.get_descriptor(skill.skill_id)
            targets.append(
                StrategyBacktestProfileTarget(
                    strategy_id=descriptor.strategy_id,
                    strategy_version=descriptor.effective_version,
                    date_from=signal_start,
                    date_to=signal_end,
                    parameter_set=dict(
                        validate_backtest_parameter_set(descriptor, None)
                    ),
                    execution_policy=policy,
                    data_requirement_fingerprint=canonical_fingerprint(
                        descriptor.data_requirement.canonical_payload()
                    ),
                )
            )
        return tuple(targets)

    def current_target(
        self,
        strategy_id: str,
        *,
        as_of: str | None = None,
    ) -> StrategyBacktestProfileTarget | None:
        return next(
            (
                target
                for target in self.current_targets(as_of=as_of)
                if target.strategy_id == strategy_id
            ),
            None,
        )

    def read_many(
        self,
        targets: tuple[StrategyBacktestProfileTarget, ...],
    ) -> dict[str, dict[str, Any] | None]:
        return {target.strategy_id: self.read(target) for target in targets}

    def read_many_rolled(
        self,
        targets: tuple[StrategyBacktestProfileTarget, ...],
    ) -> dict[str, dict[str, Any] | None]:
        return {target.strategy_id: self.read_rolled(target) for target in targets}

    def refresh(
        self,
        target: StrategyBacktestProfileTarget,
        *,
        runner: ResearchBacktestRunner | None = None,
    ) -> dict[str, Any]:
        existing = self.read(target)
        if existing is not None:
            return {"status": "completed", "reused": True, "profile": existing}
        active_runner = runner or ResearchBacktestRunner(
            ResearchBacktestRepository(self._db),
            candidate_evidence_scope=CANDIDATE_EVIDENCE_ACTIONABLE,
        )
        shared_snapshot_id = self._compatible_snapshot_id(target)
        outcome = (
            active_runner.run_on_snapshot(target.request(), shared_snapshot_id)
            if shared_snapshot_id is not None
            else active_runner.run(target.request())
        )
        if str(outcome.get("status") or "") != "completed":
            return {
                "status": str(outcome.get("status") or "failed"),
                "reused": bool(outcome.get("reused")),
                "profile": None,
                "backtest": outcome,
            }
        profile = self.read(target)
        if profile is None:
            raise RuntimeError("completed strategy profile backtest was not readable")
        return {
            "status": "completed",
            "reused": bool(outcome.get("reused")),
            "profile": profile,
            "shared_snapshot_id": shared_snapshot_id,
        }

    def read(self, target: StrategyBacktestProfileTarget) -> dict[str, Any] | None:
        expected_request = target.request().to_canonical_json()
        runs = self._db.scalars(
            select(BacktestRunV2)
            .where(
                BacktestRunV2.experiment_id == STRATEGY_PROFILE_EXPERIMENT_ID,
                BacktestRunV2.strategy_id == target.strategy_id,
                BacktestRunV2.strategy_version == target.strategy_version,
                BacktestRunV2.date_from == target.date_from,
                BacktestRunV2.date_to == target.date_to,
                BacktestRunV2.execution_policy_fingerprint
                == target.execution_policy.fingerprint(),
                BacktestRunV2.status == "completed",
            )
            .order_by(BacktestRunV2.finished_at.desc(), BacktestRunV2.created_at.desc())
        ).all()
        run = next(
            (
                item
                for item in runs
                if _canonical_json_text(str(item.request_json)) == expected_request
                and self._snapshot_matches_input_schema(str(item.snapshot_id))
            ),
            None,
        )
        if run is None:
            return None
        return self._profile_from_run(run, target)

    def read_rolled(
        self, target: StrategyBacktestProfileTarget
    ) -> dict[str, Any] | None:
        """读取当前版本最近的半年画像，窗口随交易日漂移时向后滚动复用。

        优先精确匹配当前目标窗口；窗口已漂移（每来一个新交易日整体平移一天）时，
        回退到**同一 strategy_version** 下最新完成的画像——版本仍是硬约束，
        不跨版本混合，满足「不展示跨版本混合收益」。画像附 ``target_date_from/to``
        与 ``window_rolled``，供前端显式标注真实信号窗口。
        """
        exact = self.read(target)
        if exact is not None:
            return exact
        runs = self._db.scalars(
            select(BacktestRunV2)
            .where(
                BacktestRunV2.experiment_id == STRATEGY_PROFILE_EXPERIMENT_ID,
                BacktestRunV2.strategy_id == target.strategy_id,
                BacktestRunV2.strategy_version == target.strategy_version,
                BacktestRunV2.execution_policy_fingerprint
                == target.execution_policy.fingerprint(),
                BacktestRunV2.status == "completed",
                BacktestRunV2.date_to <= target.date_to,
            )
            .order_by(
                BacktestRunV2.date_to.desc(),
                BacktestRunV2.finished_at.desc(),
                BacktestRunV2.created_at.desc(),
            )
        ).all()
        for run in runs:
            if not self._snapshot_matches_input_schema(str(run.snapshot_id)):
                continue
            profile = self._profile_from_run(run, target, run_window=True)
            if profile is None:
                continue
            profile["window_rolled"] = True
            return profile
        return None

    def _profile_from_run(
        self,
        run: BacktestRunV2,
        target: StrategyBacktestProfileTarget,
        *,
        run_window: bool = False,
    ) -> dict[str, Any] | None:
        """由一条 completed run 重建画像；canonical request 与期望不符时返回 None。

        run_window=True（rolled 复用）时，按 run 实际落库的 date_from/date_to 重建期望
        request 做校验——版本/参数/成交政策仍锚定当前 target，仅窗口取 run 自身的。
        画像窗口始终以 run 实际窗口为准，并附 target 窗口供前端标注漂移。
        """
        if run_window:
            expected = StrategyBacktestProfileTarget(
                strategy_id=target.strategy_id,
                strategy_version=target.strategy_version,
                date_from=str(run.date_from),
                date_to=str(run.date_to),
                parameter_set=target.parameter_set,
                execution_policy=target.execution_policy,
                data_requirement_fingerprint=target.data_requirement_fingerprint,
            )
            expected_request = expected.request().to_canonical_json()
        else:
            expected_request = target.request().to_canonical_json()
        if _canonical_json_text(str(run.request_json)) != expected_request:
            return None
        trades = tuple(
            _trade_from_json(str(row.trade_json))
            for row in self._db.scalars(
                select(BacktestTrade)
                .where(BacktestTrade.backtest_id == run.backtest_id)
                .order_by(
                    BacktestTrade.signal_date,
                    BacktestTrade.code,
                    BacktestTrade.trade_id,
                )
            ).all()
        )
        metrics = compute_metrics(trades)
        portfolio = _portfolio_metrics(trades)
        date_from = str(run.date_from)
        date_to = str(run.date_to)
        window_metrics = {
            str(window_days): _window_metrics(
                trades,
                anchor_date=date_to,
                window_days=window_days,
            )
            for window_days in STRATEGY_PROFILE_WINDOW_OPTIONS
        }
        return {
            "schema_version": STRATEGY_PROFILE_SCHEMA_VERSION,
            "source": "canonical_backtest",
            "profile_fingerprint": canonical_fingerprint(
                {
                    "target_fingerprint": target.fingerprint,
                    "schema_version": STRATEGY_PROFILE_SCHEMA_VERSION,
                    "portfolio_model": STRATEGY_PROFILE_PORTFOLIO_MODEL,
                }
            ),
            "backtest_id": str(run.backtest_id),
            "strategy_id": target.strategy_id,
            "strategy_version": target.strategy_version,
            "parameter_fingerprint": canonical_fingerprint(dict(target.parameter_set)),
            "execution_policy_fingerprint": target.execution_policy.fingerprint(),
            "portfolio_model": STRATEGY_PROFILE_PORTFOLIO_MODEL,
            "date_from": date_from,
            "date_to": date_to,
            "target_date_from": target.date_from,
            "target_date_to": target.date_to,
            "window_rolled": False,
            "generated_at": run.finished_at.isoformat() if run.finished_at else None,
            "sample_count": metrics.sample_count,
            "executed_trade_count": metrics.executed_trade_count,
            "win_count": metrics.win_count,
            "loss_count": metrics.loss_count,
            "win_rate_pct": round(metrics.win_rate_pct, 4),
            "average_net_return_pct": round(metrics.average_net_return_pct, 4),
            "profit_loss_ratio": (
                round(metrics.profit_loss_ratio, 4)
                if metrics.profit_loss_ratio is not None
                else None
            ),
            "total_return_pct": portfolio["total_return_pct"],
            "max_drawdown_pct": portfolio["max_drawdown_pct"],
            "confidence": metrics.confidence.value,
            "window_metrics": window_metrics,
            "monthly_returns": _monthly_returns(
                trades,
                date_from=date_from,
                date_to=date_to,
            ),
        }

    def _latest_fully_settleable_signal_date(
        self,
        policy: ExecutionPolicy,
        *,
        as_of: str | None,
    ) -> str:
        statement = select(DailyKbar.trade_date).distinct()
        if as_of is not None:
            statement = statement.where(DailyKbar.trade_date <= as_of)
        trade_dates = tuple(
            str(value)
            for value in self._db.scalars(
                statement.order_by(DailyKbar.trade_date.desc()).limit(
                    policy.entry_lookahead_days + policy.horizon_days + 1
                )
            ).all()
        )
        required_future_days = policy.entry_lookahead_days + policy.horizon_days
        if len(trade_dates) <= required_future_days:
            raise ValueError("not enough local trade dates for a settled strategy profile")
        return trade_dates[required_future_days]

    def _compatible_snapshot_id(
        self,
        target: StrategyBacktestProfileTarget,
    ) -> str | None:
        if not target.data_requirement_fingerprint:
            return None
        current = {item.strategy_id: item for item in self.current_targets()}
        candidates = self._db.scalars(
            select(BacktestRunV2)
            .where(
                BacktestRunV2.experiment_id == STRATEGY_PROFILE_EXPERIMENT_ID,
                BacktestRunV2.date_from == target.date_from,
                BacktestRunV2.date_to == target.date_to,
                BacktestRunV2.execution_policy_fingerprint
                == target.execution_policy.fingerprint(),
            )
            .order_by(BacktestRunV2.finished_at.desc(), BacktestRunV2.created_at.desc())
        ).all()
        for run in candidates:
            source = current.get(str(run.strategy_id))
            if source is None or source.strategy_version != str(run.strategy_version):
                continue
            if (
                source.data_requirement_fingerprint
                != target.data_requirement_fingerprint
            ):
                continue
            if not self._snapshot_matches_input_schema(str(run.snapshot_id)):
                continue
            return str(run.snapshot_id)
        return None

    def _snapshot_matches_input_schema(self, snapshot_id: str) -> bool:
        """候选快照必须由当前 input schema 版本生成。

        ``research_runner.capture`` 把 ``RESEARCH_INPUT_SCHEMA_VERSION`` 编码进
        partition manifest 的 ``source_version``（local-daily-kbars/vN）。字段面
        升级（v2：辅助表注入 + 相对特征 + 因子管线输入）后，旧 v1 快照的
        signal stock 缺少策略依赖字段，直接复用会让 prefilter 拿不到字段 →
        0 信号。此处校验 manifest 的 source_version，不匹配则拒绝复用。
        """
        try:
            manifest = self._db.scalar(
                select(BacktestDataSnapshot.partition_manifest_json).where(
                    BacktestDataSnapshot.snapshot_id == snapshot_id
                )
            )
        except Exception:
            return False
        if not manifest:
            return False
        try:
            payload = json.loads(str(manifest))
        except (TypeError, json.JSONDecodeError):
            return False
        # manifest 结构：{"schema_version": 2, "partitions": [PartitionManifest...],
        # "artifacts_by_signal_date": {...}}；source_version 在 partition 上。
        partitions = payload.get("partitions") or ()
        return any(
            str(partition.get("source_version") or "")
            == f"local-daily-kbars/v{RESEARCH_INPUT_SCHEMA_VERSION}"
            for partition in partitions
        )


def _canonical_json_text(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _window_metrics(
    trades: tuple[Trade, ...],
    *,
    anchor_date: str,
    window_days: int,
) -> dict[str, Any]:
    """Build a rolling signal-date window without changing the canonical run."""
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    anchor = date.fromisoformat(anchor_date)
    window_start = anchor - timedelta(days=window_days - 1)
    selected = tuple(
        trade
        for trade in trades
        if window_start.isoformat() <= trade.signal_date <= anchor_date
    )
    metrics = compute_metrics(selected)
    portfolio = _portfolio_metrics(selected)
    has_executed = metrics.executed_trade_count > 0
    return {
        "window_days": window_days,
        "date_from": window_start.isoformat(),
        "date_to": anchor_date,
        "sample_count": metrics.executed_trade_count,
        "success_count": metrics.win_count,
        "loss_count": metrics.loss_count,
        "hit_rate": (
            round(metrics.win_rate_pct / 100, 6) if has_executed else None
        ),
        "avg_return": (
            round(metrics.average_net_return_pct, 4) if has_executed else None
        ),
        "profit_loss_ratio": (
            round(metrics.profit_loss_ratio, 4)
            if metrics.profit_loss_ratio is not None
            else None
        ),
        "total_return_pct": (
            portfolio["total_return_pct"] if has_executed else None
        ),
        "max_drawdown_pct": (
            portfolio["max_drawdown_pct"] if has_executed else None
        ),
        "monthly_returns": _monthly_returns(
            selected,
            date_from=window_start.isoformat(),
            date_to=anchor_date,
        ),
    }


def _trade_from_json(raw: str) -> Trade:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("backtest trade payload must be an object")
    return Trade(**payload)


def _monthly_returns(
    trades: tuple[Trade, ...],
    *,
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    returns_by_month: dict[str, list[float]] = defaultdict(list)
    trade_count_by_month: dict[str, int] = defaultdict(int)
    for trade_date, daily_return_pct in _daily_portfolio_returns(trades):
        returns_by_month[trade_date[:7]].append(daily_return_pct)
    for trade in trades:
        if trade.executed and trade.exit_date:
            trade_count_by_month[trade.exit_date[:7]] += 1
    last_exit_date = max(
        (trade.exit_date for trade in trades if trade.executed and trade.exit_date),
        default=date_to,
    )
    result = []
    for month in _month_keys(date_from, max(date_to, last_exit_date)):
        equity = 1.0
        for daily_return_pct in returns_by_month[month]:
            equity *= 1 + daily_return_pct / 100
        result.append(
            {
                "month": month,
                "return_pct": round((equity - 1) * 100, 4),
                "executed_trade_count": trade_count_by_month[month],
            }
        )
    return result


def _portfolio_metrics(trades: tuple[Trade, ...]) -> dict[str, float]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for _trade_date, daily_return_pct in _daily_portfolio_returns(trades):
        equity *= max(0.0, 1 + daily_return_pct / 100)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100 if peak else 0.0
        max_drawdown = max(max_drawdown, drawdown)
    return {
        "total_return_pct": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
    }


def _daily_portfolio_returns(
    trades: tuple[Trade, ...],
) -> tuple[tuple[str, float], ...]:
    trades_by_exit: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        if trade.executed and trade.exit_date and trade.net_return_pct is not None:
            trades_by_exit[trade.exit_date].append(trade)
    result = []
    for exit_date in sorted(trades_by_exit):
        cohort = trades_by_exit[exit_date]
        gross_exposure = sum(max(0.0, trade.position_size) for trade in cohort)
        allocated_return = sum(
            trade.net_return_pct * max(0.0, trade.position_size)
            for trade in cohort
        )
        result.append(
            (exit_date, allocated_return / max(1.0, gross_exposure))
        )
    return tuple(result)


def _month_keys(date_from: str, date_to: str) -> tuple[str, ...]:
    cursor = date.fromisoformat(date_from).replace(day=1)
    end = date.fromisoformat(date_to).replace(day=1)
    result = []
    while cursor <= end:
        result.append(cursor.strftime("%Y-%m"))
        cursor = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
    return tuple(result)


__all__ = [
    "STRATEGY_PROFILE_EXPERIMENT_ID",
    "STRATEGY_PROFILE_PORTFOLIO_MODEL",
    "STRATEGY_PROFILE_SCHEMA_VERSION",
    "STRATEGY_PROFILE_WINDOW_DAYS",
    "StrategyBacktestProfileTarget",
    "StrategyBacktestProfiles",
]
