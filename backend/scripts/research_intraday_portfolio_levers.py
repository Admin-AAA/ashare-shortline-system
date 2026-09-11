"""Research causal return levers outside the exhausted daily-factor search.

This script is intentionally read-only.  It combines the frozen candidate
snapshot with signal-day 15-minute bars and the production causal exit engine
to test two families of hypotheses:

1. Signal-day intraday behaviour as a new cross-sectional data dimension.
2. Portfolio construction and market-level late-breadth exposure overlays.

The output is JSON so the reviewed aggregates can be handed to the analytics
report renderer without copying database rows or credentials into the report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.outcome_simulation import _resolve_exit_causal


SNAPSHOT = Path(__file__).resolve().parents[1] / "output" / "frozen_snapshots" / "holdout_244d.json"
STOP_LOSS_PCT = -4.5
TAKE_PROFIT_PCT = 6.0
HORIZON_DAYS = 5
FEE_PCT = (0.0001 * 2 + 0.0005) * 100
SLIPPAGE_BPS = 10.0
MAX_ENTRY_GAP_PCT = 10.0
SCORE_FLOOR = 72.0
TOP_N = 6
TRAIN_FRACTION = 0.60
RANDOM_SEED = 20260910
BASE_GROSS_EXPOSURE = 0.60
DAILY_SLEEVE_FRACTION = BASE_GROSS_EXPOSURE / HORIZON_DAYS

FEATURES = {
    "close_location": "收盘在日内区间的位置",
    "late_return_pct": "最后一小时收益率",
    "afternoon_return_pct": "午后收益率",
    "close_vs_vwap_pct": "收盘相对日内VWAP",
    "late_volume_share": "最后一小时成交量占比",
    "intraday_efficiency": "日内趋势效率",
}


@dataclass(frozen=True)
class SimulatedTrade:
    signal_date: str
    code: str
    industry: str
    final_score: float
    market_base: str
    sentiment: str
    net_return_pct: float
    exit_reason: str


def _bar_obj(bar: dict[str, Any], pct_chg: float = 0.0) -> Any:
    # 修复：outcome_bars 无 amount 字段，而 no_liquidity_reason_from_bar 要求
    # volume>0 且 amount>0 才判为可卖出；缺 amount 会让每根 bar 都被当成"无流动性
    # 卖不出"，导致 resolve_causal_exit 从不触发盘中止损/止盈、全部持有到期末并被
    # 误标 limit_down_locked_horizon_mark。这里用 volume*close 作为成交额正值代理
    # （仅用于 >0 流动性闸门，成交价路径只用 OHLC）；pct_chg 由前收计算，供一字板判定。
    volume = float(bar.get("volume") or 0)
    close = float(bar.get("close") or 0)
    return type(
        "Bar",
        (),
        {
            "trade_date": bar.get("trade_date"),
            "open": bar.get("open"),
            "high": bar.get("high"),
            "low": bar.get("low"),
            "close": bar.get("close"),
            "volume": bar.get("volume"),
            "amount": volume * close,
            "pct_chg": pct_chg,
            "tradable": bar.get("tradable", True),
        },
    )()


def _simulate_card(day: dict[str, Any], card: dict[str, Any]) -> SimulatedTrade | None:
    raw_bars = (day.get("outcome_bars") or {}).get(str(card.get("code"))) or []
    if len(raw_bars) < 2:
        return None
    entry_bar = raw_bars[0]
    entry_open = float(entry_bar.get("open") or 0)
    if entry_open <= 0 or not entry_bar.get("tradable", True):
        return None
    signal_close = float(card.get("close") or card.get("recommend_price") or 0)
    if signal_close <= 0:
        return None
    entry_gap = (entry_open / signal_close - 1) * 100
    if entry_gap > MAX_ENTRY_GAP_PCT:
        return None
    entry_price = entry_open * (1 + SLIPPAGE_BPS / 1e4)
    # 顺序计算每根 bar 的 pct_chg（首根前收用信号日收盘），供一字涨跌停判定
    holding_raw = raw_bars[: HORIZON_DAYS + 1]
    holding = []
    prev_close = signal_close
    for bar in holding_raw:
        c = float(bar.get("close") or 0)
        pct = (c / prev_close - 1) * 100 if prev_close > 0 and c > 0 else 0.0
        holding.append(_bar_obj(bar, pct))
        if c > 0:
            prev_close = c
    try:
        exit_price, _exit_date, exit_reason = _resolve_exit_causal(
            holding,
            stop_loss_price=entry_price * (1 + STOP_LOSS_PCT / 100),
            take_profit_price=entry_price * (1 + TAKE_PROFIT_PCT / 100),
            code=str(card.get("code")),
            entry_price=entry_price,
            dynamic_trailing=True,
            trailing_policy="profit_floor",
            profit_lock_ratio=0.5,
            activate_pct=None,
            min_lock_pct=None,
        )
    except Exception:
        return None
    if not exit_price or exit_price <= 0:
        return None
    net_return = (float(exit_price) * (1 - SLIPPAGE_BPS / 1e4) / entry_price - 1) * 100 - FEE_PCT
    snapshot = day.get("market_snapshot") or {}
    return SimulatedTrade(
        signal_date=str(day["signal_date"]),
        code=str(card.get("code")),
        industry=str(card.get("industry") or "未知"),
        final_score=float(card.get("final_score") or 0),
        market_base=_market_base(snapshot),
        sentiment=_market_sentiment(snapshot),
        net_return_pct=round(net_return, 8),
        exit_reason=str(exit_reason or "unknown"),
    )


def _load_snapshot() -> list[dict[str, Any]]:
    return list(json.loads(SNAPSHOT.read_text())["days"])


def _fetch_candidate_intraday(
    signal_date: str,
    codes: Iterable[str],
) -> tuple[str, dict[str, dict[str, float]]]:
    code_list = sorted(set(codes))
    if not code_list:
        return signal_date, {}
    stmt = text(
        """
        SELECT code, trade_time, open, high, low, close, volume, amount
        FROM minute_kbars
        WHERE trade_date = :trade_date AND freq = '15min' AND code IN :codes
        ORDER BY code, trade_time
        """
    ).bindparams(bindparam("codes", expanding=True))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with SessionLocal() as session:
        for row in session.execute(stmt, {"trade_date": signal_date, "codes": code_list}).mappings():
            grouped[str(row["code"])].append(dict(row))
    result: dict[str, dict[str, float]] = {}
    for code, bars in grouped.items():
        if len(bars) != 16:
            continue
        first_open = float(bars[0]["open"] or 0)
        afternoon_open = float(bars[8]["open"] or 0)
        late_open = float(bars[-4]["open"] or 0)
        last_close = float(bars[-1]["close"] or 0)
        day_high = max(float(bar["high"] or 0) for bar in bars)
        positive_lows = [float(bar["low"] or 0) for bar in bars if float(bar["low"] or 0) > 0]
        day_low = min(positive_lows) if positive_lows else 0
        total_volume = sum(float(bar["volume"] or 0) for bar in bars)
        total_amount = sum(float(bar["amount"] or 0) for bar in bars)
        if min(first_open, afternoon_open, late_open, last_close, day_low) <= 0 or total_volume <= 0:
            continue
        vwap = total_amount / total_volume if total_amount > 0 else 0
        if vwap <= 0:
            continue
        bar_moves = [
            abs(float(bar["close"] or 0) / float(bar["open"] or 1) - 1)
            for bar in bars
            if float(bar["open"] or 0) > 0 and float(bar["close"] or 0) > 0
        ]
        path_length = sum(bar_moves)
        result[code] = {
            "close_location": (last_close - day_low) / max(day_high - day_low, 1e-12),
            "late_return_pct": (last_close / late_open - 1) * 100,
            "afternoon_return_pct": (last_close / afternoon_open - 1) * 100,
            "close_vs_vwap_pct": (last_close / vwap - 1) * 100,
            "late_volume_share": sum(float(bar["volume"] or 0) for bar in bars[-4:]) / total_volume,
            "intraday_efficiency": abs(last_close / first_open - 1) / max(path_length, 1e-12),
        }
    return signal_date, result


def _fetch_market_late_breadth(signal_date: str) -> tuple[str, dict[str, float] | None]:
    stmt = text(
        """
        SELECT COUNT(*) AS stocks,
               AVG((close_1500 / open_1415 - 1) * 100) AS late_mean_pct,
               AVG(close_1500 > open_1415) AS late_up_ratio,
               AVG(close_1500 > vwap) AS above_vwap_ratio
        FROM (
            SELECT code,
                   MAX(CASE WHEN RIGHT(trade_time, 8) = '14:15:00' THEN open END) AS open_1415,
                   MAX(CASE WHEN RIGHT(trade_time, 8) = '15:00:00' THEN close END) AS close_1500,
                   SUM(amount) / NULLIF(SUM(volume), 0) AS vwap
            FROM minute_kbars
            WHERE trade_date = :trade_date AND freq = '15min'
            GROUP BY code
        ) AS per_stock
        WHERE open_1415 > 0 AND close_1500 > 0 AND vwap > 0
        """
    )
    with SessionLocal() as session:
        row = session.execute(stmt, {"trade_date": signal_date}).mappings().one()
    if int(row["stocks"] or 0) < 1000:
        return signal_date, None
    return signal_date, {
        "stocks": int(row["stocks"]),
        "late_mean_pct": float(row["late_mean_pct"]),
        "late_up_ratio": float(row["late_up_ratio"]),
        "above_vwap_ratio": float(row["above_vwap_ratio"]),
    }


def _bootstrap_mean_ci(values: np.ndarray, confidence: float, draws: int = 4000) -> tuple[float, float]:
    if len(values) < 2:
        return math.nan, math.nan
    rng = np.random.default_rng(RANDOM_SEED)
    indices = rng.integers(0, len(values), size=(draws, len(values)))
    samples = values[indices].mean(axis=1)
    alpha = (1 - confidence) / 2
    return float(np.quantile(samples, alpha)), float(np.quantile(samples, 1 - alpha))


def _metric_summary(
    returns: pd.Series,
    relative_exposures: pd.Series | None = None,
) -> dict[str, float | int]:
    clean = returns.dropna().astype(float)
    portfolio_returns = clean * DAILY_SLEEVE_FRACTION
    if relative_exposures is not None:
        portfolio_returns = portfolio_returns * relative_exposures.reindex(clean.index).fillna(0).astype(float)
    if clean.empty:
        return {
            "days": 0,
            "avg_cohort_return_pct": 0.0,
            "avg_portfolio_contribution_pct": 0.0,
            "cumulative_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "positive_day_pct": 0.0,
        }
    equity = (1 + portfolio_returns / 100).cumprod()
    drawdown = (equity / equity.cummax() - 1) * 100
    return {
        "days": int(len(clean)),
        "avg_cohort_return_pct": round(float(clean.mean()), 4),
        "avg_portfolio_contribution_pct": round(float(portfolio_returns.mean()), 4),
        "cumulative_pct": round(float((equity.iloc[-1] - 1) * 100), 4),
        "max_drawdown_pct": round(float(drawdown.min()), 4),
        "positive_day_pct": round(float((clean > 0).mean() * 100), 2),
    }


def _portfolio_research(days: list[dict[str, Any]], simulated: dict[tuple[str, str], SimulatedTrade]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    policies = ["equal_top6", "equal_top3", "rank_50_25_25", "top1", "industry_cap1"]
    for day in days:
        date = str(day["signal_date"])
        eligible = [
            (index, card, simulated.get((date, str(card.get("code")))))
            for index, card in enumerate(day.get("cards") or [])
            if float(card.get("final_score") or 0) >= SCORE_FLOOR
            and simulated.get((date, str(card.get("code")))) is not None
        ]
        eligible.sort(key=lambda item: (-float(item[1].get("final_score") or 0), item[0]))
        if not eligible:
            continue
        selections: dict[str, tuple[list[SimulatedTrade], list[float]]] = {}
        top6 = [item[2] for item in eligible[:TOP_N] if item[2] is not None]
        top3 = top6[:3]
        selections["equal_top6"] = (top6, [1 / len(top6)] * len(top6))
        selections["equal_top3"] = (top3, [1 / len(top3)] * len(top3))
        rank_weights = [0.50, 0.25, 0.25][: len(top3)]
        rank_total = sum(rank_weights)
        selections["rank_50_25_25"] = (top3, [weight / rank_total for weight in rank_weights])
        selections["top1"] = (top6[:1], [1.0])
        diversified: list[SimulatedTrade] = []
        seen_industries: set[str] = set()
        for _index, _card, trade in eligible:
            if trade is None or trade.industry in seen_industries:
                continue
            diversified.append(trade)
            seen_industries.add(trade.industry)
            if len(diversified) == TOP_N:
                break
        selections["industry_cap1"] = (diversified, [1 / len(diversified)] * len(diversified))
        for policy in policies:
            trades, weights = selections[policy]
            if not trades:
                continue
            rows.append(
                {
                    "signal_date": date,
                    "policy": policy,
                    "basket_return_pct": sum(trade.net_return_pct * weight for trade, weight in zip(trades, weights)),
                    "positions": len(trades),
                    "market_base": trades[0].market_base,
                    "sentiment": trades[0].sentiment,
                }
            )
    frame = pd.DataFrame(rows)
    dates = sorted(frame["signal_date"].unique())
    split_date = dates[max(1, int(len(dates) * TRAIN_FRACTION)) - 1]
    summary: dict[str, Any] = {"split_date": split_date, "policies": {}}
    for policy in policies:
        part = frame[frame["policy"] == policy].set_index("signal_date").sort_index()
        train = part[part.index <= split_date]
        oos = part[part.index > split_date]
        quarters = []
        for number, index in enumerate(np.array_split(np.arange(len(part)), 4), start=1):
            block = part.iloc[index]
            quarters.append(
                {
                    "block": number,
                    "start": str(block.index.min()),
                    "end": str(block.index.max()),
                    "days": int(len(block)),
                    "avg_daily_pct": round(float(block["basket_return_pct"].mean()), 4),
                }
            )
        summary["policies"][policy] = {
            "full": _metric_summary(part["basket_return_pct"]),
            "train": _metric_summary(train["basket_return_pct"]),
            "oos": _metric_summary(oos["basket_return_pct"]),
            "avg_positions": round(float(part["positions"].mean()), 2),
            "time_blocks": quarters,
        }
    return rows, summary


def _candidate_feature_research(frame: pd.DataFrame, split_date: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    bonferroni_confidence = 1 - 0.05 / len(FEATURES)
    for feature, label in FEATURES.items():
        valid = frame.dropna(subset=[feature, "net_return_pct"]).copy()
        day_effects: list[dict[str, Any]] = []
        for date, day in valid.groupby("signal_date"):
            if len(day) < 6 or day[feature].nunique() < 3:
                continue
            median = float(day[feature].median())
            high = day[day[feature] >= median]
            low = day[day[feature] < median]
            if min(len(high), len(low)) < 2:
                continue
            day_effects.append(
                {
                    "signal_date": date,
                    "month": date[:7],
                    "delta_pct": float(high["net_return_pct"].mean() - low["net_return_pct"].mean()),
                    "high_n": int(len(high)),
                    "low_n": int(len(low)),
                }
            )
        effects = pd.DataFrame(day_effects)
        train_effects = effects[effects["signal_date"] <= split_date]
        oos_effects = effects[effects["signal_date"] > split_date]
        monthly = effects.groupby("month", as_index=False)["delta_pct"].mean()
        oos_values = oos_effects["delta_pct"].to_numpy(dtype=float)
        ci95 = _bootstrap_mean_ci(oos_values, 0.95)
        ci_adjusted = _bootstrap_mean_ci(oos_values, bonferroni_confidence)
        train_rows = valid[valid["signal_date"] <= split_date]
        oos_rows = valid[valid["signal_date"] > split_date]
        q_low = float(train_rows[feature].quantile(1 / 3))
        q_high = float(train_rows[feature].quantile(2 / 3))
        oos_high = oos_rows[oos_rows[feature] >= q_high]
        oos_low = oos_rows[oos_rows[feature] <= q_low]
        consistency = float((monthly["delta_pct"] > 0).mean()) if len(monthly) else 0.0
        passed = bool(
            len(oos_values) >= 20
            and len(oos_high) + len(oos_low) >= 100
            and float(train_effects["delta_pct"].mean()) > 0
            and float(oos_effects["delta_pct"].mean()) > 0
            and ci_adjusted[0] > 0
            and consistency >= 0.80
        )
        results.append(
            {
                "feature": feature,
                "label": label,
                "matched_trades": int(len(valid)),
                "effect_days": int(len(effects)),
                "train_day_delta_pct": round(float(train_effects["delta_pct"].mean()), 4),
                "oos_day_delta_pct": round(float(oos_effects["delta_pct"].mean()), 4),
                "oos_ci95_low": round(ci95[0], 4),
                "oos_ci95_high": round(ci95[1], 4),
                "oos_adjusted_ci_low": round(ci_adjusted[0], 4),
                "oos_adjusted_ci_high": round(ci_adjusted[1], 4),
                "monthly_positive_fraction": round(consistency, 4),
                "months": int(len(monthly)),
                "oos_high_n": int(len(oos_high)),
                "oos_low_n": int(len(oos_low)),
                "oos_high_avg_pct": round(float(oos_high["net_return_pct"].mean()), 4),
                "oos_low_avg_pct": round(float(oos_low["net_return_pct"].mean()), 4),
                "oos_tercile_delta_pct": round(float(oos_high["net_return_pct"].mean() - oos_low["net_return_pct"].mean()), 4),
                "passes_shadow_gate": passed,
            }
        )
    return results


def _market_overlay_research(
    portfolio_rows: list[dict[str, Any]],
    market_features: dict[str, dict[str, float]],
    split_date: str,
) -> list[dict[str, Any]]:
    baskets = pd.DataFrame(portfolio_rows)
    baskets = baskets[baskets["policy"] == "equal_top6"].copy()
    for name in ["late_up_ratio", "late_mean_pct", "above_vwap_ratio"]:
        baskets[name] = baskets["signal_date"].map(lambda date: (market_features.get(date) or {}).get(name))
    results: list[dict[str, Any]] = []
    for feature, label in [
        ("late_up_ratio", "尾盘上涨家数占比"),
        ("late_mean_pct", "全市场尾盘平均收益"),
        ("above_vwap_ratio", "收盘站上VWAP占比"),
    ]:
        valid = baskets.dropna(subset=[feature]).set_index("signal_date").sort_index()
        train = valid[valid.index <= split_date]
        oos = valid[valid.index > split_date]
        low_cut = float(train[feature].quantile(1 / 3))
        high_cut = float(train[feature].quantile(2 / 3))
        oos_low = oos[oos[feature] <= low_cut]
        oos_high = oos[oos[feature] >= high_cut]
        train_low = train[train[feature] <= low_cut]
        train_high = train[train[feature] >= high_cut]
        relative_exposure = pd.Series(1.0, index=oos.index)
        relative_exposure.loc[oos[feature] <= low_cut] = 2 / 3
        relative_exposure.loc[oos[feature] >= high_cut] = 4 / 3
        baseline = _metric_summary(oos["basket_return_pct"])
        rotation = _metric_summary(oos["basket_return_pct"], relative_exposure)
        day_delta = float(oos_high["basket_return_pct"].mean() - oos_low["basket_return_pct"].mean())
        monthly = valid.assign(month=valid.index.str[:7]).groupby("month").apply(
            lambda month: month[[feature, "basket_return_pct"]].corr(method="spearman").iloc[0, 1],
            include_groups=False,
        )
        consistency = float((monthly > 0).mean()) if len(monthly) else 0.0
        rng = np.random.default_rng(RANDOM_SEED)
        bootstrap_deltas = []
        high_values = oos_high["basket_return_pct"].to_numpy(dtype=float)
        low_values = oos_low["basket_return_pct"].to_numpy(dtype=float)
        for _ in range(4000):
            bootstrap_deltas.append(
                float(rng.choice(high_values, len(high_values), replace=True).mean())
                - float(rng.choice(low_values, len(low_values), replace=True).mean())
            )
        adjusted_confidence = 1 - 0.05 / 3
        alpha = (1 - adjusted_confidence) / 2
        adjusted_ci = (
            float(np.quantile(bootstrap_deltas, alpha)),
            float(np.quantile(bootstrap_deltas, 1 - alpha)),
        )
        improves_both = bool(
            rotation["cumulative_pct"] > baseline["cumulative_pct"]
            and rotation["max_drawdown_pct"] > baseline["max_drawdown_pct"]
        )
        results.append(
            {
                "feature": feature,
                "label": label,
                "days": int(len(valid)),
                "train_low_cut": round(low_cut, 6),
                "train_high_cut": round(high_cut, 6),
                "oos_low_days": int(len(oos_low)),
                "oos_high_days": int(len(oos_high)),
                "oos_low_avg_pct": round(float(oos_low["basket_return_pct"].mean()), 4),
                "oos_high_avg_pct": round(float(oos_high["basket_return_pct"].mean()), 4),
                "oos_high_low_delta_pct": round(day_delta, 4),
                "train_low_avg_pct": round(float(train_low["basket_return_pct"].mean()), 4),
                "train_high_avg_pct": round(float(train_high["basket_return_pct"].mean()), 4),
                "train_high_low_delta_pct": round(
                    float(train_high["basket_return_pct"].mean() - train_low["basket_return_pct"].mean()),
                    4,
                ),
                "oos_adjusted_ci_low": round(adjusted_ci[0], 4),
                "oos_adjusted_ci_high": round(adjusted_ci[1], 4),
                "monthly_positive_fraction": round(consistency, 4),
                "months": int(len(monthly)),
                "oos_baseline": baseline,
                "oos_40_60_80_rotation": rotation,
                "avg_gross_exposure_pct": round(
                    float(relative_exposure.mean() * BASE_GROSS_EXPOSURE * 100),
                    2,
                ),
                "improves_cumulative_and_drawdown": improves_both,
                "passes_shadow_gate": bool(
                    len(oos_low) >= 20
                    and len(oos_high) >= 20
                    and float(train_high["basket_return_pct"].mean()) > float(train_low["basket_return_pct"].mean())
                    and day_delta > 0
                    and adjusted_ci[0] > 0
                    and consistency >= 0.80
                    and improves_both
                ),
            }
        )
    return results


def run() -> dict[str, Any]:
    days = _load_snapshot()
    simulated: dict[tuple[str, str], SimulatedTrade] = {}
    for day in days:
        for card in day.get("cards") or []:
            trade = _simulate_card(day, card)
            if trade is not None:
                simulated[(trade.signal_date, trade.code)] = trade

    jobs = [
        (str(day["signal_date"]), [str(card.get("code")) for card in day.get("cards") or []])
        for day in days
    ]
    candidate_intraday: dict[str, dict[str, dict[str, float]]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for date, features in pool.map(lambda args: _fetch_candidate_intraday(*args), jobs):
            candidate_intraday[date] = features

    feature_rows: list[dict[str, Any]] = []
    for trade in simulated.values():
        features = (candidate_intraday.get(trade.signal_date) or {}).get(trade.code)
        if not features:
            continue
        feature_rows.append({**asdict(trade), **features})
    feature_frame = pd.DataFrame(feature_rows)

    portfolio_rows, portfolio_summary = _portfolio_research(days, simulated)
    split_date = str(portfolio_summary["split_date"])
    candidate_results = _candidate_feature_research(feature_frame, split_date)

    market_features: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for date, features in pool.map(_fetch_market_late_breadth, [str(day["signal_date"]) for day in days]):
            if features:
                market_features[date] = features
    market_results = _market_overlay_research(portfolio_rows, market_features, split_date)

    with SessionLocal() as session:
        auction = session.execute(
            text(
                """
                SELECT (SELECT trade_date FROM auction_data ORDER BY trade_date ASC LIMIT 1) AS first_date,
                       (SELECT trade_date FROM auction_data ORDER BY trade_date DESC LIMIT 1) AS last_date,
                       COUNT(DISTINCT trade_date) AS day_count,
                       COUNT(*) AS row_count
                FROM auction_data
                """
            )
        ).mappings().one()
        auction_matches = session.execute(
            text(
                """
                SELECT COUNT(DISTINCT CONCAT(r.trade_date, '/', r.code)) AS matched_pairs
                FROM recommendations AS r
                JOIN recommendation_runs AS rr ON rr.run_id = r.run_id
                JOIN recommendation_outcomes AS o ON o.recommendation_id = r.recommendation_id
                JOIN auction_data AS a ON a.trade_date = r.trade_date AND a.code = r.code
                WHERE rr.status = 'completed'
                  AND rr.evidence_class IN ('historical_reconstruction', 'live_forward')
                  AND o.entry_triggered = 1
                """
            )
        ).scalar_one()

    return {
        "study": {
            "snapshot": str(SNAPSHOT),
            "signal_date_start": str(days[0]["signal_date"]),
            "signal_date_end": str(days[-1]["signal_date"]),
            "signal_days": len(days),
            "simulated_candidate_trades": len(simulated),
            "intraday_matched_trades": len(feature_frame),
            "intraday_coverage_pct": round(len(feature_frame) / max(len(simulated), 1) * 100, 2),
            "train_fraction": TRAIN_FRACTION,
            "split_date": split_date,
            "score_floor": SCORE_FLOOR,
            "top_n": TOP_N,
            "base_gross_exposure_pct": BASE_GROSS_EXPOSURE * 100,
            "daily_sleeve_pct": DAILY_SLEEVE_FRACTION * 100,
            "exit_policy": {
                "stop_loss_pct": STOP_LOSS_PCT,
                "take_profit_pct": TAKE_PROFIT_PCT,
                "horizon_days": HORIZON_DAYS,
                "fee_pct": FEE_PCT,
                "slippage_bps_each_side": SLIPPAGE_BPS,
                "max_entry_gap_pct": MAX_ENTRY_GAP_PCT,
            },
        },
        "candidate_intraday_features": candidate_results,
        "portfolio_policies": portfolio_summary,
        "market_late_breadth_overlays": market_results,
        "auction_data_readiness": {
            "first_date": str(auction["first_date"]),
            "last_date": str(auction["last_date"]),
            "days": int(auction["day_count"]),
            "rows": int(auction["row_count"]),
            "matched_settled_formal_pairs": int(auction_matches),
            "research_ready": bool(int(auction_matches) >= 100 and int(auction["day_count"]) >= 20),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args()
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2, default=str))


if __name__ == "__main__":
    main()
