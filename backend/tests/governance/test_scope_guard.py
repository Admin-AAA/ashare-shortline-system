"""Task 0.10 Scope_Guard fail-closed contract tests.

**Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7**

All inputs are constructed/mocked.  These tests never probe a real brokerage
resource and never touch the network.
"""
from __future__ import annotations

import hashlib
import json
import sys

import pytest
from pydantic import ValidationError

from app.governance.envelope import GovernanceError
from app.governance.scope_guard import (
    ALLOWED_RISK_PORTFOLIO_KINDS,
    BROKERAGE_ACCESS_DENIED,
    DEFERRED_INITIATIVE_BLOCKED,
    HISTORICAL_RECONSTRUCTION_INITIATIVE,
    RISK_PORTFOLIO_KIND_DENIED,
    BrokerageResource,
    DeferredInitiative,
    ScopeViolation,
    assert_initiative_allowed,
    assert_no_brokerage_access,
    assert_risk_portfolio_kind,
)


# ── D1/D2/D3 延期范围 initiative 一律拒绝（fail-closed） ──


@pytest.mark.parametrize("initiative", list(DeferredInitiative))
def test_deferred_initiatives_always_raise(initiative: DeferredInitiative):
    with pytest.raises(ScopeViolation) as excinfo:
        assert_initiative_allowed(initiative, context="unit")
    exc = excinfo.value
    assert isinstance(exc, GovernanceError)
    assert exc.code == DEFERRED_INITIATIVE_BLOCKED
    assert exc.event["initiative"] == initiative.value
    assert exc.event["context"] == "unit"
    assert exc.event["reason"]


@pytest.mark.parametrize(
    "declared",
    [
        "D1_FULL_HISTORICAL_BACKFILL",
        "d2_new_alpha",
        "d3_brokerage_live_trading",
        DeferredInitiative.D3_BROKERAGE_LIVE_TRADING.value,
    ],
)
def test_deferred_initiatives_raise_for_string_declarations(declared: str):
    with pytest.raises(ScopeViolation) as excinfo:
        assert_initiative_allowed(declared, context="unit")
    assert excinfo.value.code == DEFERRED_INITIATIVE_BLOCKED


@pytest.mark.parametrize(
    "declared",
    [
        HISTORICAL_RECONSTRUCTION_INITIATIVE,
        "historical_reconstruction",
        "live_forward",
        "some_future_in_scope_initiative",
        None,
        123,
    ],
)
def test_allowed_initiatives_pass(declared):
    # guard 按调用方声明的 initiative 判定，不按日期范围猜测。
    assert_initiative_allowed(declared, context="unit")


# ── 五类 brokerage 资源拒绝（含嵌套 payload 与大小写变体） ──


@pytest.mark.parametrize(
    ("payload", "resource", "key_path"),
    [
        ({"credential": "x"}, BrokerageResource.CREDENTIAL.value, "credential"),
        ({"api_Credentials": "x"}, BrokerageResource.CREDENTIAL.value, "api_Credentials"),
        ({"brokerApiKey": "tok"}, BrokerageResource.CREDENTIAL.value, "brokerApiKey"),
        ({"BrokerAccount": "acc-1"}, BrokerageResource.ACCOUNT.value, "BrokerAccount"),
        ({"outer": {"Positions": {"sh600519": 1}}}, BrokerageResource.POSITION.value, "outer.Positions"),
        ({"BALANCE": 0}, BrokerageResource.BALANCE.value, "BALANCE"),
        ({"order_id": "o-1"}, BrokerageResource.ORDER.value, "order_id"),
        ({"deep": {"nested": {"OrderId": "o-2"}}}, BrokerageResource.ORDER.value, "deep.nested.OrderId"),
        ({"brokerage": {}}, "brokerage", "brokerage"),
        ({"券商账户": "x"}, "brokerage", "券商账户"),
    ],
)
def test_brokerage_resources_denied(payload: dict, resource: str, key_path: str):
    with pytest.raises(ScopeViolation) as excinfo:
        assert_no_brokerage_access(payload, context="unit")
    exc = excinfo.value
    assert exc.code == BROKERAGE_ACCESS_DENIED
    assert exc.event["event"] == "scope_guard.brokerage_access_denied"
    assert exc.event["resource"] == resource
    assert exc.event["matched"] == "key"
    assert exc.event["key_path"] == key_path
    assert exc.event["context"] == "unit"
    assert exc.event["reason"]


