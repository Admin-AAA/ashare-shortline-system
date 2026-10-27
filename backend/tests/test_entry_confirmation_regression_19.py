"""回归：10-14 以来单策略实盘 17 只 + 诊断表里的 2 只组合单（共 19 只），用入场日真实日 K 跑新的入场日检查。

旧逻辑：17 只单策略全部被取消/过期（跌破推荐止损 5、require_setup 重判 4、硬风控 2、盘中没被检查过期 6）。
新逻辑（与回测买入规则一致）：19 只入场日都不是一字涨停、可成交、开盘相对推荐价都没超过 +6%——
回测会在推荐后第 1 个交易日按开盘价买入，盘中确认也应确认买入。没有一只会被入场日检查拦下。
数据为 daily_kbars 入场日（推荐后首个交易日）的 open/high/low/close 与前收。
"""
import json
from types import SimpleNamespace

import pytest

from app.services import outcome_simulation as osim
from app.services.entry_day_checks import check_entry_bar, entry_window_days
from app.services.execution_confirmation import _derive_decisions

#   信号日,      代码,     名称,       推荐价, (open, high, low, close, pre_close)
CASES = [
    ("2026-09-23", "002897", "意华股份", 67.85, (67.54, 67.98, 65.71, 65.71, 67.85)),
    ("2026-10-08", "603978", "深圳新星", 26.25, (26.78, 26.89, 24.61, 25.19, 26.25)),
    ("2026-10-14", "300444", "双杰电气", 8.87, (8.6, 8.6, 8.06, 8.06, 8.87)),
    ("2026-10-14", "603626", "科森科技", 21.97, (21.22, 21.68, 20.02, 20.22, 21.97)),
    ("2026-10-14", "688099", "晶晨股份", 93.04, (89.5, 92.6, 89.1, 91.15, 93.04)),
    ("2026-10-14", "688578", "艾力斯", 81.8, (80.8, 84.88, 80.45, 84.46, 81.8)),
    ("2026-10-14", "688652", "京仪装备", 137.91, (131.07, 149.75, 131.07, 149.74, 137.91)),
    ("2026-10-15", "001225", "和泰机电", 38.5, (37.36, 37.36, 34.65, 34.65, 38.5)),
    ("2026-10-15", "001301", "尚太科技", 49.71, (49.9, 49.98, 46.91, 47.34, 49.71)),
    ("2026-10-15", "301269", "华大九天", 98.65, (99.23, 100.65, 97.95, 98.35, 98.65)),
    ("2026-10-15", "688578", "艾力斯", 84.46, (85.2, 85.63, 80.92, 81.79, 84.46)),
    ("2026-10-19", "000893", "亚钾国际", 36.22, (35.4, 37.0, 34.66, 36.83, 36.22)),
    ("2026-10-19", "002379", "宏桥控股", 14.66, (14.5, 14.58, 14.15, 14.3, 14.66)),
    ("2026-10-19", "002850", "科达利", 147.13, (146.35, 157.92, 145.05, 152.0, 147.13)),
    ("2026-10-20", "002600", "领益智造", 11.15, (10.99, 11.07, 10.8, 10.85, 11.15)),
    ("2026-10-20", "300969", "恒帅股份", 68.34, (68.18, 68.96, 66.88, 67.36, 68.34)),
    ("2026-10-21", "301127", "武汉天源", 14.34, (14.25, 14.44, 13.67, 13.67, 14.34)),
    ("2026-10-23", "300969", "恒帅股份", 71.4, (70.69, 70.95, 66.4, 67.56, 71.4)),
    ("2026-10-23", "603501", "豪威集团", 82.2, (81.5, 85.62, 81.31, 84.65, 82.2)),
]


