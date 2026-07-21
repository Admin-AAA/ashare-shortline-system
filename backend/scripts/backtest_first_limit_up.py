"""
低位首板接力 — 独立回测脚本（T1 策略研发 / 诚实样本外验证）。

因子定义（因果机制）:
  首板 = 个股当日为「首次涨停」（limit_up_board.limit_times == 1），且前 5 个交易日
  非涨停（真正的首板，而非连板/回封的延续）。
  低位 = 收盘价处于近 60 日价格区间的下 35%（非已连续大涨的多板高位票）。
  板块共振 = 当日同行业（limit_up_board.industry）另有 >=2 只涨停（limit_type='U'）。
    因果：板块同日多股涨停 = 资金系统性涌入该板块，首板是「先锋/补涨」而非孤立庄股，
    次日有更高的承接概率（游资经典「首板套利」）。

交易规则（无未来函数）:
  - 信号日 T 收盘后判定（只用 T 及之前的数据）
  - T+1 开盘买入；若 T+1 开盘 >= +9.5%（一字涨停买不到）则跳过
  - 持有至 T+3 收盘卖出（隔日/短打），或期间任一日开盘较买入价 <= -5% 止损（次日开盘出）
  - 成本：单边 0.07%（佣金万1 + 印花税0.05%卖出 + 滑点代理），与 T2 口径一致

实现说明:
  数据一次性加载到内存，回测在内存中按时间窗口切片模拟，避免每个滚动窗口重查 1.2M 行。

用法:
  cd backend && source .venv/bin/activate && python scripts/backtest_first_limit_up.py
  cd backend && source .venv/bin/activate && python scripts/backtest_first_limit_up.py --mode rolling
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

HOLD_DAYS = 3
STOP_LOSS_PCT = -5.0
MIN_AMOUNT_YI = 3.0
MIN_TURNOVER = 1.5
MAX_TURNOVER = 15.0
BASE_POS_MAX = 0.35
PRIOR_LIMITUP_LOOKBACK = 5
SECTOR_RESONANCE_MIN = 2
INITIAL_CAPITAL = 100000.0

cash = INITIAL_CAPITAL  # 全局现金（开仓扣减 / 平仓回收+盈亏）


def main() -> None:
    parser = argparse.ArgumentParser(description="低位首板接力回测")
    parser.add_argument("--mode", choices=["single", "rolling"], default="single")
    parser.add_argument("--start-date", default=START_DATE)
    parser.add_argument("--end-date", default=END_DATE)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--step", type=int, default=15)
    args = parser.parse_args()

    import os
    db_url = os.environ.get("DATABASE_URL", "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4")
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

    lu_rows = db.scalars(
        select(LimitUpBoard).where(LimitUpBoard.trade_date >= trade_dates[0], LimitUpBoard.trade_date <= trade_dates[-1])
    ).all()
    lu_by_date: dict[str, list[Any]] = defaultdict(list)
    for r in lu_rows:
        lu_by_date[str(r.trade_date)].append(r)
    sector_limitup_by_date: dict[str, dict[str, int]] = {}
    for td, rows in lu_by_date.items():
        ind_count: dict[str, int] = defaultdict(int)
        for r in rows:
            if str(r.limit_type) == "U":
                ind_count[str(r.industry or "未知")] += 1
        sector_limitup_by_date[td] = ind_count

    all_codes = set(by_code.keys())
    stock_meta_list = db.scalars(select(Stock).where(Stock.code.in_(all_codes))).all()
    stock_meta = {str(s.code): {"name": str(s.name or s.code), "industry": s.industry or "未分类", "is_st": bool(s.is_st)} for s in stock_meta_list}

    # 60 日高低区间（用截至当日历史，避免未来函数）
    base_pos_cache: dict[tuple[str, str], float] = {}
    for code, bars in by_code.items():
        closes = [_f(b.close) for b in bars]
        highs = [_f(b.high) for b in bars]
        lows = [_f(b.low) for b in bars]
        for i, b in enumerate(bars):
            lo = min(lows[max(0, i - 59):i + 1])
            hi = max(highs[max(0, i - 59):i + 1])
            base_pos_cache[(code, str(b.trade_date))] = (closes[i] - lo) / (hi - lo) if hi > lo else 1.0

    return {
        "trade_dates": trade_dates, "by_code": by_code, "bar_map": bar_map,
        "date_to_idx": date_to_idx, "lu_by_date": lu_by_date,
        "sector_limitup_by_date": sector_limitup_by_date, "stock_meta": stock_meta,
        "base_pos_cache": base_pos_cache,
    }


def _simulate(data: dict[str, Any], trade_dates: list[str]) -> tuple[dict[str, Any], list[dict], list[float]]:
    """在已加载数据上模拟回测。trade_dates 为完整或切片后的交易日序列。"""
    global cash
    by_code = data["by_code"]
    bar_map = data["bar_map"]
    date_to_idx = data["date_to_idx"]
    lu_by_date = data["lu_by_date"]
    sector_limitup_by_date = data["sector_limitup_by_date"]
    stock_meta = data["stock_meta"]
    base_pos_cache = data["base_pos_cache"]
    date_set = set(trade_dates)

    trades: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    cash = INITIAL_CAPITAL
    pv = [1.0]

    for idx in range(len(trade_dates) - 1):
        td = trade_dates[idx]
        next_td = trade_dates[idx + 1]

        # ── 管理已有持仓（止损 / 到期） ──
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
                _close(pos, trades, active, bar_map, next_td, date_set, "止损(-5%)")
                continue
            if days_held >= HOLD_DAYS:
                _close(pos, trades, active, bar_map, next_td, date_set, f"持有{HOLD_DAYS}天到期")
                continue

        # ── 寻找当日首板信号 ──
        candidates = []
        for r in lu_by_date.get(td, []):
            if str(r.limit_type) != "U" or int(r.limit_times or 1) != 1:
                continue
            code = str(r.code)
            if code in active:
                continue
            meta = stock_meta.get(code) or {}
            if meta.get("is_st"):
                continue
            bar = bar_map.get((code, td))
            if bar is None:
                continue
            amount_yi = _f(bar.amount) / 1e8
            # turnover_rate 列在 2023-2025 全 NULL、2026 大量缺失 → 不可信，已移除该过滤；
            # 流动性由 amount(成交额) 兜底（MIN_AMOUNT_YI 已门控）。
            if amount_yi < MIN_AMOUNT_YI:
                continue
            if _prior_limit_up(by_code.get(code, []), td, date_to_idx, PRIOR_LIMITUP_LOOKBACK):
                continue
            if base_pos_cache.get((code, td), 1.0) > BASE_POS_MAX:
                continue
            ind = str(r.industry or meta.get("industry") or "未知")
            if sector_limitup_by_date.get(td, {}).get(ind, 0) < SECTOR_RESONANCE_MIN + 1:
                continue
            nb = bar_map.get((code, next_td))
            if nb is None:
                continue
            open_p = _f(nb.open) or _f(nb.close)
            if open_p <= 0 or (_f(nb.open) / _f(bar.close) - 1) * 100 >= 9.5:
                continue
            candidates.append((code, meta, ind, open_p))

        # ── 开新仓（等分管仓，扣现金） ──
        n_cand = max(1, len(candidates))
        for (code, meta, ind, open_p) in candidates:
            notional = cash * 0.5 / n_cand
            if notional <= 0:
                break
            cash -= notional
            active[code] = {
                "code": code, "name": meta.get("name", code), "industry": ind,
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


def _prior_limit_up(bars: list, td: str, date_to_idx: dict, lookback: int) -> bool:
    if td not in date_to_idx or not bars:
        return False
    i = date_to_idx[td]
    for b in bars:
        bi = date_to_idx.get(str(b.trade_date))
        if bi is None:
            continue
        if 0 < (i - bi) <= lookback and _f(b.pct_chg) >= 9.5:
            return True
    return False


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
    print("\n========== 低位首板接力 回测结果 ==========")
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
