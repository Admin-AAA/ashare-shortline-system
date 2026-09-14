"""Task 2.1 regression tests for canonical ExecutionPolicy and its Registry."""
from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.services import outcome_simulation
from app.api.backtests import ExecutionPolicyRequest
from app.engine.board_rules import limit_pct_for_code, limit_pct_for_stock
from app.engine.execution_policy import (
    DEFAULT_EXECUTION_POLICY,
    DEFAULT_EXECUTION_POLICY_REGISTRY,
    BoardLimitPolicy,
    ExecutionPolicy,
    ExecutionPolicyArtifactError,
    ExecutionPolicyNotFoundError,
    ExecutionPolicyRegistry,
    ExecutionPolicyValidationError,
    PositionCapComposition,
    PositionSizingPolicy,
    StopLossPolicy,
    TakeProfitPolicy,
    execution_policy_from_config_projection,
    execution_policy_from_payload,
    load_execution_policy_artifact,
    render_execution_policy_markdown,
)
from app.engine.trading_costs import (
    ADDITIONAL_COST_PCT,
    COMMISSION_RATE_PCT,
    STAMP_TAX_RATE_PCT,
)
from app.services.entry_policy import CAUSAL_STRATEGY_ENTRY
from app.services.outcome_simulation import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    ENTRY_LOOKAHEAD_DAYS,
    ENTRY_SLIPPAGE_BPS,
    EXIT_SLIPPAGE_BPS,
    MAX_ENTRY_GAP_PCT,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    STOP_LOSS_GAP_CAP_PCT,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    TRAILING_STOP_MIN_LOCK_PCT,
    TRAILING_STOP_PROFIT_LOCK_RATIO,
    _resolve_position_size,
)

_EXPECTED_V1_FINGERPRINT = "e0ba29df487228c25628297e8d70fbc64928504f980de10abfaf5851c94a60c5"
_EXPECTED_V2_FINGERPRINT = "b79ec9f6e5a4b99a69268d7362865df076d0612508a09abc01b52151d9982810"
# v3（2026-09-15 发布）：关掉 +6% 止盈、trailing 触发从 +2.0% 提前到 +1.5%、
# 止损从 −4.5% 放宽到 −5.5%。三窗 canonical 复算：WR/avg/PF/sum 全部改善、
# 三窗累计收益首次全部为正（详见 docs/execution-policy-v3-2026-09-15.md）。
_EXPECTED_V3_FINGERPRINT = "86553a1fcd4a92d97b0395cd71caae1a11b6090360686b9ee75077db943e6824"
_POLICY_DIR = Path(__file__).resolve().parents[1] / "app" / "policies"

_CAP_FIELD_NAMES = (
    "single_name_cap",
    "strategy_cap",
    "gross_exposure_cap",
    "net_exposure_cap",
    "industry_cap",
    "theme_cap",
)


def _artifact_payload(policy: ExecutionPolicy) -> dict[str, Any]:
    return {
        "artifact_schema_version": 1,
        "policy_fingerprint": policy.fingerprint(),
        "policy": policy.canonical_payload(),
    }


