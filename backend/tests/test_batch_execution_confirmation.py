"""批量次日执行确认（batch_execution_confirmation）单元测试。

验证 Spec 第 10.1 条不变量：
- 无 outcome / pending / provisional 进入资格集合；success/failed/missed 不进入
- 过滤后为空时行情 provider 完全不被调用
- 多笔未结算只发生一次公共行情/实时 K 批量加载
- 同股多笔推荐共享股票特征但按各自策略独立结论
- 实时 K 替换同日历史 K 不重复追加
- 陈旧行情永不返回 buy
- 单票实时行情缺失只产生该票错误
- refresh=false 命中缓存，refresh=true 重新计算

mock 策略：monkeypatch get_market_universe / _fetch_rtk_quotes / _build_signal_for_strategy，
隔离网络（Tushare）与策略加载，聚焦资格/合成/缓存/错误隔离不变量。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    DailyKbar,
    Recommendation,
    RecommendationExecutionState,
    RecommendationOutcome,
    RecommendationRun,
)
from app.services.evidence import identity_from_persisted_run
from app.services import batch_execution_confirmation as svc


# ── fixtures ──────────────────────────────────────────────────────────────

def test_batch_item_keeps_existing_holding_decision_for_history_page():
    holding_decision = {
        "code": "exit",
        "label": "退出持仓",
        "reason": "当前价格已经跌破原推荐止损位",
    }

    item = svc._to_batch_item({
        "recommendation_id": "rec_holding_decision",
        "code": "600775",
        "status": "ok",
        "original": {"trade_date": "2026-07-29"},
        "entry_decision": {"code": "cancel", "label": "取消买入", "reason": "止损已破"},
        "holding_decision": holding_decision,
    })

    assert item["holding_decision"] == holding_decision

def _make_run(db: Session, run_id: str, trade_date: str = "2026-07-28") -> str:
    run = RecommendationRun(
        run_id=run_id,
        run_name="批量确认测试",
        run_mode="formal",
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id=f"snapshot-{run_id}",
        evidence_class="live_forward",
        actual_generated_at=datetime.fromisoformat(f"{trade_date}T02:00:00"),
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        provenance_schema_version=1,
        status="completed",
    )
    run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
    db.add(run)
    db.flush()
    return run_id


def _make_rec(
    db: Session,
    rec_id: str,
    run_id: str,
    code: str,
    trade_date: str = "2026-07-28",
    name: str = "测试股",
    bucket: str = "daily-stock-analysis-skill",
    recommend_price: float = 10.0,
    stop_loss: float = 9.5,
) -> str:
    db.add(Recommendation(
        recommendation_id=rec_id,
        run_id=run_id,
        trade_date=trade_date,
        code=code,
        name=name,
        recommend_price=recommend_price,
        bucket=bucket,
        final_score=80,
        # 默认构造盘后推荐（UTC 08:00 = 北京 16:00），需要盘中语义的测试显式覆盖。
        recommended_at=datetime.fromisoformat(f"{trade_date}T08:00:00"),
        feature_snapshot_json=json.dumps({"stop_loss": stop_loss}),
    ))
    return rec_id


def _add_outcome(db: Session, rec_id: str, label: str) -> None:
    db.add(RecommendationOutcome(
        recommendation_id=rec_id, outcome_label=label, outcome_label_legacy=label,
    ))


def _add_kbar(db: Session, code: str, trade_date: str, close: float = 10.0, **kw: Any) -> None:
    bar = db.scalar(select(DailyKbar).where(
        DailyKbar.code == code,
        DailyKbar.trade_date == trade_date,
    ))
    if bar is None:
        bar = DailyKbar(code=code, trade_date=trade_date)
        db.add(bar)
    bar.open = kw.get("open", close)
    bar.high = kw.get("high", close)
    bar.low = kw.get("low", close)
    bar.close = close
    bar.volume = kw.get("volume", 100)
    bar.amount = kw.get("amount", 1000)
    bar.pct_chg = kw.get("pct_chg", 0.0)
    bar.turnover_rate = kw.get("turnover_rate", 1.0)


def _fake_stock(code: str, bars: list[dict[str, Any]] | None = None, price: float = 10.5) -> dict[str, Any]:
    return {
        "code": code, "name": "测试股", "exchange": "SH", "industry": "电子",
        "price": price, "pct": 1.5, "amount_yi": 5.0, "turnover_rate": 2.0,
        "free_mv_yi": 100.0, "total_mv_yi": 200.0, "pe_ttm": 30.0,
        "main_net_inflow_yi": 0.0, "is_st": False,
        "quote_source": "rt_k", "kline_source": "local_pool",
        "quote_time": "2026-07-29T10:06:08",
        "_bars": bars or [{"trade_date": "2026-07-27", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "volume": 100, "amount": 1000, "pct_chg": 0.0}],
    }


def _fake_snapshot(trade_date: str = "2026-07-29") -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "main_industries": ["电子"],
        "up_count": 2000, "down_count": 2000, "limit_up_count": 50, "limit_down_count": 5,
        "breadth": {"regime": "震荡", "quote_source": "rt_k"},
    }


def _rt(code: str, close: float = 10.5, high: float | None = None, low: float | None = None, vol: float = 200) -> dict[str, Any]:
    return {
        "open": 10.2,
        "high": high if high is not None else max(10.2, close),
        "low": low if low is not None else min(10.2, close),
        "close": close, "pre_close": 10.0, "vol": vol, "amount": 2000, "name": "测试股",
        "trade_date": "2026-07-29", "quote_time": "2026-07-29T10:06:08",
    }


_FAKE_SKILL = SimpleNamespace(skill_id="test-strategy", name="测试策略")
_FAKE_SIGNAL = {"phase": "确认", "score": 76, "setup_match": "分歧回封", "hard_veto": False, "risk_flags": []}


def _setup_decision_mocks(
    monkeypatch,
    *,
    expected_date: str = "2026-07-29",
    data_as_of: str = "2026-07-29",
    stocks: list[dict[str, Any]] | None = None,
    rt_quotes: dict[str, dict[str, Any]] | None = None,
    signal: dict[str, Any] | None = None,
    prefilter_ok: bool = True,
    entry_window_closed: bool = False,
) -> dict[str, int]:
    """统一 mock 决策路径依赖（行情/策略/特征），返回调用计数器供断言。"""
    calls = {"universe": 0, "rtk": 0}

    def _universe():
        calls["universe"] += 1
        return (stocks or [], _fake_snapshot(data_as_of))

    def _rtk(codes):
        calls["rtk"] += 1
        return rt_quotes or {}

    monkeypatch.setattr(svc, "latest_allowed_quote_date", lambda: expected_date)
    monkeypatch.setattr("app.services.market_data.get_market_universe", _universe)
    monkeypatch.setattr("app.services.realtime_pnl_service._fetch_rtk_quotes", _rtk)
    monkeypatch.setattr(svc, "load_skills", lambda *_a, **_kw: [_FAKE_SKILL])
    monkeypatch.setattr(
        svc, "_build_signal_for_strategy",
        lambda skill, current, snapshot: (dict(signal or _FAKE_SIGNAL), prefilter_ok, ""),
    )
    monkeypatch.setattr("app.services.execution_confirmation._adjust_stop_loss_for_ex_right", lambda db, code, sd, da, sl: sl)
    monkeypatch.setattr("app.services.execution_confirmation.latest_allowed_quote_date", lambda: expected_date)
    monkeypatch.setattr("app.services.execution_confirmation._entry_window_closed", lambda d, n: entry_window_closed)
    monkeypatch.setattr(svc, "_entry_window_closed", lambda d, n: entry_window_closed)
    monkeypatch.setattr("app.services.feature_builder.build_features", lambda stocks, snapshot, **_kw: stocks)
    monkeypatch.setattr("app.services.factors.registry.bootstrap_default_factor_registry", lambda: None)
    return calls


# ── 资格与过滤 ────────────────────────────────────────────────────────────

def test_pending_provisional_eligible_settled_excluded(db):
    """无 outcome / pending / provisional 进入；success/failed/missed 不进入。"""
    run_id = _make_run(db, "test_batch_quals_run")
    pending = _make_rec(db, "test_batch_pending", run_id, "000001")
    provisional = _make_rec(db, "test_batch_prov", run_id, "000002")
    success = _make_rec(db, "test_batch_success", run_id, "000003")
    failed = _make_rec(db, "test_batch_failed", run_id, "000004")
    missed = _make_rec(db, "test_batch_missed", run_id, "000005")
    _add_outcome(db, provisional, "provisional")
    _add_outcome(db, success, "success")
    _add_outcome(db, failed, "failed")
    _add_outcome(db, missed, "missed")
    db.flush()

    eligible, skipped = svc._load_eligible_recs(db, [
        pending, provisional, success, failed, missed,
    ])
    eligible_ids = {r.recommendation_id for r, _ in eligible}

    assert pending in eligible_ids
    assert provisional in eligible_ids
    assert success not in eligible_ids
    assert failed not in eligible_ids
    assert missed not in eligible_ids
    assert skipped == 3  # success/failed/missed


def test_historical_replay_and_forged_live_are_not_execution_eligible(db):
    replay_run_id = _make_run(db, "test_batch_replay_run")
    replay_run = db.get(RecommendationRun, replay_run_id)
    replay_run.evidence_class = "historical_reconstruction"
    replay_run.reconstruction_batch_id = "batch-replay"
    replay_run.actual_generated_at = datetime(2026, 8, 1, 2, 0)
    replay_run.evidence_identity_hash = identity_from_persisted_run(replay_run).identity_hash
    replay_rec = _make_rec(db, "test_batch_replay_rec", replay_run_id, "000006")

    forged_run_id = _make_run(db, "test_batch_forged_run")
    forged_run = db.get(RecommendationRun, forged_run_id)
    forged_run.evidence_identity_hash = "0" * 64
    forged_rec = _make_rec(db, "test_batch_forged_rec", forged_run_id, "000007")
    db.flush()

    eligible, skipped = svc._load_eligible_recs(db, [replay_rec, forged_rec])

    assert eligible == []
    assert skipped == 2


def test_empty_after_filter_does_not_call_quote_provider(db, monkeypatch):
    """过滤后无可计算记录时，get_market_universe 与 _fetch_rtk_quotes 都不被调用。"""
    called: dict[str, int] = {"universe": 0, "rtk": 0}

    def _boom_universe():
        called["universe"] += 1
        raise AssertionError("不应在空批次调用 get_market_universe")

    monkeypatch.setattr(svc, "latest_allowed_quote_date", lambda: "2026-07-29")
    monkeypatch.setattr(
        "app.services.market_data.get_market_universe",
        lambda: (_boom_universe()),
    )
    monkeypatch.setattr(
        "app.services.realtime_pnl_service._fetch_rtk_quotes",
        lambda codes: called.__setitem__("rtk", called["rtk"] + 1) or {},
    )

    # 全部已结算
    run_id = _make_run(db, "test_batch_empty_run")
    rec = _make_rec(db, "test_batch_settled_only", run_id, "000001")
    _add_outcome(db, rec, "success")
    db.flush()

    # 清缓存
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert result["items"] == []
    assert called["universe"] == 0
    assert called["rtk"] == 0
    assert result["summary"]["settled_skipped_count"] == 1


def test_nonexistent_ids_counted_as_skipped(db, monkeypatch):
    """不存在/非 formal 的 ID 不泄露存在性，计入 settled_skipped。"""
    monkeypatch.setattr(svc, "latest_allowed_quote_date", lambda: "2026-07-29")
    monkeypatch.setattr("app.services.market_data.get_market_universe", lambda: ([], _fake_snapshot()))
    monkeypatch.setattr(
        "app.services.realtime_pnl_service._fetch_rtk_quotes",
        lambda codes: {},
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, ["rec_does_not_exist"], refresh=True)
    assert result["items"] == []
    assert result["summary"]["settled_skipped_count"] == 1
    assert result["summary"]["requested_count"] == 1


# ── 一次批量加载 ──────────────────────────────────────────────────────────

def test_single_market_universe_and_rtk_call_for_multiple_recs(db, monkeypatch):
    """多笔未结算只发生一次 get_market_universe + 一次 _fetch_rtk_quotes。"""
    run_id = _make_run(db, "test_batch_once_run")
    r1 = _make_rec(db, "test_batch_once_r1", run_id, "600775", bucket=_FAKE_SKILL.skill_id)
    r2 = _make_rec(db, "test_batch_once_r2", run_id, "000002", bucket=_FAKE_SKILL.skill_id)
    # signal_date=07-28, data_as_of=07-29 → age 1（首个执行交易日）
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    _add_kbar(db, "000002", "2026-07-29", close=10.5)
    db.flush()

    calls = _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775"), _fake_stock("000002")],
        rt_quotes={"600775": _rt("600775"), "000002": _rt("000002")},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [r1, r2], refresh=True)

    assert calls["universe"] == 1, "get_market_universe 应只调用一次"
    assert calls["rtk"] == 1, "_fetch_rtk_quotes 应只调用一次"
    assert result["summary"]["requested_count"] == 2
    assert result["summary"]["success_count"] == 2
    assert len(result["items"]) == 2


def test_batch_response_reuses_realtime_quotes_for_pnl(db, monkeypatch):
    """一次批量 rt_k 同时产出买入确认和实时盈亏，不再由前端发第二个行情请求。"""
    code = "999996"
    run_id = _make_run(db, "test_batch_shared_pnl_run")
    rec = _make_rec(
        db,
        "test_batch_shared_pnl_rec",
        run_id,
        code,
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    _add_kbar(db, code, "2026-07-28", close=10.0)
    _add_kbar(db, code, "2026-07-29", close=10.5)
    db.flush()

    calls = _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: _rt(code)},
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert calls["rtk"] == 1
    assert result["items"][0]["recommendation_id"] == rec
    assert result["pnl_items"][0]["recommendation_id"] == rec


def test_batch_strategy_uses_enriched_objects_returned_by_build_features(db, monkeypatch):
    """策略必须读取 build_features 返回的新对象，不能继续使用重建前的映射。"""
    run_id = _make_run(db, "test_batch_enriched_return_run")
    rec = _make_rec(
        db,
        "test_batch_enriched_return_rec",
        run_id,
        "600775",
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775")],
        rt_quotes={"600775": _rt("600775")},
    )
    monkeypatch.setattr(
        "app.services.feature_builder.build_features",
        lambda stocks, snapshot, **_kw: [dict(stock, enriched_marker=True) for stock in stocks],
    )

    def _signal_from_enriched(skill, current, snapshot):
        assert current.get("enriched_marker") is True
        return dict(_FAKE_SIGNAL), True, ""

    monkeypatch.setattr(svc, "_build_signal_for_strategy", _signal_from_enriched)
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert result["summary"]["error_count"] == 0
    assert result["items"][0]["entry_decision"]["code"] == "buy"


def test_fresh_computation_persists_state_but_cache_hit_does_not_rewrite(db, monkeypatch):
    code = "999992"
    run_id = _make_run(db, "test_batch_persist_state_run")
    rec = _make_rec(
        db,
        "test_batch_persist_state_rec",
        run_id,
        code,
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    _add_kbar(db, code, "2026-07-29", close=10.5)
    db.flush()
    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: _rt(code)},
    )
    svc._cache["value"] = None

    first = svc.confirm_batch_executions(db, [rec], refresh=True)
    state = db.get(RecommendationExecutionState, rec)
    first_updated_at = state.updated_at if state else None
    second = svc.confirm_batch_executions(db, [rec], refresh=False)
    db.refresh(state)

    assert first["cached"] is False
    assert second["cached"] is True
    assert state is not None
    assert state.latest_decision_code == "buy"
    assert state.ever_buy_confirmed is True
    assert state.updated_at == first_updated_at


# ── 实时 K 合成 ───────────────────────────────────────────────────────────

def test_rt_bar_replaces_same_day_history_not_appends():
    """当本地最后一根 K 与实时 K 同日时，用实时 K 替换，不重复追加。"""
    bars = [
        {"trade_date": "2026-07-27", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "volume": 100, "amount": 1000, "pct_chg": 0.0},
        {"trade_date": "2026-07-29", "open": 10.1, "high": 10.3, "low": 10.0, "close": 10.2, "volume": 90, "amount": 900, "pct_chg": 2.0},  # 同日历史
    ]
    stock = _fake_stock("600775", bars=bars)

    # 用真实 _build_stock_from_bars 验证替换语义
    from app.services.market_data import _build_stock_from_bars

    rebuilt = svc._rebuild_stock_with_rt_k(
        stock, _rt("600775", close=10.5, high=10.6, low=10.4), "2026-07-29",
        build_stock_from_bars=_build_stock_from_bars,
    )
    rebuilt_bars = rebuilt["_bars"]
    # 同日应被替换，不追加第二根 07-29
    same_day = [b for b in rebuilt_bars if str(b["trade_date"]) == "2026-07-29"]
    assert len(same_day) == 1, "同日历史 K 必须替换而非追加"
    assert same_day[0]["close"] == 10.5  # rt_k 现价
    assert same_day[0]["high"] == 10.6  # rt_k high，不是历史的 10.3


def test_rt_bar_appended_when_history_missing_today():
    """本地最后一根 K 早于实时 K 日期时，追加今日临时 K。"""
    bars = [
        {"trade_date": "2026-07-27", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "volume": 100, "amount": 1000, "pct_chg": 0.0},
    ]
    stock = _fake_stock("600775", bars=bars)
    from app.services.market_data import _build_stock_from_bars

    rebuilt = svc._rebuild_stock_with_rt_k(
        stock, _rt("600775", close=10.5), "2026-07-29",
        build_stock_from_bars=_build_stock_from_bars,
    )
    dates = [str(b["trade_date"]) for b in rebuilt["_bars"]]
    assert "2026-07-29" in dates
    assert dates.count("2026-07-29") == 1


def test_rt_high_low_volume_enter_strategy_features():
    """实时 high/low/volume 会进入重算特征，不能只更新 close。"""
    bars = [
        {"trade_date": "2026-07-27", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0, "volume": 100, "amount": 1000, "pct_chg": 0.0},
    ]
    stock = _fake_stock("600775", bars=bars)
    from app.services.market_data import _build_stock_from_bars

    rebuilt = svc._rebuild_stock_with_rt_k(
        stock, _rt("600775", close=10.5, high=11.0, low=9.5, vol=500),
        "2026-07-29", build_stock_from_bars=_build_stock_from_bars,
    )
    # high_20d（近20日最高）应包含今日 high=11.0
    assert rebuilt.get("high_20d") == 11.0
    assert rebuilt.get("low_20d") == 9.5
    assert rebuilt.get("latest_trade_date") == "2026-07-29"


# ── 陈旧行情 ──────────────────────────────────────────────────────────────

def test_stale_snapshot_never_returns_buy(db, monkeypatch):
    """陈旧行情（data_as_of != expected）绝不返回 buy。"""
    run_id = _make_run(db, "test_batch_stale_run", trade_date="2026-07-29")
    rec = _make_rec(
        db,
        "test_batch_stale_rec",
        run_id,
        "600775",
        trade_date="2026-07-29",
        bucket=_FAKE_SKILL.skill_id,
    )
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    db.flush()

    # 期望今日 07-30，但行情快照是 07-29（陈旧）→ fresh=False
    _setup_decision_mocks(
        monkeypatch,
        expected_date="2026-07-30",
        data_as_of="2026-07-29",
        stocks=[_fake_stock("600775")],
        rt_quotes={"600775": _rt("600775")},
        signal={"phase": "确认", "score": 90, "setup_match": "分歧回封", "hard_veto": False, "risk_flags": []},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["fresh"] is False
    assert item["entry_decision"]["code"] != "buy"  # 陈旧行情不返回 buy


def test_incomplete_realtime_bar_returns_not_ready_instead_of_buy(db, monkeypatch):
    """实时 OHLCV 不完整时只能 not_ready，不能仅凭 close 运行买入规则。"""
    code = "999995"
    run_id = _make_run(db, "test_batch_incomplete_rt_run")
    rec = _make_rec(
        db,
        "test_batch_incomplete_rt_rec",
        run_id,
        code,
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    _add_kbar(db, code, "2026-07-29", close=10.5)
    db.flush()

    incomplete = _rt(code, close=10.5)
    incomplete.update({"open": 0.0, "high": 0.0, "low": 0.0, "vol": 0.0, "amount": 0.0})
    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: incomplete},
        signal={"phase": "确认", "score": 90, "setup_match": "分歧回封", "hard_veto": False, "risk_flags": []},
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert result["items"][0]["entry_decision"]["code"] == "not_ready"


# ── expired 状态 ───────────────────────────────────────────────────────────

def test_expired_when_age_greater_than_one(db, monkeypatch):
    """第 2 个交易日且第 1 日按回测口径可买 → expired，不拉该票 rt_k。"""
    run_id = _make_run(db, "test_batch_expired_run", trade_date="2026-07-25")
    rec = _make_rec(db, "test_batch_expired_rec", run_id, "600775", trade_date="2026-07-25")
    db.flush()

    rtk_calls = {"n": 0}
    monkeypatch.setattr(svc, "latest_allowed_quote_date", lambda: "2026-07-29")
    # age>1 直接预判 expired；mock _load_signal_ages 返回 age=2，避免写真实 K 线撞生产主键
    monkeypatch.setattr(
        svc,
        "_load_signal_ages",
        lambda db, recs, da: {str(rec.recommendation_id): 2 for rec, _ in recs},
    )
    monkeypatch.setattr(svc, "day1_status_in_window", lambda *a, **k: ("buyable", "2026-07-27"))
    monkeypatch.setattr("app.services.market_data.get_market_universe", lambda: ([], _fake_snapshot()))
    monkeypatch.setattr(
        "app.services.realtime_pnl_service._fetch_rtk_quotes",
        lambda codes: rtk_calls.__setitem__("n", rtk_calls["n"] + 1) or {},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert len(result["items"]) == 1
    assert result["items"][0]["entry_decision"]["code"] == "expired"
    assert result["summary"]["expired_count"] == 1
    assert rtk_calls["n"] == 0  # 过期票不拉 rt_k


def test_day1_bar_missing_is_not_pre_expired(db, monkeypatch):
    """第 2 个交易日但第 1 日日 K 没入库：不预判过期，进入实时确认（共享状态机判数据未就绪）。"""
    run_id = _make_run(db, "test_batch_day1_missing_run", trade_date="2026-07-25")
    rec = _make_rec(db, "test_batch_day1_missing_rec", run_id, "600775", trade_date="2026-07-25")
    db.flush()

    rtk_calls = {"n": 0}
    monkeypatch.setattr(svc, "latest_allowed_quote_date", lambda: "2026-07-29")
    monkeypatch.setattr(
        svc, "_load_signal_ages", lambda db, recs, da: {str(rec.recommendation_id): 2 for rec, _ in recs},
    )
    monkeypatch.setattr(svc, "day1_status_in_window", lambda *a, **k: ("missing_market", "2026-07-27"))
    monkeypatch.setattr("app.services.market_data.get_market_universe", lambda: ([], _fake_snapshot()))
    monkeypatch.setattr(
        "app.services.realtime_pnl_service._fetch_rtk_quotes",
        lambda codes: rtk_calls.__setitem__("n", rtk_calls["n"] + 1) or {},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert result["summary"]["expired_count"] == 0
    assert all(item["entry_decision"]["code"] != "expired" for item in result["items"])


def test_same_stock_recommendations_use_their_own_signal_dates(db, monkeypatch):
    """同股不同推荐日分别计算年龄：旧信号过期，昨日信号仍可确认。"""
    code = "999998"
    run_id = _make_run(db, "test_batch_same_code_dates_run", trade_date="2026-07-28")
    old_rec = _make_rec(
        db,
        "test_batch_same_code_old",
        run_id,
        code,
        trade_date="2026-07-25",
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    recent_rec = _make_rec(
        db,
        "test_batch_same_code_recent",
        run_id,
        code,
        trade_date="2026-07-28",
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    _add_kbar(db, code, "2026-07-28", close=10.2)
    _add_kbar(db, code, "2026-07-29", close=10.5)
    db.flush()

    calls = _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: _rt(code)},
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [old_rec, recent_rec], refresh=True)
    decisions = {
        item["recommendation_id"]: item["entry_decision"]["code"]
        for item in result["items"]
    }

    assert decisions[old_rec] == "expired"
    assert decisions[recent_rec] == "buy"
    assert calls["rtk"] == 1


def test_same_day_signal_is_not_ready_even_after_entry_window_closes(db, monkeypatch):
    """推荐后尚无新交易日时优先 not_ready，不能因当前已收盘改成 expired。"""
    code = "999997"
    run_id = _make_run(db, "test_batch_same_day_not_ready_run", trade_date="2026-07-29")
    rec = _make_rec(
        db,
        "test_batch_same_day_not_ready_rec",
        run_id,
        code,
        trade_date="2026-07-29",
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: _rt(code)},
        entry_window_closed=True,
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [rec], refresh=True)

    assert result["items"][0]["entry_decision"]["code"] == "not_ready"


def test_same_day_intraday_recommendation_can_confirm_buy(db, monkeypatch):
    """盘中生成的推荐在推荐当日直接重跑策略，不再统一等待次日。"""
    code = "999996"
    run_id = _make_run(db, "test_batch_intraday_buy_run", trade_date="2026-07-29")
    rec_id = _make_rec(
        db,
        "test_batch_intraday_buy_rec",
        run_id,
        code,
        trade_date="2026-07-29",
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    db.flush()
    db.get(Recommendation, rec_id).recommended_at = datetime(2026, 7, 29, 2, 0)
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: _rt(code)},
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [rec_id], refresh=True)

    assert result["items"][0]["entry_decision"]["code"] == "buy"


def test_intraday_recommendation_expires_after_close_without_fetching_quote(db, monkeypatch):
    """盘中推荐收盘后直接过期，不为已关闭的入场窗口拉实时 K。"""
    code = "999995"
    run_id = _make_run(db, "test_batch_intraday_closed_run", trade_date="2026-07-29")
    rec_id = _make_rec(
        db,
        "test_batch_intraday_closed_rec",
        run_id,
        code,
        trade_date="2026-07-29",
        bucket=_FAKE_SKILL.skill_id,
        stop_loss=1.0,
    )
    db.flush()
    db.get(Recommendation, rec_id).recommended_at = datetime(2026, 7, 29, 2, 0)
    db.flush()

    calls = _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock(code)],
        rt_quotes={code: _rt(code)},
        entry_window_closed=True,
    )
    svc._cache["value"] = None

    result = svc.confirm_batch_executions(db, [rec_id], refresh=True)

    assert result["items"][0]["entry_decision"]["code"] == "expired"
    assert result["items"][0]["entry_decision"]["reason"] == "当前交易日已收盘，不再追认盘中买点"
    assert calls["universe"] == 0
    assert calls["rtk"] == 0


# ── 跌破止损优先 + hard veto ─────────────────────────────────────────────

def test_price_below_recommend_stop_still_buys_like_backtest(db, monkeypatch):
    """回测止损按实际买入价设定：入场前跌破推荐止损位照样按开盘价买入；持仓提示退出。"""
    run_id = _make_run(db, "test_batch_stop_run")
    rec = _make_rec(db, "test_batch_stop_rec", run_id, "600775", bucket=_FAKE_SKILL.skill_id, recommend_price=10.0, stop_loss=9.8)
    _add_kbar(db, "600775", "2026-07-29", close=9.4)
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775", price=9.4)],
        rt_quotes={"600775": _rt("600775", close=9.4)},
        signal={"phase": "确认", "score": 90, "setup_match": "分歧回封", "hard_veto": False, "risk_flags": []},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)
    assert result["items"][0]["entry_decision"]["code"] == "buy"
    assert result["items"][0]["holding_decision"]["code"] == "exit"


def test_hard_veto_rerun_on_entry_day_no_longer_cancels(db, monkeypatch):
    """入场日重跑的策略硬否决 / prefilter 不过，不再取消买入（回测没有这类入场规则）。"""
    run_id = _make_run(db, "test_batch_veto_run")
    rec = _make_rec(db, "test_batch_veto_rec", run_id, "600775", bucket=_FAKE_SKILL.skill_id, stop_loss=1.0)
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775")],
        rt_quotes={"600775": _rt("600775")},
        signal={"phase": "确认", "score": 90, "setup_match": "", "hard_veto": True,
                "veto_reason": "未命中任何入场 setup（策略要求 require_setup）", "risk_flags": []},
        prefilter_ok=False,
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)
    item = result["items"][0]
    assert item["entry_decision"]["code"] == "buy"
    assert item["condition_summary"]["scope"] == "entry_day"


def test_gap_up_over_six_percent_waits_for_next_day(db, monkeypatch):
    """开盘高于推荐价 >6%：回测跳过该日 → 第 1 日判「今日不买，次日再确认」。"""
    run_id = _make_run(db, "test_batch_gap_run")
    rec = _make_rec(db, "test_batch_gap_rec", run_id, "600775", bucket=_FAKE_SKILL.skill_id, recommend_price=10.0, stop_loss=1.0)
    _add_kbar(db, "600775", "2026-07-29", close=10.8)
    db.flush()
    rt = {**_rt("600775", close=10.8), "open": 10.7}
    _setup_decision_mocks(monkeypatch, stocks=[_fake_stock("600775", price=10.8)], rt_quotes={"600775": rt})
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)
    item = result["items"][0]
    assert item["entry_decision"]["code"] == "wait"
    assert item["condition_summary"]["code"] == "entry_gap_up"


# ── 单票错误隔离 ──────────────────────────────────────────────────────────

def test_single_quote_missing_only_errors_that_rec_others_ok(db, monkeypatch):
    """一只股票实时行情缺失只产生该票错误，同批其他股票正常给结论。"""
    run_id = _make_run(db, "test_batch_isolate_run")
    r_ok = _make_rec(db, "test_batch_isolate_ok", run_id, "600775", bucket=_FAKE_SKILL.skill_id)
    r_miss = _make_rec(db, "test_batch_isolate_miss", run_id, "000002", bucket=_FAKE_SKILL.skill_id)
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    _add_kbar(db, "000002", "2026-07-29", close=10.5)
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775"), _fake_stock("000002")],
        # 000002 行情缺失
        rt_quotes={"600775": _rt("600775")},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [r_ok, r_miss], refresh=True)
    assert len(result["items"]) == 1
    assert result["items"][0]["recommendation_id"] == r_ok
    assert len(result["errors"]) == 1
    assert result["errors"][0]["recommendation_id"] == r_miss


# ── 缓存 ──────────────────────────────────────────────────────────────────

def test_cache_hit_on_refresh_false_recompute_on_refresh_true(db, monkeypatch):
    """refresh=false 命中缓存，refresh=true 重新计算。"""
    run_id = _make_run(db, "test_batch_cache_run")
    rec = _make_rec(db, "test_batch_cache_rec", run_id, "600775")
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    db.flush()

    compute_calls = {"n": 0}

    def _fake_compute(_db, eligible, *, requested_count, settled_skipped):
        compute_calls["n"] += 1
        rid = str(eligible[0][0].recommendation_id) if eligible else "rec_x"
        return {
            "computed_at": "2026-07-29T10:00:00",
            "data_as_of": "2026-07-29",
            "cached": False,
            "summary": {"requested_count": requested_count, "eligible_count": len(eligible), "settled_skipped_count": settled_skipped, "expired_count": 0, "success_count": len(eligible), "error_count": 0},
            "items": [{"recommendation_id": rid, "code": "600775", "trade_date": "2026-07-28", "status": "ok", "fresh": True, "entry_decision": {"code": "buy", "label": "允许执行", "reason": "买点成立"}, "strategy_signal": {}}],
            "errors": [],
        }

    monkeypatch.setattr(svc, "_compute_batch", _fake_compute)
    svc._cache["value"] = None

    # 首次：计算 1 次
    svc.confirm_batch_executions(db, [rec], refresh=True)
    assert compute_calls["n"] == 1

    # refresh=false：命中缓存，不重新计算
    cached = svc.confirm_batch_executions(db, [rec], refresh=False)
    assert compute_calls["n"] == 1
    assert cached["cached"] is True

    # refresh=true：重新计算
    svc.confirm_batch_executions(db, [rec], refresh=True)
    assert compute_calls["n"] == 2


def test_page_reload_reuses_result_for_three_minutes(db, monkeypatch):
    """自动请求在 3 分钟内复用结果，超过 3 分钟才重新判断。"""
    run_id = _make_run(db, "test_batch_three_minute_cache_run")
    rec = _make_rec(db, "test_batch_three_minute_cache_rec", run_id, "999993")
    db.flush()
    clock = {"now": 1_000.0}
    compute_calls = {"n": 0}

    def _fake_compute(_db, eligible, *, requested_count, settled_skipped):
        compute_calls["n"] += 1
        return {
            "computed_at": "2026-07-29T10:00:00",
            "data_as_of": "2026-07-29",
            "cached": False,
            "summary": {
                "requested_count": requested_count,
                "eligible_count": len(eligible),
                "settled_skipped_count": settled_skipped,
                "expired_count": 0,
                "success_count": 0,
                "error_count": 0,
            },
            "items": [],
            "errors": [],
            "pnl_items": [],
        }

    monkeypatch.setattr(svc, "_compute_batch", _fake_compute)
    monkeypatch.setattr(svc.time, "time", lambda: clock["now"])
    svc._cache["value"] = None

    svc.confirm_batch_executions(db, [rec], refresh=True)
    clock["now"] = 1_179.0
    cached = svc.confirm_batch_executions(db, [rec], refresh=False)

    assert compute_calls["n"] == 1
    assert cached["cached"] is True

    clock["now"] = 1_181.0
    refreshed = svc.confirm_batch_executions(db, [rec], refresh=False)

    assert compute_calls["n"] == 2
    assert refreshed["cached"] is False


def test_cached_eligible_result_keeps_current_request_summary(db, monkeypatch):
    """相同未结算集合可共享结果，但请求数和跳过数必须属于当前请求。"""
    run_id = _make_run(db, "test_batch_cache_summary_run")
    rec = _make_rec(db, "test_batch_cache_summary_rec", run_id, "999994")
    db.flush()
    compute_calls = {"n": 0}

    def _fake_compute(_db, eligible, *, requested_count, settled_skipped):
        compute_calls["n"] += 1
        return {
            "computed_at": "2026-07-29T10:00:00",
            "data_as_of": "2026-07-29",
            "cached": False,
            "summary": {
                "requested_count": requested_count,
                "eligible_count": len(eligible),
                "settled_skipped_count": settled_skipped,
                "expired_count": 0,
                "success_count": 0,
                "error_count": 0,
            },
            "items": [],
            "errors": [],
            "pnl_items": [],
        }

    monkeypatch.setattr(svc, "_compute_batch", _fake_compute)
    svc._cache["value"] = None
    svc.confirm_batch_executions(db, [rec], refresh=True)

    cached = svc.confirm_batch_executions(db, [rec, "rec_missing_for_cache_summary"], refresh=False)

    assert compute_calls["n"] == 1
    assert cached["cached"] is True
    assert cached["summary"]["requested_count"] == 2
    assert cached["summary"]["settled_skipped_count"] == 1


def test_same_key_concurrent_requests_share_one_computation(monkeypatch):
    """同 key 并发 miss 必须 single-flight，不能重复击穿行情上游。"""
    rec = SimpleNamespace(recommendation_id="rec_concurrent")
    monkeypatch.setattr(svc, "_load_eligible_recs", lambda db, ids: ([(rec, None)], 0))
    compute_started = threading.Event()
    release_compute = threading.Event()
    compute_calls = {"n": 0}

    def _fake_compute(_db, eligible, *, requested_count, settled_skipped):
        compute_calls["n"] += 1
        compute_started.set()
        release_compute.wait(timeout=2)
        return {
            "computed_at": "2026-07-29T10:00:00",
            "data_as_of": "2026-07-29",
            "cached": False,
            "summary": {"requested_count": 1, "eligible_count": 1, "settled_skipped_count": 0, "expired_count": 0, "success_count": 1, "error_count": 0},
            "items": [],
            "errors": [],
            "pnl_items": [],
        }

    monkeypatch.setattr(svc, "_compute_batch", _fake_compute)
    svc._cache["value"] = None
    results: list[dict[str, Any]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                svc.confirm_batch_executions(object(), ["rec_concurrent"], refresh=False)
            )
        )
        for _ in range(2)
    ]

    threads[0].start()
    assert compute_started.wait(timeout=1)
    threads[1].start()
    time.sleep(0.05)
    release_compute.set()
    for thread in threads:
        thread.join(timeout=2)

    assert compute_calls["n"] == 1
    assert len(results) == 2
