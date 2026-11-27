"""中线组合视图：同一信号日同一只票被多个策略推荐时，账户层面实际拿了多少敞口、收益和回撤怎样（L-224）。

只做度量，不改任何策略的出票和结算。输入是 horizon_analytics.trades 的平铺行（每个策略的每一笔）。

资金模型（L-203 的槽位滚动）：资金等分 N 份（N = 设计持有期，现役中线都是 20 日），每个信号日用 1 份买入当天的票，
持有期走完这份收回再用；没有信号的交易日那份留现金，批内已卖出的部分也是现金。每笔收益在自己的持有窗口
（入场日之后到卖出日，与 EWALL.A 基准同一窗口）几何均摊成日收益，组合日收益 = Σ(批内占比 × 日收益) / N，按交易日复利。
这是系统里唯一的账户层面组合模型（L-203 2026-11-27 修订）。原 return_quality.portfolio_metrics 按「当天覆盖的批数」
均分资金、以信号日为时间轴并止于最后一个信号日，最后一批的持有期被截掉、少数批次被放大成满仓，已删除；
这里按交易日历走完全部持有期，空槽按现金计。

两种批内分法：
- A「跟全部策略买」：每笔一份。同一信号日同一只票被 k 个策略推，就买 k 份（重叠加权，即现有逐笔口径）。
- B「去重每票一份」：同一信号日同一只票只买一份；k 个策略同推时这一份平分给这 k 笔（各 1/k），各按自己的离场走。

基准：每笔换成同一窗口的 EWALL.A 复利（扣同样成本），按同样占比和排期组成「同排期基准」；超额 = 组合 − 基准（百分点）。
敞口：只统计滚动期（第 N 个交易日起到最后一批入场日，去掉首尾建仓/清仓爬坡），权重 = 占全部资金的比例，
同一只票在不同信号日的仓位合并算一只票；有效持仓数 = (Σw)² / Σw²。信号日跨度不足 N 个交易日时（筛选后样本很少），
滚动期从第一个持有日算起、含建仓爬坡，rolling_window.ramped 为 False。
成本：结算 ret 已扣 0.3%（market_benchmark.MIDTERM_ROUND_TRIP_COST_PCT），这里统一换成 0.07% 往返税费。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from app.engine.trading_costs import ADDITIONAL_COST_PCT
from app.services.market_benchmark import MIDTERM_ROUND_TRIP_COST_PCT

COST_PCT = ADDITIONAL_COST_PCT
# 结算 ret 扣的是 0.3%；换成 0.07% 往返税费要加回的百分点。
_COST_ADDBACK = MIDTERM_ROUND_TRIP_COST_PCT - ADDITIONAL_COST_PCT
# 出票层去重明细只回最近几个信号日；全期分布看诊断页「按同推策略数」分桶。
RECENT_SIGNAL_DAYS = 10
SCHEMES = ("A", "B")
_STATUS_ORDER = ("holding", "pending", "closed", "skipped")


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def _daily(total_pct: float, ticks: int) -> float:
    """持有 ticks 个交易日的总收益（%）几何均摊成日收益（小数）。亏完时记 -100%，避免负数开方。"""
    growth = 1 + total_pct / 100
    return growth ** (1 / ticks) - 1 if growth > 0 else -1.0


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _positions(rows: list[Mapping[str, Any]], idx: dict[str, int], pct: list[float]) -> tuple[list[dict[str, Any]], int]:
    """可进组合的笔：已平仓、有收益、入场日与卖出日都在 EWALL.A 交易日历里。返回 (仓位, 排除的已平仓笔数)。"""
    closed = [r for r in rows if r.get("status") == "closed" and r.get("ret") is not None]
    picked = []
    for r in closed:
        e = idx.get(str(r.get("entry_date") or "")[:10])
        x = idx.get(str(r.get("exit_date") or "")[:10])
        if e is not None and x is not None and x > e:
            picked.append((r, e, x))
    overlap = Counter((str(r["signal_date"]), str(r["code"])) for r, _, _ in picked)
    out = []
    for r, e, x in picked:
        ticks = x - e
        net = float(r["ret"]) + (_COST_ADDBACK if r.get("ret_includes_cost") else -COST_PCT)
        growth = 1.0
        for t in range(e + 1, x + 1):
            growth *= 1 + pct[t] / 100
        bench = (growth - 1) * 100 - COST_PCT
        key = (str(r["signal_date"]), str(r["code"]))
        out.append({"signal_date": key[0], "code": key[1], "start": e + 1, "end": x, "hold": int(r.get("hold_days") or ticks),
                    "net": net, "bench": bench, "k": overlap[key],
                    "daily": _daily(net, ticks), "bench_daily": _daily(bench, ticks)})
    return out, len(closed) - len(picked)


def _curve(daily: list[float], bench_daily: list[float], dates: list[str]) -> tuple[dict[str, Any], list[float], list[float]]:
    """逐日收益 → 全期/逐月（按持有日所在月）净收益、同排期基准、超额、最大回撤；另回两条累计收益（%）序列。"""
    eq = beq = peak = bpeak = 1.0
    mdd = bmdd = 0.0
    cum, bcum = [], []
    months: dict[str, list[int]] = defaultdict(list)
    for t, (x, b) in enumerate(zip(daily, bench_daily)):
        eq *= 1 + x
        beq *= 1 + b
        peak, bpeak = max(peak, eq), max(bpeak, beq)
        mdd, bmdd = min(mdd, eq / peak - 1), min(bmdd, beq / bpeak - 1)
        cum.append(round((eq - 1) * 100, 3))
        bcum.append(round((beq - 1) * 100, 3))
        months[dates[t][:7]].append(t)
    by_month = []
    for month, ts in sorted(months.items(), reverse=True):
        m_eq = m_beq = m_peak = 1.0
        m_mdd = 0.0
        for t in ts:
            m_eq *= 1 + daily[t]
            m_beq *= 1 + bench_daily[t]
            m_peak = max(m_peak, m_eq)
            m_mdd = min(m_mdd, m_eq / m_peak - 1)
        by_month.append({"month": month, "days": len(ts), "net_ret": _r((m_eq - 1) * 100),
                         "bench_ret": _r((m_beq - 1) * 100), "excess_ret": _r((m_eq - m_beq) * 100),
                         "max_drawdown": _r(m_mdd * 100)})
    summary = {"net_ret": _r((eq - 1) * 100), "bench_ret": _r((beq - 1) * 100), "excess_ret": _r((eq - beq) * 100),
               "max_drawdown": _r(mdd * 100), "bench_max_drawdown": _r(bmdd * 100), "by_month": by_month}
    return summary, cum, bcum


def _recent_days(rows: list[Mapping[str, Any]], days: int) -> list[dict[str, Any]]:
    """出票层去重明细：最近几个信号日的出票数、去重后票数，以及被 ≥2 个策略同推的票（哪些策略、A/B 两种分法下占当日出票的比例）。
    按出票算，不看后来是否买到。"""
    by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("signal_date") and r.get("code"):
            by_day[str(r["signal_date"])].append(r)
    out = []
    for day in sorted(by_day, reverse=True)[:days]:
        picks = by_day[day]
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in picks:
            groups[str(r["code"])].append(r)
        shared = []
        for code, ps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if len(ps) < 2:
                continue
            statuses = {str(p.get("status")) for p in ps}
            shared.append({"code": code, "name": ps[0].get("name"), "overlap": len(ps),
                           "strategies": sorted({str(p.get("strategy_name") or p.get("strategy_id")) for p in ps}),
                           "weight_a_pct": _r(len(ps) / len(picks) * 100), "weight_b_pct": _r(100 / len(groups)),
                           "status": next((s for s in _STATUS_ORDER if s in statuses), sorted(statuses)[0])})
        out.append({"signal_date": day, "picks": len(picks), "stocks": len(groups), "shared": shared})
    return out


def build(rows: Iterable[Mapping[str, Any]], calendar: list[str], pct_by_date: Mapping[str, float],
          *, recent_signal_days: int = RECENT_SIGNAL_DAYS) -> dict[str, Any] | None:
    """calendar 为 EWALL.A 升序交易日，pct_by_date 为当日等权涨跌幅（%）。没有可比的已平仓笔时返回 None。"""
    rows = list(rows)
    idx = {d: i for i, d in enumerate(calendar)}
    pct = [float(pct_by_date[d]) for d in calendar]
    positions, excluded = _positions(rows, idx, pct)
    if not positions:
        return None

    slots = max(p["hold"] for p in positions)
    batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in positions:
        batches[p["signal_date"]].append(p)
    for ps in batches.values():
        distinct = len({p["code"] for p in ps})
        for p in ps:
            # 该笔在本批（1/N 资金）里的占比：A 每笔一份；B 每只票一份，k 个策略同推时各 1/k。
            p["share"] = {"A": 1 / len(ps), "B": 1 / p["k"] / distinct}

    t0 = min(p["start"] for p in positions)
    n = max(p["end"] for p in positions) - t0 + 1
    dates = calendar[t0:t0 + n]
    active: list[list[dict[str, Any]]] = [[] for _ in range(n)]
    acc = {s: [0.0] * n for s in SCHEMES}
    bacc = {s: [0.0] * n for s in SCHEMES}
    for p in positions:
        for t in range(p["start"] - t0, p["end"] - t0 + 1):
            active[t].append(p)
            for s in SCHEMES:
                acc[s][t] += p["share"][s] * p["daily"]
                bacc[s][t] += p["share"][s] * p["bench_daily"]

    last_start = max(min(p["start"] for p in ps) for ps in batches.values()) - t0
    ramped = slots - 1 <= last_start
    rolling = range(slots - 1 if ramped else 0, last_start + 1)
    # 曲线只回一条基准：A 的同排期 EWALL.A。两种分法的信号日和持有窗口相同、只是批内权重不同，
    # 两条基准几乎重合；B 的精确超额看 summary。
    curves: dict[str, Any] = {"dates": dates}
    out: dict[str, Any] = {}
    for s in SCHEMES:
        summary, curves[s], bench_cum = _curve([x / slots for x in acc[s]], [x / slots for x in bacc[s]], dates)
        if s == "A":
            curves["bench"] = bench_cum
        invested, positions_n, stocks_n, eff_n, day_max = [], [], [], [], []
        peak_w, peak_code, peak_date = 0.0, None, None
        for t in rolling:
            weights: dict[str, float] = defaultdict(float)
            for p in active[t]:
                weights[p["code"]] += p["share"][s] / slots
            w = list(weights.values())
            invested.append(sum(w))
            positions_n.append(len(active[t]) if s == "A" else len({(p["signal_date"], p["code"]) for p in active[t]}))
            stocks_n.append(len(w))
            if not w:
                continue
            eff_n.append(sum(w) ** 2 / sum(x * x for x in w))
            code, top = max(weights.items(), key=lambda kv: kv[1])
            day_max.append(top)
            if top > peak_w:
                peak_w, peak_code, peak_date = top, code, dates[t]
        out[s] = {**summary, "avg_invested_pct": _r((_mean(invested) or 0) * 100, 1),
                  "avg_positions": _r(_mean(positions_n), 1), "avg_stocks": _r(_mean(stocks_n), 1),
                  "avg_effective_n": _r(_mean(eff_n), 1), "max_single_weight": _r(peak_w * 100),
                  "max_single_weight_code": peak_code, "max_single_weight_date": peak_date,
                  "avg_daily_max_weight": _r((_mean(day_max) or 0) * 100)}

    def per_trade(weight_of) -> dict[str, Any]:
        ws = [weight_of(p) for p in positions]
        total = sum(ws)
        net = sum(w * p["net"] for w, p in zip(ws, positions)) / total
        bench = sum(w * p["bench"] for w, p in zip(ws, positions)) / total
        return {"n": round(total), "net_ret": _r(net), "bench_ret": _r(bench), "excess_ret": _r(net - bench)}

    return {
        "cost_pct": COST_PCT, "slots": slots, "eligible_picks": len(positions),
        "eligible_signals": len({(p["signal_date"], p["code"]) for p in positions}),
        "excluded_closed": excluded, "signal_days": len(batches),
        "rolling_window": {"start": dates[rolling.start], "end": dates[rolling.stop - 1], "days": len(rolling),
                           "ramped": ramped},
        # 逐笔对照（不复利）：每笔一份 = 诊断页现有「平均/笔」的权重；去重 = 每个（信号日，代码）一份。都换成 0.07% 成本。
        "per_trade": {"all": per_trade(lambda p: 1.0), "dedup": per_trade(lambda p: 1 / p["k"])},
        "A": out["A"], "B": out["B"], "curves": curves,
        "recent_signal_days": recent_signal_days, "recent_days": _recent_days(rows, recent_signal_days),
    }