def _write_artifact(path: Path, policy: ExecutionPolicy) -> None:
    path.write_text(
        json.dumps(_artifact_payload(policy), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _reverse_mappings(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _reverse_mappings(item)
            for key, item in reversed(tuple(value.items()))
        }
    if isinstance(value, list):
        return [_reverse_mappings(item) for item in value]
    return value


def test_default_policy_is_complete_immutable_and_canonically_fingerprinted() -> None:
    policy = DEFAULT_EXECUTION_POLICY

    assert DEFAULT_EXECUTION_POLICY_REGISTRY.current is policy
    assert policy.policy_id == "a-share-shortline"
    assert policy.version == "3.0.0"
    assert policy.schema_version == 1
    assert policy.entry.max_entry_trade_days == 2
    assert policy.t_plus_one is True
    assert policy.horizon_days == 5
    assert policy.fees.round_trip_pct == pytest.approx(0.07)
    assert policy.slippage.entry_bps == policy.slippage.exit_bps == 10.0
    assert policy.board_limits == BoardLimitPolicy()
    assert policy.liquidity.require_positive_amount is True
    assert policy.position.cap_composition is PositionCapComposition.MIN_ALL_APPLICABLE
    assert policy.position.single_name_cap == pytest.approx(0.35)
    assert policy.position.strategy_cap == pytest.approx(0.60)
    assert policy.position.gross_exposure_cap == pytest.approx(0.80)
    assert policy.position.net_exposure_cap == pytest.approx(0.80)
    assert policy.position.industry_cap == pytest.approx(0.40)
    assert policy.position.theme_cap == pytest.approx(0.40)
    # v3 出场策略：放宽止损、关闭止盈、trailing 更早触发
    assert policy.stop_loss == StopLossPolicy(enabled=True, default_loss_pct=5.5, maximum_loss_pct=5.7)
    assert policy.take_profit.enabled is False
    assert policy.take_profit.default_gain_pct == 6.0  # 数值保留但不再生效
    assert policy.trailing.activation_gain_pct == 1.5
    assert policy.trailing.profit_lock_ratio == 0.5
    assert policy.gap_risk.stop_gap_loss_cap_pct == 15.0
    assert policy.fingerprint() == _EXPECTED_V3_FINGERPRINT
    assert " " not in policy.to_canonical_json()
    assert "\n" not in policy.to_canonical_json()

    with pytest.raises(FrozenInstanceError):
        policy.horizon_days = 3  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        policy.entry.max_entry_trade_days = 1  # type: ignore[misc]



def test_default_request_resolves_exact_registry_current_v3() -> None:
    request = ExecutionPolicyRequest()

    resolved = request.to_domain()

    assert resolved is DEFAULT_EXECUTION_POLICY
    assert resolved.version == "3.0.0"
    assert resolved.fingerprint() == _EXPECTED_V3_FINGERPRINT
    assert resolved.position.cap_composition is PositionCapComposition.MIN_ALL_APPLICABLE
    assert tuple(getattr(resolved.position, name) for name in _CAP_FIELD_NAMES) == pytest.approx(
        (0.35, 0.60, 0.80, 0.80, 0.40, 0.40)
    )


def test_flat_override_gets_deterministic_derived_identity_and_retains_v3_caps() -> None:
    payload = dict(DEFAULT_EXECUTION_POLICY.config_projection())
    payload["horizon_days"] = 7

    first = execution_policy_from_config_projection(payload)
    second = execution_policy_from_config_projection(dict(reversed(tuple(payload.items()))))
    another = execution_policy_from_config_projection(
        {**payload, "horizon_days": 8}
    )

    assert first == second
    assert first.policy_id == "a-share-shortline-derived"
    assert first.version.startswith("3.0.0-cfg-")
    assert first.version != DEFAULT_EXECUTION_POLICY.version
    assert first.version != another.version
    assert first.fingerprint() != DEFAULT_EXECUTION_POLICY.fingerprint()
    assert first.position.cap_composition is PositionCapComposition.MIN_ALL_APPLICABLE
    assert tuple(getattr(first.position, name) for name in _CAP_FIELD_NAMES) == pytest.approx(
        (0.35, 0.60, 0.80, 0.80, 0.40, 0.40)
    )


def test_flat_projection_adapter_rejects_missing_or_unknown_fields() -> None:
    payload = dict(DEFAULT_EXECUTION_POLICY.config_projection())
    payload.pop("horizon_days")
    with pytest.raises(ExecutionPolicyValidationError, match="schema mismatch"):
        execution_policy_from_config_projection(payload)

    payload = dict(DEFAULT_EXECUTION_POLICY.config_projection())
    payload["unregistered_override"] = True
    with pytest.raises(ExecutionPolicyValidationError, match="schema mismatch"):
        execution_policy_from_config_projection(payload)

def test_canonical_fingerprint_is_invariant_to_every_mapping_order() -> None:
    policy = DEFAULT_EXECUTION_POLICY
    reordered = _reverse_mappings(policy.canonical_payload())

    loaded = execution_policy_from_payload(reordered)

    assert loaded == policy
    assert loaded.to_canonical_json() == policy.to_canonical_json()
    assert loaded.fingerprint() == _EXPECTED_V3_FINGERPRINT


def test_outcome_settlement_honours_disabled_exit_switches(monkeypatch) -> None:
    """结算路径必须尊重 enabled 开关，否则 v3 的"关止盈"会静默失效。

    2026-09-15：发布 execution_policy v3 时发现 outcome_simulation 只读数值常数、
    并把 dynamic_trailing 写死 True → "关掉止盈/止损/trailing"在实盘结算里无效。
    本测试锁住修复：投影必须带三个 enabled 标志，且结算读取它们。
    """
    projection = DEFAULT_EXECUTION_POLICY.runtime_constant_projection()
    assert projection["TAKE_PROFIT_ENABLED"] is False
    assert projection["STOP_LOSS_ENABLED"] is True
    assert projection["TRAILING_ENABLED"] is True

    snap = {"stop_loss": 0.0, "take_profit": 0.0}
    # 默认（v3，止盈关闭）：止盈价返回 None（NULL 语义"无目标价"；
    # 不能用超大数——outcome 表列是 MySQL FLOAT，1e308 会 DataError 整笔结算失败）
    assert outcome_simulation._resolve_take_profit_price(10.0, snap) is None
    # 止损开启：仍按 −5.5% 解析出停止价
    assert outcome_simulation._resolve_stop_loss_price(10.0, snap) == pytest.approx(9.45, abs=0.01)

    # 反向：显式打开止盈 / 关掉止损时，两个解析器必须跟着变
    monkeypatch.setattr(outcome_simulation, "TAKE_PROFIT_ENABLED", True)
    monkeypatch.setattr(outcome_simulation, "STOP_LOSS_ENABLED", False)
    assert outcome_simulation._resolve_take_profit_price(10.0, snap) == pytest.approx(10.6, abs=0.01)
    assert outcome_simulation._resolve_stop_loss_price(10.0, snap) == 0.0


def test_flat_research_adapter_projects_into_canonical_fields_only() -> None:
    policy = ExecutionPolicy(
        entry_lookahead_days=1,
        horizon_days=3,
        commission_rate=0.0,
        stamp_tax_rate=0.0,
        entry_slippage_bps=0.0,
        exit_slippage_bps=0.0,
        position_size=0.25,
        stop_loss_pct=None,
        take_profit_pct=12.0,
        max_entry_gap_pct=None,
    )

    assert policy.entry.max_entry_trade_days == 1
    assert policy.stop_loss.enabled is False
    assert policy.take_profit.default_gain_pct == 12.0
    assert policy.position.default_fraction == 0.25
    assert policy.gap_risk.max_entry_gap_up_pct is None
    assert set(policy.canonical_payload()) == {
        "schema_version",
        "policy_id",
        "version",
        "entry",
        "t_plus_one",
        "horizon_days",
        "fees",
        "slippage",
        "board_limits",
        "liquidity",
        "position",
        "stop_loss",
        "take_profit",
        "trailing",
        "gap_risk",
        "intraday_exit_priority",
    }


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ExecutionPolicy(schema_version=2), "schema_version"),
        (lambda: ExecutionPolicy(horizon_days=0), "horizon_days"),
        (lambda: StopLossPolicy(default_loss_pct=5.0, maximum_loss_pct=4.0), "maximum_loss_pct"),
    ],
)
def test_policy_validation_rejects_schema_and_risk_boundaries(factory: Any, message: str) -> None:
    with pytest.raises(ExecutionPolicyValidationError, match=message):
        factory()