@pytest.mark.parametrize("signal_date,code,name,rec_price,ohlc", CASES, ids=[f"{c[0][5:]}-{c[1]}" for c in CASES])
def test_regression_entry_day_confirms_buy(signal_date, code, name, rec_price, ohlc):
    o, h, l, c, pre = ohlc
    bar = {"open": o, "high": h, "low": l, "close": c, "pre_close": pre, "volume": 1e5, "amount": 1e8}
    check = check_entry_bar(code, bar, rec_price, session_closed=False)
    assert check.blocked is False, f"{name} 被入场日检查拦下：{check.reason}"
    # 入场日盘中重跑的策略信号即使硬否决 / prefilter 不过，也不影响买入确认
    entry, _ = _derive_decisions(
        fresh=True, signal_age_trade_days=1, entry_window_closed=False, prefilter_ok=False,
        signal={"hard_veto": True, "veto_reason": "未命中任何入场 setup（策略要求 require_setup）",
                "score": 70.0, "setup_match": ""},
        review=None, current_price=c, stop_loss=round(rec_price * 0.945, 2), entry_check=check,
    )
    assert entry["code"] == "buy"


def test_regression_count_is_nineteen():
    assert len(CASES) == 19
    assert set(STRATEGY) == {(c[0], c[1]) for c in CASES}


# 推荐的封存字段（生产库 recommendations 只读核对）：策略、entry_mode、理想/次级买点
STRATEGY = {
    ("2026-09-23", "002897"): ("chao-gu-yang-jia-skill", "next_open", 69.36, 65.81),
    ("2026-10-08", "603978"): ("daily-stock-analysis-skill", "next_open", 25.86, 25.46),
    ("2026-10-14", "300444"): ("chao-gu-yang-jia-skill", "next_open", 8.86, 8.86),
    ("2026-10-14", "603626"): ("chao-gu-yang-jia-skill", "next_open", 23.23, 21.94),
    ("2026-10-14", "688099"): ("sector-relative-strength-leader", "target_touch", 93.69, 90.48),
    ("2026-10-14", "688578"): ("oversold-capital-reversal", "next_open", 97.96, 97.96),
    ("2026-10-14", "688652"): ("sector-relative-strength-leader", "target_touch", 136.88, 136.88),
    ("2026-10-15", "001225"): ("oversold-capital-reversal", "next_open", 40.05, 40.05),
    ("2026-10-15", "001301"): ("chen-xiaoqun-skill", "next_open", 49.31, 49.31),
    ("2026-10-15", "301269"): ("sector-relative-strength-leader", "target_touch", 97.17, 95.69),
    ("2026-10-15", "688578"): ("oversold-capital-reversal", "next_open", 96.96, 96.96),
    ("2026-10-19", "000893"): ("oversold-capital-reversal", "next_open", 37.35, 37.35),
    ("2026-10-19", "002379"): ("oversold-capital-reversal", "next_open", 16.32, 16.32),
    ("2026-10-19", "002850"): ("oversold-capital-reversal", "next_open", 160.05, 160.05),
    ("2026-10-20", "002600"): ("oversold-capital-reversal", "next_open", 11.61, 11.61),
    ("2026-10-20", "300969"): ("oversold-capital-reversal", "next_open", 73.09, 73.09),
    ("2026-10-21", "301127"): ("oversold-capital-reversal", "next_open", 15.52, 15.52),
    ("2026-10-23", "300969"): ("oversold-capital-reversal", "next_open", 71.5, 71.5),
    ("2026-10-23", "603501"): ("sector-relative-strength-leader", "target_touch", 80.97, 79.73),
}
# 推荐日 → 推荐后第 1 个交易日（交易所日历）
ENTRY_DAY = {"2026-09-23": "2026-09-24", "2026-10-08": "2026-10-09", "2026-10-14": "2026-10-15",
             "2026-10-15": "2026-10-16", "2026-10-19": "2026-10-20", "2026-10-20": "2026-10-21",
             "2026-10-21": "2026-10-22", "2026-10-23": "2026-10-26"}


