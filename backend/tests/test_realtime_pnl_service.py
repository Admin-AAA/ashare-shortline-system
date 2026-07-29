"""realtime_pnl_service 单测：mock rt_k + _resolve_entry，验证实盘浮盈计算与降级。"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services import realtime_pnl_service as svc


class _Rec:
    """最小 Recommendation 替身，承载 _resolve_entry 所需字段。"""

    def __init__(self, code, trade_date="2026-07-24", recommend_price=18.0, fs=None):
        self.recommendation_id = f"rec_{code}"
        self.code = code
        self.trade_date = trade_date
        self.recommend_price = recommend_price
        self.recommended_at = None  # 盘后处理
        import json

        self.feature_snapshot_json = json.dumps(fs or {"ideal_buy": 18.23, "secondary_buy": 17.95})


def _quote(open_p, high, low, close, vol=1000, amount=1e6):
    return {"open": open_p, "high": high, "low": low, "close": close,
            "pre_close": close, "vol": vol, "amount": amount, "name": "测试"}


def test_load_pending_recs_includes_provisional_and_excludes_settled(db):
    """provisional 仍未最终结算，应与 pending 一起进入实时盈亏。"""
    run_id = "test_realtime_pnl_provisional_run"
    provisional_id = "test_realtime_pnl_provisional_rec"
    settled_id = "test_realtime_pnl_settled_rec"
    db.add(
        RecommendationRun(
            run_id=run_id,
            run_name="实时盈亏 provisional 回归测试",
            run_mode="formal",
            trade_date="2026-07-24",
        )
    )
    db.add_all(
        [
            Recommendation(
                recommendation_id=provisional_id,
                run_id=run_id,
                trade_date="2026-07-24",
                code="000001",
                name="测试临时态",
                final_score=80,
                status="provisional",
            ),
            Recommendation(
                recommendation_id=settled_id,
                run_id=run_id,
                trade_date="2026-07-24",
                code="000002",
                name="测试已结算",
                final_score=80,
                status="success",
            ),
        ]
    )
    db.add_all(
        [
            RecommendationOutcome(
                recommendation_id=provisional_id,
                outcome_label="provisional",
                outcome_label_legacy="provisional",
            ),
            RecommendationOutcome(
                recommendation_id=settled_id,
                outcome_label="success",
                outcome_label_legacy="success",
            ),
        ]
    )
    db.flush()

    loaded_ids = {rec.recommendation_id for rec in svc._load_pending_recs(db)}

    assert provisional_id in loaded_ids
    assert settled_id not in loaded_ids


def test_next_open_gap_filter_does_not_backfill_target_touch(monkeypatch):
    # 次日高开超过阈值应放弃，不因日内 low 触及目标价而回填更优成交。
    monkeypatch.setattr(svc, "_fetch_rtk_quotes", lambda codes: {"600539": _quote(20.36, 20.36, 16.88, 17.78)})
    rec = _Rec("600539", recommend_price=18.5)
    out = svc._compute_one(rec, svc._fetch_rtk_quotes([])["600539"], signal_bar=None)
    assert out["status"] == "watching"
    assert "跳空高开" in out["reason"]


def test_holding_pnl_profit(monkeypatch):
    # 买点命中，现价高于入场价 → 浮盈
    monkeypatch.setattr(svc, "_fetch_rtk_quotes", lambda codes: {"605028": _quote(25.28, 28.0, 25.28, 27.7)})
    rec = _Rec("605028", recommend_price=26.5, fs={"ideal_buy": 26.79, "secondary_buy": 26.38})
    out = svc._compute_one(rec, svc._fetch_rtk_quotes([])["605028"], signal_bar=None)
    assert out["status"] == "holding"
    assert out["pnl_pct"] > 0


def test_unavailable_when_no_quote():
    rec = _Rec("000011")
    out = svc._compute_one(rec, None, signal_bar=None)
    assert out["status"] == "unavailable"
    assert "无数据" in out["reason"] or "不可用" in out["reason"]


def test_unavailable_when_quote_close_zero():
    rec = _Rec("000011")
    out = svc._compute_one(rec, _quote(8.0, 8.5, 7.9, 0), signal_bar=None)
    assert out["status"] == "unavailable"


def test_fetch_rtk_quotes_falls_back_to_realtime_quote_when_rt_k_fails(monkeypatch):
    """rt_k 主链失败时，结算中记录仍应从批量实时报价拿到现价。"""
    from app.services import realtime_quote_client, tushare_client

    class _FailingPro:
        def rt_k(self, **_kwargs):
            raise RuntimeError("rt_k unavailable")

    monkeypatch.setattr(tushare_client, "get_pro", lambda: _FailingPro())
    monkeypatch.setattr(
        realtime_quote_client,
        "fetch_realtime_quotes",
        lambda codes, use_cache=False: {
            "600775": {
                "name": "南京熊猫",
                "price": 10.35,
                "open": 9.80,
                "high": 10.35,
                "low": 9.70,
                "pre_close": 9.41,
                "volume": 1234,
                "amount": 5678,
                "trade_date": "2026-07-28",
            }
        },
    )

    quotes = svc._fetch_rtk_quotes(["600775"])

    assert quotes["600775"] == {
        "open": 9.80,
        "high": 10.35,
        "low": 9.70,
        "close": 10.35,
        "pre_close": 9.41,
        "vol": 1234,
        "amount": 5678,
        "name": "南京熊猫",
        "trade_date": "2026-07-28",
    }


def test_watching_when_target_not_touched(monkeypatch):
    # low 远高于买点 → 买点未触发 → watching + 距买点
    rec = _Rec("000011", fs={"ideal_buy": 7.0, "secondary_buy": 6.8})
    # open/low 都高于 7.0，且无买点命中 → _resolve_entry 走开盘回退仍会 triggered
    # 改为 low 高于买点但 open 有效 → 应触发开盘回退 holding；真正的 watching 需要
    # _resolve_entry 返回 triggered=False。用 gap 过大触发未触发路径更稳。
    # 这里直接验证 watching 分支结构：构造一个 triggered=False 的 _resolve_entry
    monkeypatch.setattr(svc, "_resolve_entry", lambda *a, **k: {"triggered": False, "reason": "买点未触发"})
    out = svc._compute_one(rec, _quote(8.0, 8.5, 7.9, 8.15), signal_bar=None)
    assert out["status"] == "watching"
    assert out["now_price"] == pytest.approx(8.15)
    assert out["nearest_buy_target"] == pytest.approx(7.0)
    assert out["distance_to_target_pct"] is not None


def test_signal_bar_used_for_intraday_judgement(monkeypatch):
    # 提供 signal_bar（trade_date 当日日K）时 _resolve_entry 能区分盘中/盘后
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 27)

    monkeypatch.setattr(svc, "date", _FixedDate)
    rec = _Rec("601606", recommend_price=30.5)
    sig = DailyKbar(code="601606", trade_date="2026-07-24", open=30.0, high=31.0, low=29.5,
                    close=30.5, volume=1000, amount=1e6)
    q = _quote(32.26, 35.5, 32.25, 35.26)
    out = svc._compute_one(rec, q, signal_bar=sig)
    assert out["status"] == "holding"
    assert out["entry_date"] == "2026-07-27"


def test_same_day_after_close_recommendation_waits_for_next_trade_day(monkeypatch):
    """盘后才生成的推荐不能用推荐前的当日 OHLC 虚构持仓和浮盈。"""

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 28)

    monkeypatch.setattr(svc, "date", _FixedDate)
    rec = _Rec(
        "002195",
        trade_date="2026-07-28",
        recommend_price=6.83,
        fs={"ideal_buy": 6.11, "secondary_buy": 6.03},
    )
    rec.recommended_at = datetime(2026, 7, 28, 10, 34, 25)  # UTC，对应北京时间 18:34

    out = svc._compute_one(rec, _quote(6.11, 6.83, 6.10, 6.83), signal_bar=None)

    assert out["status"] == "waiting"
    assert "下一交易日" in out["reason"]
    assert "entry_price" not in out
    assert "pnl_pct" not in out


def test_same_day_intraday_recommendation_uses_recommendation_price(monkeypatch):
    """盘中推荐按推荐时点快照价入场，不能使用当天更早的开盘价或最低价。"""

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 28)

    monkeypatch.setattr(svc, "date", _FixedDate)
    rec = _Rec(
        "600775",
        trade_date="2026-07-28",
        recommend_price=10.00,
        fs={"ideal_buy": 9.80, "secondary_buy": 9.70},
    )
    rec.recommended_at = datetime(2026, 7, 28, 5, 2, 12)  # UTC，对应北京时间 13:02
    full_day_bar = DailyKbar(
        code="600775",
        trade_date="2026-07-28",
        open=9.50,
        high=10.50,
        low=9.40,
        close=10.35,
        volume=1000,
        amount=1e6,
    )

    out = svc._compute_one(
        rec,
        _quote(9.50, 10.50, 9.40, 10.35),
        signal_bar=full_day_bar,
    )

    assert out["status"] == "holding"
    assert out["entry_date"] == "2026-07-28"
    assert out["entry_price"] == pytest.approx(10.00)
    assert out["pnl_pct"] == pytest.approx(3.5)


def test_one_word_limit_up_blocks_entry(monkeypatch):
    """#2: 合成 bar 补 pct_chg 后，一字涨停「买不进」拦截生效，不再返回幻影持仓。
    前收 10.0、今日一字涨停 11.0（pct_chg=+10%），四点齐平=涨停价 → 应判买不进。"""
    rec = _Rec("600539", recommend_price=10.0, fs={"ideal_buy": 10.0, "secondary_buy": 9.9})
    # 一字涨停：open=high=low=close=11.0，pre_close=10.0
    q = {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
         "pre_close": 10.0, "vol": 1000, "amount": 1e6, "name": "测试"}
    out = svc._compute_one(rec, q, signal_bar=None)
    # 买点 10.0 未被 low 触及（low=11.0），开盘回退也一字涨停买不进 → 不应是 holding
    assert out["status"] != "holding"


def test_gap_up_blocked_watching_not_holding():
    """#3: 20cm 板跳空高开 >10%（合法涨幅、非一字）应放弃入场，watching 而非幻影 holding。
    创业板 300539，recommend_price=10.0，今日开盘 11.5（+15%，未触 20% 涨停），
    low 11.4 > 买点 10.0 不触发买点 → 走开盘回退入场价 11.5 → gap=15% > 10% → watching。"""
    rec = _Rec("300539", recommend_price=10.0, fs={"ideal_buy": 10.0, "secondary_buy": 9.9})
    q = {"open": 11.5, "high": 11.6, "low": 11.4, "close": 11.5,
         "pre_close": 10.0, "vol": 1000, "amount": 1e6, "name": "测试"}
    out = svc._compute_one(rec, q, signal_bar=None)
    assert out["status"] == "watching"
    assert "跳空高开" in out["reason"]


def test_ex_right_day_pnl_not_falsified():
    """#4: 跨除权日（10送10 价格腰斩）pnl 不应显示 -50% 虚假亏损。
    推荐日入场 10.0，次日除权价 5.0（pct_chg≈0），今日现价 5.0 → 真实约 0% 而非 -50%。"""
    rec = _Rec("600539", recommend_price=10.0, fs={"ideal_buy": 10.0})
    # 买点 10.0 在历史 bar 触及入场；构造中间真实 bar 为除权日（pct_chg≈0，价格腰斩）
    ex_bar = DailyKbar(code="600539", trade_date="2026-07-25", open=5.0, high=5.1, low=4.95,
                      close=5.0, volume=1000, amount=1e6, pct_chg=0.0)
    # 今日合成 bar：现价 5.0，pre_close=5.0（无再除权），pct_chg=0
    q = {"open": 5.0, "high": 5.1, "low": 4.95, "close": 5.0,
         "pre_close": 5.0, "vol": 1000, "amount": 1e6, "name": "测试"}
    out = svc._compute_one(rec, q, signal_bar=None, intermediate_bars=[ex_bar])
    assert out["status"] == "holding"
    # 除权修正后应接近 0%；raw 口径会是 (5.0-10.0)/10.0*100 = -50% 虚假亏损
    assert abs(out["pnl_pct"]) < 1.0


def test_stale_pending_uses_real_intermediate_entry_date():
    """#5: 滞留 pending 加载真实中间日K，入场日用真实 T+1 而非今日。
    推荐日 07-24，买点 10.0 在 07-25 触及；今日 07-28 low=10.9>买点不触发，
    若缺中间 bar 会回退今日开盘；有中间 bar 应在 07-25 入场。"""
    rec = _Rec("600539", trade_date="2026-07-24", recommend_price=10.0, fs={"ideal_buy": 10.0})
    t1 = DailyKbar(code="600539", trade_date="2026-07-25", open=10.2, high=10.5, low=9.98,
                   close=10.3, volume=1000, amount=1e6, pct_chg=3.0)
    q = _quote(10.9, 11.0, 10.85, 10.9)  # 今日 low 10.85 > 买点 10.0，今日不触发
    q["pre_close"] = 10.3
    out = svc._compute_one(rec, q, signal_bar=None, intermediate_bars=[t1])
    assert out["status"] == "holding"
    assert out["entry_date"] == "2026-07-25"  # 真实入场日，非今日
