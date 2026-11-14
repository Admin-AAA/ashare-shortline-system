"""R12「整个窗口无样本」口径与巡检可读文案回归测试。"""
from __future__ import annotations

from app.services import data_quality_monitor as dq
from app.services import rolling_r12 as rr


def test_full_window_no_data_is_unavailable():
    days = [f"2026-{m:02d}-{d:02d}" for m in range(1, 4) for d in range(1, 6)]
    midlong = {"mid-a": {"verdict": "insufficient_sample"}, "mid-b": {"verdict": "positive"},
               "mid-c": {"verdict": "insufficient_sample"}}
    out = rr.find_unavailable_strategies({"mid-a": days, "mid-c": days[:2]}, midlong, len(days))
    assert out == {"mid-a": len(days)}  # mid-c 只失败 2 天，仍属样本不足


def test_zero_window_days_returns_empty():
    assert rr.find_unavailable_strategies({"mid-a": ["2026-01-01"]}, {"mid-a": {}}, 0) == {}


def test_crashed_strategy_keeps_error_verdict():
    days = ["2026-01-01", "2026-01-02"]
    midlong = {"mid-a": {"verdict": "error"}}
    assert rr.find_unavailable_strategies({"mid-a": days}, midlong, 2) == {}


def test_duplicated_failure_days_do_not_inflate_count():
    days = ["2026-01-01", "2026-01-01", "2026-01-02"]
    out = rr.find_unavailable_strategies({"mid-a": days}, {"mid-a": {}}, 2)
    assert out == {"mid-a": 2}


def _done(replay: dict) -> dict:
    return {"month": "2026-11", "status": "completed", "cutoff": "2026-11-02",
            "crashed": {}, "replay": replay}


def test_legacy_report_detail_is_ordered_and_explains_cumulative_days():
    # 11 月报告：271 天是跨夜续跑累计，本次运行只有 227 天，裸 dict 完全看不出量级。
    item = dq.rolling_r12_item(_done({
        "days": 227,
        "incomplete_days": {"long-dividend-lowvol": 4, "mid-incentive-option": 271,
                            "mid-incentive-vol": 271, "mid-block-trade-premium": 6},
    }), "2026-11", 3, 0)
    assert item["severity"] == dq.YELLOW
    assert item["detail"].index("271") < item["detail"].index("6 天")
    assert "本次运行 227 天" in item["detail"] and "跨夜续跑累计" in item["detail"]
    assert "{" not in item["detail"]  # 不再是裸 dict


def test_new_report_marks_unavailable_strategies_and_channel_hint():
    item = dq.rolling_r12_item(_done({
        "days": 271, "window_days": 271,
        "incomplete_days": {"mid-incentive-option": 271, "mid-block-trade-premium": 6},
        "unavailable_strategies": ["mid-incentive-option"],
    }), "2026-11", 3, 0)
    assert item["severity"] == dq.YELLOW
    assert "mid-incentive-option 271/271 天（指标不可得）" in item["detail"]
    assert "修通道后重算" in item["detail"]
    assert "mid-block-trade-premium 6/271 天" in item["detail"]