def test_repository_artifact_and_manifest_load_by_version_and_fingerprint(tmp_path: Path) -> None:
    repository_policy = load_execution_policy_artifact(_POLICY_DIR / "execution_policy.v1.json")
    # current 已升至 v2：v1 artifact 与裸构造同身份，不再等于 DEFAULT_EXECUTION_POLICY。
    assert repository_policy == DEFAULT_EXECUTION_POLICY_REGISTRY.get("a-share-shortline", "1.0.0")
    assert repository_policy == ExecutionPolicy()
    assert repository_policy.fingerprint() == _EXPECTED_V1_FINGERPRINT

    old_policy = ExecutionPolicy(policy_id="fixture-policy", version="1.0.0")
    current_policy = ExecutionPolicy(policy_id="fixture-policy", version="1.1.0", horizon_days=6)
    _write_artifact(tmp_path / "fixture.v1.json", old_policy)
    _write_artifact(tmp_path / "fixture.v1_1.json", current_policy)
    manifest = {
        "registry_schema_version": 1,
        "current": {
            "policy_id": current_policy.policy_id,
            "version": current_policy.version,
            "fingerprint": current_policy.fingerprint(),
        },
        "artifacts": [
            {
                "file": "fixture.v1.json",
                "policy_id": old_policy.policy_id,
                "version": old_policy.version,
                "fingerprint": old_policy.fingerprint(),
            },
            {
                "file": "fixture.v1_1.json",
                "policy_id": current_policy.policy_id,
                "version": current_policy.version,
                "fingerprint": current_policy.fingerprint(),
            },
        ],
    }
    manifest_path = tmp_path / "registry.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = ExecutionPolicyRegistry.from_manifest(manifest_path)

    assert registry.current == current_policy
    assert registry.get("fixture-policy", "1.0.0") == old_policy
    assert registry.get_by_fingerprint(current_policy.fingerprint()) == current_policy
    assert registry.identities() == (("fixture-policy", "1.0.0"), ("fixture-policy", "1.1.0"))
    with pytest.raises(ExecutionPolicyNotFoundError):
        registry.get("fixture-policy", "9.9.9")


