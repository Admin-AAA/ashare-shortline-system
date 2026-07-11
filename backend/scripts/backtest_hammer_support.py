"""
长下影支撑反转（上升趋势中的 MA20 支撑锤子线）— 独立回测脚本（T1 策略研发 / 诚实样本外验证）。

因子定义（因果机制）:
  在「中长期上升趋势」中，短期回调把价格带回到 20 日线（MA20）这一关键支撑位。
  测试日 T 当天：卖盘把价格打压到 MA20 下方（盘中最低触及支撑），但买盘坚决拒绝更低
  价格 —— 形成「长下影线」（hammer / 锤子线）：下影线长度显著大于实体与全幅。
  这是「支撑位抛压耗尽 + 资金承接」的经典形态。若支撑有效，在仍完好的上升趋势里，
  价格向均值回归向上是大概率事件。

  与现有策略的区分：
    - trend-pullback-continuation（回调延续）：买「上升趋势中的回调」期待趋势延续，
      不要求锤子线形态、也不要求是「精准回踩 MA20 支撑」；
    - 分歧日低吸 #170（limit-up 后分歧日）：依赖涨停事件；
    - chip-single-peak-breakout（筹码单峰突破）：突破形态，非回调支撑反转；
  本因子是「上升趋势 + 回调至 MA20 + 锤子线拒绝」三者同时成立的支撑反转，填补空白。

信号判定（只用 T 日及之前数据，无未来函数）:
  T 日锤子线：
    - 下影线 lower = min(open,close) - low；实体 body = abs(close-open)；全幅 range = high-low
    - lower >= HAMMER_SHADOW_RATIO * range  （下影占全幅主体）
    - lower >= HAMMER_BODY_MULT * body      （下影至少为实体 N 倍；实体极小时放宽）
    - upper_shadow <= HAMMER_UPPER_RATIO * lower  （上影极小，标准锤子形态）
  T 日处于「上升趋势中的回调至支撑」：
    - 中长期向上：close >= ma60 * 0.96  （未破长期趋势，非崩坏股）
    - 短期回调至 MA20：ma20*0.95 <= close < ma20  （价格刚跌破 20 日线、仍在支撑带内）
    - 盘中最低精准回踩支撑：ma20*0.96 <= low <= ma20*1.01  （下影正好打到 20 日线）
  非极端：
    - 当日涨跌幅 pct_chg ∈ [-7%, +6%]  （锤子日既非暴跌也非涨停）
    - 成交额 amount >= MIN_AMOUNT_YI 亿；换手 turnover ∈ [MIN_TURNOVER, MAX_TURNOVER]
    - 量比 vol_ratio_5 ∈ [0.6, 3.0]  （有参与但非爆量赶顶）
    - 非 ST

交易规则（无未来函数）:
  - T 日收盘后判定信号（仅用 T 及之前数据）；
  - T+1 开盘买入；若 T+1 开盘较 T 收盘低开 >1.5%（支撑破位，反转失败）或 >= +9%（一字买不到）则跳过；
  - 持有至 T+1..T+5（5 个交易日），或期间任一日开盘较买入价 <= -5.7% 止损，或 >= +8% 止盈；
    未触发则到期收盘卖出；
  - 成本：单边 0.07%（佣金万1 + 印花税0.05%卖出 + 滑点代理），与 T2 / round-168/170 口径一致；
  - 同日最多开 MAX_POSITIONS_PER_DAY 个仓位（等权），避免单日信号过多导致过度分散。

实现说明:
  数据一次性加载到内存；第一遍按 code 扫描预计算所有信号（O(1.3M)），第二遍按交易日
  模拟组合（O(signals+dates)），避免每个滚动窗口重查 1.3M 行。

用法:
  cd backend && source .venv/bin/activate && python scripts/backtest_hammer_support.py
  cd backend && source .venv/bin/activate && python scripts/backtest_hammer_support.py --mode rolling
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

START_DATE = "2023-01-01"
END_DATE = "2026-06-24"

BUY_COST_RATIO = 0.0007
SELL_COST_RATIO = 0.0007

HOLD_DAYS = 5
STOP_LOSS_PCT = -5.7
TAKE_PROFIT_PCT = 8.0

# ── 锤子线判定阈值 ──
HAMMER_SHADOW_RATIO = 0.55   # 下影线 >= 全幅的 55%
HAMMER_BODY_MULT = 1.5       # 下影线 >= 实体 1.5 倍（实体极小时放宽，见 _is_hammer）
HAMMER_UPPER_RATIO = 0.5     # 上影 <= 下影的 50%
# ── 支撑 / 趋势带 ──
MA60_FLOOR = 0.96            # close >= ma60*0.96（长期趋势未破）
MA20_BAND_LO = 0.95          # close >= ma20*0.95（仍在支撑带内，非崩坏）
MA20_LOW_LO = 0.96           # low  >= ma20*0.96（盘中最低精准回踩）
MA20_LOW_HI = 1.01           # low  <= ma20*1.01
# ── 非极端 / 流动性 ──
PCT_LO = -7.0
PCT_HI = 6.0
MIN_AMOUNT_YI = 3.0
MIN_TURNOVER = 1.5
MAX_TURNOVER = 20.0
VR_LO = 0.6
VR_HI = 3.0
# ── 入场跳空过滤 ──
ENTRY_GAPDOWN_SKIP = -1.5    # T+1 较 T 收盘低开 >1.5% → 支撑破位，跳过
ENTRY_GAPUP_SKIP = 9.0       # T+1 较 T 收盘 >= +9% → 一字买不到，跳过
# ── 组合 ──
MAX_POSITIONS_PER_DAY = 8
INITIAL_CAPITAL = 100000.0
cash = INITIAL_CAPITAL


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _is_hammer(b: Any) -> bool:
    """T 日锤子线：长下影 + 极小上影。"""
    o, h, l, c = _f(b.open), _f(b.high), _f(b.low), _f(b.close)
    if h <= l or l <= 0:
        return False
    rng = h - l
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    # 下影占全幅主体
    if lower < HAMMER_SHADOW_RATIO * rng:
        return False
    # 下影至少为实体的 N 倍；实体极小时（<0.5% 全幅）放宽到「下影>1% 全幅」即可
    if body > 0.005 * rng:
        if lower < HAMMER_BODY_MULT * body:
            return False
    else:
        if lower < 0.01 * rng:
            return False
    # 上影极小
    if upper > HAMMER_UPPER_RATIO * max(lower, 1e-9):
        return False
    return True


def _passes_support_trend(b: Any) -> bool:
    """上升趋势中的回调至 MA20 支撑。"""
    if _f(b.ma20) <= 0 or _f(b.ma60) <= 0:
        return False
    close = _f(b.close)
    low = _f(b.low)
    ma20 = _f(b.ma20)
    ma60 = _f(b.ma60)
    # 中长期向上（未破长期趋势）
    if close < ma60 * MA60_FLOOR:
        return False
    # 短期回调至 MA20 支撑带（刚跌破 20 日线、仍在其附近）
    if not (ma20 * MA20_BAND_LO <= close < ma20):
        return False
    # 盘中最低精准回踩支撑
    if not (ma20 * MA20_LOW_LO <= low <= ma20 * MA20_LOW_HI):
        return False
    return True


def _passes_non_extreme(b: Any) -> bool:
    pct = _f(b.pct_chg)
    if not (PCT_LO <= pct <= PCT_HI):
        return False
    amount_yi = _f(b.amount) / 1e8
    turnover = _f(b.turnover_rate)
    if amount_yi < MIN_AMOUNT_YI:
        return False
    if turnover < MIN_TURNOVER or turnover > MAX_TURNOVER:
        return False
    vr = _f(b.vol_ratio_5)
    if vr > 0 and (vr < VR_LO or vr > VR_HI):
        return False
    return True


def _load_data(db: Session, start_date: str, end_date: str) -> dict[str, Any]:
    from app.models.tables import DailyKbar, Stock

    trade_dates = [str(d) for d in db.scalars(
        select(DailyKbar.trade_date).distinct()
        .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
        .order_by(DailyKbar.trade_date.asc())
    ).all()]
    logger.info("交易日历: %s ~ %s, %d 天", trade_dates[0], trade_dates[-1], len(trade_dates))

    all_bars = db.scalars(
        select(DailyKbar).where(DailyKbar.trade_date >= trade_dates[0], DailyKbar.trade_date <= trade_dates[-1])
        .order_by(DailyKbar.trade_date.asc())
    ).all()

    by_code: dict[str, list[Any]] = defaultdict(list)
    bar_map: dict[tuple[str, str], Any] = {}
    for b in all_bars:
        by_code[str(b.code)].append(b)
        bar_map[(str(b.code), str(b.trade_date))] = b
    for code in by_code:
        by_code[code].sort(key=lambda x: str(x.trade_date))

    all_codes = set(by_code.keys())
    stock_meta_list = db.scalars(select(Stock).where(Stock.code.in_(all_codes))).all()
    stock_meta = {str(s.code): {"name": str(s.name or s.code), "industry": s.industry or "未分类",
                                "is_st": bool(s.is_st)} for s in stock_meta_list}

    return {"trade_dates": trade_dates, "by_code": by_code, "bar_map": bar_map, "stock_meta": stock_meta}


def _precompute_signals(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """第一遍：按 code 扫描，预计算所有锤子支撑信号（仅用 T 及之前数据）。

    返回 entry_date -> [signal]。signal 含 t_date / entry_date / entry_price(=T+1 开盘) /
    score(下影占比，用于同日多信号排序择优)。
    """
    by_code = data["by_code"]
    stock_meta = data["stock_meta"]
    signals_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    for code, bars in by_code.items():
        meta = stock_meta.get(code) or {}
        if meta.get("is_st"):
            continue
        n = len(bars)
        for i in range(1, n - 1):  # 需要 i+1 = T+1
            b = bars[i]
            # 锤子线 + 支撑趋势 + 非极端
            if not _is_hammer(b):
                continue
            if not _passes_support_trend(b):
                continue
            if not _passes_non_extreme(b):
                continue
            nb = bars[i + 1]
            open_p = _f(nb.open) or _f(nb.close)
            if open_p <= 0:
                continue
            # 入场跳空过滤（T+1 开盘 vs T 收盘）
            entry_gap = (open_p / _f(b.close) - 1) * 100.0
            if entry_gap >= ENTRY_GAPUP_SKIP or entry_gap < ENTRY_GAPDOWN_SKIP:
                continue
            rng = _f(b.high) - _f(b.low)
            score = (min(_f(b.open), _f(b.close)) - _f(b.low)) / rng if rng > 0 else 0
            signals_by_entry[str(nb.trade_date)].append({
                "code": code, "name": meta.get("name", code), "industry": meta.get("industry", "未分类"),
                "t_date": str(b.trade_date), "entry_date": str(nb.trade_date),
                "entry_price": open_p, "score": score,
            })
            total += 1
    logger.info("预计算信号总数: %d", total)
    return signals_by_entry


def _simulate(data: dict[str, Any], trade_dates: list[str],
              signals_by_entry: dict[str, list[dict]], ) -> tuple[dict[str, Any], list[dict], list[float]]:
    global cash
    bar_map = data["bar_map"]
    date_set = set(trade_dates)

    trades: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    cash = INITIAL_CAPITAL
    pv = [1.0]

    for idx in range(1, len(trade_dates) - 1):
        td = trade_dates[idx]
        next_td = trade_dates[idx + 1]

        # ── 管理已有持仓（止损 / 止盈 / 到期）──
        for code, pos in list(active.items()):
            if not pos.get("entered"):
                continue
            nb = bar_map.get((code, td))
            if nb is None:
                continue
            open_p = _f(nb.open) or _f(nb.close)
            ret = (open_p - pos["entry_price"]) / pos["entry_price"] * 100.0
            days_held = pos["day_held"] + 1
            pos["day_held"] = days_held
            if ret <= STOP_LOSS_PCT:
                _close(pos, trades, active, bar_map, next_td, "止损(-5.7%)")
                continue
            if ret >= TAKE_PROFIT_PCT:
                _close(pos, trades, active, bar_map, next_td, "止盈(+8%)")
                continue
            if days_held >= HOLD_DAYS:
                _close(pos, trades, active, bar_map, next_td, f"持有{HOLD_DAYS}天到期")
                continue

        # ── 新信号入场（同日多信号按 score 择优，限仓）──
        todays = signals_by_entry.get(td, [])
        todays = [s for s in todays if s["code"] not in active]
        todays.sort(key=lambda s: s["score"], reverse=True)
        todays = todays[:MAX_POSITIONS_PER_DAY]
        n_cand = max(1, len(todays))
        for s in todays:
            notional = cash * 0.5 / n_cand
            if notional <= 0:
                break
            cash -= notional
            active[s["code"]] = {
                "code": s["code"], "name": s["name"], "industry": s["industry"],
                "entry_date": s["entry_date"], "entry_price": s["entry_price"],
                "day_held": 0, "notional": notional, "entered": False,
            }

        for code, pos in active.items():
            if not pos.get("entered") and td >= pos["entry_date"]:
                pos["entered"] = True

        equity = cash
        for code, pos in active.items():
            if not pos.get("entered"):
                continue
            cb = bar_map.get((code, td))
            px = _f(cb.close) if cb else pos["entry_price"]
            equity += pos["notional"] * px / pos["entry_price"]
        pv.append(equity / INITIAL_CAPITAL)

    last_td = trade_dates[-1]
    for code, pos in list(active.items()):
        _close(pos, trades, active, bar_map, last_td, "回测结束平仓", force_close=True)

    metrics = _compute_metrics(trades, pv)
    return metrics, trades, pv


def _close(pos, trades, active, bar_map, exit_date, reason, force_close: bool = False) -> None:
    global cash
    code = pos["code"]
    entry = pos["entry_price"]
    notional = pos.get("notional", 0.0)
    eb = bar_map.get((code, exit_date))
    if eb is None:
        exit_p = entry
    else:
        exit_p = _f(eb.open) if (not force_close and _f(eb.open) > 0) else _f(eb.close)
    if exit_p <= 0:
        exit_p = entry
    gross = (exit_p - entry) / entry * 100.0
    net = gross - (BUY_COST_RATIO + SELL_COST_RATIO) * 100.0
    trades.append({
        "code": code, "name": pos.get("name", code), "industry": pos.get("industry", ""),
        "entry_date": pos["entry_date"], "exit_date": exit_date,
        "entry_price": round(entry, 3), "exit_price": round(exit_p, 3),
        "net_return_pct": round(net, 3), "holding_days": pos.get("day_held", 1),
        "accurate": net > 0, "exit_reason": reason,
    })
    cash += notional * (1 + net / 100.0)
    active.pop(code, None)


def _compute_metrics(trades, pv) -> dict[str, Any]:
    if not trades:
        return {"total_trades": 0, "accuracy": 0.0, "avg_net": 0.0, "pl_ratio": 0.0, "max_dd": 0.0, "cum_ret": 0.0}
    wins = [t for t in trades if t["net_return_pct"] > 0]
    losses = [t for t in trades if t["net_return_pct"] <= 0]
    avg_win = sum(t["net_return_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["net_return_pct"] for t in losses) / len(losses)) if losses else 1
    pl = avg_win / avg_loss if avg_loss > 0 else 0
    acc = sum(1 for t in trades if t["accurate"]) / len(trades) * 100
    avg_net = sum(t["net_return_pct"] for t in trades) / len(trades)
    peak = 1.0
    mdd = 0.0
    for v in pv:
        if v > peak:
            peak = v
        mdd = max(mdd, (peak - v) / peak * 100 if peak > 0 else 0)
    return {"total_trades": len(trades), "accuracy": acc, "avg_net": avg_net, "pl_ratio": pl, "max_dd": mdd, "cum_ret": (pv[-1] - 1) * 100}


def _print(trades, m, trade_dates) -> None:
    print("\n========== 长下影支撑反转（MA20 锤子线）回测结果 ==========")
    print(f"区间: {trade_dates[0]} ~ {trade_dates[-1]}  交易日: {len(trade_dates)}")
    print(f"总交易: {m['total_trades']}")
    print(f"胜率: {m['accuracy']:.1f}%")
    print(f"平均净收益: {m['avg_net']:.2f}%")
    print(f"盈亏比(净): {m['pl_ratio']:.2f}")
    print(f"最大回撤: {m['max_dd']:.2f}%")
    print(f"累计收益: {m['cum_ret']:.2f}%")
    print("最近 12 笔:")
    for t in trades[-12:]:
        print(f"  {t['entry_date']} {t['code']} {t.get('name','')}: 持{t['holding_days']}天 {t['net_return_pct']:+.2f}% {'✓' if t['accurate'] else '✗'} [{t['exit_reason']}]")
    _print_yearly(trades)


def _print_yearly(trades: list[dict[str, Any]]) -> None:
    by_year: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_year[t["entry_date"][:4]].append(t)
    print("\n--- 分年度表现（稳定性检验）---")
    for y in sorted(by_year):
        ys = by_year[y]
        n = len(ys)
        wr = sum(1 for t in ys if t["accurate"]) / n * 100
        avg = sum(t["net_return_pct"] for t in ys) / n
        wins = [t for t in ys if t["net_return_pct"] > 0]
        losses = [t for t in ys if t["net_return_pct"] <= 0]
        aw = sum(t["net_return_pct"] for t in wins) / len(wins) if wins else 0
        al = abs(sum(t["net_return_pct"] for t in losses) / len(losses)) if losses else 1
        pl = aw / al if al > 0 else 0
        print(f"  {y}: {n:>3} 笔  胜率 {wr:5.1f}%  均净 {avg:+.2f}%  盈亏比 {pl:.2f}")


def rolling_window_backtest(data, signals_by_entry, *, train_days=60, test_days=30, step=15) -> list[dict]:
    all_dates = data["trade_dates"]
    total = len(all_dates)
    if total < train_days + test_days:
        logger.error("交易日不足")
        return []
    rows = []
    max_start = total - train_days - test_days
    for i in range(0, max_start + 1, step):
        es = all_dates[i + train_days]
        ee = all_dates[i + train_days + test_days - 1]
        window = all_dates[i:i + train_days + test_days]
        # 仅用窗口内的信号（预计算时不限窗口，这里按 entry_date 过滤）
        win_signals = {d: signals_by_entry.get(d, []) for d in window}
        m, all_trades, _ = _simulate(data, window, win_signals)
        eval_trades = [t for t in all_trades if es <= t["entry_date"] <= ee]
        if not eval_trades:
            continue
        n = len(eval_trades)
        acc = sum(1 for t in eval_trades if t["accurate"]) / n * 100
        avg = sum(t["net_return_pct"] for t in eval_trades) / n
        wins = [t for t in eval_trades if t["net_return_pct"] > 0]
        losses = [t for t in eval_trades if t["net_return_pct"] <= 0]
        aw = sum(t["net_return_pct"] for t in wins) / len(wins) if wins else 0
        al = abs(sum(t["net_return_pct"] for t in losses) / len(losses)) if losses else 1
        pl = aw / al if al > 0 else 0
        rows.append({"win": i // step + 1, "es": es, "ee": ee, "n": n, "acc": acc, "avg": avg, "pl": pl})
        logger.info("窗口%2d %s~%s 笔%d 胜率%5.1f%% 盈亏比%5.2f 均净%+.2f%%", rows[-1]["win"], es[5:], ee[5:], n, acc, pl, avg)
    if not rows:
        print("无有效窗口")
        return rows
    import statistics
    print("\n=== 滚动窗口汇总（%d 窗口，仅评测期交易）===" % len(rows))
    print(f"平均胜率 {statistics.mean(r['acc'] for r in rows):.1f}%  最小 {min(r['acc'] for r in rows):.1f}%  最大 {max(r['acc'] for r in rows):.1f}%")
    print(f"平均盈亏比 {statistics.mean(r['pl'] for r in rows):.2f}  最小 {min(r['pl'] for r in rows):.2f}")
    print(f"平均净收益 {statistics.mean(r['avg'] for r in rows):.2f}%")
    print(f"胜率>50% 窗口占比: {sum(1 for r in rows if r['acc']>50)/len(rows)*100:.0f}%")
    print(f"盈亏比>1.4 窗口占比: {sum(1 for r in rows if r['pl']>1.4)/len(rows)*100:.0f}%")
    print(f"均净>0 窗口占比: {sum(1 for r in rows if r['avg']>0)/len(rows)*100:.0f}%")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="长下影支撑反转（MA20 锤子线）回测")
    parser.add_argument("--mode", choices=["single", "rolling"], default="single")
    parser.add_argument("--start-date", default=START_DATE)
    parser.add_argument("--end-date", default=END_DATE)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--step", type=int, default=15)
    args = parser.parse_args()

    from app.db.session import SessionLocal
    with SessionLocal() as db:
        data = _load_data(db, args.start_date, args.end_date)
        signals_by_entry = _precompute_signals(data)
        if args.mode == "single":
            metrics, trades, pv = _simulate(data, data["trade_dates"], signals_by_entry)
            _print(trades, metrics, data["trade_dates"])
        else:
            rolling_window_backtest(data, signals_by_entry, train_days=args.train_days,
                                    test_days=args.test_days, step=args.step)


if __name__ == "__main__":
    main()
