# -*- coding: utf-8 -*-
"""单元测试：成本感知回测纯计算逻辑（不依赖 DB / 外部服务）。"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_cost_aware import (
    ADDITIONAL_COST_PCT,
    BacktestRow,
    compute_metrics,
    dedup_rows,
)


def _row(executed_return, label="success", score=80.0, tdate="2026-01-01",
         code="600000", strategies=None, phase="活跃", holding=3, reason="take_profit_hit",
         rid=None, source=None):
    rid = rid or f"{tdate}-{code}-{score}"
    # 与生产 load_rows_from_db 一致：按前缀推断 source，除非显式覆盖
    if source is None:
        source = "backtest" if rid.startswith("btrec_") else "live"
    return BacktestRow(
        recommendation_id=rid,
        trade_date=tdate, code=code, executed_return=executed_return,
        outcome_label=label, exit_reason=reason, holding_days=holding,
        final_score=score, strategy_ids=strategies or [], phase=phase, source=source,
    )


def test_cost_subtraction_is_applied():
    r = _row(5.0)
    assert r.net_return == pytest.approx(5.0 - ADDITIONAL_COST_PCT)
    # executed_return 未含成本；net 扣 0.10%
    assert ADDITIONAL_COST_PCT == 0.10


def test_win_rate_net_of_cost():
    rows = [_row(5.0, "success"), _row(0.05, "failed"), _row(-3.0, "failed")]
    m = compute_metrics(rows)
    # net: 4.9, -0.05, -3.1 -> 只有第1笔 >0
    assert m["n"] == 3
    assert m["win_rate"] == pytest.approx(round(1 / 3, 4), abs=1e-3)
    assert m["avg_net_return"] == pytest.approx(round((4.9 - 0.05 - 3.1) / 3, 4), abs=1e-3)


def test_pl_ratio_net():
    rows = [_row(10.0, "success"), _row(6.0, "success"), _row(-4.0, "failed")]
    m = compute_metrics(rows)
    # wins net: 9.9, 5.9 avg=7.9 ; loss net abs: 4.1
    assert m["avg_win"] == pytest.approx(round(7.9, 4), abs=1e-3)
    assert m["avg_loss_abs"] == pytest.approx(round(4.1, 4), abs=1e-3)
    assert m["pl_ratio"] == pytest.approx(round(7.9 / 4.1, 4), abs=1e-3)


def test_max_loss_and_sharpe():
    rows = [_row(2.0), _row(4.0), _row(-1.0, "failed")]
    m = compute_metrics(rows)
    assert m["max_loss_net"] == pytest.approx(round(-1.0 - ADDITIONAL_COST_PCT, 4), abs=1e-3)  # -1.1
    assert m["sharpe_per_trade"] != 0.0


def test_dedup_by_recommendation_id_keeps_highest_score():
    # 同一 recommendation_id 出现两次，保留 final_score 更高的一条
    a = _row(5.0, score=70.0, rid="rec_x")
    b = _row(1.0, score=95.0, rid="rec_x")  # 同 id，score 更高
    c = _row(3.0, score=80.0, rid="rec_y")
    out = dedup_rows([a, b, c])
    ids = sorted(r.recommendation_id for r in out)
    assert ids == ["rec_x", "rec_y"]
    kept = [r for r in out if r.recommendation_id == "rec_x"][0]
    assert kept.final_score == 95.0


def test_dedup_does_not_merge_same_trade_date_code():
    # 关键修正：同一 (trade_date, code) 上的不同 recommendation_id 必须各自保留，
    # 不能像旧版那样按 (td,code) 去重（会非法合并回测的多条独立交易）。
    a = _row(5.0, code="600001", rid="btrec_aaa")   # 回测交易 1
    b = _row(1.0, code="600001", rid="btrec_bbb")   # 回测交易 2（同一股票同日，不同 id）
    c = _row(3.0, code="600002", rid="rec_ccc")     # 实盘交易
    out = dedup_rows([a, b, c])
    assert len(out) == 3  # 三条都保留，互不合并且不污染口径


def test_source_classification_live_vs_backtest():
    live = _row(5.0, rid="rec_001")
    bt = _row(1.0, rid="btrec_001")
    assert live.source == "live"
    assert bt.source == "backtest"
    # loader 逻辑等价：前缀判定
    assert ("btrec_001".startswith("btrec_"))
    assert not ("rec_001".startswith("btrec_"))


def test_strategy_attribution():
    rows = [
        _row(5.0, strategies=["chen-xiaoqun-skill"]),
        _row(-3.0, "failed", strategies=["chen-xiaoqun-skill", "overnight-holding-method"]),
        _row(2.0, strategies=["overnight-holding-method"]),
    ]
    m = compute_metrics(rows)
    ss = m["strategy_stats"]
    assert set(ss.keys()) == {"chen-xiaoqun-skill", "overnight-holding-method"}
    # chen: 2 trades, 1 win -> 50%
    assert ss["chen-xiaoqun-skill"]["n"] == 2
    assert ss["chen-xiaoqun-skill"]["win_rate"] == pytest.approx(0.5)
    assert ss["overnight-holding-method"]["n"] == 2


def test_phase_information_flag():
    rows = [
        _row(5.0, phase="高潮"), _row(4.0, phase="高潮"), _row(6.0, phase="高潮"),
        _row(5.0, phase="活跃"), _row(4.0, phase="活跃"), _row(6.0, phase="活跃"),
    ]
    m = compute_metrics(rows)
    # 两相位胜率都 100% -> 极差 0 -> 无信息量
    assert m["phase_winrate_spread_pp"] == pytest.approx(0.0)
    assert m["phase_has_information"] is False

    rows2 = [
        _row(5.0, phase="高潮"), _row(-3.0, "failed", phase="高潮"),
        _row(5.0, phase="冰点"), _row(5.0, phase="冰点"),
    ]
    m2 = compute_metrics(rows2)
    # 高潮 50%, 冰点 100% -> 极差 50pp -> 有信息量
    assert m2["phase_has_information"] is True
    assert m2["phase_winrate_spread_pp"] >= 50.0


def test_empty_rows():
    m = compute_metrics([])
    assert m == {"n": 0}
