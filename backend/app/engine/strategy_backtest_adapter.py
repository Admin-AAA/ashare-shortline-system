from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill, load_skills
from app.services.cost_service import record_task_run


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
    if not strategy_id:
        return None
    skill = _load_skill(strategy_id, get_settings().skills_dir)
    if skill is None:
        return None
    adapter = ((skill.contract or {}).get("meta") or {}).get("backtest_adapter")
    if not isinstance(adapter, dict):
        return None
    action_name = action or str(adapter.get("default_action") or "run")
    action_spec = (adapter.get("actions") or {}).get(action_name)
    if isinstance(action_spec, str):
        action_spec = {"function": action_spec}
    if not isinstance(action_spec, dict):
        raise ValueError(f"strategy backtest action not configured: {strategy_id}.{action_name}")
    module_name = str(adapter.get("module") or "").strip()
    function_name = str(action_spec.get("function") or "").strip()
    if not module_name or not function_name:
        return None
    func = _load_adapter_callable(module_name, function_name)
    result = func(db, start_date, end_date)
    if not isinstance(result, dict):
        result = {"result": result}
    result.setdefault("strategy_id", strategy_id)
    result.setdefault("action", action_name)
    result.setdefault("strategy_window", {"start_date": start_date, "end_date": end_date})
    task_run = action_spec.get("task_run")
    if isinstance(task_run, dict):
        record_task_run(
            db,
            str(task_run.get("task_id") or f"{strategy_id}:{action_name}"),
            str(result.get("status") or "unknown"),
            trigger_source=str(task_run.get("trigger_source") or "manual"),
            task_name=str(task_run.get("task_name") or action_name),
            task_type=str(task_run.get("task_type") or "strategy_backtest_action"),
            payload={
                "strategy_id": strategy_id,
                "action": action_name,
                "start_date": start_date,
                "end_date": end_date,
            },
            result={**result, **(task_run.get("result_extra") or {})},
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
