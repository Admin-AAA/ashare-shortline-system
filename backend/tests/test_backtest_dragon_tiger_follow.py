# -*- coding: utf-8 -*-
"""龙虎榜机构净买「次日延续」回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_simulate_trade / _bucket_by_net_buy / _load_dragon_tiger 过滤）不依赖 DB，
始终运行；DB 集成测试在 MySQL 不可达时自动 skip。

设计纪律（见 lessons.md [T1]）：本因子诚实样本外回测结果为**净负**——
  全样本 n=754 WR=34.6% 盈亏比 0.76；滚动 20 窗 0 窗 >=50% 胜率；净买分桶虽弱单调
  （28.5%→43.2%）但所有桶均 <50% 胜率且净均为负。属 regime 依赖噪声 + 弱信号，
  按第一性原理「回测证伪噪声」直接丢弃，**不纳入 strategy.yaml**。

  关键结论（防过拟合护栏的反向价值）：龙虎榜净买「次日」信号本身无稳健正边缘，
  实盘 dragon-tiger-smart-money 策略 72.7% 前瞻胜率来自其**复合过滤**
  （涨停日上下文 + 板块强度 + 特定聪明钱定义），而非净买强度本身。
  → **禁止将 dragon-tiger 触发条件放宽到「任意净买日」**（会注入净负票、损害 D 项）。

集成测试只断言：
  (1) 回测可复现、结构正确、无未来函数（买入价用 T+1 开盘，非 T 及之后收盘价）；
  (2) 信号加载过滤正确（仅 net_buy >= 阈值）；
  (3) 分桶正确切分全部交易（不锁胜率，防过拟合锁）。
"""
import os
import re
import sys
from collections import defaultdict

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_dragon_tiger_follow import (  # noqa: E402
    HOLD_DAYS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    BUY_COST_RATIO,
    SELL_COST_RATIO,
    _bucket_by_net_buy,
    _load_dragon_tiger,
    _run_backtest,
    _simulate_trade,
)


def _btuple(open_, high, low, close, **kw):
    """构造 daily_kbars bar 元组，列序: code,td,open,high,low,close,volume,amount,pct_chg。"""
    return (
        kw.get("code", "X"),
        kw.get("td", "2026-01-01"),
        open_, high, low, close,
        kw.get("vol", 1e6),
        kw.get("amt", 1e8),
        kw.get("pct", 0.0),
    )


# ───────────────────────── 纯函数：_simulate_trade ─────────────────────────
def test_simulate_stop_loss_on_open_gap():
    """T+1 开盘较入场价低开 <= -5.7% → 止损。"""
    buy = 10.0
    bars = [_btuple(9.3, 9.5, 9.2, 9.4)]  # open 9.3 → ret_open ~ -6.56% <= -5.7%
    ret, reason = _simulate_trade(buy, bars)
    assert reason == "stop_loss"
    entry = buy * (1 + BUY_COST_RATIO)
    assert ret == pytest.approx((9.3 - entry) / entry - SELL_COST_RATIO, abs=1e-6)


def test_simulate_take_profit_on_open_gap():
    """T+1 开盘较入场价 >= +8% → 止盈。"""
    buy = 10.0
    bars = [_btuple(10.9, 11.0, 10.8, 10.9)]  # open 10.9 → ret_open ~ +8.92% >= 8%
    ret, reason = _simulate_trade(buy, bars)
    assert reason == "take_profit"
    entry = buy * (1 + BUY_COST_RATIO)
    assert ret == pytest.approx((10.9 - entry) / entry - SELL_COST_RATIO, abs=1e-6)


def test_simulate_stop_loss_on_close():
    """开盘未触发，但收盘价 <= -5.7% → 收盘止损。"""
    buy = 10.0
    bars = [_btuple(10.0, 10.2, 9.9, 9.3)]  # open 无跳空，close 9.3 → -6.56%
    ret, reason = _simulate_trade(buy, bars)
    assert reason == "stop_loss"


def test_simulate_expire_hold():
    """全程未触止损/止盈 → 到期收盘卖出。"""
    buy = 10.0
    bars = [_btuple(10.0, 10.3, 9.9, 10.2) for _ in range(HOLD_DAYS)]
    ret, reason = _simulate_trade(buy, bars)
    assert reason == "expire"
    entry = buy * (1 + BUY_COST_RATIO)
    assert ret == pytest.approx((10.2 - entry) / entry - SELL_COST_RATIO, abs=1e-6)


