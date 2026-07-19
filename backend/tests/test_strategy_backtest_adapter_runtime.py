from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.engine.strategy_backtest_adapter import (
    BacktestAdapterRequest,
    StrategyBacktestRuntime,
    build_backtest_adapter_spec,
)
from app.engine.skill_loader import StrategySkill, load_skill


def _adapter_skill(tmp_path: Path) -> StrategySkill:
    skill_dir = tmp_path / "sample-adapter"
    skill_dir.mkdir()
    (skill_dir / "manifest.json").write_text(
        '{"id":"sample-adapter","name":"示例适配策略","version":"0.1.0",'
        '"mode":"declarative","status":"active","requirements":[],"capabilities":[],"enabled":true}',
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("# 示例适配策略\n\n用于测试回测适配契约。\n", encoding="utf-8")
    (skill_dir / "strategy.yaml").write_text(
        """
id: sample-adapter
name: 示例适配策略
enabled: true
meta:
  version: 0.1.0
  backtest_adapter:
    module: tests.fake_adapter
    default_action: run
    actions:
      run:
        function: run_backtest
      preview:
        function: preview_gate
      remediate:
        function: remediate_gaps
        task_run:
          task_id: manual_test_remediation
          task_name: 手动测试补数
          task_type: data_backfill
          result_extra:
            remediation_type: test_gap
contract:
  type: strategy
  executable: true
""",
        encoding="utf-8",
    )
    return load_skill(skill_dir)


def test_adapter_spec_normalizes_actions_from_strategy_contract(tmp_path):
    skill = _adapter_skill(tmp_path)

    spec = build_backtest_adapter_spec(skill)

    assert spec is not None
    assert spec.strategy_id == "sample-adapter"
    assert spec.strategy_name == "示例适配策略"
    assert spec.module == "tests.fake_adapter"
    assert spec.default_action == "run"
    assert sorted(spec.actions) == ["preview", "remediate", "run"]
    assert spec.resolve_action("").name == "run"
    assert spec.resolve_action("preview").function == "preview_gate"
    assert spec.resolve_action("remediate").task_run["task_id"] == "manual_test_remediation"


def test_runtime_normalizes_adapter_result_and_records_task_run(tmp_path, monkeypatch):
    skill = _adapter_skill(tmp_path)
    skills_dir = tmp_path
    db = MagicMock()
    calls: list[tuple[object, str, str]] = []

    def fake_callable(session, start_date, end_date):
        calls.append((session, start_date, end_date))
        return {"status": "completed", "custom": 1}

    record_calls: list[dict] = []

    monkeypatch.setattr(
        "app.engine.strategy_backtest_adapter._load_adapter_callable",
        lambda module, function: fake_callable,
    )
    monkeypatch.setattr(
        "app.engine.strategy_backtest_adapter.record_task_run",
        lambda *args, **kwargs: record_calls.append({"args": args, "kwargs": kwargs}),
    )

    runtime = StrategyBacktestRuntime(skills_dir=skills_dir)
    result = runtime.run(
        db,
        BacktestAdapterRequest(
            strategy_id=skill.skill_id,
            action="remediate",
            start_date="2026-05-19",
            end_date="2026-05-21",
        ),
    )

    assert result is not None
    assert calls == [(db, "2026-05-19", "2026-05-21")]
    payload = result.payload
    assert payload["strategy_id"] == "sample-adapter"
    assert payload["strategy_name"] == "示例适配策略"
    assert payload["action"] == "remediate"
    assert payload["strategy_window"] == {"start_date": "2026-05-19", "end_date": "2026-05-21"}
    assert payload["adapter"]["capabilities"] == ["preview", "remediate", "run"]
    assert record_calls
    assert record_calls[0]["args"][:3] == (db, "manual_test_remediation", "completed")
    assert record_calls[0]["kwargs"]["payload"]["action"] == "remediate"
    assert record_calls[0]["kwargs"]["result"]["remediation_type"] == "test_gap"


def test_runtime_rejects_unknown_action(tmp_path):
    runtime = StrategyBacktestRuntime(skills_dir=tmp_path)
    _adapter_skill(tmp_path)

    with pytest.raises(ValueError, match="sample-adapter.missing"):
        runtime.run(
            MagicMock(),
            BacktestAdapterRequest(
                strategy_id="sample-adapter",
                action="missing",
                start_date="2026-05-19",
                end_date="2026-05-21",
            ),
        )
