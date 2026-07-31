"""PATH-A 策略信号回测：在全历史上用 production 参数验证 20cm-only champion。

对每个交易日重建候选集，用 run_strategy() 生成信号 →
apply_pool_rules 级聚合过滤（20cm board/DS 排除/结构性强势 veto/regime 惩罚）→
因果退出引擎模拟离场 → 扣费后比较 champion vs challenger 参数。

生产 champion (bf509b8):
  board_universe=20cm, stop_loss=-4.5%, take_profit=6.0%,
  trailing profit_floor 0.30, structural_strong hard_veto, exclude DS from 20cm

用法:
  python scripts/si_path_a_signal_backtest.py [--champion] [--challenger] [--windows 2024,2025,2026]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.engine.aggregator import (
    STRUCTURAL_STRONG_HARD_VETO_REGIME,
    _is_20cm_board,
)
from app.engine.skill_loader import load_skills
from app.engine.strategy import StrategyContext, StrategySkill
from app.engine.strategy_runner import run_strategy
from app.models.tables import DailyKbar, Stock
from app.services.outcome_simulation import (
    STOP_LOSS_GAP_CAP_PCT,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    TRAILING_STOP_PROFIT_LOCK_RATIO,
    _as_float,
    _resolve_exit_causal,
    _exit_execution_block_reason,
    limit_pct_for_code,
)

# ── Champion 参数 ───────────────────────────────────────────────────────────
CHAMPION_CONFIG = {
    "board_universe": "20cm",
    "stop_loss_pct": -4.5,
    "take_profit_pct": 6.0,
    "trailing_profit_lock_ratio": 0.50,
    "trailing_activate_pct": None,  # None → 生产默认 TRAILING_STOP_ACTIVATE_PCT (2.0)
    "trailing_min_lock_pct": None,  # None → 生产默认 TRAILING_STOP_MIN_LOCK_PCT (0.3)
    "structural_strong_hard_veto": True,
    "excluded_20cm_strategies": frozenset({"daily-stock-analysis-skill"}),
    "horizon_days": 5,
    "score_line": 72.0,
    "commission_rate": 0.0001,
    "stamp_tax_rate": 0.0005,
    "entry_slippage_bps": 10.0,
    "exit_slippage_bps": 10.0,
    "max_entry_gap_pct": 10.0,
}

# ── 预定义回测窗口 ─────────────────────────────────────────────────────────
BACKTEST_WINDOWS = {
    # local daily_kbars 全量覆盖从 2025-01-02 开始（5300+ 只）。2024 及之前仅 5 只，不可用。
    # 独立窗口：2025-H1 / 2025-H2 / 2026-H1 — 覆盖三种不同市场阶段（强势→震荡→弱势）。
    "2025_H1": ("2025-01-02", "2025-06-30"),
    "2025_H2": ("2025-07-01", "2025-12-31"),
    "2026_H1": ("2026-01-01", "2026-07-30"),
    "2025_full": ("2025-01-02", "2025-12-31"),
    "2025_2026": ("2025-01-02", "2026-07-30"),
}


# ── 数据模型 ───────────────────────────────────────────────────────────────

@dataclass
class SignalRecord:
    """单只股票单日单策略的评估结果。"""

    trade_date: str
    code: str
    strategy_id: str
    score: float
    hard_veto: bool
    veto_reason: str
    dsa_signal_score: float
    market_regime: str
    close: float
    pos60: float = 50.0
    chg20: float = 0.0
    is_limit_up: bool = False


@dataclass
class SimulatedTrade:
    """一笔模拟交易的结果。"""

    trade_date: str
    code: str
    strategy_id: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    gross_return_pct: float
    net_return_pct: float
    holding_days: int
    market_regime: str
    entry_triggered: bool


@dataclass
class WindowMetrics:
    """单个窗口的聚合指标。"""

    window_label: str
    n_trades: int
    n_signals: int
    n_dates: int
    win_rate_pct: float
    avg_net_return_pct: float
    cumulative_net_return_pct: float
    profit_factor: float
    max_drawdown_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    max_single_loss_pct: float
    avg_holding_days: float
    exit_reason_breakdown: dict[str, int] = field(default_factory=dict)
    regime_breakdown: dict[str, dict] = field(default_factory=dict)
    strategy_breakdown: dict[str, dict] = field(default_factory=dict)
    coverage_pct: float = 0.0
    entry_not_triggered: int = 0


# ── 回测引擎 ───────────────────────────────────────────────────────────────

class PathABacktestRunner:
    """全历史策略信号回测运行器。"""

    def __init__(
        self,
        session: Session,
        *,
        config: dict[str, Any] | None = None,
        skills: list[StrategySkill] | None = None,
    ) -> None:
        self._session = session
        self._config = dict(CHAMPION_CONFIG, **(config or {}))
        self._skills = skills or self._load_skills()
        # 每次 run() 的模拟交易按 window_label 保存，供 paired bootstrap 使用
        self.trades: dict[str, list[SimulatedTrade]] = {}

        # 预加载 stock 元数据
        stock_rows = list(session.scalars(select(Stock)))
        self._stock_meta: dict[str, dict[str, Any]] = {
            str(row.code): {
                "name": str(row.name or row.code),
                "industry": str(row.industry or "未分类"),
                "is_st": bool(row.is_st),
            }
            for row in stock_rows
        }

    def _load_skills(self) -> list[StrategySkill]:
        """加载所有已启用、可执行、有 contract 的策略。"""
        all_skills = load_skills(get_settings().skills_dir)
        return [
            s for s in all_skills
            if s.enabled and s.executable and s.contract is not None
        ]

    def _is_20cm_stock(self, code: str) -> bool:
        return _is_20cm_board(code)

    def _strategy_allowed_in_20cm(self, skill_id: str) -> bool:
        excluded = self._config["excluded_20cm_strategies"]
        return skill_id not in excluded

    def _fee_pct(self) -> float:
        """来回交易成本（百分比点），不含滑点。"""
        c = self._config
        return (c["commission_rate"] * 2 + c["stamp_tax_rate"]) * 100

    def run(
        self,
        start_date: str,
        end_date: str,
        *,
        window_label: str = "",
    ) -> WindowMetrics:
        """在指定日期范围内运行策略信号回测。"""
        config = self._config
        board = config["board_universe"]
        score_line = config["score_line"]
        horizon_days = config["horizon_days"]

        # ── 1. 获取交易日列表 ──
        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            return self._empty_metrics(window_label, start_date, end_date)

        # ── 2. 预加载窗口内所有日K数据 ──
        # 信号日 + 未来 horizon_days+3 天需要 bars
        lookahead_end = (
            date.fromisoformat(trade_dates[-1]) + timedelta(days=horizon_days + 14)
        ).isoformat()
        lookback_start = (
            date.fromisoformat(trade_dates[0]) - timedelta(days=180)
        ).isoformat()

        all_kbars = list(
            self._session.scalars(
                select(DailyKbar)
                .options(
                    load_only(
                        DailyKbar.code, DailyKbar.trade_date,
                        DailyKbar.open, DailyKbar.high, DailyKbar.low, DailyKbar.close,
                        DailyKbar.volume, DailyKbar.amount, DailyKbar.pct_chg,
                        DailyKbar.turnover_rate, DailyKbar.ma5, DailyKbar.ma10,
                        DailyKbar.ma20, DailyKbar.ma60,
                        DailyKbar.macd_dif, DailyKbar.macd_dea,
                        DailyKbar.rsi_12, DailyKbar.breakout20, DailyKbar.breakout60,
                        DailyKbar.chip_cost_15, DailyKbar.chip_cost_85,
                        DailyKbar.bias_5,
                        DailyKbar.vol_ratio_5, DailyKbar.vol_ratio_20,
                        DailyKbar.max_dd20, DailyKbar.max_dd60,
                        DailyKbar.upper_shadow_count20,
                    )
                )
                .where(
                    DailyKbar.trade_date >= lookback_start,
                    DailyKbar.trade_date <= lookahead_end,
                )
                .order_by(DailyKbar.code, DailyKbar.trade_date)
            )
        )

        bars_by_code: dict[str, dict[str, DailyKbar]] = defaultdict(dict)
        for bar in all_kbars:
            bars_by_code[str(bar.code)][str(bar.trade_date)] = bar

        # Market breadth per day (for regime)
        bars_by_date: dict[str, list[DailyKbar]] = defaultdict(list)
        for bar in all_kbars:
            bars_by_date[str(bar.trade_date)].append(bar)

        # ── 3. 逐日生成信号并模拟交易 ──
        all_signals: list[SignalRecord] = []
        all_trades: list[SimulatedTrade] = []
        total_stocks_evaluated = 0
        total_20cm_stocks = 0

        for td in trade_dates:
            day_bars = bars_by_date.get(td, [])
            if not day_bars:
                continue

            # 获取该日市场广度和 regime
            market_context = self._market_context_for_date(td, bars_by_date)

            # 过滤 20cm 股票
            if board == "20cm":
                day_stocks = [
                    bar for bar in day_bars
                    if self._is_20cm_stock(str(bar.code))
                ]
            else:
                day_stocks = list(day_bars)

            total_stocks_evaluated += len(day_bars)
            total_20cm_stocks += len(day_stocks)

            if not day_stocks:
                continue

            # 对每只股票每个策略生成信号
            day_signals: list[SignalRecord] = []
            for bar in day_stocks:
                code = str(bar.code)
                stock_dict = self._bar_to_stock_dict(bar, bars_by_code, td, market_context)
                if stock_dict is None:
                    continue

                for skill in self._skills:
                    if board == "20cm" and not self._strategy_allowed_in_20cm(skill.skill_id):
                        continue
                    try:
                        signal = run_strategy(skill, stock_dict, [])
                    except Exception:
                        continue
                    score = float(signal.get("score", 0))
                    hard_veto = bool(signal.get("hard_veto", False))
                    veto_reason = str(signal.get("veto_reason", ""))
                    sr = SignalRecord(
                        trade_date=td,
                        code=code,
                        strategy_id=skill.skill_id,
                        score=score,
                        hard_veto=hard_veto,
                        veto_reason=veto_reason,
                        dsa_signal_score=float(stock_dict.get("dsa_signal_score", 0)),
                        market_regime=market_context["regime"],
                        close=float(bar.close or 0),
                        pos60=float(stock_dict.get("pos60", 50)),
                        chg20=float(stock_dict.get("chg20", 0)),
                        is_limit_up=bool(stock_dict.get("is_limit_up", False)),
                    )
                    day_signals.append(sr)

            # ── 应用聚合层过滤 ──
            actionable = self._apply_aggregator_filters(day_signals, market_context, score_line)

            all_signals.extend(day_signals)

            # ── 对每个 actionable 信号模拟交易 ──
            for sr in actionable:
                trade = self._simulate_trade(sr, bars_by_code, horizon_days, market_context)
                if trade is not None:
                    all_trades.append(trade)

        # ── 4. 计算指标 ──
        self.trades[window_label] = list(all_trades)
        return self._compute_metrics(
            all_trades, all_signals, window_label,
            start_date, end_date, trade_dates,
            total_stocks_evaluated, total_20cm_stocks,
        )

    def _get_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        rows = self._session.execute(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
            .group_by(DailyKbar.trade_date)
            .order_by(DailyKbar.trade_date)
        ).all()
        return [str(row[0]) for row in rows]

    def _market_context_for_date(
        self, td: str, bars_by_date: dict[str, list[DailyKbar]]
    ) -> dict[str, Any]:
        """用当日的全市场 bars 重建 regime（与 production 同源实现）。"""
        rows = bars_by_date.get(td, [])
        sample_count = len(rows)
        up_count = sum(1 for r in rows if (r.pct_chg or 0) > 0)
        down_count = sum(1 for r in rows if (r.pct_chg or 0) < 0)
        limit_up_count = sum(1 for r in rows if (r.pct_chg or 0) >= 9.8)
        limit_down_count = sum(1 for r in rows if (r.pct_chg or 0) <= -9.8)
        up_ratio = up_count / max(1, sample_count)
        limit_up_ratio = limit_up_count / max(1, sample_count)

        if up_ratio >= 0.62 and limit_up_ratio >= 0.03:
            regime = "强势"
        elif up_ratio >= 0.55:
            regime = "结构性强势"
        elif up_ratio >= 0.42:
            regime = "震荡"
        else:
            regime = "弱势"

        return {
            "sample_count": sample_count,
            "up_count": up_count,
            "down_count": down_count,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "up_ratio": round(up_ratio, 4),
            "regime": regime,
        }

    def _bar_to_stock_dict(
        self,
        bar: DailyKbar,
        bars_by_code: dict[str, dict[str, DailyKbar]],
        signal_date: str,
        market_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """将 DailyKbar 行转换为 strategy.evaluate() 所需的 stock dict。"""
        code = str(bar.code)
        history = bars_by_code.get(code, {})
        prior = [
            v for k, v in history.items()
            if str(k) <= signal_date and (v.open or 0) > 0
        ]
        closes = [float(v.close or 0) for v in prior if (v.close or 0) > 0]
        highs = [float(v.high or 0) for v in prior if (v.high or 0) > 0]
        lows = [float(v.low or 0) for v in prior if (v.low or 0) > 0]
        volumes = [max(0.0, float(v.volume or 0)) for v in prior]

        if not closes:
            return None

        close = float(bar.close or 0)
        if close <= 0:
            return None

        lookback20 = closes[-20:]
        lookback60 = closes[-60:]
        recent_high20 = max(highs[-20:] or [close])
        recent_low20 = min(lows[-20:] or [close])
        recent_high60 = max(highs[-60:] or [close])
        recent_low60 = min(lows[-60:] or [close])

        ma5 = float(bar.ma5 or 0) or (sum(closes[-5:]) / len(closes[-5:]) if closes[-5:] else 0)
        ma10 = float(bar.ma10 or 0) or (sum(closes[-10:]) / len(closes[-10:]) if closes[-10:] else 0)
        ma20 = float(bar.ma20 or 0) or (sum(lookback20) / len(lookback20) if lookback20 else 0)
        ma60 = float(bar.ma60 or 0) or (sum(lookback60) / len(lookback60) if lookback60 else 0)

        vol5 = sum(volumes[-5:]) / len(volumes[-5:]) if volumes[-5:] else 0
        vol20 = sum(volumes[-20:]) / len(volumes[-20:]) if volumes[-20:] else 0
        vol5_20 = vol5 / vol20 if vol20 > 0 else 0

        def _pos(v, lo, hi):
            return 50.0 if hi <= lo else max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))

        def _pct_delta(v, ref):
            return (v / ref - 1) * 100 if ref > 0 else 0

        pos20 = _pos(close, recent_low20, recent_high20)
        pos60 = _pos(close, recent_low60, recent_high60)

        # DSA 子分
        dsa_trend = 10 * int(close >= ma20) + 10 * int(ma5 >= ma10) + 10 * int(ma10 >= ma20)
        dsa_bias = 14 if -2.0 <= _pct_delta(close, ma5) <= 5.0 else 6
        dsa_volume = min(15, int(max(0.0, vol5_20) * 5))
        dsa_support = 10 if close >= ma5 * 0.985 else 3
        dsa_macd = 10 if (bar.macd_dif or 0) >= (bar.macd_dea or 0) else 2
        rsi = float(bar.rsi_12 or 50)
        dsa_rsi = 10 if 40 <= rsi <= 70 else 4

        # chip_concentration — 与生产 feature_builder 一致：90% 筹码集中度 =
        # (85%成本 - 15%成本) / 15%成本 * 100。值越小越集中（生产 memory：
        # 低 chip(≤18) 更集中 WR 59.4%）。旧实现用 100 - 价差/close*100 方向相反，
        # 使策略层 chip_concentration>=20 在 path A 里几乎恒真失真。
        chip_low = float(bar.chip_cost_15 or 0)
        chip_high = float(bar.chip_cost_85 or 0)
        chip_concentration = (chip_high - chip_low) / chip_low * 100 if chip_low > 0 and chip_high > chip_low else 22.0

        # max_dd20
        peak = 0.0
        max_dd = 0.0
        for c in closes[-20:]:
            peak = max(peak, c)
            if peak > 0:
                max_dd = min(max_dd, (c / peak - 1) * 100)

        meta = self._stock_meta.get(code, {})

        return {
            "code": code,
            "name": str(meta.get("name") or code),
            "industry": str(meta.get("industry") or "未分类"),
            "is_st": bool(meta.get("is_st", False)),
            "trade_date": signal_date,
            "price": close,
            "close": close,
            "open": float(bar.open or 0),
            "high": float(bar.high or 0),
            "low": float(bar.low or 0),
            "pct": float(bar.pct_chg or 0),
            "amount_yi": float(bar.amount or 0) / 100_000_000,
            "turnover_rate": float(bar.turnover_rate or 0),
            "volume": float(bar.volume or 0),
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "rsi_12": rsi,
            "macd_dif": float(bar.macd_dif or 0),
            "macd_dea": float(bar.macd_dea or 0),
            "breakout20": int(bool(bar.breakout20) or close >= recent_high20),
            "breakout60": int(bool(bar.breakout60) or close >= recent_high60),
            "max_dd20": max_dd,
            "chip_concentration": chip_concentration,
            "pos20": pos20,
            "pos60": pos60,
            "dist20": _pct_delta(close, ma20),
            "dist60": _pct_delta(close, ma60),
            "off_high_pct": _pct_delta(close, recent_high60),
            "chg20": _pct_delta(close, lookback20[0]) if lookback20 else 0,
            "chg60": _pct_delta(close, lookback60[0]) if lookback60 else 0,
            "vol5_20": vol5_20,
            "support_ma5": close >= ma5 * 0.985,
            "support_ma10": close >= ma10 * 0.985,
            "volume_shrink_pullback": vol5_20 < 0.9 and float(bar.pct_chg or 0) <= 0,
            "volume_breakout_signal": vol5_20 >= 1.1 and close >= recent_high20,
            "resistance_distance_pct": _pct_delta(recent_high20, close),
            "limit_up_days20": sum(1 for v in prior[-20:] if (v.pct_chg or 0) >= 9.5),
            "dsa_trend_score": float(dsa_trend),
            "dsa_bias_score": float(dsa_bias),
            "dsa_volume_score": float(dsa_volume),
            "dsa_support_score": float(dsa_support),
            "dsa_macd_score": float(dsa_macd),
            "dsa_rsi_score": float(dsa_rsi),
            "dsa_signal_score": float(dsa_trend + dsa_bias + dsa_volume + dsa_support + dsa_macd + dsa_rsi),
            "market_regime": market_context["regime"],
            "up_count": market_context["up_count"],
            "down_count": market_context["down_count"],
            "limit_up_count": market_context["limit_up_count"],
            "limit_down_count": market_context["limit_down_count"],
            "twenty_cm_risk_gate": "normal",
            "news_risk_count": 0,
            "announcement_negative_count": 0,
            "limit_up_overheat_score": 0.0,
            "capital_resonance_score": 0.0,
            "sector_ladder_completeness_score": 0.0,
            "is_main_theme": False,
            "market_sample_count": market_context["sample_count"],
            "data_completeness_score": 100.0,
            "evidence_quality_score": 100.0,
            "is_limit_up": float(bar.pct_chg or 0) >= 9.8,
            "is_20cm_stock": self._is_20cm_stock(code),
            "upper_shadow_count20": int(bar.upper_shadow_count20 or 0) if hasattr(bar, 'upper_shadow_count20') else 0,
            "industry_capacity_like_count": 0,
            "free_mv_yi": 50.0,  # 默认值，20cm 股票通常市值够大通过 filter
        }

    def _apply_aggregator_filters(
        self,
        signals: list[SignalRecord],
        market_context: dict[str, Any],
        score_line: float,
    ) -> list[SignalRecord]:
        """应用与 production aggregator apply_pool_rules 一致的过滤逻辑。"""
        config = self._config
        regime = market_context["regime"]
        limit_down_count = market_context["limit_down_count"]

        # 按 score 降序排列
        ordered = sorted(signals, key=lambda s: s.score, reverse=True)

        # 去重（同 code 只保留最高分）
        seen: set[str] = set()
        deduped: list[SignalRecord] = []
        for sr in ordered:
            if sr.hard_veto:
                continue
            if sr.code in seen:
                continue
            seen.add(sr.code)
            deduped.append(sr)

        # 应用 score_line 和 regime 调整
        result: list[SignalRecord] = []
        for sr in deduped:
            effective_sl = score_line
            passed = True

            # 结构性强势 hard veto
            if config.get("structural_strong_hard_veto") and regime == STRUCTURAL_STRONG_HARD_VETO_REGIME:
                passed = False
            # 强势 +8 惩罚
            if regime == "强势":
                effective_sl = round(effective_sl + 8.0, 2)
            # 震荡 × pos60<50 交叉否决
            if regime == "震荡" and 0 < sr.pos60 < 50 and not sr.is_limit_up:
                effective_sl = round(effective_sl + 12.0, 2)
            # 弱势 × chg20<15 交叉惩罚
            if regime == "弱势" and 0 <= sr.chg20 < 15 and not sr.is_limit_up:
                effective_sl = round(effective_sl + 12.0, 2)

            # 恐慌日否决
            if limit_down_count > 100:
                effective_sl = round(effective_sl + 12.0, 2)

            if passed and sr.score >= effective_sl:
                result.append(sr)

        # 日输出上限：模拟生产不无限刷屏，限制与 resolve_display_limit 对称
        # 结构性强势 0 票，强势不限、弱市/冰点限制 2、高潮折半、普通日最多 15
        sentiment = market_context.get("up_ratio", 0.5)
        if regime == STRUCTURAL_STRONG_HARD_VETO_REGIME and config.get("structural_strong_hard_veto"):
            max_output = 0
        elif regime == "弱势" and sentiment < 0.2:
            max_output = 2
        elif sentiment >= 0.62:
            max_output = len(result)  # 强势日不限
        else:
            max_output = 15

        # 高潮折半
        limit_up_ratio = market_context.get("limit_up_count", 0) / max(1, market_context.get("sample_count", 1))
        if limit_up_ratio >= 0.03 and sentiment >= 0.62:
            max_output = max(1, len(result) // 2)

        return result[:max_output]

    def _simulate_trade(
        self,
        sr: SignalRecord,
        bars_by_code: dict[str, dict[str, DailyKbar]],
        horizon_days: int,
        market_context: dict[str, Any],
    ) -> SimulatedTrade | None:
        """用因果退出引擎模拟单笔交易。"""
        config = self._config
        code = sr.code
        signal_date = sr.trade_date
        code_bars = bars_by_code.get(code, {})

        # 获取信号日之后的 bars（T+1 entry）
        future_bars = [
            bar for dt, bar in sorted(code_bars.items())
            if str(dt) > signal_date
        ]
        if len(future_bars) < 2:
            return None

        # 入场逻辑：T+1 开盘 + 滑点
        entry_bar = future_bars[0]
        entry_open = float(entry_bar.open or 0)

        # 检查入场可行性
        if entry_open <= 0:
            return SimulatedTrade(
                trade_date=signal_date, code=code, strategy_id=sr.strategy_id,
                entry_date="", exit_date="", entry_price=0, exit_price=0,
                exit_reason="entry_not_filled", gross_return_pct=0, net_return_pct=0,
                holding_days=0, market_regime=sr.market_regime, entry_triggered=False,
            )

        # 跳空高开检查
        gap_pct = (entry_open / sr.close - 1) * 100 if sr.close > 0 else 0
        max_gap = config.get("max_entry_gap_pct")
        if max_gap is not None and gap_pct > max_gap:
            return SimulatedTrade(
                trade_date=signal_date, code=code, strategy_id=sr.strategy_id,
                entry_date="", exit_date="", entry_price=0, exit_price=0,
                exit_reason="entry_gap_too_high", gross_return_pct=0, net_return_pct=0,
                holding_days=0, market_regime=sr.market_regime, entry_triggered=False,
            )

        # 停牌/无成交量 = skip
        volume = float(entry_bar.volume or 0)
        if volume <= 0:
            return SimulatedTrade(
                trade_date=signal_date, code=code, strategy_id=sr.strategy_id,
                entry_date="", exit_date="", entry_price=0, exit_price=0,
                exit_reason="entry_not_tradable", gross_return_pct=0, net_return_pct=0,
                holding_days=0, market_regime=sr.market_regime, entry_triggered=False,
            )

        entry_price = round(entry_open * (1 + config["entry_slippage_bps"] / 10_000), 10)

        # 止损/止盈价格
        stop_loss_price = entry_price * (1 + config["stop_loss_pct"] / 100)
        take_profit_price = entry_price * (1 + config["take_profit_pct"] / 100)

        # 构建 holding_bars（入场日 + 后续 horizon_days 天）
        holding_bars = list(future_bars[: horizon_days + 1])
        if not holding_bars:
            return None

        # 使用因果退出引擎
        try:
            trailing_policy = config.get("trailing_policy", "profit_floor")
            if trailing_policy == "peak_callback":
                exit_px, exit_date_str, exit_reason = _resolve_exit_causal(
                    holding_bars,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    code=code,
                    entry_price=entry_price,
                    dynamic_trailing=True,
                    trailing_policy="peak_callback",
                    profit_lock_ratio=config["trailing_profit_lock_ratio"],
                    trailing_callback=config.get("trailing_callback"),
                    activate_pct=config.get("trailing_activate_pct"),
                    min_lock_pct=config.get("trailing_min_lock_pct"),
                )
            else:
                exit_px, exit_date_str, exit_reason = _resolve_exit_causal(
                    holding_bars,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    code=code,
                    entry_price=entry_price,
                    dynamic_trailing=True,
                    trailing_policy="profit_floor",
                    profit_lock_ratio=config["trailing_profit_lock_ratio"],
                    activate_pct=config.get("trailing_activate_pct"),
                    min_lock_pct=config.get("trailing_min_lock_pct"),
                )
        except Exception:
            return None

        if exit_px <= 0 or not exit_date_str:
            return None

        exit_price = round(exit_px * (1 - config["exit_slippage_bps"] / 10_000), 10)
        gross_return_pct = round((exit_price / entry_price - 1) * 100, 10)
        fee_pct = self._fee_pct()
        net_return_pct = round(gross_return_pct - fee_pct, 10)

        # 计算持仓天数
        entry_date_str = str(holding_bars[0].trade_date)
        holding_days_count = 0
        for bar in holding_bars:
            holding_days_count += 1
            if str(bar.trade_date) == exit_date_str:
                break

        return SimulatedTrade(
            trade_date=signal_date,
            code=code,
            strategy_id=sr.strategy_id,
            entry_date=entry_date_str,
            exit_date=exit_date_str,
            entry_price=entry_price,
            exit_price=exit_price,
            exit_reason=exit_reason,
            gross_return_pct=gross_return_pct,
            net_return_pct=net_return_pct,
            holding_days=holding_days_count,
            market_regime=sr.market_regime,
            entry_triggered=True,
        )

    def _compute_metrics(
        self,
        trades: list[SimulatedTrade],
        signals: list[SignalRecord],
        window_label: str,
        start_date: str,
        end_date: str,
        trade_dates: list[str],
        total_stocks: int,
        total_20cm: int,
    ) -> WindowMetrics:
        """从模拟交易列表计算聚合指标。"""
        executed = [t for t in trades if t.entry_triggered]
        n = len(executed)
        n_signals = len(signals)
        n_dates = len(trade_dates)

        if n == 0:
            return WindowMetrics(
                window_label=window_label,
                n_trades=0, n_signals=n_signals, n_dates=n_dates,
                win_rate_pct=0, avg_net_return_pct=0,
                cumulative_net_return_pct=0, profit_factor=0,
                max_drawdown_pct=0, avg_win_pct=0, avg_loss_pct=0,
                max_single_loss_pct=0, avg_holding_days=0,
                coverage_pct=0, entry_not_triggered=len(trades) - n,
            )

        # 胜率
        wins = [t for t in executed if t.net_return_pct > 0]
        losses = [t for t in executed if t.net_return_pct <= 0]
        win_rate = len(wins) / n * 100

        # 收益
        avg_net = sum(t.net_return_pct for t in executed) / n

        # 累计净收益：等权日组合口径。每天把所有活跃仓位的收益等权平均，
        # 得到日组合收益，再链式累乘。避免把数千笔并发仓位当单链复利溢出。
        trades_by_exit_date: dict[str, list[float]] = defaultdict(list)
        for t in executed:
            if t.exit_date:
                trades_by_exit_date[t.exit_date].append(t.net_return_pct)
        daily_returns = sorted(
            (dt, sum(rets) / len(rets)) for dt, rets in trades_by_exit_date.items()
        )
        cumulative = 1.0
        for _, daily_ret in daily_returns:
            cumulative *= (1 + daily_ret / 100)
        cumulative_net = (cumulative - 1) * 100

        # Profit Factor
        gross_profit = sum(t.net_return_pct for t in wins)
        gross_loss = abs(sum(t.net_return_pct for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # 平均盈亏
        avg_win = sum(t.net_return_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.net_return_pct for t in losses) / len(losses) if losses else 0
        max_loss = min(t.net_return_pct for t in executed)

        # 最大回撤（等权日组合资金曲线）
        peak = float("-inf")
        max_dd = 0.0
        running = 1.0
        for _, daily_ret in daily_returns:
            running *= (1 + daily_ret / 100)
            equity = (running - 1) * 100
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)

        # 平均持仓天数
        avg_hold = sum(t.holding_days for t in executed) / n

        # 出场原因分布
        exit_breakdown: dict[str, int] = defaultdict(int)
        for t in executed:
            exit_breakdown[t.exit_reason] += 1

        # regime 分布
        regime_breakdown: dict[str, dict] = {}
        for reg in ["强势", "结构性强势", "震荡", "弱势"]:
            reg_trades = [t for t in executed if t.market_regime == reg]
            if reg_trades:
                reg_wins = [t for t in reg_trades if t.net_return_pct > 0]
                regime_breakdown[reg] = {
                    "n": len(reg_trades),
                    "win_rate": len(reg_wins) / len(reg_trades) * 100,
                    "avg_net": sum(t.net_return_pct for t in reg_trades) / len(reg_trades),
                    "pf": (
                        sum(t.net_return_pct for t in reg_wins) / abs(sum(t.net_return_pct for t in reg_trades if t.net_return_pct <= 0))
                        if sum(abs(t.net_return_pct) for t in reg_trades if t.net_return_pct <= 0) > 0
                        else None
                    ),
                }

        # 策略分布
        strategy_breakdown: dict[str, dict] = {}
        for t in executed:
            sid = t.strategy_id
            if sid not in strategy_breakdown:
                sid_trades = [t2 for t2 in executed if t2.strategy_id == sid]
                sid_wins = [t2 for t2 in sid_trades if t2.net_return_pct > 0]
                strategy_breakdown[sid] = {
                    "n": len(sid_trades),
                    "win_rate": len(sid_wins) / len(sid_trades) * 100 if sid_trades else 0,
                    "avg_net": sum(t2.net_return_pct for t2 in sid_trades) / len(sid_trades) if sid_trades else 0,
                }

        # 覆盖率
        coverage = total_20cm / max(1, total_stocks) * 100 if total_stocks > 0 else 0

        return WindowMetrics(
            window_label=window_label,
            n_trades=n,
            n_signals=n_signals,
            n_dates=n_dates,
            win_rate_pct=round(win_rate, 2),
            avg_net_return_pct=round(avg_net, 4),
            cumulative_net_return_pct=round(cumulative_net, 4),
            profit_factor=round(pf, 4),
            max_drawdown_pct=round(max_dd, 4),
            avg_win_pct=round(avg_win, 4),
            avg_loss_pct=round(avg_loss, 4),
            max_single_loss_pct=round(max_loss, 4),
            avg_holding_days=round(avg_hold, 1),
            exit_reason_breakdown=dict(exit_breakdown),
            regime_breakdown=regime_breakdown,
            strategy_breakdown=strategy_breakdown,
            coverage_pct=round(coverage, 1),
            entry_not_triggered=len(trades) - n,
        )

    def _empty_metrics(self, label: str, start: str, end: str) -> WindowMetrics:
        return WindowMetrics(
            window_label=label,
            n_trades=0, n_signals=0, n_dates=0,
            win_rate_pct=0, avg_net_return_pct=0,
            cumulative_net_return_pct=0, profit_factor=0,
            max_drawdown_pct=0, avg_win_pct=0, avg_loss_pct=0,
            max_single_loss_pct=0, avg_holding_days=0, coverage_pct=0,
        )


# ── 报告 ───────────────────────────────────────────────────────────────────

def print_window_report(m: WindowMetrics) -> None:
    """打印单个窗口的指标报告。"""
    print(f"\n{'='*80}")
    print(f"  窗口: {m.window_label}  |  {m.n_dates} 交易日  |  {m.n_trades} 笔交易")
    print(f"{'='*80}")
    print(f"  信号数: {m.n_signals}  |  入场未触发: {m.entry_not_triggered}  |  覆盖率: {m.coverage_pct:.1f}%")
    print(f"  {'─'*76}")
    print(f"  净胜率:        {m.win_rate_pct:.2f}%")
    print(f"  平均净收益:    {m.avg_net_return_pct:+.4f}%")
    print(f"  累计净收益:    {m.cumulative_net_return_pct:+.4f}%")
    print(f"  Profit Factor: {m.profit_factor:.4f}")
    print(f"  最大回撤:      {m.max_drawdown_pct:+.4f}%")
    print(f"  平均盈利:      {m.avg_win_pct:+.4f}%")
    print(f"  平均亏损:      {m.avg_loss_pct:+.4f}%")
    print(f"  最大单笔亏损:  {m.max_single_loss_pct:+.4f}%")
    print(f"  平均持仓:      {m.avg_holding_days:.1f} 天")

    if m.exit_reason_breakdown:
        print(f"\n  出场分布:")
        for reason, count in sorted(m.exit_reason_breakdown.items(), key=lambda x: -x[1]):
            pct = count / m.n_trades * 100
            print(f"    {reason:<35} {count:>4} ({pct:.1f}%)")

    if m.regime_breakdown:
        print(f"\n  分 Regime:")
        for reg, data in sorted(m.regime_breakdown.items()):
            pf_str = f"{data['pf']:.4f}" if data['pf'] is not None else "N/A"
            print(f"    {reg:<10} n={data['n']:>4}  WR={data['win_rate']:.1f}%  avg={data['avg_net']:+.4f}%  PF={pf_str}")

    if m.strategy_breakdown:
        print(f"\n  分策略:")
        for sid, data in sorted(m.strategy_breakdown.items(), key=lambda x: -x[1]["n"]):
            print(f"    {sid:<45} n={data['n']:>4}  WR={data['win_rate']:.1f}%  avg={data['avg_net']:+.4f}%")


def _bootstrap_paired_diff(
    champion_trades: list[SimulatedTrade],
    challenger_trades: list[SimulatedTrade],
    n_bootstrap: int = 10000,
    seed: int = 42,
) -> dict[str, Any]:
    """按交易日分组的 paired cluster bootstrap。

    返回 champion/challenger 各指标差值及 95% CI。
    """
    import random as _random
    import math

    # 按交易日分组
    def _group_by_date(trades):
        groups: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            if t.entry_triggered:
                groups[t.trade_date].append(t.net_return_pct)
        # 每个交易日的平均收益
        return {td: sum(rets) / len(rets) for td, rets in groups.items()}

    c_groups = _group_by_date(champion_trades)
    t_groups = _group_by_date(challenger_trades)

    # 共同的交易日
    common_dates = sorted(set(c_groups) & set(t_groups))
    if len(common_dates) < 5:
        return {"n_dates": len(common_dates), "error": "insufficient_common_dates"}

    _rng = _random.Random(seed)

    def _compute_diffs(dates):
        c_vals = [c_groups[d] for d in dates]
        t_vals = [t_groups[d] for d in dates]
        # 均值差
        mean_diff = sum(t_vals) / len(t_vals) - sum(c_vals) / len(c_vals)
        return mean_diff

    obs_diff = _compute_diffs(common_dates)

    # Bootstrap
    boot_diffs = []
    n = len(common_dates)
    for _ in range(n_bootstrap):
        sample = [_rng.choice(common_dates) for _ in range(n)]
        boot_diffs.append(_compute_diffs(sample))

    boot_diffs.sort()
    lo = boot_diffs[int(n_bootstrap * 0.025)]
    hi = boot_diffs[int(n_bootstrap * 0.975)]

    return {
        "n_dates": n,
        "obs_mean_diff_pct": round(obs_diff, 6),
        "ci_95_lo": round(lo, 6),
        "ci_95_hi": round(hi, 6),
        "n_bootstrap": n_bootstrap,
    }


def print_comparison(
    champion_metrics: dict[str, WindowMetrics],
    challenger_metrics: dict[str, WindowMetrics],
    champion_trades: dict[str, list[SimulatedTrade]] | None = None,
    challenger_trades: dict[str, list[SimulatedTrade]] | None = None,
) -> None:
    """打印 champion vs challenger 跨窗口对比。"""
    print(f"\n\n{'='*80}")
    print(f"  CHAMPION vs CHALLENGER 对比")
    print(f"{'='*80}")

    for window in sorted(champion_metrics):
        c = champion_metrics[window]
        t = challenger_metrics.get(window)
        if t is None:
            continue

        print(f"\n  ── {window} ──")
        print(f"  {'指标':<22} {'Champion':>12} {'Challenger':>12} {'Δ':>12}")
        print(f"  {'─'*22} {'─'*12} {'─'*12} {'─'*12}")

        for label, attr in [
            ("净胜率 %", "win_rate_pct"),
            ("平均净收益 %", "avg_net_return_pct"),
            ("累计净收益 %", "cumulative_net_return_pct"),
            ("Profit Factor", "profit_factor"),
            ("最大回撤 %", "max_drawdown_pct"),
            ("交易笔数", "n_trades"),
        ]:
            cv = getattr(c, attr)
            tv = getattr(t, attr)
            delta = tv - cv
            sign = "+" if delta > 0 else ""
            print(f"  {label:<22} {cv:>12.4f} {tv:>12.4f} {sign}{delta:>11.4f}")

        # Bootstrap CI
        if champion_trades and challenger_trades:
            c_trades = champion_trades.get(window, [])
            t_trades = challenger_trades.get(window, [])
            if c_trades and t_trades:
                boot = _bootstrap_paired_diff(c_trades, t_trades)
                if "error" not in boot:
                    print(f"\n  Paired bootstrap (交易日 cluster, {boot['n_bootstrap']}次):")
                    print(f"    均值差: {boot['obs_mean_diff_pct']:+.6f}%")
                    print(f"    95% CI: [{boot['ci_95_lo']:+.6f}%, {boot['ci_95_hi']:+.6f}%]")
                    print(f"    共用交易日: {boot['n_dates']}")


# ── 入口 ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="PATH-A 策略信号回测")
    parser.add_argument(
        "--windows", default="2025_H1,2025_H2,2026_H1",
        help="逗号分隔的回测窗口标签 (default: 2025_H1,2025_H2,2026_H1)"
    )
    parser.add_argument(
        "--champion-only", action="store_true",
        help="仅运行 champion 参数"
    )
    parser.add_argument(
        "--challenger-only", action="store_true",
        help="仅运行 challenger 参数"
    )
    parser.add_argument(
        "--challenger-args", type=str, default=None,
        help='JSON dict 覆盖 challenger 参数，例如 \'{"max_entry_gap_pct": 3.0}\''
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="JSON 输出文件路径"
    )
    args = parser.parse_args()

    window_labels = [w.strip() for w in args.windows.split(",")]
    run_champion = not args.challenger_only
    run_challenger = not args.champion_only
    challenger_overrides: dict[str, Any] = {}
    if args.challenger_args:
        challenger_overrides = json.loads(args.challenger_args)

    db = SessionLocal()
    try:
        # ── Champion 运行 ──
        champion_metrics: dict[str, WindowMetrics] = {}
        champion_trades: dict[str, list[SimulatedTrade]] = {}
        if run_champion:
            print("🏆 运行 CHAMPION 参数回测...")
            champion_config = dict(CHAMPION_CONFIG)
            runner = PathABacktestRunner(db, config=champion_config)

            for label in window_labels:
                w = BACKTEST_WINDOWS.get(label)
                if not w:
                    print(f"  ⚠️ 未知窗口: {label}，跳过")
                    continue
                print(f"  📅 窗口 {label}: {w[0]} → {w[1]}")
                metrics = runner.run(w[0], w[1], window_label=label)

                # We need to track trades for bootstrap - for now the runner
                # returns only aggregated metrics. For the comparison report
                # we reconstruct from the stored state.
                champion_metrics[label] = metrics
                champion_trades[label] = runner.trades.get(label, [])
                print_window_report(metrics)

        # ── Challenger 运行（如果有的话）──
        challenger_metrics: dict[str, WindowMetrics] = {}
        challenger_trades_dict: dict[str, list[SimulatedTrade]] = {}
        if run_challenger and challenger_overrides:
            print("\n🏆 运行 CHALLENGER 参数回测:", json.dumps(challenger_overrides))
            challenger_config = dict(CHAMPION_CONFIG)
            challenger_config.update(challenger_overrides)
            challenger_runner = PathABacktestRunner(db, config=challenger_config)

            for label in window_labels:
                w = BACKTEST_WINDOWS.get(label)
                if not w:
                    continue
                print(f"  📅 窗口 {label}: {w[0]} → {w[1]}")
                metrics = challenger_runner.run(w[0], w[1], window_label=label)
                challenger_metrics[label] = metrics
                challenger_trades_dict[label] = challenger_runner.trades.get(label, [])
                print_window_report(metrics)

            print_comparison(
                champion_metrics, challenger_metrics,
                champion_trades, challenger_trades_dict,
            )

        # ── 跨窗口汇总 ──
        print(f"\n\n{'='*80}")
        print(f"  跨窗口汇总")
        print(f"{'='*80}")
        print(f"  {'窗口':<20} {'交易日':>6} {'交易':>6} {'胜率':>8} {'均净收益':>10} {'PF':>8} {'最大回撤':>10}")
        print(f"  {'─'*20} {'─'*6} {'─'*6} {'─'*8} {'─'*10} {'─'*8} {'─'*10}")

        all_positive = True
        independent_windows_positive = 0
        independent_windows_total = 0

        for label in window_labels:
            m = champion_metrics.get(label) or challenger_metrics.get(label)
            if m is None or m.n_trades == 0:
                continue
            print(f"  {label:<20} {m.n_dates:>6} {m.n_trades:>6} {m.win_rate_pct:>7.2f}% {m.avg_net_return_pct:>+9.4f}% {m.profit_factor:>8.4f} {m.max_drawdown_pct:>+9.4f}%")

            if m.avg_net_return_pct <= 0 or m.profit_factor <= 1.0:
                all_positive = False

            # 独立年份计数
            if label in ("2025_H1", "2025_H2", "2026_H1"):
                independent_windows_total += 1
                if m.avg_net_return_pct > 0 and m.profit_factor > 1.0:
                    independent_windows_positive += 1

        # ── Champion 稳定性判定 ──
        print(f"\n  Champion 稳定性判定:")
        print(f"    独立窗口正期望: {independent_windows_positive}/{independent_windows_total}")
        if independent_windows_positive >= 2:
            print(f"    ✅ Champion 在两个以上独立窗口表现正期望 — 可宣告稳定")
        else:
            print(f"    ⚠️  Champion 尚未满足两窗口正期望条件 (L-019)")
            print(f"    → 需继续在新窗口验证或转向新策略研究")

        # ── JSON 输出 ──
        if args.output:
            output_data = {
                "champion_metrics": {
                    label: {
                        "n_trades": m.n_trades,
                        "n_signals": m.n_signals,
                        "n_dates": m.n_dates,
                        "win_rate_pct": m.win_rate_pct,
                        "avg_net_return_pct": m.avg_net_return_pct,
                        "cumulative_net_return_pct": m.cumulative_net_return_pct,
                        "profit_factor": m.profit_factor,
                        "max_drawdown_pct": m.max_drawdown_pct,
                        "avg_win_pct": m.avg_win_pct,
                        "avg_loss_pct": m.avg_loss_pct,
                        "max_single_loss_pct": m.max_single_loss_pct,
                        "exit_reason_breakdown": m.exit_reason_breakdown,
                        "regime_breakdown": m.regime_breakdown,
                        "strategy_breakdown": m.strategy_breakdown,
                    }
                    for label, m in champion_metrics.items()
                },
                "champion_config": {
                    k: (list(v) if isinstance(v, frozenset) else v)
                    for k, v in CHAMPION_CONFIG.items()
                },
                "independent_windows_positive": independent_windows_positive,
                "independent_windows_total": independent_windows_total,
            }
            with open(args.output, "w") as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            print(f"\n  📄 输出已写入: {args.output}")

    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
