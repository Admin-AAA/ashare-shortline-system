"""
涨停后分歧日低吸（弱转强）— 独立回测脚本（T1 策略研发 / 诚实样本外验证）。

因子定义（因果机制）:
  T 日 = 涨停（limit_up_board.limit_type='U' 且当日涨幅 >= 9.5%）。
  游资封板后，次日（T+1，分歧日）通常出现「高开 + 冲高回落 + 放量」的换手洗盘：
    - 高开（T+1 开盘较 T 收盘 >= +1%）表示有承接预期；
    - 收弱（T+1 涨幅 <= +1.5%，且盘中上探 open 之上 = 有分歧换手）表示浮筹被洗；
    - 放量（T+1 成交量 >= 前 6 日均量 1.2 倍）表示充分换手；
    - 不破位（T+1 最低 >= T 最低 * 0.97）表示洗盘未破坏涨停日结构。
  T+2（一致日）开盘平开/高开 = 弱转强确认 → 买入。
  因果：涨停后一日分歧换手洗掉跟风盘，第三日一致转强是二波拉升的经典起点
  （游资「分歧转一致」弱转强模式），与「炸板回封」(limit-up-reseal, 同日完成)
  和「首板接力」(低位首板 + 板块共振) 均不同，是对「涨停后二次介入」空白的填补。

交易规则（无未来函数）:
  - 分歧日 T+1 收盘后判定信号（只用 T+1 及之前的数据）；
  - T+2 开盘买入；若 T+2 开盘较 T+1 收盘低开 >1%（弱转强失败）或 >= +9%（一字买不到）则跳过；
  - 持有至 T+2..T+6（5 个交易日），或期间任一日开盘较买入价 <= -5.7% 止损，
    或 >= +9%（再涨停）止盈，未触发则到期收盘卖出；
  - 成本：单边 0.07%（佣金万1 + 印花税0.05%卖出 + 滑点代理），与 T2 / round-168 口径一致。

实现说明:
  数据一次性加载到内存，回测在内存中按时间窗口切片模拟，避免每个滚动窗口重查 1.3M 行。

用法:
  cd backend && source .venv/bin/activate && python scripts/backtest_limitup_divergence.py
  cd backend && source .venv/bin/activate && python scripts/backtest_limitup_divergence.py --mode rolling
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import create_engine, select
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
TAKE_PROFIT_PCT = 9.0

# ── 分歧日判定阈值 ──
GAP_MIN_PCT = 1.0          # T+1 开盘较 T 收盘高开 >= +1%（有承接）
DIVERGE_PCT_MAX = 1.5      # T+1 当日涨幅 <= +1.5%（弱跟随 / 分歧收弱）
DIVERGE_PROBE = 1.01       # T+1 盘中上探 open 之上（有分歧换手）
VOL_RATIO = 1.2            # T+1 量 >= 前 6 日均量 1.2 倍（放量换手）
SUPPORT_RATIO = 0.97       # T+1 最低 >= T 最低 * 0.97（洗盘不破位）
# ── 流动性 / 风控 ──
MIN_AMOUNT_YI = 3.0
MIN_TURNOVER = 1.5
MAX_TURNOVER = 20.0
ENTRY_GAP_SKIP = 9.0       # T+2 开盘较 T+1 收盘 >= +9% 一字买不到，跳过
ENTRY_GAPDOWN_SKIP = -1.0  # T+2 开盘较 T+1 收盘低开 >1% 弱转强失败，跳过

INITIAL_CAPITAL = 100000.0
cash = INITIAL_CAPITAL


def main() -> None:
    parser = argparse.ArgumentParser(description="涨停后分歧日低吸（弱转强）回测")
    parser.add_argument("--mode", choices=["single", "rolling"], default="single")
    parser.add_argument("--start-date", default=START_DATE)
    parser.add_argument("--end-date", default=END_DATE)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--step", type=int, default=15)
    args = parser.parse_args()

    import os
    db_url = os.environ.get("DATABASE_URL",
                             "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4")
    engine = create_engine(db_url, connect_args={"charset": "utf8mb4"}, pool_pre_ping=True)

    with Session(engine) as db:
        data = _load_data(db, args.start_date, args.end_date)
        if args.mode == "single":
            metrics, trades, pv = _simulate(data, data["trade_dates"])
            _print(trades, metrics, data["trade_dates"], pv)
        else:
            rolling_window_backtest(data, train_days=args.train_days, test_days=args.test_days, step=args.step)
    engine.dispose()


def _load_data(db: Session, start_date: str, end_date: str) -> dict[str, Any]:
    from app.models.tables import DailyKbar, LimitUpBoard, Stock

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
    date_to_idx = {d: i for i, d in enumerate(trade_dates)}

    # 涨停日集合（limit_type='U'）→ 用于 T 日判定
    lu_rows = db.scalars(
        select(LimitUpBoard).where(LimitUpBoard.trade_date >= trade_dates[0], LimitUpBoard.trade_date <= trade_dates[-1])
    ).all()
    limit_up_codes_by_date: dict[str, set[str]] = defaultdict(set)
    for r in lu_rows:
        if str(r.limit_type) == "U":
            limit_up_codes_by_date[str(r.trade_date)].add(str(r.code))

    all_codes = set(by_code.keys())
    stock_meta_list = db.scalars(select(Stock).where(Stock.code.in_(all_codes))).all()
    stock_meta = {str(s.code): {"name": str(s.name or s.code), "industry": s.industry or "未分类",
                                "is_st": bool(s.is_st)} for s in stock_meta_list}

    return {
        "trade_dates": trade_dates, "by_code": by_code, "bar_map": bar_map,
        "date_to_idx": date_to_idx, "limit_up_codes_by_date": limit_up_codes_by_date,
        "stock_meta": stock_meta,
    }


def _is_limit_up_day(data: dict[str, Any], code: str, td: str) -> bool:
    """T 日是否为涨停：limit_up_board 标记 + 当日涨幅 >= 9.5%（双校验，避免遗漏/误标）。"""
    if code not in data["limit_up_codes_by_date"].get(td, set()):
        return False
    b = data["bar_map"].get((code, td))
    return b is not None and _f(b.pct_chg) >= 9.5


def _avg_vol_before(by_code: dict[str, list], code: str, cur_bar: Any, n: int = 8) -> float:
    """涨停前正常量基线：取分歧日(T+1)之前 n 天（不含涨停日 T）的均量。

    关键 1：基线必须排除涨停日 T 自身（T 量能异常放大），否则分歧日放量门槛
    会被涨停日巨量抬高，导致几乎永不触发（曾使样本从 475 坍缩到 2）。
    关键 2：用 by_code[code] 的「本地下标」定位分歧日 bar（bars[pos]=T+1，
    bars[pos-1]=T），绝不能用全局 date_to_idx 下标去切本地列表——两者不等价，
    会切到错误/空位置使基线恒为 0。取 bars[max(0,pos-1-n):pos-1] 即 T 之前 n 天。
    """
    bars = by_code.get(code, [])
    try:
        pos = bars.index(cur_bar)
    except ValueError:
        return 0.0
    lo = max(0, pos - 1 - n)
    hi = pos - 1  # 不含 T（pos-1）与分歧日（pos）
    vols = [_f(bars[j].volume) for j in range(lo, hi)]
    return sum(vols) / len(vols) if vols else 0.0


def _simulate(data: dict[str, Any], trade_dates: list[str]) -> tuple[dict[str, Any], list[dict], list[float]]:
    """在已加载数据上模拟回测。trade_dates 为完整或切片后的交易日序列。"""
    global cash
    by_code = data["by_code"]
    bar_map = data["bar_map"]
    date_to_idx = data["date_to_idx"]
    stock_meta = data["stock_meta"]
    date_set = set(trade_dates)

    trades: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    cash = INITIAL_CAPITAL
    pv = [1.0]

    for idx in range(1, len(trade_dates) - 1):
        td = trade_dates[idx]
        next_td = trade_dates[idx + 1]
        prev_td = trade_dates[idx - 1]

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
                _close(pos, trades, active, bar_map, next_td, date_set, "止损(-5.7%)")
                continue
            if ret >= TAKE_PROFIT_PCT:
                _close(pos, trades, active, bar_map, next_td, date_set, "止盈(+9%涨停)")
                continue
            if days_held >= HOLD_DAYS:
                _close(pos, trades, active, bar_map, next_td, date_set, f"持有{HOLD_DAYS}天到期")
                continue

        # ── 寻找分歧日信号（T+1 收盘后判定）──
        candidates = []
        for code in data["limit_up_codes_by_date"].get(prev_td, set()):
            if code in active:
                continue
            meta = stock_meta.get(code) or {}
            if meta.get("is_st"):
                continue
            if not _is_limit_up_day(data, code, prev_td):
                continue
            prev_b = bar_map.get((code, prev_td))
            bar = bar_map.get((code, td))
            nb = bar_map.get((code, next_td))
            if prev_b is None or bar is None or nb is None:
                continue
            # 流动性
            amount_yi = _f(bar.amount) / 1e8
            # turnover_rate 列在 2023-2025 全 NULL、2026 大量缺失 → 不可信，已移除该过滤；
            # 流动性由 amount(成交额) 兜底（MIN_AMOUNT_YI 已门控）。
            if amount_yi < MIN_AMOUNT_YI:
                continue
            # 分歧日：高开（承接）
            gap = (_f(bar.open) / _f(prev_b.close) - 1) * 100.0
            if gap < GAP_MIN_PCT:
                continue
            # 分歧日：收弱（弱跟随）
            if _f(bar.pct_chg) > DIVERGE_PCT_MAX:
                continue
            # 分歧日：盘中上探（有分歧换手）
            if _f(bar.high) <= _f(bar.open) * DIVERGE_PROBE:
                continue
            # 分歧日：放量换手（基线 = 涨停前正常量，排除涨停日 T 巨量）
            avgv = _avg_vol_before(by_code, code, bar, 8)
            if avgv <= 0 or _f(bar.volume) < avgv * VOL_RATIO:
                continue
            # 分歧日：不破位（洗盘未破坏涨停日结构）
            if _f(bar.low) < _f(prev_b.low) * SUPPORT_RATIO:
                continue
            # T+2 入场价（开盘）；弱转强失败（低开）/ 一字（买不到）跳过
            open_p = _f(nb.open) or _f(nb.close)
            entry_gap = (open_p / _f(bar.close) - 1) * 100.0
            if open_p <= 0 or entry_gap >= ENTRY_GAP_SKIP or entry_gap < ENTRY_GAPDOWN_SKIP:
                continue
            candidates.append((code, meta, open_p))

        # ── 开新仓（等分管仓，扣现金）──
        n_cand = max(1, len(candidates))
        for (code, meta, open_p) in candidates:
            notional = cash * 0.5 / n_cand
            if notional <= 0:
                break
            cash -= notional
            active[code] = {
                "code": code, "name": meta.get("name", code), "industry": meta.get("industry", "未分类"),
                "entry_date": next_td, "entry_price": open_p, "day_held": 0,
                "notional": notional, "entered": False,
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
        _close(pos, trades, active, bar_map, last_td, date_set, "回测结束平仓", force_close=True)

    metrics = _compute_metrics(trades, pv, INITIAL_CAPITAL)
    return metrics, trades, pv


def _close(pos, trades, active, bar_map, exit_date, date_set, reason, force_close: bool = False) -> None:
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


def _compute_metrics(trades, pv, initial) -> dict[str, Any]:
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


def _print(trades, m, trade_dates, pv) -> None:
    print("\n========== 涨停后分歧日低吸（弱转强）回测结果 ==========")
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
    from collections import defaultdict
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


def rolling_window_backtest(data: dict[str, Any], *, train_days=60, test_days=30, step=15) -> None:
    all_dates = data["trade_dates"]
    total = len(all_dates)
    if total < train_days + test_days:
        logger.error("交易日不足")
        return
    rows = []
    max_start = total - train_days - test_days
    for i in range(0, max_start + 1, step):
        ws, we = all_dates[i], all_dates[i + train_days + test_days - 1]
        es, ee = all_dates[i + train_days], all_dates[i + train_days + test_days - 1]
        m, all_trades, _ = _simulate(data, all_dates[i:i + train_days + test_days])
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
        return
    import statistics
    print("\n=== 滚动窗口汇总（%d 窗口，仅评测期交易）===" % len(rows))
    print(f"平均胜率 {statistics.mean(r['acc'] for r in rows):.1f}%  最小 {min(r['acc'] for r in rows):.1f}%  最大 {max(r['acc'] for r in rows):.1f}%")
    print(f"平均盈亏比 {statistics.mean(r['pl'] for r in rows):.2f}  最小 {min(r['pl'] for r in rows):.2f}")
    print(f"平均净收益 {statistics.mean(r['avg'] for r in rows):.2f}%")
    print(f"胜率>50% 窗口占比: {sum(1 for r in rows if r['acc']>50)/len(rows)*100:.0f}%")
    print(f"盈亏比>1.4 窗口占比: {sum(1 for r in rows if r['pl']>1.4)/len(rows)*100:.0f}%")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
