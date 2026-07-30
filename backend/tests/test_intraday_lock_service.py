"""intraday_lock_service 影子锁利服务的 L1 单元测试。

用 SQLite 内存库建两张影子表 + mock pro(不碰真实生产库/真实Tushare)。
聚焦: 触及判定纯函数、lock_delta 计算、poll_once 触发与幂等。
"""
from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import Base, IntradayLockMonitor, IntradayLockSignal
from app.services.intraday_lock_service import (
    _first_hit_bar,
    _lock_delta_pct,
    _ts_code,
    poll_once,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        IntradayLockMonitor.__table__, IntradayLockSignal.__table__,
    ])
    return sessionmaker(bind=engine)()


def _add_monitor(db, monitor_id="ilm_t1", code="600519", entry_price=100.0, target_pct=5.0, status="monitoring"):
    from datetime import datetime
    now = datetime.utcnow()
    db.add(IntradayLockMonitor(
        monitor_id=monitor_id, recommendation_id="rec_t1", code=code,
        trade_date="2026-07-29", entry_date="2026-07-29", entry_price=entry_price,
        target_pct=target_pct, lock_ratio=0.5, status=status,
        created_at=now, updated_at=now,
    ))
    db.commit()


class _MockPro:
    """mock tushare pro,按 ts_code 返回预设 5min 数据。"""

    def __init__(self, frames):
        self._frames = frames  # {ts_code: DataFrame}

    def query(self, api_name, ts_code=None, freq=None, start_date=None, end_date=None):
        return self._frames.get(ts_code)


def _frame(rows):
    """rows: [(trade_time, open, high, low, close)]"""
    return pd.DataFrame(rows, columns=["trade_time", "open", "high", "low", "close"])


# ── 纯函数 ────────────────────────────────────────────────────────────────

def test_ts_code_shanghai_vs_shenzhen():
    assert _ts_code("600519") == "600519.SH"
    assert _ts_code("688981") == "688981.SH"
    assert _ts_code("000001") == "000001.SZ"
    assert _ts_code("300750") == "300750.SZ"


def test_first_hit_bar_returns_first_touch():
    # 服务 _fetch_minute_highs 返回 (trade_time, high, low) 3 元素
    bars = [
        ("2026-07-29 09:35:00", 102.0, 100.5),
        ("2026-07-29 09:40:00", 105.5, 101.8),  # 首个 high>=105
        ("2026-07-29 09:45:00", 107.0, 104.0),
    ]
    hit = _first_hit_bar(bars, 105.0)
    assert hit is not None
    assert hit[0] == "2026-07-29 09:40:00"
    assert hit[1] == 105.0


def test_first_hit_bar_none_when_never_touched():
    bars = [
        ("2026-07-29 09:35:00", 103.0, 99.5),
        ("2026-07-29 09:40:00", 104.0, 101.0),
    ]
    assert _first_hit_bar(bars, 105.0) is None


def test_lock_delta_sign():
    # 锁利价高于实际出场 → 正(锁利更优)
    assert _lock_delta_pct(105.0, 100.0, 100.0) == pytest.approx(5.0)
    # 锁利价低于实际出场 → 负(锁利吃亏,趋势走强)
    assert _lock_delta_pct(105.0, 110.0, 100.0) == pytest.approx(-5.0)


# ── poll_once 触发与幂等 ─────────────────────────────────────────────────

def test_poll_once_triggers_signal_on_touch():
    db = _session()
    _add_monitor(db, code="600519", entry_price=100.0, target_pct=5.0)
    # 5min 数据: 第二根 bar high 105.5 触及 target(100*1.05=105)
    pro = _MockPro({"600519.SH": _frame([
        ("2026-07-29 09:35:00", 100.0, 102.0, 99.5, 101.0),
        ("2026-07-29 09:40:00", 102.0, 105.5, 101.8, 104.0),
    ])})
    stats = poll_once(db, pro, now_str="2026-07-29 15:00:00")
    assert stats["triggered"] == 1
    # 信号已落库
    sig = db.query(IntradayLockSignal).filter_by(monitor_id="ilm_t1").one()
    assert sig.trigger_price == pytest.approx(105.0)
    assert sig.trigger_time == "2026-07-29 09:40:00"
    assert sig.status == "open"
    # 监控状态翻为 triggered
    mon = db.query(IntradayLockMonitor).filter_by(monitor_id="ilm_t1").one()
    assert mon.status == "triggered"


def test_poll_once_no_signal_when_not_touched():
    db = _session()
    _add_monitor(db, code="600519", entry_price=100.0, target_pct=5.0)
    pro = _MockPro({"600519.SH": _frame([
        ("2026-07-29 09:35:00", 100.0, 103.0, 99.5, 101.0),
    ])})
    stats = poll_once(db, pro, now_str="2026-07-29 15:00:00")
    assert stats["triggered"] == 0
    assert db.query(IntradayLockSignal).count() == 0


def test_poll_once_idempotent_no_duplicate_signal():
    db = _session()
    _add_monitor(db, code="600519", entry_price=100.0, target_pct=5.0)
    pro = _MockPro({"600519.SH": _frame([
        ("2026-07-29 09:40:00", 102.0, 105.5, 101.8, 104.0),
    ])})
    poll_once(db, pro, now_str="2026-07-29 15:00:00")
    # 监控已 triggered,第二次 poll 不再检查(状态非 monitoring),不重复记
    stats2 = poll_once(db, pro, now_str="2026-07-29 15:00:00")
    assert stats2["triggered"] == 0
    assert db.query(IntradayLockSignal).filter_by(monitor_id="ilm_t1").count() == 1
