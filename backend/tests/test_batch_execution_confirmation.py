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
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services import batch_execution_confirmation as svc


# ── fixtures ──────────────────────────────────────────────────────────────

def _make_run(db: Session, run_id: str, trade_date: str = "2026-07-28") -> str:
    db.add(RecommendationRun(run_id=run_id, run_name="批量确认测试", run_mode="formal", trade_date=trade_date))
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
        feature_snapshot_json=json.dumps({"stop_loss": stop_loss}),
    ))
    return rec_id


def _add_outcome(db: Session, rec_id: str, label: str) -> None:
    db.add(RecommendationOutcome(
        recommendation_id=rec_id, outcome_label=label, outcome_label_legacy=label,
    ))


def _add_kbar(db: Session, code: str, trade_date: str, close: float = 10.0, **kw: Any) -> None:
    db.add(DailyKbar(
        code=code, trade_date=trade_date,
        open=kw.get("open", close), high=kw.get("high", close), low=kw.get("low", close),
        close=close, volume=kw.get("volume", 100), amount=kw.get("amount", 1000),
        pct_chg=kw.get("pct_chg", 0.0), turnover_rate=kw.get("turnover_rate", 1.0),
    ))


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
        "open": 10.2, "high": high if high is not None else close, "low": low if low is not None else close,
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
    monkeypatch.setattr(svc, "_entry_window_closed", lambda d, n: entry_window_closed)
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
    run_id = _make_run(db, "test_batch_stale_run")
    rec = _make_rec(db, "test_batch_stale_rec", run_id, "600775", bucket=_FAKE_SKILL.skill_id)
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


# ── expired 状态 ───────────────────────────────────────────────────────────

def test_expired_when_age_greater_than_one(db, monkeypatch):
    """推荐日之后已超过首个交易日 → expired，不拉该票 rt_k。"""
    run_id = _make_run(db, "test_batch_expired_run", trade_date="2026-07-25")
    rec = _make_rec(db, "test_batch_expired_rec", run_id, "600775", trade_date="2026-07-25")
    db.flush()

    rtk_calls = {"n": 0}
    monkeypatch.setattr(svc, "latest_allowed_quote_date", lambda: "2026-07-29")
    # age>1 直接预判 expired；mock _load_signal_ages 返回 age=2，避免写真实 K 线撞生产主键
    monkeypatch.setattr(svc, "_load_signal_ages", lambda db, codes, sd_map, da: {c: 2 for c in codes})
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


# ── 跌破止损优先 + hard veto ─────────────────────────────────────────────

def test_stop_broken_returns_cancel_over_supportive_signal(db, monkeypatch):
    """跌破原止损优先于支持性规则 → cancel。"""
    run_id = _make_run(db, "test_batch_stop_run")
    rec = _make_rec(db, "test_batch_stop_rec", run_id, "600775", bucket=_FAKE_SKILL.skill_id, recommend_price=10.0, stop_loss=9.8)
    _add_kbar(db, "600775", "2026-07-29", close=9.5)  # 现价低于止损
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775", price=9.5)],
        rt_quotes={"600775": _rt("600775", close=9.5)},
        # signal 看似支持入场，但跌破止损应优先 cancel
        signal={"phase": "确认", "score": 90, "setup_match": "分歧回封", "hard_veto": False, "risk_flags": []},
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)
    assert result["items"][0]["entry_decision"]["code"] == "cancel"


def test_hard_veto_returns_cancel(db, monkeypatch):
    """策略 hard veto → cancel。"""
    run_id = _make_run(db, "test_batch_veto_run")
    rec = _make_rec(db, "test_batch_veto_rec", run_id, "600775", bucket=_FAKE_SKILL.skill_id, stop_loss=1.0)  # 止损极低避免触发
    _add_kbar(db, "600775", "2026-07-29", close=10.5)
    db.flush()

    _setup_decision_mocks(
        monkeypatch,
        stocks=[_fake_stock("600775")],
        rt_quotes={"600775": _rt("600775")},
        signal={"phase": "确认", "score": 90, "setup_match": "分歧回封", "hard_veto": True, "veto_reason": "非主线热点", "risk_flags": []},
        prefilter_ok=False,
    )
    svc._cache["value"] = None
    result = svc.confirm_batch_executions(db, [rec], refresh=True)
    assert result["items"][0]["entry_decision"]["code"] == "cancel"


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