def test_identifier_shaped_values_denied():
    with pytest.raises(ScopeViolation) as excinfo:
        assert_no_brokerage_access({"provider": "brokerage"}, context="unit")
    assert excinfo.value.event["matched"] == "value"
    with pytest.raises(ScopeViolation) as excinfo:
        assert_no_brokerage_access({"mode": "ORDER"}, context="unit")
    assert excinfo.value.event["resource"] == BrokerageResource.ORDER.value


def test_cli_tokens_scanned():
    assert_no_brokerage_access(["--start", "2026-05-22", "--skip-existing"], context="cli")
    with pytest.raises(ScopeViolation) as excinfo:
        assert_no_brokerage_access(["--broker-token", "abc"], context="cli")
    assert excinfo.value.event["matched"] == "token"
    with pytest.raises(ScopeViolation):
        assert_no_brokerage_access("brokerage", context="cli")


# ── 拒绝事件绝不携带原敏感值，只有 identity/hash/reason ──


def test_denial_event_never_contains_raw_secret_value():
    secret = "s3cr3t-broker-token-998877"
    with pytest.raises(ScopeViolation) as excinfo:
        assert_no_brokerage_access({"brokerage_token": secret}, context="unit")
    exc = excinfo.value
    blob = json.dumps(exc.event, ensure_ascii=False)
    assert secret not in blob
    assert secret not in str(exc)
    assert exc.event["value_sha256_12"] == hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
    assert exc.event["key_path"] == "brokerage_token"
    assert exc.event["reason"]


# ── 既有系统词汇（行情数据/模拟仓位/排序）不受影响 ──


@pytest.mark.parametrize(
    "payload",
    [
        {"skills": [{"skill_id": "alpha", "weight": 1.0, "sort_order": 0}]},
        {"execution_policy": {"position_size": 0.5}},
        {"position_range": "5-15%"},
        {"target_tables": ["broker_recommend", "daily_kbar"]},
        {"risk_template": "balanced"},
        {"password": "whatever"},
        {"dataset": "top_buy_broker"},
        {"trade_date_override": "2026-07-01", "run_mode": "formal", "data_source": "local_pool"},
        # 散文值（自由文本）绝不触发 guard：
        {"note": "通过券商下单的散文消息"},
        {"message": "position out of range error"},
        [{"level": "error", "message": "券商金股 position 拉取超时"}],
    ],
)
def test_benign_system_vocabulary_passes(payload):
    assert_no_brokerage_access(payload, context="unit")


# ── Risk_System 组合边界白名单（供 S6 复用） ──


def test_allowed_risk_portfolio_kinds_constant():
    assert ALLOWED_RISK_PORTFOLIO_KINDS == frozenset(
        {"recommendation", "simulation", "paper", "shadow"}
    )


@pytest.mark.parametrize("kind", sorted(ALLOWED_RISK_PORTFOLIO_KINDS))
def test_risk_portfolio_kind_whitelist_passes(kind: str):
    assert_risk_portfolio_kind(kind, context="unit")


@pytest.mark.parametrize("kind", ["live", "LIVE", "brokerage", "production", "real", "", None])
def test_risk_portfolio_kind_denied(kind):
    with pytest.raises(ScopeViolation) as excinfo:
        assert_risk_portfolio_kind(kind, context="unit")
    assert excinfo.value.code == RISK_PORTFOLIO_KIND_DENIED


# ── config loader：Settings 拒绝 brokerage_* 配置段 ──


def test_settings_rejects_brokerage_config_kwargs():
    from app.core.config import Settings

    with pytest.raises(ValidationError, match="brokerage"):
        Settings(_env_file=None, brokerage_api_token="x")


