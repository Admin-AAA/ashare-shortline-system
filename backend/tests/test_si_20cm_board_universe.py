"""Regression tests for the 20cm-only board universe production filter.

Self-improve 2026-07-30 (si-20260730-1957-20cm-prod): clean>=72 前瞻样本中 20cm 板
胜率/盈亏比显著高于 10cm 板（WR 57.6% vs 37.8%、PF 1.76 vs 0.30）。将 formal universe
限制为 20cm 板后，四个核心指标全部改善（avg_net +1.51% vs -0.59%、WR 57.6% vs 46.2%、
PF 1.76 vs 0.78、max_loss -5.97% vs -8.57%）。本文件锁定生产链路 entry 侧过滤行为，
确保 10cm 主板/中小板被剔除、20cm 创业板/科创板保留，且默认（None）不启用过滤。
"""
import pytest

from app.engine.aggregator import _is_20cm_board, apply_pool_rules


def _card(code: str, final_score: float = 80.0):
    return {
        "code": code,
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def _neutral_snapshot():
    return {"sample_count": 100, "up_count": 45, "limit_up_count": 0, "limit_down_count": 0}


def test_is_20cm_board_prefix_logic():
    assert _is_20cm_board("300750")
    assert _is_20cm_board("301269")
    assert _is_20cm_board("688981")
    assert _is_20cm_board("689009")
    assert not _is_20cm_board("600519")
    assert not _is_20cm_board("000001")
    assert not _is_20cm_board("002594")
    assert not _is_20cm_board("")


def test_board_universe_none_keeps_all_boards():
    cards = [
        _card("300750", 80),  # 20cm
        _card("688981", 82),  # 20cm
        _card("600519", 78),  # 10cm
        _card("000001", 76),  # 10cm
    ]
    out = apply_pool_rules(
        cards, score_line=72, market_snapshot=_neutral_snapshot(), return_watchlist=True,
    )
    codes = [c["code"] for c in out["recommendations"]]
    assert set(codes) == {"300750", "688981", "600519", "000001"}
    assert out["board_universe"] is None


def test_board_universe_20cm_drops_10cm_keeps_20cm():
    cards = [
        _card("300750", 80),  # 20cm -> keep
        _card("688981", 82),  # 20cm -> keep
        _card("600519", 95),  # 10cm high score -> drop
        _card("000001", 90),  # 10cm high score -> drop
    ]
    out = apply_pool_rules(
        cards, score_line=72, market_snapshot=_neutral_snapshot(), return_watchlist=True,
        board_universe="20cm",
    )
    codes = [c["code"] for c in out["recommendations"]]
    assert set(codes) == {"300750", "688981"}
    assert "600519" not in codes
    assert "000001" not in codes
    assert out["board_universe"] == "20cm"


def test_board_universe_20cm_empty_when_only_10cm_pass():
    cards = [
        _card("600519", 95),  # 10cm
        _card("000001", 90),  # 10cm
    ]
    out = apply_pool_rules(
        cards, score_line=72, market_snapshot=_neutral_snapshot(), return_watchlist=True,
        board_universe="20cm",
    )
    assert out["recommendations"] == []
    # 即便无 20cm 过线，也不应靠 10cm 软降线兜底出票（entry 侧 universe 限制）。
    assert out["board_universe"] == "20cm"


def test_board_universe_filter_is_entry_side_only_no_score_change():
    # 同分数下，20cm 与 10cm 在过滤前走完全相同的 score_line 逻辑；
    # 过滤只按板块剔除，不改变任何通过票的 effective_score_line。
    cards = [_card("300750", 80), _card("600519", 80)]
    base = apply_pool_rules(cards, score_line=72, market_snapshot=_neutral_snapshot())
    filtered = apply_pool_rules(
        cards, score_line=72, market_snapshot=_neutral_snapshot(), board_universe="20cm",
    )
    assert len(base) == 2
    assert len(filtered) == 1
    assert filtered[0]["code"] == "300750"
    assert filtered[0]["score_line_meta"]["effective_score_line"] == base[0]["score_line_meta"]["effective_score_line"]
