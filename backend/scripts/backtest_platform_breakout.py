"""
平台突破（tight-range consolidation breakout on volume surge）— 独立回测脚本（T1 策略研发 / 诚实样本外验证）。

因子定义（因果机制）:
  个股在一段时间（L 日）内维持「窄幅横盘」：最高价与最低价之差占均价比例很小，且价格
  基本水平（未明显下行）。这是「抛压耗尽、筹码沉淀」的典型形态——卖盘不愿低价出，
  浮筹被消化。横盘期间**成交量相对前段明显萎缩**（缩量蓄势），印证供给端枯竭。
  随后某日 T 以**显著放量**向上突破横盘区上沿（收盘价创 L 日新高），是「需求重新涌入、
  平台突破」的确认信号。游资（陈小群等）最爱的「平台突破战法」即此逻辑。

  与现有策略的区分：
    - trend-pullback-continuation（回调延续）：买上升趋势中的回调，不要求横盘蓄势+放量突破；
    - chip-single-peak-breakout（筹码单峰突破）：从筹码分布角度突破，不要求「缩量横盘」结构；
    - limit-up-reseal / 首板接力：依赖涨停事件；
  本因子是「窄幅横盘 + 缩量蓄势 + 放量突破上沿」三者同时成立的平台突破，填补空白，
  属动量延续（momentum continuation），非均值回归——与 #168/#170/#182 失败的均值回归因子
  因果方向相反。

信号判定（只用 T 及之前数据，无未来函数）:
  - 横盘窗口 window = bars[i-L+1 .. i]（L 日）：
      * 窄幅：(max(high)-min(low)) / mean(close) <= RANGE_PCT
      * 水平：window 末收盘 >= window 初收盘 * 0.97（未明显下行，排除下降通道）
      * 缩量蓄势：window 均量 <= DRYUP * 前段(前 2L 日)均量（供给枯竭）
  - 突破日 T = bars[i+1]：
      * 收盘价 > window 最高价（创 L 日新高，突破上沿）
      * 成交量 > BREAKOUT_VOL_MULT * window 均量（放量确认）
      * 当日涨跌幅 pct_chg ∈ [BREAK_PCT_LO, BREAK_PCT_HI]（真实上攻，非一字/非暴跌）
      * close_T >= ma20_T * 0.98（温和站上 20 日线，处上升基座而非底部猎杀）
  - 流动性：amount_T >= MIN_AMOUNT（亿）；换手 turnover ∈ [MIN_TURNOVER, MAX_TURNOVER]；非 ST
交易规则（无未来函数）:
  - T+1 开盘买入；若 T+1 开盘较 T 收盘低开 >1.5%（突破失败）或 >= +9%（一字买不到）则跳过；
  - 持有至 T+1..T+5（5 个交易日），或期间任一日开盘较买入价 <= -5.7% 止损，或 >= +8% 止盈；
    未触发则到期收盘卖出；
  - 成本：单边 0.07%（佣金万1 + 印花税0.05%卖出 + 滑点代理，与 production ADDITIONAL_COST_PCT
    及 T2 / self_check D 项口径一致，整笔 round-trip 0.07%）；
  - 同日最多开 MAX_POSITIONS_PER_DAY 个仓位（等权），避免单日信号过多过度分散。

诚实样本外验证：
  - 纯函数核心（_detect_platform_breakout / _compute_metrics）与 DB 解耦，可单测；
  - `--mode rolling` 用滚动窗口（训练/评测）统计「胜率>50% 窗口占比 / 盈亏比>1.4 占比 /
    均净>0 占比」，判定因子是否样本外稳定（并非噪声）。

用法:
  cd backend && source .venv/bin/activate && python scripts/backtest_platform_breakout.py
  cd backend && source .venv/bin/activate && python scripts/backtest_platform_breakout.py --mode rolling
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

START_DATE = "2024-01-01"
END_DATE = "2026-06-24"

# 成本：整笔 round-trip 0.07%，与 production ADDITIONAL_COST_PCT 及 self_check D 项口径一致
COST_PCT = 0.0007

HOLD_DAYS = 5
STOP_LOSS_PCT = -5.7
TAKE_PROFIT_PCT = 8.0

# ── 横盘窗口参数 ──
L = 15                      # 横盘窗口长度（交易日）
RANGE_PCT = 0.12            # 窄幅：(max_high-min_low)/mean(close) <= 12%
HORIZONTAL_FLOOR = 0.97     # window 末收盘 >= window 初收盘 * 0.97（基本水平）
DRYUP = 0.75                # 缩量：window 均量 <= 前段均量 * 0.75
# ── 突破日参数 ──
BREAKOUT_VOL_MULT = 1.8     # 突破日量 >= window 均量 * 1.8
BREAK_PCT_LO = 3.0          # 突破日涨跌幅下限
BREAK_PCT_HI = 9.5          # 突破日涨跌幅上限（非一字）
MA20_ALIGN = 0.98           # close_T >= ma20_T * 0.98（温和站上 20 日线）
# ── 流动性 ──
MIN_AMOUNT_YI = 3.0
MIN_TURNOVER = 1.5
MAX_TURNOVER = 20.0
# ── 入场跳空过滤 ──
ENTRY_GAPDOWN_SKIP = -1.5
ENTRY_GAPUP_SKIP = 9.0
# ── 组合 ──
MAX_POSITIONS_PER_DAY = 8
INITIAL_CAPITAL = 100000.0


@dataclass
class Params:
    L: int = L
    range_pct: float = RANGE_PCT
    horizontal_floor: float = HORIZONTAL_FLOOR
    dryup: float = DRYUP
    breakout_vol_mult: float = BREAKOUT_VOL_MULT
    break_pct_lo: float = BREAK_PCT_LO
    break_pct_hi: float = BREAK_PCT_HI
    ma20_align: float = MA20_ALIGN
    min_amount_yi: float = MIN_AMOUNT_YI
    min_turnover: float = MIN_TURNOVER
    max_turnover: float = MAX_TURNOVER


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _detect_platform_breakout(window: List[dict], t_bar: dict, params: Params,
                              prior_window: Optional[List[dict]] = None) -> bool:
    """纯函数：判定 T 日是否为「平台突破」信号。

    window: 横盘区 K 线（含 open/high/low/close/volume 等），长度 = params.L
    t_bar:  突破日 K 线（bars[i+1]）
    prior_window: 横盘区之前的 K 线（用于缩量对比），长度 >= params.L；为 None 时不判缩量
    无未来函数：只用 window + t_bar + prior_window（均 <= T 日）的数据。
    """
    if window is None or len(window) < params.L:
        return False
    highs = [_f(b["high"]) for b in window]
    lows = [_f(b["low"]) for b in window]
    closes = [_f(b["close"]) for b in window]
    vols = [_f(b["volume"]) for b in window]
    hh = max(highs)
    ll = min(lows)
    avgc = _mean(closes)
    if avgc <= 0 or hh <= ll:
        return False

    # 窄幅横盘
    if (hh - ll) / avgc > params.range_pct:
        return False
    # 基本水平（未明显下行）
    if closes[-1] < closes[0] * params.horizontal_floor:
        return False
    win_vol = _mean(vols)

    # 缩量蓄势（相对前段）
    if prior_window:
        prior_vols = [_f(b["volume"]) for b in prior_window]
        if not prior_vols:
            return False
        if win_vol > params.dryup * _mean(prior_vols):
            return False

    # 突破日判定
    t_close = _f(t_bar["close"])
    t_high = _f(t_bar["high"])
    t_vol = _f(t_bar["volume"])
    t_pct = _f(t_bar.get("pct_chg"))
    # 注意：daily_kbars.ma20 / turnover_rate 历史回填严重缺失（2024/2025 全 NULL），
    # 不能直接用 DB 列，否则会静默杀死全部历史信号。改用在内存中从 OHLCV 计算的
    # ma20_calc（见 _load_data 的尾部滚动均值预计算），使回测自洽、不受回填缺口影响。
    t_ma20 = _f(t_bar.get("ma20_calc"))
    t_amount = _f(t_bar.get("amount")) / 1e8

    if win_vol <= 0:
        return False
    # 收盘价创横盘区新高（突破上沿）
    if t_close <= hh:
        return False
    # 放量确认
    if t_vol < params.breakout_vol_mult * win_vol:
        return False
    # 真实上攻幅度，非一字/非暴跌
    if not (params.break_pct_lo <= t_pct <= params.break_pct_hi):
        return False
    # 温和站上 20 日线（处上升基座）——用内存计算的 ma20_calc
    if t_ma20 > 0 and t_close < t_ma20 * params.ma20_align:
        return False
    # 流动性（成交额完整，替代缺失的 turnover_rate）
    if t_amount < params.min_amount_yi:
        return False
    # 突破日不能是涨停板（一字买不到，且无后续空间）
    if t_high >= t_close * 1.098:
        return False
    return True


def _load_data(db: Session, start_date: str, end_date: str) -> dict:
    """一次性加载 OHLCV 到内存（列投影，降低内存占用）。

    关键：daily_kbars 的 ma20 / turnover_rate 历史回填严重缺失（2024/2025 全 NULL，
    2026 也大部分缺失），直接用会静默杀死全部历史信号。因此本加载器只取完整可靠的
    OHLCV + amount + pct_chg，并在内存中为每个 code 预计算滚动 20 日收盘均线
    ma20_calc，供因子判定使用，使回测自洽、不受回填缺口影响。
    """
    rows = db.execute(
        text(
            "SELECT code, trade_date, `open`, high, low, close, volume, amount, pct_chg "
            "FROM daily_kbars "
            "WHERE trade_date >= :s AND trade_date <= :e ORDER BY trade_date ASC"
        ).bindparams(s=start_date, e=end_date)
    ).fetchall()

    trade_dates_set: set = set()
    by_code: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        code = str(r[0])
        td = str(r[1])
        trade_dates_set.add(td)
        by_code[code].append({
            "code": code, "trade_date": td,
            "open": _f(r[2]), "high": _f(r[3]), "low": _f(r[4]), "close": _f(r[5]),
            "volume": _f(r[6]), "amount": _f(r[7]), "pct_chg": _f(r[8]),
            "ma20_calc": 0.0,
        })
    for code in by_code:
        bars = by_code[code]
        bars.sort(key=lambda x: x["trade_date"])
        # 内存预计算滚动 20 日收盘均线（缺失回填缺口的鲁棒替代）
        closes = [b["close"] for b in bars]
        n = len(closes)
        for i in range(n):
            lo = max(0, i - 19)
            bars[i]["ma20_calc"] = _mean(closes[lo:i + 1])

    trade_dates = sorted(trade_dates_set)
    all_codes = set(by_code.keys())
    stock_meta_list = db.execute(
        text("SELECT code, name, industry, is_st FROM stocks WHERE code IN :codes")
        .bindparams(codes=tuple(sorted(all_codes))) if len(all_codes) > 1
        else text(f"SELECT code, name, industry, is_st FROM stocks WHERE code = '{list(all_codes)[0]}'")
    ).fetchall()
    stock_meta = {str(s[0]): {"name": str(s[1] or s[0]), "industry": s[2] or "未分类",
                              "is_st": bool(s[3])} for s in stock_meta_list}

    logger.info("交易日历: %s ~ %s, %d 天; 个股数 %d; K线 %d 行",
                trade_dates[0], trade_dates[-1], len(trade_dates), len(by_code), len(rows))
    return {"trade_dates": trade_dates, "by_code": by_code, "stock_meta": stock_meta}


def _precompute_signals(data: dict, params: Params) -> Dict[str, List[dict]]:
    """第一遍：按 code 扫描，预计算所有平台突破信号（仅用 T 及之前数据）。"""
    by_code = data["by_code"]
    stock_meta = data["stock_meta"]
    signals_by_entry: Dict[str, List[dict]] = defaultdict(list)
    total = 0
    for code, bars in by_code.items():
        meta = stock_meta.get(code) or {}
        if meta.get("is_st"):
            continue
        n = len(bars)
        need = 3 * params.L  # 横盘 L + 前段 2L
        if n < need + 2:
            continue
        for i in range(params.L - 1, n - 2):  # window=[i-L+1..i], T=i+1, entry=i+2
            window = bars[i - params.L + 1: i + 1]
            t_bar = bars[i + 1]
            entry_bar = bars[i + 2]
            if not _detect_platform_breakout(window, t_bar, params, prior_window=bars[i - 3 * params.L + 1: i - params.L + 1]):
                continue
            open_p = entry_bar["open"] or t_bar["close"]
            if open_p <= 0:
                continue
            entry_gap = (open_p / t_bar["close"] - 1) * 100.0
            if entry_gap >= ENTRY_GAPUP_SKIP or entry_gap < ENTRY_GAPDOWN_SKIP:
                continue
            signals_by_entry[entry_bar["trade_date"]].append({
                "code": code, "name": meta.get("name", code), "industry": meta.get("industry", "未分类"),
                "t_date": t_bar["trade_date"], "entry_date": entry_bar["trade_date"],
                "entry_price": open_p, "score": (t_bar["close"] - window_high(window)) / window_high(window) if window_high(window) > 0 else 0,
            })
            total += 1
    logger.info("预计算信号总数: %d", total)
    return signals_by_entry


def window_high(window: List[dict]) -> float:
    return max(_f(b["high"]) for b in window)


def _simulate(data: dict, trade_dates: List[str], signals_by_entry: Dict[str, List[dict]],
              ) -> tuple:
    """带现金流的组合模拟（等权、半仓开新、止损止盈到期平仓）。"""
    bar_map: Dict[tuple, dict] = {}
    for code, bars in data["by_code"].items():
        for b in bars:
            bar_map[(code, b["trade_date"])] = b

    trades: List[dict] = []
    active: Dict[str, dict] = {}
    cash = INITIAL_CAPITAL
    pv = [1.0]

    def close_pos(pos, exit_date, reason, force=False):
        nonlocal cash
        code = pos["code"]
        entry = pos["entry_price"]
        notional = pos.get("notional", 0.0)
        eb = bar_map.get((code, exit_date))
        if eb is None:
            exit_p = entry
        else:
            exit_p = eb["open"] if (not force and eb["open"] > 0) else eb["close"]
        if exit_p <= 0:
            exit_p = entry
        gross = (exit_p - entry) / entry * 100.0
        net = gross - COST_PCT * 100.0
        trades.append({
            "code": code, "name": pos.get("name", code), "industry": pos.get("industry", ""),
            "entry_date": pos["entry_date"], "exit_date": exit_date,
            "entry_price": round(entry, 3), "exit_price": round(exit_p, 3),
            "net_return_pct": round(net, 3), "holding_days": pos.get("day_held", 1),
            "accurate": net > 0, "exit_reason": reason,
        })
        cash += notional * (1 + net / 100.0)
        active.pop(code, None)

    for idx in range(1, len(trade_dates) - 1):
        td = trade_dates[idx]
        next_td = trade_dates[idx + 1]

        for code, pos in list(active.items()):
            if not pos.get("entered"):
                continue
            nb = bar_map.get((code, td))
            if nb is None:
                continue
            open_p = nb["open"] or nb["close"]
            ret = (open_p - pos["entry_price"]) / pos["entry_price"] * 100.0
            days_held = pos["day_held"] + 1
            pos["day_held"] = days_held
            if ret <= STOP_LOSS_PCT:
                close_pos(pos, next_td, "止损(-5.7%)")
                continue
            if ret >= TAKE_PROFIT_PCT:
                close_pos(pos, next_td, "止盈(+8%)")
                continue
            if days_held >= HOLD_DAYS:
                close_pos(pos, next_td, f"持有{HOLD_DAYS}天到期")
                continue

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
            px = cb["close"] if cb else pos["entry_price"]
            equity += pos["notional"] * px / pos["entry_price"]
        pv.append(equity / INITIAL_CAPITAL)

    last_td = trade_dates[-1]
    for code, pos in list(active.items()):
        close_pos(pos, last_td, "回测结束平仓", force=True)

    metrics = _compute_metrics(trades, pv)
    return metrics, trades, pv


def _compute_metrics(trades: List[dict], pv: List[float]) -> dict:
    if not trades:
        return {"total_trades": 0, "accuracy": 0.0, "avg_net": 0.0, "pl_ratio": 0.0,
                "max_dd": 0.0, "cum_ret": 0.0}
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
    return {"total_trades": len(trades), "accuracy": acc, "avg_net": avg_net,
            "pl_ratio": pl, "max_dd": mdd, "cum_ret": (pv[-1] - 1) * 100}


def _print(trades: List[dict], m: dict, trade_dates: List[str]) -> None:
    print("\n========== 平台突破（缩量横盘+放量突破）回测结果 ==========")
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


def _print_yearly(trades: List[dict]) -> None:
    by_year: Dict[str, List[dict]] = defaultdict(list)
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


def rolling_window_backtest(data: dict, signals_by_entry: Dict[str, List[dict]],
                            *, train_days: int = 60, test_days: int = 30, step: int = 15) -> List[dict]:
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
    parser = argparse.ArgumentParser(description="平台突破（缩量横盘+放量突破）回测")
    parser.add_argument("--mode", choices=["single", "rolling"], default="single")
    parser.add_argument("--start-date", default=START_DATE)
    parser.add_argument("--end-date", default=END_DATE)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--step", type=int, default=15)
    args = parser.parse_args()

    params = Params()
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        data = _load_data(db, args.start_date, args.end_date)
        signals_by_entry = _precompute_signals(data, params)
        if args.mode == "single":
            metrics, trades, pv = _simulate(data, data["trade_dates"], signals_by_entry)
            _print(trades, metrics, data["trade_dates"])
        else:
            rolling_window_backtest(data, signals_by_entry, train_days=args.train_days,
                                    test_days=args.test_days, step=args.step)


if __name__ == "__main__":
    main()
