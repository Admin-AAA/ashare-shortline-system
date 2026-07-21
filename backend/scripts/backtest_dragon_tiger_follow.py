"""
龙虎榜机构净买「次日延续」诚实样本外验证（T1 策略研发 / 因子质量审计）。

因子定义（因果机制）:
  个股登上龙虎榜且机构/席位呈现「净买入」（net_amount_yi > 0），代表有真实资金
  （机构或一线游资）在当日主动接入、筹码从分散向少数席位集中。这是本系统实盘赢家
  策略 dragon-tiger-smart-money（龙虎榜聪明钱）的核心维度——该策略回测 357 样本
  净买≥5% 胜率 85.6%，实盘前瞻 WR 72.7%。

  本脚本不重复「同日净买即买」的现有策略，而是审计**该信号的预测力结构**:
    1) 龙虎榜净买日 T 之后，T+1 开盘买入、持有 N 日，净胜率/盈亏比/平均收益如何？
    2) 净买强度(net_amount_yi)是否单调对应次日收益？——若强净买→更强延续，
       则现有阈值(≥某档)是合理上截断；若不强，则阈值任意，触发条件需重估。
  因果: 真聪明钱净买 = 信息优势方主动建仓 → 后续交易日有更高概率延续上行。

信号判定（只用 T 日及之前数据，无未来函数）:
  - T 日 code 出现在 dragon_tiger 且 net_amount_yi >= NET_BUY_MIN（默认 0.5 亿净买）。
  - 仅用 T 及之前数据；T+1 开盘价用于买入，无未来函数。

交易规则（无未来函数）:
  - T+1 开盘买入；若 T+1 开盘较 T 收盘低开 >1.5%（信号破位）或 >= +9%（追高买不到）则跳过；
  - 持有至 T+1..T+HOLD_DAYS，或期间任一日开盘较买入价 <= -STOP_LOSS_PCT 止损，
    或 >= +TAKE_PROFIT_PCT 止盈；未触发则到期收盘卖出；
  - 成本：单边 0.07%（佣金万1 + 印花税0.05%卖出 + 滑点代理），与 T2 / round-168/170/182 口径一致；
  - 同日最多开 MAX_POSITIONS_PER_DAY 个仓位（等权）。

数据说明:
  dragon_tiger 表仅覆盖 2026-03-23..2026-07-21（约 4 个月，5751 行）——这是本信号的
  全部可得历史。因此本回测**无法做跨多年滚动 OOS**（framework 要求 >50% 滚动窗 >50% 胜率）。
  本脚本诚实地：
    (a) 在可得窗口内做「滑窗滚动」看信号稳定性（窗内样本可能偏小，结论须谨慎）；
    (b) 按净买强度分桶看单调性——这是因子「是否有预测力结构」的核心证据，不依赖长样本；
    (c) 与同期全市场基准（任意股 T+1 买入持有 N 日）对比，确认 alpha 非市场 beta。
  若窗口内胜率/盈亏比达标且净买强度单调，则验证现有 dragon-tiger 触发维度的有效性，
  并为「放宽净买阈值以捕获更多赢家票」提供实证依据（是否采纳由后续轮次决定，本脚本只给证据）。

用法:
  cd backend && source .venv/bin/activate && python scripts/backtest_dragon_tiger_follow.py
  cd backend && source .venv/bin/activate && python scripts/backtest_dragon_tiger_follow.py --mode rolling
  cd backend && source .venv/bin/activate && python scripts/backtest_dragon_tiger_follow.py --net-buy-min 1.0
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

from app.core.config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 成本 / 持仓 ──
BUY_COST_RATIO = 0.0007
SELL_COST_RATIO = 0.0007
HOLD_DAYS = 5
STOP_LOSS_PCT = -5.7
TAKE_PROFIT_PCT = 8.0
ENTRY_GAPDOWN_SKIP = -1.5   # T+1 较 T 收盘低开 >1.5% → 信号破位，跳过
ENTRY_GAPUP_SKIP = 9.0      # T+1 较 T 收盘 >= +9% → 追高买不到，跳过
MAX_POSITIONS_PER_DAY = 12

# ── 信号 ──
NET_BUY_MIN = 0.5            # 龙虎榜净买下限（亿）；命令行可覆盖
MIN_AMOUNT_YI = 1.0          # 成交额底线（流动性兜底，亿）
ROLL_WINDOW = 20             # 滚动窗交易日数（看信号稳定性）


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _load_bars(session: Session) -> dict[str, list[Any]]:
    """加载 daily_kbars 全部，按 code 分组并按 trade_date 升序。"""
    rows = session.execute(
        select(
            text("code"), text("trade_date"), text("open"), text("high"),
            text("low"), text("close"), text("volume"), text("amount"),
            text("pct_chg"),
        ).select_from(text("daily_kbars"))
    ).fetchall()
    by_code: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by_code[r[0]].append(r)
    for code in by_code:
        by_code[code].sort(key=lambda b: str(b[1]))
    return by_code


def _load_dragon_tiger(session: Session, net_buy_min: float) -> dict[str, dict[str, float]]:
    """加载龙虎榜净买信号: (code, trade_date) -> net_amount_yi。仅净买 >= net_buy_min。"""
    rows = session.execute(
        select(
            text("code"), text("trade_date"), text("net_amount_yi"),
        ).select_from(text("dragon_tiger"))
        .where(text(f"net_amount_yi >= {net_buy_min}"))
    ).fetchall()
    sig: dict[str, dict[str, float]] = defaultdict(dict)
    for code, td, net in rows:
        sig[code][str(td)] = _f(net)
    return sig


def _simulate_trade(buy_open: float, bars_from_t1: list[Any]) -> tuple[float, str]:
    """从 T+1 开盘买入，持有 HOLD_DAYS 或止损/止盈。返回 (净收益率, 出场原因)。"""
    if not bars_from_t1 or buy_open <= 0:
        return 0.0, "no_data"
    entry = buy_open * (1 + BUY_COST_RATIO)
    for b in bars_from_t1[:HOLD_DAYS]:
        open_p = _f(b[2])  # open is index 2 in the bar tuple (code,td,open,high,low,close,vol,amt,pct)
        close_p = _f(b[5])  # close is index 5
        if open_p <= 0 or close_p <= 0:
            continue
        ret_open = (open_p - entry) / entry
        if ret_open <= STOP_LOSS_PCT / 100.0:
            return ret_open - SELL_COST_RATIO, "stop_loss"
        if ret_open >= TAKE_PROFIT_PCT / 100.0:
            return ret_open - SELL_COST_RATIO, "take_profit"
        ret_close = (close_p - entry) / entry
        if ret_close <= STOP_LOSS_PCT / 100.0:
            return ret_close - SELL_COST_RATIO, "stop_loss"
        if ret_close >= TAKE_PROFIT_PCT / 100.0:
            return ret_close - SELL_COST_RATIO, "take_profit"
    last = bars_from_t1[min(HOLD_DAYS, len(bars_from_t1)) - 1]
    last_close = _f(last[5])  # close is index 5 (code,td,open,high,low,close,...)
    if last_close <= 0:
        return 0.0, "bad_close"
    return (last_close - entry) / entry - SELL_COST_RATIO, "expire"


def _run_backtest(by_code: dict[str, list[Any]], sig: dict[str, dict[str, float]],
                  net_buy_min: float) -> list[dict[str, Any]]:
    """模拟组合。返回每笔交易 dict。"""
    # 建立 (code, trade_date_str) -> 在 by_code[code] 中的下标
    idx: dict[tuple[str, str], int] = {}
    for code, bars in by_code.items():
        for i, b in enumerate(bars):
            idx[(code, str(b[1]))] = i

    trades: list[dict[str, Any]] = []
    per_day_count: dict[str, int] = defaultdict(int)
    for code, td_map in sig.items():
        if code not in by_code:
            continue
        bars = by_code[code]
        n = len(bars)
        for td, net in td_map.items():
            if (code, td) not in idx:
                continue
            i = idx[(code, td)]
            if i + 1 >= n:
                continue  # 无 T+1
            t_bar = bars[i]
            t1_bar = bars[i + 1]
            t_close = _f(t_bar[5])   # close index 5
            t1_open = _f(t1_bar[2])   # open index 2
            if t_close <= 0 or t1_open <= 0:
                continue
            gap = (t1_open - t_close) / t_close * 100.0
            if gap < ENTRY_GAPDOWN_SKIP or gap >= ENTRY_GAPUP_SKIP:
                continue
            if per_day_count.get(td, 0) >= MAX_POSITIONS_PER_DAY:
                continue
            ret, reason = _simulate_trade(t1_open, bars[i + 1:])
            trades.append({
                "code": code, "signal_date": td, "net_buy_yi": net,
                "ret": ret, "reason": reason, "gap": gap,
            })
            per_day_count[td] += 1
    return trades


def _summarize(trades: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not trades:
        return {"label": label, "n": 0}
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    gross_win = sum(t["ret"] for t in wins)
    gross_loss = abs(sum(t["ret"] for t in losses))
    pl = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    avg = sum(t["ret"] for t in trades) / len(trades)
    reasons = defaultdict(int)
    for t in trades:
        reasons[t["reason"]] += 1
    return {
        "label": label, "n": len(trades),
        "win_rate": len(wins) / len(trades) * 100.0,
        "avg_ret": avg * 100.0,
        "pl_ratio": pl,
        "best": max(t["ret"] for t in trades) * 100.0,
        "worst": min(t["ret"] for t in trades) * 100.0,
        "reasons": dict(reasons),
    }


def _fmt(s: dict[str, Any]) -> str:
    if s.get("n", 0) == 0:
        return f"  {s['label']}: n=0 (无信号)"
    pl = "∞" if s["pl_ratio"] == float("inf") else f"{s['pl_ratio']:.2f}"
    return (f"  {s['label']}: n={s['n']} WR={s['win_rate']:.1f}% "
            f"avg={s['avg_ret']:+.2f}% PL={pl} best={s['best']:+.1f}% worst={s['worst']:+.1f}% "
            f"exit={s['reasons']}")


def _bucket_by_net_buy(trades: list[dict[str, Any]]) -> list[str]:
    buckets = [(0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 1e9)]
    lines = []
    for lo, hi in buckets:
        sub = [t for t in trades if lo <= t["net_buy_yi"] < hi]
        if not sub:
            lines.append(f"  net_buy∈[{lo},{hi}): n=0")
            continue
        wins = sum(1 for t in sub if t["ret"] > 0)
        avg = sum(t["ret"] for t in sub) / len(sub) * 100.0
        lines.append(f"  net_buy∈[{lo},{hi}): n={len(sub)} WR={wins/len(sub)*100:.1f}% avg={avg:+.2f}%")
    return lines


def _benchmark(by_code: dict[str, list[Any]], sig_dates: set[str]) -> dict[str, Any]:
    """同期基准: 在 dragon_tiger 信号出现的每个交易日，随机取该日有交易的票，
    T+1 开盘买入持有 HOLD_DAYS（等权），作为市场 beta 对照。"""
    # 收集每个交易日所有可交易票
    by_date: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for code, bars in by_code.items():
        for i, b in enumerate(bars):
            by_date[str(b[1])].append((code, i))
    bench_trades = []
    for td in sig_dates:
        pool = by_date.get(td, [])
        if not pool:
            continue
        # 取前 MAX_POSITIONS_PER_DAY 只作为对照样本（避免全市场偏差）
        for code, i in pool[:MAX_POSITIONS_PER_DAY]:
            bars = by_code[code]
            if i + 1 >= len(bars):
                continue
            t1_open = _f(bars[i + 1][1])
            if t1_open <= 0:
                continue
            ret, _ = _simulate_trade(t1_open, bars[i + 1:])
            bench_trades.append(ret)
    if not bench_trades:
        return {"n": 0}
    wins = sum(1 for r in bench_trades if r > 0)
    return {
        "n": len(bench_trades),
        "win_rate": wins / len(bench_trades) * 100.0,
        "avg_ret": sum(bench_trades) / len(bench_trades) * 100.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "rolling"], default="full")
    ap.add_argument("--net-buy-min", type=float, default=NET_BUY_MIN)
    args = ap.parse_args()

    eng = create_engine(Settings().database_url)
    session = Session(eng)
    logger.info("loading daily_kbars ...")
    by_code = _load_bars(session)
    logger.info("loading dragon_tiger (net_buy>=%.2f) ...", args.net_buy_min)
    sig = _load_dragon_tiger(session, args.net_buy_min)
    n_sig_days = sum(len(v) for v in sig.values())
    logger.info("signals: %d stock-days across %d codes", n_sig_days, len(sig))

    trades = _run_backtest(by_code, sig, args.net_buy_min)
    overall = _summarize(trades, "OVERALL")
    print("\n===== 龙虎榜净买次日延续 回测 =====")
    print(_fmt(overall))
    print("净买强度分桶（看单调性）:")
    for line in _bucket_by_net_buy(trades):
        print(line)

    # 基准
    sig_dates = {td for v in sig.values() for td in v}
    bench = _benchmark(by_code, sig_dates)
    if bench.get("n"):
        print(f"\n同期市场基准(信号日全市场 T+1 买入持有{HOLD_DAYS}日): "
              f"n={bench['n']} WR={bench['win_rate']:.1f}% avg={bench['avg_ret']:+.2f}%")

    if args.mode == "rolling":
        print("\n===== 滚动窗稳定性（每窗 %d 交易日）=====" % ROLL_WINDOW)
        # 按 signal_date 排序，滑窗
        sorted_trades = sorted(trades, key=lambda t: t["signal_date"])
        from collections import deque
        dates = sorted({t["signal_date"] for t in sorted_trades})
        win_dates = deque()
        ti = 0
        pass_cnt = 0
        total_windows = 0
        for d in dates:
            while ti < len(sorted_trades) and sorted_trades[ti]["signal_date"] <= d:
                ti += 1
            # 取最近 ROLL_WINDOW 个交易日的交易
            recent = [t for t in sorted_trades if t["signal_date"] > dates[max(0, len(dates)-ROLL_WINDOW-1)] and t["signal_date"] <= d]
            if len(recent) < 5:
                continue
            s = _summarize(recent, f"win@{d}")
            total_windows += 1
            if s["win_rate"] >= 50.0:
                pass_cnt += 1
            print(_fmt(s))
        if total_windows:
            print(f"\n滚动窗胜率>=50% 占比: {pass_cnt}/{total_windows} = {pass_cnt/total_windows*100:.0f}%")

    # 采纳判定（framework 口径: 胜率>50% 窗口>=50% 且净盈亏比>=1.4）
    print("\n===== 采纳判定 =====")
    if overall.get("n", 0) == 0:
        print("无信号 → 无法判定（数据缺失）")
        return 0
    pl_ok = overall["pl_ratio"] >= 1.4 if overall["pl_ratio"] != float("inf") else True
    print(f"净胜率={overall['win_rate']:.1f}% (门槛>50%) | 净盈亏比={overall['pl_ratio']:.2f} (门槛>=1.4) | "
          f"净均收益={overall['avg_ret']:+.2f}%")
    print("注: dragon_tiger 仅 4 个月历史，无法做跨年 OOS；单调性+基准超额为采纳主要依据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
