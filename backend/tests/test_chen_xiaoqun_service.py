"""
陈小群龙头战法服务 — 单元测试。

覆盖：情绪周期检测、仓位管理、止损规则。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.chen_xiaoqun_service import (
    EmotionCycle,
    apply_stop_loss,
    detect_emotion_cycle,
    get_position_pct,
)


# ═════════════════════════════════════════════════════════════════════════════
# 情绪周期检测
# ═════════════════════════════════════════════════════════════════════════════


def _make_mock_db():
    """创建一个 Mock DB session。"""
    return MagicMock()


def _mock_scalar(mock_db, side_effect):
    """设置 db.scalar 的 side_effect。"""
    mock_db.scalar.side_effect = side_effect


def _mock_scalars(mock_db, return_value):
    """设置 db.scalars(...).all() 的返回值。"""
    mock_db.scalars.return_value.all.return_value = return_value


def _mock_execute_scalar(mock_db, side_effect):
    """设置 db.execute(...).scalar() 的 side_effect。"""
    mock_db.execute.return_value.scalar.side_effect = side_effect


class TestDetectEmotionCycle:
    """情绪周期四阶段判定测试。"""

    def _setup_mock(self, limit_up: int, limit_down_lub: int, limit_down_kbar: int, max_height: int):
        """统一 mock 设置。

        4 次 db.scalar 调用顺序：
          1. _count_limit_up → count of 'U' in limit_up_board
          2. _count_limit_down → count of 'D' in limit_up_board
          3. _count_limit_down → count of pct_chg <= -9.8 in daily_kbars
          4. _max_limit_up_consecutive → max(limit_times)
        """
        db = _make_mock_db()
        # max() 处理：_count_limit_down 取两路的最大值，
        # 但函数内部是分别 scalar 后 max()，所以 side_effect 需要依次返回
        db.scalar.side_effect = [limit_up, limit_down_lub, limit_down_kbar, max_height]
        return db

    def test_retreat_low_height(self):
        """退潮：连板高度 <= 2。"""
        db = self._setup_mock(limit_up=20, limit_down_lub=10, limit_down_kbar=5, max_height=2)
        result = detect_emotion_cycle("2026-01-09", db)
        assert result == EmotionCycle.RETREAT

    def test_retreat_too_many_limit_down(self):
        """退潮：跌停 > 50 只。"""
        # limit_down_kbar=55 会触发 max(10, 55) = 55 > 50
        db = self._setup_mock(limit_up=20, limit_down_lub=10, limit_down_kbar=55, max_height=4)
        result = detect_emotion_cycle("2026-01-09", db)
        assert result == EmotionCycle.RETREAT

    def test_climax(self):
        """高潮：涨停 > 85 且 连板高度 >= 5。"""
        db = self._setup_mock(limit_up=120, limit_down_lub=5, limit_down_kbar=3, max_height=7)
        result = detect_emotion_cycle("2026-01-09", db)
        assert result == EmotionCycle.CLIMAX

    def test_ferment(self):
        """发酵：涨停 >= 8 且 晋级率 > 18% 且 梯队完整。"""
        db = _make_mock_db()
        # _count_limit_up → 8, _count_limit_down → 3 (lub) / 2 (kbar), _max_height → 4,
        # promotion yesterday_limit_up → 8, tier_2_count → 10, tier_3_count → 5
        db.scalar.side_effect = [8, 3, 2, 4, 8, 10, 5]

        # scalars: first call yesterday codes, second call today codes
        yesterday_codes = ["000001", "000002", "600000", "600001", "000005", "000006", "000007", "000008"]
        today_codes = ["000001", "000002", "600000", "600001", "000010"]
        db.scalars.return_value.all.side_effect = [yesterday_codes, today_codes]

        with patch("app.services.chen_xiaoqun_service._prev_trade_date", return_value="2026-01-08"):
            result = detect_emotion_cycle("2026-01-09", db)

        # 涨停数 8 >= 5, max_consecutive=4, promotion_rate=4/8=0.5>0.4
        assert result == EmotionCycle.FERMENT

    def test_startup(self):
        """启动：连板高度 >= 3。"""
        db = _make_mock_db()
        # _count_limit_up → 3, _count_limit_down → 5 (lub) / 2 (kbar=2), _max_height → 3
        db.scalar.side_effect = [3, 5, 2, 3]

        result = detect_emotion_cycle("2026-01-09", db)
        assert result == EmotionCycle.STARTUP


# ═════════════════════════════════════════════════════════════════════════════
# 仓位管理
# ═════════════════════════════════════════════════════════════════════════════


class TestGetPositionPct:
    """仓位比例测试。"""

    def test_startup_15pct(self):
        assert get_position_pct(EmotionCycle.STARTUP) == 0.15

    def test_ferment_pct(self):
        assert get_position_pct(EmotionCycle.FERMENT) == 0.15

    def test_climax_60pct(self):
        assert get_position_pct(EmotionCycle.CLIMAX) == 0.60

    def test_retreat_0pct(self):
        assert get_position_pct(EmotionCycle.RETREAT) == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# 止损规则
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyStopLoss:
    """止损纪律三规则测试。"""

    def test_stop_loss_single_day_drop_over_5pct(self):
        """规则 1：单日跌 > 5% → 强制止损。"""
        trade: dict = {}
        result = apply_stop_loss(trade, current_price=9.3, entry_price=10.0, days_held=1)
        assert result["action"] == "stop_loss"
        assert "5%" in result["reason"]

    def test_half_position_3_days_no_gain(self):
        """规则 2：持仓 3 天不涨 → 减半。"""
        trade: dict = {}
        result = apply_stop_loss(trade, current_price=10.0, entry_price=10.0, days_held=3)
        assert result["action"] == "half"
        assert "减半" in result["reason"]

        # 3 天还跌了也减半
        result2 = apply_stop_loss(trade, current_price=9.8, entry_price=10.0, days_held=3)
        assert result2["action"] == "half"

    def test_no_half_before_3_days(self):
        """规则 2 边界：持仓不足 3 天不触发减半。"""
        trade: dict = {}
        # days_held < 3, 即使 -5% 跌幅也不触发减半（-5% 触发 stop_loss）
        # -5.1% → 严格 < -5.0% → stop_loss
        result = apply_stop_loss(trade, current_price=9.49, entry_price=10.0, days_held=2)
        assert result["action"] == "stop_loss"

    def test_half_position_3_days_slight_loss(self):
        """规则 2：持仓 3 天微亏也减半。"""
        trade: dict = {}
        result = apply_stop_loss(trade, current_price=9.99, entry_price=10.0, days_held=3)
        assert result["action"] == "half"

    def test_exit_high_volume_stagnation(self):
        """规则 3：高位放量滞涨 → 出。"""
        trade: dict = {
            "daily_volume": 1_000_000,
            "signal_day_volume": 500_000,
            "current_volume": 1_600_000,  # > 1.5 * daily_volume
        }
        # 涨幅 1% < 2% 但量能是 signal 日的 3.2 倍 > 1.5 倍
        result = apply_stop_loss(trade, current_price=10.1, entry_price=10.0, days_held=2)
        assert result["action"] == "exit"

    def test_hold_normal_condition(self):
        """正常情况：不触发任何止损。"""
        trade: dict = {
            "daily_volume": 600_000,
            "signal_day_volume": 500_000,
        }
        # 小幅上涨，量能正常
        result = apply_stop_loss(trade, current_price=10.5, entry_price=10.0, days_held=1)
        assert result["action"] == "hold"

    def test_hold_2_days_small_gain(self):
        """持仓 2 天小涨 → 持有。"""
        trade: dict = {}
        result = apply_stop_loss(trade, current_price=10.3, entry_price=10.0, days_held=2)
        assert result["action"] == "hold"

    def test_signal_day_volume_fallback(self):
        """使用 signal_day_volume 作为 fallback。"""
        trade: dict = {
            "signal_day_volume": 400_000,
            "current_volume": 700_000,
        }
        result = apply_stop_loss(trade, current_price=10.08, entry_price=10.0, days_held=1)
        # 涨幅 0.8% < 2%, 但量比 1.75 > 1.5
        assert result["action"] == "exit"

    def test_volume_not_high_enough(self):
        """量能不足以触发放量滞涨。"""
        trade: dict = {
            "daily_volume": 600_000,
            "signal_day_volume": 500_000,
        }
        # 量比 1.2 < 1.5
        result = apply_stop_loss(trade, current_price=10.08, entry_price=10.0, days_held=1)
        assert result["action"] == "hold"


if __name__ == "__main__":
    pytest.main([__file__])