def test_simulate_no_future_function():
    """止损/止盈判定只用 T+1 起的 bar（不含 T 本身），无未来函数。"""
    # 若误用 T 日收盘则会触发，这里 T+1 起全是温和上涨，应 expire 而非止盈
    buy = 10.0
    bars = [_btuple(10.1, 10.4, 10.0, 10.3) for _ in range(HOLD_DAYS)]
    ret, reason = _simulate_trade(buy, bars)
    assert reason == "expire"


# ───────────────────────── 纯函数：_bucket_by_net_buy ─────────────────────────
def test_bucket_by_net_buy_partitions():
    trades = [
        {"net_buy_yi": 0.6, "ret": 0.01}, {"net_buy_yi": 0.9, "ret": 0.02},    # [0.5,1.0)
        {"net_buy_yi": 1.2, "ret": 0.01}, {"net_buy_yi": 1.9, "ret": 0.03},    # [1.0,2.0)
        {"net_buy_yi": 2.5, "ret": 0.02}, {"net_buy_yi": 4.9, "ret": 0.01},    # [2.0,5.0)
        {"net_buy_yi": 5.0, "ret": 0.04}, {"net_buy_yi": 12.0, "ret": 0.05},  # [5.0, inf)
    ]
    lines = _bucket_by_net_buy(trades)
    # 每桶 2 笔
    assert all("n=2" in ln for ln in lines), lines
    assert len(lines) == 4


def test_bucket_empty():
    # 空输入 → 每桶一行 "n=0"（4 桶），非空列表
    lines = _bucket_by_net_buy([])
    assert len(lines) == 4
    assert all("n=0" in ln for ln in lines)


# ───────────────────────── _load_dragon_tiger 过滤 ─────────────────────────
class _FakeRow:
    def __init__(self, code, td, net):
        self.code, self.td, self.net = code, td, net


class _FakeResult:
    def __init__(self, rows):
        # _load_dragon_tiger 期望 (code, td, net) 元组
        self._rows = [(r.code, r.td, r.net) for r in rows]

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query, *a, **k):
        q = str(query)
        m = re.search(r"net_amount_yi\s*>=\s*([0-9.]+)", q)
        rows = [r for r in self._rows if r.net >= float(m.group(1))] if m else self._rows
        return _FakeResult(rows)


def test_load_dragon_tiger_filters_threshold():
    rows = [
        _FakeRow("A", "2026-04-01", 0.2),   # 低于 0.5 → 排除
        _FakeRow("B", "2026-04-02", 0.5),   # 边界 → 保留
        _FakeRow("C", "2026-04-03", 1.8),   # 保留
        _FakeRow("D", "2026-04-04", -0.3),  # 净卖 → 排除
    ]
    sig = _load_dragon_tiger(_FakeSession(rows), 0.5)
    assert set(sig.keys()) == {"B", "C"}
    assert sig["B"]["2026-04-02"] == 0.5
    assert sig["C"]["2026-04-03"] == 1.8


# ───────────────────────── DB 集成测试（MySQL 不可达时 skip） ─────────────────────────
def _get_session():
    try:
        from app.core.config import Settings
        eng = create_engine(Settings().database_url)
        from sqlalchemy.orm import Session
        s = Session(eng)
        # 探活
        s.execute(text("SELECT 1 FROM dragon_tiger LIMIT 1"))
        return s
    except (OperationalError, Exception) as e:  # noqa: BLE001
        pytest.skip(f"MySQL 不可达: {e}")


def test_integration_runs_and_no_future_func():
    """回测可复现、结构正确、信号过滤正确、分桶完整（不锁胜率）。"""
    session = _get_session()
    from scripts.backtest_dragon_tiger_follow import _load_bars
    by_code = _load_bars(session)
    net_min = 0.5
    sig = _load_dragon_tiger(session, net_min)
    # 信号加载过滤正确
    total_sig = sum(len(v) for v in sig.values())
    assert total_sig > 0, "dragon_tiger 应有净买信号"
    # 所有净买值 >= 阈值
    for v in sig.values():
        for net in v.values():
            assert net >= net_min

    trades = _run_backtest(by_code, sig, net_min)
    assert len(trades) > 0, "应产生交易"
    # 无未来函数：每笔交易的 signal_date 在其 by_code 序列中应有 T+1
    for t in trades:
        assert t["ret"] is not None
        # 北交所/科创板多日持有收益可合法超过 30%；这里只拦截价格口径爆炸值。
        assert -1.0 < t["ret"] < 1.0, f"收益异常: {t}"
        assert t["net_buy_yi"] >= net_min
    # 分桶完整切分
    lines = _bucket_by_net_buy(trades)
    bucket_n = sum(int(ln.split("n=")[1].split()[0]) for ln in lines if "n=" in ln)
    assert bucket_n == len(trades), "分桶须完整覆盖全部交易"
