"""
放量新高突破（趋势延续 / momentum breakout continuation）— 独立回测脚本（T1 策略研发 / 诚实样本外验证）。

因子定义（因果机制）:
  个股已处于「确立的上升趋势」中——短中期均线多头排列（MA20 > MA60 且 收盘价 > MA20），
  趋势方向明确、非震荡非下跌。在趋势中，价格放量突破「近 N 日最高价」——
  这是动量延续（trend-following）的经典信号：已强者借量能确认继续走强。

  与已有策略 / 已破产因子的区分：
    - platform-breakout（平台突破 #15/#193，净负已丢弃）：要求「长期窄幅横盘+缩量蓄势+放量突破」，
      本质是「收敛后的方向选择」；本因子反其道——要求趋势**已确立多头排列**，突破的是近期新高，
      不做横盘蓄势假设，是纯动量延续，机制迥异；
    - hammer-support（锤子支撑反转 #182，净负已丢弃）：均值回归（回调至支撑），本因子是动量延续；
    - 现有实盘赢家 sector-relative-strength-leader（板块相对强度）/ dragon-tiger-smart-money（龙虎榜聪明钱）
      是「板块/资金」维度的动量，本因子是「个股价格结构」维度的动量，正交互补，可填补空白。
  因果：确立上升趋势 + 放量创新高 = 既有动量被新增买盘确认、趋势大概率延续向上。

信号判定（只用 T 日及之前数据，无未来函数）:
  T 日趋势多头排列：
    - MA20 > MA60（短中期趋势向上）；close > MA20（价格在均线上方、非跌破）
  T 日放量突破近 N 日新高：
    - high_T > max(high[ T-20 .. T-1 ]) * (1 + EPS)  （创近期新高，EPS 防浮点）
    - 强收盘：close_T >= low_T + 0.55 * (high_T - low_T)  （收盘贴近高点，非上影回落）
  T 日量能确认：
    - vol_ratio_5 >= VOL_RATIO_MIN（相对 5 日均量放量，资金真进场）
  T 日非极端 / 非追板：
    - 当日涨跌幅 pct_chg ∈ [-7%, +9.5%)  （非暴跌、也非一字涨停买不到）
    - 成交额 amount >= MIN_AMOUNT_YI 亿（流动性兜底）
    - 量比 vol_ratio_5 ∈ [0.6, 5.0]
    - 非 ST

交易规则（无未来函数）:
  - T 日收盘后判定信号（仅用 T 及之前数据）；
  - T+1 开盘买入；若 T+1 开盘较 T 收盘低开 >1.5%（突破失败破位）或 >= +9%（追高买不到）则跳过；
  - 持有至 T+1..T+5（5 个交易日），或期间任一日开盘较买入价 <= -5.7% 止损，或 >= +8% 止盈；
    未触发则到期收盘卖出；
  - 成本：单边 0.07%（佣金万1 + 印花税0.05%卖出 + 滑点代理），与 T2 / round-168/170/182 口径一致；
  - 同日最多开 MAX_POSITIONS_PER_DAY 个仓位（等权），避免单日信号过多导致过度分散。

实现说明:
  数据一次性加载到内存；第一遍按 code 扫描预计算所有信号（O(1.3M)），第二遍按交易日
  模拟组合（O(signals+dates)）。MA20/MA60/量比5 全部内存重算（不读 daily_kbars 的 corrupted 列，
  见 round #15/#193 数据坑）。

用法:
  cd backend && source .venv/bin/activate && python scripts/backtest_momentum_breakout.py
  cd backend && source .venv/bin/activate && python scripts/backtest_momentum_breakout.py --mode rolling
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

# ── 趋势多头排列 ──
# MA20 > MA60 * MA60_ALIGN：短中期趋势向上；close > MA20 * CLOSE_ABOVE_MA20：价格在均线上
MA60_ALIGN = 1.0
CLOSE_ABOVE_MA20 = 1.0
# ── 新高突破 ──
LOOKBACK = 20                 # 近 20 个交易日
NEW_HIGH_EPS = 0.001          # high_T > 近20日最高 * (1+EPS)
STRONG_CLOSE_RATIO = 0.55     # close >= low + 0.55*range（强收盘，非上影回落）
# ── 量能确认 ──
VOL_RATIO_MIN = 1.5           # 量比5 >= 1.5（放量确认）
# ── 非极端 / 流动性 ──
PCT_LO = -7.0
PCT_HI = 9.5
MIN_AMOUNT_YI = 3.0
VR_LO = 0.6
VR_HI = 5.0
# ── 入场跳空过滤 ──
ENTRY_GAPDOWN_SKIP = -1.5     # T+1 较 T 收盘低开 >1.5% → 突破失败破位，跳过
ENTRY_GAPUP_SKIP = 9.0        # T+1 较 T 收盘 >= +9% → 追高买不到，跳过
# ── 组合 ──
MAX_POSITIONS_PER_DAY = 8
INITIAL_CAPITAL = 100000.0
cash = INITIAL_CAPITAL


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _sma(vals: list[float], i: int, n: int) -> float:
    """第 i 位（含）向前 n 日的简单移动平均；数据不足返回 0。"""
    if i + 1 < n:
        return 0.0
    s = 0.0
    for k in range(i + 1 - n, i + 1):
        s += vals[k]
    return s / n


def _build_indicators(by_code: dict[str, list[Any]]) -> dict[tuple[str, str], dict[str, float]]:
    """内存计算 MA20 / MA60 / 量比5 —— 不使用 daily_kbars 的 ma20/ma60/vol_ratio_5 列。

    数据质量坑（round #193 / 2026-07-21）：daily_kbars.ma20/ma60/turnover_rate/vol_ratio_5
    在 2023-2025 全 NULL、2026 约 57~96% 缺失。直接读这些列会让「读 ma20 判定趋势」的因子
    在 2023-2025 全部信号被静默杀死、2026 也大量漏判 → 负向结论建立在被污染/偏置样本上。
    这里仅依赖可靠列 close / volume 在内存重算，且 ma20(i) 只用 bars[0..i]（无未来函数）。
    """
    cache: dict[tuple[str, str], dict[str, float]] = {}
    for code, bars in by_code.items():
        closes = [_f(b.close) for b in bars]
        vols = [_f(b.volume) for b in bars]
        m = len(bars)
        for i, b in enumerate(bars):
            ma20 = _sma(closes, i, 20)
            ma60 = _sma(closes, i, 60)
            lo5 = max(0, i - 4)
            win = vols[lo5:i + 1]
            s5 = sum(win)
            vr5 = (vols[i] / s5 * len(win)) if s5 > 0 else 0.0
            cache[(code, str(b.trade_date))] = {"ma20": ma20, "ma60": ma60, "vol_ratio_5": vr5}
    return cache


def _prior_high(bars: list[Any], i: int, lookback: int) -> float:
    """T 之前 lookback 根 bar 的最高价（不含 T 本身，无未来函数）。"""
    lo = max(0, i - lookback)
    h = 0.0
    for k in range(lo, i):
        h = max(h, _f(bars[k].high))
    return h


def _passes_uptrend_alignment(b: Any, ind: dict[str, float]) -> bool:
    """确立的上升趋势：短中期均线多头排列 + 价格在均线上方。"""
    ma20 = ind["ma20"]
    ma60 = ind["ma60"]
    if ma20 <= 0 or ma60 <= 0:
        return False
    if ma20 <= ma60 * MA60_ALIGN:
        return False
    close = _f(b.close)
    if close <= ma20 * CLOSE_ABOVE_MA20:
        return False
    return True


def _passes_new_high(b: Any, bars: list[Any], i: int) -> bool:
    """放量突破近 N 日新高 + 强收盘。"""
    high = _f(b.high)
    low = _f(b.low)
    close = _f(b.close)
    if high <= low:
        return False
    rng = high - low
    prior_h = _prior_high(bars, i, LOOKBACK)
    if prior_h <= 0:
        return False
    # 创近期新高
    if high <= prior_h * (1 + NEW_HIGH_EPS):
        return False
    # 强收盘：收盘贴近高点，非上影回落
    if close < low + STRONG_CLOSE_RATIO * rng:
        return False
    return True


def _passes_volume(b: Any, ind: dict[str, float]) -> bool:
    vr = ind["vol_ratio_5"]
    if vr <= 0:
        return False
    if vr < VOL_RATIO_MIN:
        return False
    return True


def _passes_non_extreme(b: Any, ind: dict[str, float]) -> bool:
    pct = _f(b.pct_chg)
    if not (PCT_LO <= pct < PCT_HI):
        return False
    amount_yi = _f(b.amount) / 1e8
    if amount_yi < MIN_AMOUNT_YI:
        return False
    vr = ind["vol_ratio_5"]
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

    # 内存重算 MA20/MA60/量比5（避免读 corrupted 列，见 _build_indicators）
    ind_cache = _build_indicators(by_code)

    all_codes = set(by_code.keys())
    stock_meta_list = db.scalars(select(Stock).where(Stock.code.in_(all_codes))).all()
    stock_meta = {str(s.code): {"name": str(s.name or s.code), "industry": s.industry or "未分类",
                                "is_st": bool(s.is_st)} for s in stock_meta_list}

    return {"trade_dates": trade_dates, "by_code": by_code, "bar_map": bar_map,
            "stock_meta": stock_meta, "ind_cache": ind_cache}


def _precompute_signals(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """第一遍：按 code 扫描，预计算所有放量新高突破信号（仅用 T 及之前数据）。"""
    by_code = data["by_code"]
    stock_meta = data["stock_meta"]
    ind = data["ind_cache"]
    signals_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    year_counts: dict[str, int] = defaultdict(int)
    for code, bars in by_code.items():
        meta = stock_meta.get(code) or {}
        if meta.get("is_st"):
            continue
        n = len(bars)
        # 需要 i >= LOOKBACK 才能算近 N 日最高；需要 i+1=T+1 才能入场
        for i in range(LOOKBACK, n - 1):
            b = bars[i]
            bi = ind.get((code, str(b.trade_date)))
            if bi is None:
                continue
            if not _passes_uptrend_alignment(b, bi):
                continue
            if not _passes_new_high(b, bars, i):
                continue
            if not _passes_volume(b, bi):
                continue
            if not _passes_non_extreme(b, bi):
                continue
            nb = bars[i + 1]
            open_p = _f(nb.open) or _f(nb.close)
            if open_p <= 0:
                continue
            entry_gap = (open_p / _f(b.close) - 1) * 100.0
            if entry_gap >= ENTRY_GAPUP_SKIP or entry_gap < ENTRY_GAPDOWN_SKIP:
                continue
            # 排序得分：量能确认强度（越高越值得优先）
            score = bi["vol_ratio_5"]
            t_date = str(b.trade_date)
            signals_by_entry[str(nb.trade_date)].append({
                "code": code, "name": meta.get("name", code), "industry": meta.get("industry", "未分类"),
                "t_date": t_date, "entry_date": str(nb.trade_date),
                "entry_price": open_p, "score": score,
            })
            total += 1
            year_counts[t_date[:4]] += 1
    logger.info("预计算信号总数: %d  分年: %s", total, dict(sorted(year_counts.items())))
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
    print("\n========== 放量新高突破（趋势延续）回测结果 ==========")
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
    parser = argparse.ArgumentParser(description="放量新高突破（趋势延续）回测")
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
