from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill, load_skills
from app.services.cost_service import record_task_run


@dataclass(frozen=True)
class BacktestAdapterRequest:
    strategy_id: str
    action: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class BacktestAdapterAction:
    name: str
    function: str
    task_run: dict[str, Any] | None = None


@dataclass(frozen=True)
class BacktestAdapterSpec:
    strategy_id: str
    strategy_name: str
    module: str
    default_action: str
    actions: dict[str, BacktestAdapterAction] = field(default_factory=dict)

    def resolve_action(self, action: str) -> BacktestAdapterAction:
        action_name = action or self.default_action
        action_spec = self.actions.get(action_name)
        if action_spec is None:
            raise ValueError(f"strategy backtest action not configured: {self.strategy_id}.{action_name}")
        return action_spec


@dataclass(frozen=True)
class BacktestAdapterResult:
    payload: dict[str, Any]
    action: BacktestAdapterAction
    spec: BacktestAdapterSpec
    request: BacktestAdapterRequest


class StrategyBacktestRuntime:
    def __init__(self, *, skills_dir: Path | None = None):
        self.skills_dir = skills_dir or get_settings().skills_dir

    def run(self, db: Session, request: BacktestAdapterRequest) -> BacktestAdapterResult | None:
        if not request.strategy_id:
            return None
        spec = self.load_spec(request.strategy_id)
        if spec is None:
            return None
        action = spec.resolve_action(request.action)
        func = _load_adapter_callable(spec.module, action.function)
        raw_result = func(db, request.start_date, request.end_date)
        payload = _normalize_adapter_result(raw_result, request=request, spec=spec, action=action)
        result = BacktestAdapterResult(payload=payload, action=action, spec=spec, request=request)
        self.record_task_run(db, result)
        return result

    def load_spec(self, strategy_id: str) -> BacktestAdapterSpec | None:
        skill = _load_skill(strategy_id, self.skills_dir)
        if skill is None:
            return None
        return build_backtest_adapter_spec(skill)

    def record_task_run(self, db: Session, result: BacktestAdapterResult) -> None:
        task_run = result.action.task_run
        if not isinstance(task_run, dict):
            return
        request = result.request
        payload = result.payload
        record_task_run(
            db,
            str(task_run.get("task_id") or f"{request.strategy_id}:{result.action.name}"),
            str(payload.get("status") or "unknown"),
            trigger_source=str(task_run.get("trigger_source") or "manual"),
            task_name=str(task_run.get("task_name") or result.action.name),
            task_type=str(task_run.get("task_type") or "strategy_backtest_action"),
            payload={
                "strategy_id": request.strategy_id,
                "action": result.action.name,
                "start_date": request.start_date,
                "end_date": request.end_date,
            },
            result={**payload, **(task_run.get("result_extra") or {})},
        )


def run_strategy_backtest_adapter(
    db: Session,
    *,
    strategy_id: str,
    action: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any] | None:
    """Run a strategy-declared backtest adapter.

    Strategy-specific backtest hooks live behind strategy.yaml metadata so the
    core API does not branch on a concrete strategy id.
    """
    runtime = StrategyBacktestRuntime()
    result = runtime.run(
        db,
        BacktestAdapterRequest(
            strategy_id=strategy_id,
            action=action,
            start_date=start_date,
            end_date=end_date,
        ),
    )
    return result.payload if result is not None else None


def build_backtest_adapter_spec(skill: StrategySkill) -> BacktestAdapterSpec | None:
    adapter = ((skill.contract or {}).get("meta") or {}).get("backtest_adapter")
    if not isinstance(adapter, dict):
        return None
    module_name = str(adapter.get("module") or "").strip()
    if not module_name:
        return None
    actions: dict[str, BacktestAdapterAction] = {}
    raw_actions = adapter.get("actions") or {}
    if isinstance(raw_actions, dict):
        for name, raw_spec in raw_actions.items():
            action_name = str(name or "").strip()
            if not action_name:
                continue
            if isinstance(raw_spec, str):
                raw_spec = {"function": raw_spec}
            if not isinstance(raw_spec, dict):
                continue
            function_name = str(raw_spec.get("function") or "").strip()
            if not function_name:
                continue
            task_run = raw_spec.get("task_run")
            actions[action_name] = BacktestAdapterAction(
                name=action_name,
                function=function_name,
                task_run=task_run if isinstance(task_run, dict) else None,
            )
    if not actions:
        return None
    return BacktestAdapterSpec(
        strategy_id=skill.skill_id,
        strategy_name=skill.name,
        module=module_name,
        default_action=str(adapter.get("default_action") or "run"),
        actions=actions,
    )


def _normalize_adapter_result(
    raw_result: Any,
    *,
    request: BacktestAdapterRequest,
    spec: BacktestAdapterSpec,
    action: BacktestAdapterAction,
) -> dict[str, Any]:
    result = raw_result if isinstance(raw_result, dict) else {"result": raw_result}
    result.setdefault("strategy_id", request.strategy_id)
    result.setdefault("strategy_name", spec.strategy_name)
    result.setdefault("action", action.name)
    result.setdefault(
        "adapter",
        {
            "strategy_id": request.strategy_id,
            "strategy_name": spec.strategy_name,
            "action": action.name,
            "module": spec.module,
            "function": action.function,
            "capabilities": sorted(spec.actions),
            "default_action": spec.default_action,
        },
    )
    result.setdefault(
        "strategy_window",
        {"start_date": request.start_date, "end_date": request.end_date},
    )
    return result


def _load_skill(strategy_id: str, skills_dir: Path) -> StrategySkill | None:
    for skill in load_skills(skills_dir):
        if skill.skill_id == strategy_id:
            return skill
    return None


def _load_adapter_callable(module_name: str, function_name: str) -> Callable[[Session, str, str], Any]:
    module = import_module(module_name)
    func = getattr(module, function_name, None)
    if not callable(func):
        raise ValueError(f"backtest adapter function not found: {module_name}.{function_name}")
    return func
