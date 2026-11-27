"""中线组合视图（overlap_portfolio）：槽位资金模型、去重分法、成本换算、基准窗口、敞口与出票层明细。不连库、不联网。"""
import datetime as dt

import pytest

from app.services import overlap_portfolio as op


def _calendar(n: int, start: str = "2026-01-05") -> list[str]:
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _pick(cal, i, code, sid, net, *, hold=20, status="closed", name=None):
    """信号日 cal[i]、T+1 入场、持有 hold 个交易日卖出；net 为扣 0.07% 后的目标净收益（结算 ret 按结算成本回推，加回量取模块常量）。"""
    return {"signal_date": cal[i], "code": code, "name": name or code, "strategy_id": sid, "strategy_name": f"策略{sid}",
            "status": status, "entry_date": cal[i + 1], "exit_date": cal[i + 1 + hold] if status == "closed" else None,
            "ret": None if net is None else round(net - op._COST_ADDBACK, 6), "ret_includes_cost": status == "closed", "hold_days": hold,
            "origin": "replay"}


def _flat(cal):
    return {d: 0.0 for d in cal}


def test_steady_state_does_not_inflate_multi_day_returns():
    """L-203：每天一批、每笔 20 日赚 2%，稳态下组合日收益就是 1.02^(1/20)-1，全期 ≈ 批数 × 2% / 20，不会被放大 20 倍。"""
    cal = _calendar(120)
    rows = [_pick(cal, i, f"S{i}", "s1", 2.0) for i in range(60)]
    res = op.build(rows, cal, _flat(cal))
    d = 1.02 ** (1 / 20) - 1
    n = 79  # 第 1 批首个持有日到第 60 批卖出日
    expected = 1.0
    for t in range(n):
        covering = sum(1 for i in range(60) if i <= t <= i + 19)
        expected *= 1 + covering * d / 20
    assert res["slots"] == 20 and res["signal_days"] == 60 and res["eligible_picks"] == 60
    assert res["A"]["net_ret"] == pytest.approx(round((expected - 1) * 100, 2))
    assert 5.5 < res["A"]["net_ret"] < 6.5
    assert res["B"]["net_ret"] == res["A"]["net_ret"]  # 无重叠时两种分法一样
    # 滚动期（第 20 个持有日起到最后一批入场）满仓 20 批、每批 1 笔。
    assert res["rolling_window"] == {"start": cal[21], "end": cal[61], "days": 41, "ramped": True}
    assert res["A"]["avg_invested_pct"] == 100.0 and res["A"]["avg_positions"] == 20.0
    assert res["A"]["max_single_weight"] == 5.0 and res["A"]["avg_effective_n"] == 20.0
    # 市场不动时基准只剩成本：每笔 -0.07%，60 批 / 20 槽 ≈ -0.21%。
    assert res["A"]["bench_ret"] == pytest.approx(-0.21, abs=0.01)
    assert len(res["curves"]["dates"]) == n and len(res["curves"]["A"]) == n
    assert set(res["curves"]) == {"dates", "A", "B", "bench"} and len(res["curves"]["bench"]) == n
    assert res["curves"]["bench"][-1] == pytest.approx(res["A"]["bench_ret"], abs=0.01)


def test_sparse_signals_keep_empty_slots_in_cash_and_run_to_last_exit():
    """L-203 2026-11-27 修订：信号稀疏时空槽是现金，曲线走到最后一笔卖出。

    每 40 个交易日才一批、每批 20 日赚 2%：每批只占 1/20 资金，全期 ≈ 3 × 2% / 20 ≈ +0.3%。
    旧 portfolio_metrics 按「当天覆盖的批数」均分资金，会把每个孤立批次当成满仓（≈ +6%），
    并且时间轴止于最后一个信号日，最后一批的持有期被截掉。"""
    cal = _calendar(140)
    rows = [_pick(cal, i, f"S{i}", "s1", 2.0) for i in (0, 40, 80)]
    res = op.build(rows, cal, _flat(cal))
    d = 1.02 ** (1 / 20) - 1
    assert res["slots"] == 20 and res["signal_days"] == 3
    assert res["A"]["net_ret"] == pytest.approx(round(((1 + d / 20) ** 60 - 1) * 100, 2))
    assert res["A"]["net_ret"] < 0.4
    assert res["curves"]["dates"][0] == cal[2] and res["curves"]["dates"][-1] == cal[80 + 1 + 20]