@pytest.mark.parametrize("signal_date,code,name,rec_price,ohlc", CASES, ids=[f"{c[0][5:]}-{c[1]}" for c in CASES])
def test_regression_confirmed_buy_matches_settlement_fill(signal_date, code, name, rec_price, ohlc):
    """确认买入日/价 必须等于 结算成交日/价（结算 _resolve_entry 按这只推荐自己的 entry_mode / 策略）。"""
    bucket, entry_mode, ideal, secondary = STRATEGY[(signal_date, code)]
    o, h, l, c, pre = ohlc
    day1 = ENTRY_DAY[signal_date]
    # 确认：第 1 日入场检查通过 → 按回测口径开盘买入，确认日 = 第 1 日，确认价 = 开盘价
    check = check_entry_bar(code, {"open": o, "high": h, "low": l, "close": c, "pre_close": pre,
                                   "volume": 1e5, "amount": 1e8}, rec_price, session_closed=True)
    assert not check.blocked
    confirmed = (day1, o)
    # 结算：同一根第 1 日 K（再给一根第 2 日 K 让 2 天窗口完整）
    snapshot = {"entry_mode": entry_mode, "ideal_buy": ideal, "secondary_buy": secondary}
    rec = SimpleNamespace(code=code, name=name, trade_date=signal_date, recommend_price=rec_price, bucket=bucket,
                          recommended_at=None, feature_snapshot_json=json.dumps(snapshot))
    bar1 = SimpleNamespace(code=code, trade_date=day1, open=o, high=h, low=l, close=c,
                           volume=1e5, amount=1e8, pct_chg=round((c / pre - 1) * 100, 4))
    bar2 = SimpleNamespace(code=code, trade_date="2099-01-01", open=c, high=c, low=c * 0.99, close=c,
                           volume=1e5, amount=1e8, pct_chg=0.0)
    entry = osim._resolve_entry(rec, snapshot, [bar1, bar2], 5)
    assert entry["triggered"], f"{name} 结算没成交：{entry['reason']}"
    assert (entry["date"], entry["price"]) == confirmed, f"{name} 确认 {confirmed} ≠ 结算 {(entry['date'], entry['price'])}"
    assert entry_window_days(entry_mode, bucket) in (1, 2)


@pytest.mark.parametrize("signal_date,code,name,rec_price,ohlc", CASES, ids=[f"{c[0][5:]}-{c[1]}" for c in CASES])
def test_regression_settlement_stop_line_matches_backtest(signal_date, code, name, rec_price, ohlc):
    """结算止损线 = 回测止损线：都按买入成交价（含滑点）往下 5.5%（同一根第 1 日 K 买入）。"""
    import app.services.backtest.policy_experiment as PE
    from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY
    from app.engine.simulation import BarInput

    bucket, entry_mode, ideal, secondary = STRATEGY[(signal_date, code)]
    o, h, l, c, pre = ohlc
    day1 = ENTRY_DAY[signal_date]
    snapshot = {"entry_mode": entry_mode, "ideal_buy": ideal, "secondary_buy": secondary,
                "stop_loss": round(rec_price * 0.943, 2)}  # 快照止损比回测宽：新口径下不再起作用
    rec = SimpleNamespace(recommendation_id=f"r-{code}", code=code, name=name, trade_date=signal_date,
                          recommend_price=rec_price, bucket=bucket, recommended_at=None, run_id="x",
                          feature_snapshot_json=json.dumps(snapshot))
    bar1 = SimpleNamespace(code=code, trade_date=day1, open=o, high=h, low=l, close=c,
                           volume=1e5, amount=1e8, pct_chg=round((c / pre - 1) * 100, 4))
    bar2 = SimpleNamespace(code=code, trade_date="2099-01-01", open=c, high=c, low=c, close=c,
                           volume=1e5, amount=1e8, pct_chg=0.0)
    settle = osim._simulate_outcome(rec, [bar1, bar2], [day1, "2099-01-01"], horizon_days=5)

    day = SimpleNamespace(signal_date=signal_date, outcome_bars={code: tuple(
        BarInput(code=code, trade_date=b.trade_date, open=b.open, high=b.high, low=b.low, close=b.close,
                 volume=b.volume, tradable=True) for b in (bar1, bar2))})
    trade = PE._simulate_card(day, {**snapshot, "code": code, "recommend_price": rec_price, "bucket": bucket},
                              DEFAULT_EXECUTION_POLICY)["trade"]
    assert trade.executed and str(trade.entry_date) == settle["entry_date"] == day1
    bt_stop = float(trade.entry_price) * (1 - DEFAULT_EXECUTION_POLICY.stop_loss_pct / 100)
    assert settle["stop_loss_price"] == round(bt_stop, 2), f"{name} 结算止损 {settle['stop_loss_price']} ≠ 回测 {bt_stop:.4f}"