def test_loader_fails_closed_on_tampering_unknown_fields_and_path_escape(tmp_path: Path) -> None:
    artifact = _artifact_payload(DEFAULT_EXECUTION_POLICY)
    artifact["policy"]["unknown_rule"] = True
    bad_artifact = tmp_path / "bad.json"
    bad_artifact.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ExecutionPolicyArtifactError, match="unknown"):
        load_execution_policy_artifact(bad_artifact)

    duplicate = ExecutionPolicy(policy_id="duplicate", version="1.0.0")
    with pytest.raises(ExecutionPolicyArtifactError, match="duplicate execution policy identity"):
        ExecutionPolicyRegistry.build(
            [duplicate, duplicate],
            current_policy_id="duplicate",
            current_version="1.0.0",
        )

    manifest = {
        "registry_schema_version": 1,
        "current": {
            "policy_id": DEFAULT_EXECUTION_POLICY.policy_id,
            "version": DEFAULT_EXECUTION_POLICY.version,
            "fingerprint": DEFAULT_EXECUTION_POLICY.fingerprint(),
        },
        "artifacts": [
            {
                "file": "../execution_policy.v1.json",
                "policy_id": DEFAULT_EXECUTION_POLICY.policy_id,
                "version": DEFAULT_EXECUTION_POLICY.version,
                "fingerprint": DEFAULT_EXECUTION_POLICY.fingerprint(),
            }
        ],
    }
    manifest_path = tmp_path / "registry.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExecutionPolicyArtifactError, match="inside the manifest directory"):
        ExecutionPolicyRegistry.from_manifest(manifest_path)


def test_registry_direct_construction_rejects_inconsistent_indexes() -> None:
    policy = ExecutionPolicy()
    identity = (policy.policy_id, policy.version)

    with pytest.raises(ExecutionPolicyArtifactError, match="identity index"):
        ExecutionPolicyRegistry(
            {("wrong-policy", policy.version): policy},
            {policy.fingerprint(): policy},
            ("wrong-policy", policy.version),
        )

    with pytest.raises(ExecutionPolicyArtifactError, match="fingerprint index"):
        ExecutionPolicyRegistry(
            {identity: policy},
            {"0" * 64: policy},
            identity,
        )


