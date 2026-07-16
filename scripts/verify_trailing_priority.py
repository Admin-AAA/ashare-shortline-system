"""反事实回测：trailing stop 优先于固定止损（跨 bar 场景）的边际收益验证。

背景（第 162 轮 D 项）：
  002674 @ 2026-07-08：持有期 max_return +13.89%，却因 low 触及固定止损 -5.7% 出场
  （exit_reason=stop_loss_hit）。根因：outcome_simulation._resolve_exit_with_trailing
  在 534 行让固定止损无脑优先于 trailing——即使 trailing 已激活且 trailing_stop_price
  远高于 stop_loss_price，仍返回 stop_loss_hit，丢失跨 bar 锁利机会。

改动假设：
  当 trailing 已激活（trailing_activated=True）且 trailing_stop_price > stop_loss_price
  时，trailing 应优先于固定止损。仅跨 bar 场景（trailing 在前 bar 激活，本 bar low 同时
  触及止损与 trailing 触发价）——此场景时序确定（前 bar high 激活，本 bar low 触发），
  不违反 trailing-stop-backtest-pitfall 教训（同 bar 时序不确定才需 min(open, 触发价)）。

口径：
  - 只对 LIVE formal 样本（rec_*），按 (code, trade_date) 去重
  - 只看当前 exit_reason=stop_loss_hit 的笔（变体可能改成 trailing_stop_hit 锁利）
  - 扣费 0.07%/笔
  - 对比：当前逻辑净盈亏比 vs 变体净盈亏比
"""
from __future__ import annotations

import sys
from collections import Counter

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, "/Users/wangao/Desktop/ashare-shortline-system/backend")

from app.core.config import get_settings
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services.outcome_simulation import (
    DEFAULT_STOP_LOSS_PCT,
    MAX_STOP_LOSS_PCT,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    _as_float,
    _exit_execution_block_reason,
    _resolve_exit,
    _resolve_stop_loss_price,
    _resolve_take_profit_price,
)

COST_PCT = 0.07  # 单笔扣费