def test_overlap_weighting_dedup_split_and_exclusions():
    """同一信号日 X 被 2 个策略推、Y 被 1 个：A 每笔一份（X 占 2/3），B 每票一份（X 两笔各 1/4）。"""
    cal = _calendar(40)
    rows = [
        _pick(cal, 0, "X", "s1", 10.0), _pick(cal, 0, "X", "s2", 10.0), _pick(cal, 0, "Y", "s1", -2.0),
        _pick(cal, 0, "Z", "s3", None, status="holding"),
        {**_pick(cal, 5, "W", "s1", 1.0), "exit_date": "2027-01-01"},  # 卖出日不在基准日历里：排除并计数
    ]
    res = op.build(rows, cal, _flat(cal))
    assert res["eligible_picks"] == 3 and res["eligible_signals"] == 2 and res["excluded_closed"] == 1
    assert res["cost_pct"] == 0.07
    pt = res["per_trade"]
    assert pt["all"] == {"n": 3, "net_ret": 6.0, "bench_ret": -0.07, "excess_ret": 6.07}
    assert pt["dedup"] == {"n": 2, "net_ret": 4.0, "bench_ret": -0.07, "excess_ret": 4.07}
    dx, dy = 1.10 ** (1 / 20) - 1, 0.98 ** (1 / 20) - 1
    a = (1 + (2 / 3 * dx + 1 / 3 * dy) / 20) ** 20 - 1
    b = (1 + (1 / 2 * dx + 1 / 2 * dy) / 20) ** 20 - 1
    assert res["A"]["net_ret"] == round(a * 100, 2) and res["B"]["net_ret"] == round(b * 100, 2)
    assert res["A"]["net_ret"] > res["B"]["net_ret"]
    # 只有一批：跨度不足 20 个交易日，滚动期退化为这一批的第一个持有日（含建仓爬坡，ramped=False）；
    # A 在手 3 笔、B 2 份，都是 2 只票；单票最大 = 批内占比 / 20 槽。
    assert res["rolling_window"] == {"start": cal[2], "end": cal[2], "days": 1, "ramped": False}
    assert res["A"]["avg_positions"] == 3.0 and res["B"]["avg_positions"] == 2.0
    assert res["A"]["avg_stocks"] == res["B"]["avg_stocks"] == 2.0
    assert res["A"]["max_single_weight"] == 3.33 and res["B"]["max_single_weight"] == 2.5
    assert res["A"]["max_single_weight_code"] == "X" and res["A"]["avg_invested_pct"] == 5.0
    # 出票层明细：按出票算（持有中的 Z 也算一票），只列 ≥2 个策略同推的票。
    w_day, x_day = res["recent_days"]
    assert w_day["signal_date"] == cal[5] and w_day["shared"] == []
    assert x_day == {"signal_date": cal[0], "picks": 4, "stocks": 3, "shared": [
        {"code": "X", "name": "X", "overlap": 2, "strategies": ["策略s1", "策略s2"], "weight_a_pct": 50.0,
         "weight_b_pct": 33.33, "status": "closed"}]}


def test_benchmark_window_and_cost_conversion():
    """基准窗口 = 入场日之后到卖出日（不含入场当天）；结算未扣成本的笔按 -0.07% 换算。"""
    cal = _calendar(30)
    pct = _flat(cal)
    pct[cal[1]] = 1.0   # 入场当天：不计
    pct[cal[21]] = 2.0  # 卖出当天：计入
    rows = [_pick(cal, 0, "X", "s1", 3.0), {**_pick(cal, 0, "Y", "s2", 0.0), "ret": 1.01, "ret_includes_cost": None}]
    res = op.build(rows, cal, pct)
    assert res["per_trade"]["all"]["bench_ret"] == 1.93
    assert res["per_trade"]["all"]["net_ret"] == 1.97  # (3.0 + (1.01 - 0.07)) / 2


def test_monthly_rows_chain_to_full_period():
    cal = _calendar(80, start="2026-01-20")
    rows = [_pick(cal, i, f"S{i}", "s1", 1.0 + (i % 3)) for i in range(30)]
    pct = {d: (0.3 if i % 2 else -0.2) for i, d in enumerate(cal)}
    res = op.build(rows, cal, pct)
    months = res["A"]["by_month"]
    assert [m["month"] for m in months] == sorted({d[:7] for d in res["curves"]["dates"]}, reverse=True)
    assert sum(m["days"] for m in months) == len(res["curves"]["dates"])
    chained = 1.0
    for m in months:
        chained *= 1 + m["net_ret"] / 100
    assert (chained - 1) * 100 == pytest.approx(res["A"]["net_ret"], abs=0.05)
    assert res["A"]["max_drawdown"] <= 0 and res["A"]["excess_ret"] == pytest.approx(
        res["A"]["net_ret"] - res["A"]["bench_ret"], abs=0.2)


def test_no_closed_picks_returns_none():
    cal = _calendar(10)
    assert op.build([_pick(cal, 0, "X", "s1", None, status="holding")], cal, _flat(cal)) is None
    assert op.build([], cal, _flat(cal)) is None