def test_runtime_config_and_documentation_projections_match_policy_snapshot() -> None:
    policy = DEFAULT_EXECUTION_POLICY
    expected_runtime = {
        "ENTRY_LOOKAHEAD_DAYS": 2,
        "ENTRY_SLIPPAGE_BPS": 10.0,
        "EXIT_SLIPPAGE_BPS": 10.0,
        "STOP_LOSS_ENABLED": True,
        "DEFAULT_STOP_LOSS_PCT": -5.5,
        "MAX_STOP_LOSS_PCT": -5.7,
        "TAKE_PROFIT_ENABLED": False,
        "DEFAULT_TAKE_PROFIT_PCT": 6.0,
        "MAX_TAKE_PROFIT_PCT": 15.0,
        "STOP_LOSS_GAP_CAP_PCT": -15.0,
        "MAX_ENTRY_GAP_PCT": 10.0,
        "TRAILING_ENABLED": True,
        "TRAILING_STOP_ACTIVATE_PCT": 1.5,
        "TRAILING_STOP_CALLBACK_PCT": 0.8,
        "TRAILING_STOP_MIN_LOCK_PCT": 0.3,
        "TRAILING_STOP_PROFIT_LOCK_RATIO": 0.5,
    }
    observed_runtime = {
        "ENTRY_LOOKAHEAD_DAYS": ENTRY_LOOKAHEAD_DAYS,
        "ENTRY_SLIPPAGE_BPS": ENTRY_SLIPPAGE_BPS,
        "EXIT_SLIPPAGE_BPS": EXIT_SLIPPAGE_BPS,
        "STOP_LOSS_ENABLED": outcome_simulation.STOP_LOSS_ENABLED,
        "DEFAULT_STOP_LOSS_PCT": DEFAULT_STOP_LOSS_PCT,
        "MAX_STOP_LOSS_PCT": MAX_STOP_LOSS_PCT,
        "TAKE_PROFIT_ENABLED": outcome_simulation.TAKE_PROFIT_ENABLED,
        "DEFAULT_TAKE_PROFIT_PCT": DEFAULT_TAKE_PROFIT_PCT,
        "MAX_TAKE_PROFIT_PCT": MAX_TAKE_PROFIT_PCT,
        "STOP_LOSS_GAP_CAP_PCT": STOP_LOSS_GAP_CAP_PCT,
        "MAX_ENTRY_GAP_PCT": MAX_ENTRY_GAP_PCT,
        "TRAILING_ENABLED": outcome_simulation.TRAILING_ENABLED,
        "TRAILING_STOP_ACTIVATE_PCT": TRAILING_STOP_ACTIVATE_PCT,
        "TRAILING_STOP_CALLBACK_PCT": TRAILING_STOP_CALLBACK_PCT,
        "TRAILING_STOP_MIN_LOCK_PCT": TRAILING_STOP_MIN_LOCK_PCT,
        "TRAILING_STOP_PROFIT_LOCK_RATIO": TRAILING_STOP_PROFIT_LOCK_RATIO,
    }
    assert dict(policy.runtime_constant_projection()) == expected_runtime
    assert observed_runtime == expected_runtime
    assert ExecutionPolicyRequest().model_dump() == dict(policy.config_projection())
    assert CAUSAL_STRATEGY_ENTRY.policy_id == policy.entry.policy_id
    assert CAUSAL_STRATEGY_ENTRY.max_entry_trade_days == policy.entry.max_entry_trade_days
    assert COMMISSION_RATE_PCT == pytest.approx(0.02)
    assert STAMP_TAX_RATE_PCT == pytest.approx(0.05)
    assert ADDITIONAL_COST_PCT == pytest.approx(0.07)
    assert limit_pct_for_code("600000") == policy.board_limits.main_board_pct
    assert limit_pct_for_code("300001") == policy.board_limits.growth_board_pct
    assert limit_pct_for_code("688001") == policy.board_limits.star_market_pct
    assert limit_pct_for_code("830001") == policy.board_limits.beijing_exchange_pct
    assert limit_pct_for_stock({"code": "600000", "name": "ST 测试"}) == policy.board_limits.st_pct
    assert _resolve_position_size({"final_score": 90}) == policy.position.single_name_cap
    # current=v2（min_all_applicable）：弱势 regime 的 market cap 不再被 legacy bypass 绕过。
    assert _resolve_position_size({
        "final_score": 90,
        "market_discipline": {"market_regime": "弱势·冰点", "max_position_pct": 20},
    }) == 0.2
    assert _resolve_position_size({
        "final_score": 90,
        "market_discipline": {"market_regime": "震荡", "max_position_pct": 20},
    }) == 0.2

    assert render_execution_policy_markdown(policy) == (
        "# Execution Policy `a-share-shortline@3.0.0`\n"
        "\n"
        "- Schema: `1`; fingerprint: `86553a1fcd4a92d97b0395cd71caae1a11b6090360686b9ee75077db943e6824`\n"
        "- Entry: `causal_strategy_entry_v1`, window 2 trading days, source `intraday_snapshot_or_postclose_target`.\n"
        "- Settlement/horizon: T+1 `true`, 5 holding bars.\n"
        "- Costs: commission 0.0001 per side, exit stamp tax 0.0005; slippage 10/10 bps.\n"
        "- Board/liquidity: 10/20/30% limits; one-word limit-up blocks entry and one-word limit-down defers exit.\n"
        "- Position: default 1, high-score cap 0.5 from score 85, composition `min_all_applicable`.\n"
        "- Stops: loss 5.5% (maximum 5.7%), profit disabled.\n"
        "- Trailing: `profit_floor`, activation 1.5%, callback 0.8%, minimum lock 0.3%, ratio 0.5.\n"
        "- Gap risk: max entry gap 10%, stop gap cap 15%, same-bar priority `stop_loss`.\n"
    )
