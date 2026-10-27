"""2026-10-27：安全网补跑的推荐按生成时间判实盘/回放，与正常日更共用 midterm_live_seal.generated_before_entry_open。

- 信号日当晚 / 入场日 09:15 前补跑 → live_forward（evaluation_scope 与证据类都是实盘）；
- 入场日开盘后补跑、或补更早的日子 → historical_replay；
- 正常日更（没有 trade_date_override）不变；矩阵批跑始终是回放；
- 显式历史重建脚本对自己建的 run 仍改回回放口径。
"""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from app.services import midterm_live_seal as seal
from app.services.evidence import EvidenceClass, apply_formal_run_provenance
from app.services.official_sample_scope import evaluation_scope_for_run

CN = seal.CN_TZ
UTC = dt.timezone.utc
SIGNAL = "2026-10-26"  # 周一；入场日 10-27 09:15


def _next_weekday(signal_date: str) -> str:
    d = dt.date.fromisoformat(signal_date) + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


@pytest.fixture(autouse=True)
def _calendar(monkeypatch):
    monkeypatch.setattr(seal, "_local_next_open", _next_weekday)  # 不查库：日历查得到，下一个开市日 = 下一个工作日


def _cn(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CN)


def _utc_naive(ts):
    return ts.astimezone(UTC).replace(tzinfo=None)  # 库内口径


def _scope(created_cn, override=SIGNAL, matrix=False):
    return evaluation_scope_for_run(run_mode="formal", trade_date_override=override, matrix_run=matrix,
                                    generated_at=_utc_naive(created_cn))


def _run(started_cn, scope):
    summary = {"execution": {
        "strategy_fingerprints": [{"strategy_id": "oversold-capital-reversal", "strategy_version": "v1",
                                   "fingerprint": "a" * 64}],
        "execution_policy": {"policy_id": "recommendation-run", "policy_version": "v1", "fingerprint": "b" * 64},
    }}
    return SimpleNamespace(
        run_id="run_safety_net_unit", run_name="单策略 超跌资金反包 2026-10-26", run_mode="formal",
        evaluation_scope=scope, evidence_class=None, actual_generated_at=None, evidence_identity_hash=None,
        strategy_policy_fingerprint=None, execution_policy_fingerprint=None, code_revision=None,
        reconstruction_batch_id=None, provenance_schema_version=None, classification_reason=None,
        trade_date=SIGNAL, started_at=_utc_naive(started_cn), status="completed", data_as_of=SIGNAL,
        source_snapshot_id=f"snapshot_pool_historical_{SIGNAL}", result_summary_json=json.dumps(summary),
    )


def _evidence(started_cn, scope):
    run = _run(started_cn, scope)
    identity = apply_formal_run_provenance(run, code_revision="c" * 40)
    return run.evaluation_scope, (identity.evidence_class if identity else None)


def test_same_evening_safety_net_is_live():
    created = _cn(2026, 10, 26, 21, 29)  # 实测 10-26 安全网补跑时刻
    assert _scope(created) == "live_forward"
    assert _evidence(created, _scope(created)) == ("live_forward", EvidenceClass.LIVE_FORWARD)


def test_next_morning_before_open_safety_net_is_live():
    created = _cn(2026, 10, 27, 8, 30)  # 08:30 安全网
    assert _scope(created) == "live_forward"
    assert _evidence(created, _scope(created))[1] is EvidenceClass.LIVE_FORWARD


def test_after_entry_open_safety_net_is_replay():
    created = _cn(2026, 10, 27, 9, 20)
    assert _scope(created) == "historical_replay"
    # 写入器不接管回放 run：不会被记成实盘
    assert _evidence(created, _scope(created)) == ("historical_replay", None)
    # 次日早上补更早的信号日（入场日早已开盘）→ 回放
    assert _scope(_cn(2026, 10, 27, 8, 30), override="2026-10-22") == "historical_replay"


def test_normal_daily_update_unchanged():
    assert evaluation_scope_for_run(run_mode="formal", trade_date_override=None) == "live_forward"
    assert _evidence(_cn(2026, 10, 26, 18, 0), "live_forward")[1] is EvidenceClass.LIVE_FORWARD
    # 开盘后才跑出的「正常日更」照样不算实盘（与安全网同一条线）
    assert _evidence(_cn(2026, 10, 27, 9, 30), "live_forward")[1] is EvidenceClass.UNVERIFIED


def test_matrix_and_non_formal_unchanged():
    assert _scope(_cn(2026, 10, 26, 21, 0), matrix=True) == "historical_replay"
    assert evaluation_scope_for_run(run_mode="experiment", trade_date_override=SIGNAL,
                                    generated_at=_utc_naive(_cn(2026, 10, 26, 21, 0))) == "experiment"
    assert evaluation_scope_for_run(run_mode="formal", trade_date_override=SIGNAL) == "historical_replay"  # 不知道生成时间：保守


def test_one_judgment_for_short_and_midlong():
    """中长线批次封签与短线 run 用同一条线（入场日 09:15）。"""
    for created, live in ((_cn(2026, 10, 27, 9, 14), True), (_cn(2026, 10, 27, 9, 16), False)):
        batch = {"signal_date": SIGNAL, **seal.stamp(SIGNAL, created)}
        assert seal.is_late(batch) is (not live)
        assert seal.generated_before_entry_open(SIGNAL, created) is live
        assert (_scope(created) == "live_forward") is live


def test_explicit_reconstruction_keeps_replay_scope():
    from scripts import replay_all_days as rad

    run = SimpleNamespace(status="queued", run_mode="formal", evaluation_scope="live_forward", evidence_class=None,
                          reconstruction_batch_id="b", trade_date="2099-01-01", request_json="{}")
    identity = SimpleNamespace(batch_id="b", expected_trade_dates=(), request_template_fingerprint="x")
    with pytest.raises(Exception):
        rad._reserve_reconstruction_run(None, run, identity, "2026-10-26")  # 后续校验会因日期不符失败
    assert run.evaluation_scope == "historical_replay"
