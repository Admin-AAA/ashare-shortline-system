"""模拟「机器刚开机」：time.monotonic() 是开机至今秒数，刚开机时很小（这里取 5 秒）。

以前这些模块的「上次做的时间」初值写 0.0，配合 ``monotonic() - last < 冷却`` 判断，
开机后头一个冷却期（5~10 分钟）里第一次就被当成「刚做过」拦掉：
交易日历首次补数被跳过并误报「最近一次同步失败」、outcome 回填/告警过期/日历告警解除被推迟、
akshare 快照头 20 秒报限频。

要测的是模块「刚导入」时的初值，所以放在子进程里：先把 monotonic 改小再导入，
不受本进程其它用例改过的模块状态影响。
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent(
    """
    import time
    time.monotonic = lambda: 5.0  # 开机 5 秒

    from datetime import date
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core import alerts
    from app.models.market import TradeCalendar
    from app.services import entry_window_state as ews
    from app.services import trade_calendar as tc
    from app.services import workbench_service as wb
    from app.services.datasource import akshare_quote as aq

    # 1. 交易日历：本地没覆盖 → 第一次必须真的去同步，不能报「最近一次同步失败」
    engine = create_engine("sqlite:///:memory:")
    TradeCalendar.__table__.create(engine)
    db = Session(engine)
    day = date(2026, 11, 26)
    synced = []

    def fake_sync(s, pro_factory=None):
        synced.append(1)
        s.add(TradeCalendar(exchange=tc.EXCHANGE, cal_date=day.strftime("%Y%m%d"), is_open=True))
        s.commit()
        return 1

    tc.in_trading_session = lambda *a, **k: False
    tc.sync_trade_calendar = fake_sync
    tc._ensure(db, day, None)
    assert synced == [1], synced

    # 2. outcome 回填：开机后第一次请求就该跑
    labelled = []
    wb.label_pending_outcomes_service = lambda _db, horizon_days=5: labelled.append(horizon_days)
    wb._auto_backfill_outcomes(MagicMock())
    assert labelled == [5], labelled

    # 3. 日历告警解除：第一次就该去解除
    resolved = []
    alerts.resolve_alert = lambda kind, key, note="": resolved.append(kind)
    ews._resolve_calendar_gap()
    assert resolved == [ews.CALENDAR_GAP_ALERT_KIND], resolved

    # 4. 告警过期：第一次就该检查（通过节流后会把时间戳记成现在）
    alerts._load_registry = lambda: {}
    alerts.expire_stale_alerts()
    assert alerts._last_expire_at == 5.0, alerts._last_expire_at

    # 5. akshare 快照：从没翻过页，不能被最短间隔拦住
    assert 5.0 - aq._STATE["last_start"] >= aq.MIN_INTERVAL
    aq.reset_for_tests()
    assert 5.0 - aq._STATE["last_start"] >= aq.MIN_INTERVAL
    print("FRESH_BOOT_OK")
    """
)


def test_cooldowns_do_not_block_first_run_right_after_boot():
    env = {**os.environ, "ASHARE_DATABASE_URL": "sqlite://", "ASHARE_UNDER_PYTEST": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0 and "FRESH_BOOT_OK" in proc.stdout, proc.stdout[-2000:] + proc.stderr[-4000:]