def test_settings_rejects_brokerage_env_key(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("ASHARE_BROKERAGE_TOKEN", "x")
    with pytest.raises(ValidationError, match="brokerage"):
        Settings(_env_file=None)


def test_settings_without_brokerage_section_still_loads():
    from app.core.config import Settings

    Settings(_env_file=None)


# ── 接线点回归 ──


def test_replay_entry_declares_allowed_historical_reconstruction(monkeypatch):
    """scripts/replay_all_days.py 声明 initiative=historical_reconstruction 并过 guard。"""
    from scripts import replay_all_days

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        replay_all_days,
        "assert_initiative_allowed",
        lambda initiative, *, context: calls.append((initiative, context)),
    )

    class _FakeDB:
        def close(self) -> None:
            pass

    monkeypatch.setattr(replay_all_days, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(replay_all_days, "_trade_dates", lambda db, start, end: [])
    monkeypatch.setattr(
        sys, "argv", ["replay_all_days.py", "--start", "2026-07-01", "--end", "2026-07-02"]
    )

    assert replay_all_days.main() == 0
    assert calls == [(HISTORICAL_RECONSTRUCTION_INITIATIVE, "scripts/replay_all_days.py")]


def test_prefect_payload_flows_deny_brokerage_before_dispatch():
    from app.orchestration import flows

    for flow_obj in (
        flows.manual_preset_matrix_flow,
        flows.manual_data_health_remediation_flow,
        flows.manual_strategy_exposure_flow,
    ):
        with pytest.raises(ScopeViolation) as excinfo:
            flow_obj.fn("task_run_scope_guard_probe", {"brokerAccount": "acct-1"})
        assert excinfo.value.code == BROKERAGE_ACCESS_DENIED


def test_prefect_flow_guard_runs_before_service_resolution(monkeypatch):
    """flow 参数 guard 必须先于任何 service/secret 解析执行。"""
    from app.orchestration import flows

    def _sentinel(*args, **kwargs):
        raise ScopeViolation(BROKERAGE_ACCESS_DENIED, "sentinel", event={"event": "probe"})

    monkeypatch.setattr(flows, "assert_no_brokerage_access", _sentinel)
    with pytest.raises(ScopeViolation, match="sentinel"):
        flows.manual_preset_matrix_flow.fn("task_run_scope_guard_probe", {"preset_ids": []})


def _test_client():
    from fastapi.testclient import TestClient

    from app.main import app

    # 不进入 context manager：不触发 startup（DB init），只走中间件与路由。
    return TestClient(app)


def test_http_middleware_denies_brokerage_payload_before_secret_resolution():
    client = _test_client()
    response = client.post(
        "/api/auth/login",
        json={"password": "probe", "brokerAccountId": "acct-1"},
    )
    assert response.status_code == 403
    body = response.json()
    event = body["event"]
    assert event["code"] == BROKERAGE_ACCESS_DENIED
    assert event["resource"] == BrokerageResource.ACCOUNT.value
    assert "acct-1" not in json.dumps(body, ensure_ascii=False)
    assert event["value_sha256_12"] == hashlib.sha256("acct-1".encode("utf-8")).hexdigest()[:12]


def test_http_middleware_passes_normal_payload_and_preserves_body():
    """干净 payload 放行且 receive 通道重装配后端点仍能读取 body。"""
    client = _test_client()
    response = client.post(
        "/api/auth/login",
        json={"password": "scope-guard-definitely-wrong-password"},
    )
    # 认证关闭 → 200；认证开启密码错误 → 401。若 body 丢失则会是 500。
    assert response.status_code in (200, 401)
    assert isinstance(response.json(), dict)


def test_http_middleware_ignores_prose_values():
    """自由文本（含券商/position 字样的散文）不触发 guard。"""
    client = _test_client()
    response = client.post(
        "/api/frontend-logs",
        json=[{"level": "error", "category": "perf", "message": "券商金股 position 拉取超时"}],
    )
    # 401 为既有 auth 行为；关键是绝不因散文内容返回 403/500。
    assert response.status_code in (200, 401)