def _resolve_exit_with_trailing_priority(
    holding_bars: list,
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """变体：trailing 已激活且 trailing_stop_price > stop_loss_price 时，trailing 优先。

    与生产 _resolve_exit_with_trailing 的唯一差异：534 行原本 `if low <= stop_loss_price:
    return stop_loss_hit`，现改为 `if low <= stop_loss_price and (not trailing_activated
    or trailing_stop_price <= stop_loss_price): return stop_loss_hit`——即 trailing 已激活
    且 trailing 锁价位更高时，不立即返回止损，先让后面的 trailing 触发判断处理。
    """
    locked_limit_down_bars = 0
    hwm = 0.0
    trailing_activated = False
    for bar in holding_bars:
        if _exit_execution_block_reason(code, bar):
            locked_limit_down_bars += 1
            continue
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if bar_open > 0 and low > 0:
            if bar_open <= stop_loss_price:
                if entry_price > 0:
                    min_exit_price = entry_price * (1 + -15.0 / 100.0)
                    gap_exit = max(bar_open, min_exit_price)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
        activated_this_bar = False
        if entry_price > 0 and high > 0:
            bar_high_ret = (high / entry_price - 1) * 100
            if bar_high_ret > hwm:
                hwm = bar_high_ret
            if not trailing_activated and hwm >= TRAILING_STOP_ACTIVATE_PCT:
                trailing_activated = True
                activated_this_bar = True
        # === 变体核心改动：仅跨 bar 场景（trailing 在前 bar 已激活，本 bar low 触止损）===
        # 同 bar 刚激活（activated_this_bar=True）时仍 stop_loss 优先——日线时序不确定，
        # high/low 谁先到无法判断，乐观假设 high 先到会过拟合（trailing-stop-backtest-pitfall）。
        # 仅跨 bar（trailing 前一 bar 已激活，本 bar low 才触）时序确定，可让 trailing 锁利优先。
        trailing_stop_price = entry_price * (1 + (hwm - TRAILING_STOP_CALLBACK_PCT) / 100.0) if entry_price > 0 else 0.0
        if low > 0 and low <= stop_loss_price:
            if not trailing_activated or activated_this_bar or trailing_stop_price <= stop_loss_price:
                return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
            # 跨 bar：trailing 前已激活且锁价位更高，用 min(open, tsp) 保守口径让 trailing 锁利
        if trailing_activated and entry_price > 0 and low > 0:
            tsp = entry_price * (1 + (hwm - TRAILING_STOP_CALLBACK_PCT) / 100.0)
            if low <= tsp:
                if activated_this_bar:
                    exit_px = tsp
                else:
                    exit_px = min(bar_open, tsp) if bar_open > 0 else tsp
                return exit_px, str(bar.trade_date), "trailing_stop_hit"
    last_bar = holding_bars[-1]
    if locked_limit_down_bars and locked_limit_down_bars == len(holding_bars):
        return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
    if locked_limit_down_bars:
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _resolve_exit_variant(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
    """变体 _resolve_exit：基准 take_profit 路径不变，stop_loss/horizon 路径用变体 trailing。"""
    base_exit = _resolve_exit_base_export(holding_bars, stop_loss_price, take_profit_price,
                                          code=code, entry_price=entry_price)
    if base_exit[2].startswith("take_profit"):
        return base_exit
    return _resolve_exit_with_trailing_priority(
        holding_bars, stop_loss_price, take_profit_price,
        code=code, entry_price=entry_price,
    )


def _resolve_exit_base_export(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
    """复刻 _resolve_exit_base（生产私有，此处复制以便变体调用）。"""
    locked_limit_down_bars = 0
    for bar in holding_bars:
        if _exit_execution_block_reason(code, bar):
            locked_limit_down_bars += 1
            continue
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if bar_open > 0 and low > 0:
            if bar_open <= stop_loss_price:
                if entry_price > 0:
                    min_exit_price = entry_price * (1 + -15.0 / 100.0)
                    gap_exit = max(bar_open, min_exit_price)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
            if bar_open >= take_profit_price:
                return bar_open, str(bar.trade_date), "take_profit_gap"
        if low <= stop_loss_price and high >= take_profit_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_intraday_both_hit"
        if low <= stop_loss_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
        if high >= take_profit_price:
            return take_profit_price, str(bar.trade_date), "take_profit_hit"
    last_bar = holding_bars[-1]
    if locked_limit_down_bars and locked_limit_down_bars == len(holding_bars):
        return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
    if locked_limit_down_bars:
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _stats(returns: list[float]) -> dict:
    n = len(returns)
    if n == 0:
        return {"n": 0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wr = round(len(wins) * 100 / n, 1)
    avg = round(sum(returns) / n, 2)
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    pl = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0.0
    return {"n": n, "wr": wr, "avg": avg, "pl": pl, "avg_win": avg_win, "avg_loss": avg_loss}


def main():
    eng = create_engine(get_settings().database_url)
    with Session(eng) as db:
        rows = db.execute(
            select(
                Recommendation.code, Recommendation.trade_date,
                Recommendation.recommendation_id,
                RecommendationOutcome.entry_price, RecommendationOutcome.stop_loss_price,
                RecommendationOutcome.take_profit_price,
            )
            .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
            .where(
                RecommendationRun.run_mode == "formal",
                Recommendation.recommendation_id.like("rec_%"),
                RecommendationOutcome.outcome_label.in_(["success", "failed"]),
            )
        ).all()

        # 按 (code, trade_date) 去重，取第一条
        seen = set()
        recs = []
        for r in rows:
            key = (r.code, str(r.trade_date))
            if key in seen:
                continue
            seen.add(key)
            recs.append(r)
        print(f"去重后样本: {len(recs)} 笔")

        # 加载 K 线
        codes = sorted({r.code for r in recs})
        min_date = min(str(r.trade_date) for r in recs)
        bars = db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min_date)
            .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
        ).all()
        bars_by_code: dict[str, list] = {}
        for b in bars:
            bars_by_code.setdefault(str(b.code), []).append(b)
        print(f"K线股票数: {len(bars_by_code)}")

        HORIZON = 5
        prod_returns = []
        variant_returns = []
        changed = []  # 变体改变了 exit_reason 的笔
        reason_shift = Counter()

        for rec in recs:
            bars_list = bars_by_code.get(rec.code, [])
            # 取 entry 日（trade_date 次日）开始的 HORIZON 根 K 线
            td = str(rec.trade_date)
            entry_idx = None
            for i, b in enumerate(bars_list):
                if str(b.trade_date) > td:
                    entry_idx = i
                    break
            if entry_idx is None:
                continue
            holding = bars_list[entry_idx:entry_idx + HORIZON]
            if len(holding) < 1:
                continue
            # entry_price 用次日开盘（与生产一致）；若无则用 recommendation 的 entry_price
            entry_price = float(rec.entry_price) if rec.entry_price else float(_as_float(holding[0].open))
            if entry_price <= 0:
                continue
            # 直接用 outcome 表已落库的 stop_loss/take_profit（与生产当年一致）
            sl = float(rec.stop_loss_price) if rec.stop_loss_price else entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)
            tp = float(rec.take_profit_price) if rec.take_profit_price else entry_price * 1.05

            prod = _resolve_exit(holding, sl, tp, code=rec.code, entry_price=entry_price)
            var = _resolve_exit_variant(holding, sl, tp, code=rec.code, entry_price=entry_price)

            prod_ret = (prod[0] / entry_price - 1) * 100 - COST_PCT
            var_ret = (var[0] / entry_price - 1) * 100 - COST_PCT
            prod_returns.append(prod_ret)
            variant_returns.append(var_ret)

            if prod[2] != var[2]:
                changed.append((rec.code, td, prod[2], var[2], prod_ret, var_ret))
                reason_shift[(prod[2], var[2])] += 1

        print(f"\n=== 当前（生产）逻辑 ===")
        print(_stats(prod_returns))
        print(f"\n=== 变体（trailing 优先跨 bar）===")
        print(_stats(variant_returns))
        print(f"\n=== exit_reason 变更笔数: {len(changed)} ===")
        for (fr, to), n in reason_shift.most_common(15):
            print(f"  {fr} -> {to}: {n}")
        print(f"\n=== 改善笔样本（前 15）===")
        improved = sorted(changed, key=lambda x: x[5] - x[4], reverse=True)[:15]
        for c in improved:
            delta = round(c[5] - c[4], 2)
            print(f"  {c[0]} {c[1]}: {c[2]}({round(c[4],2)}%) -> {c[3]}({round(c[5],2)}%)  Δ={delta:+}")


if __name__ == "__main__":
    main()
