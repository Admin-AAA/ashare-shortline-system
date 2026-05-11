from __future__ import annotations

import statistics
from typing import Any


def _ma(values: list[float], window: int) -> float:
    sample = values[-window:] if len(values) >= window else values
    return statistics.fmean(sample) if sample else 0.0


def _ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(values: list[float], window: int) -> float:
    if len(values) <= window:
        return 50.0
    deltas = [values[index] - values[index - 1] for index in range(len(values) - window, len(values))]
    gains = [delta for delta in deltas if delta > 0]
    losses = [-delta for delta in deltas if delta < 0]
    avg_gain = statistics.fmean(gains) if gains else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _macd(values: list[float]) -> tuple[float, float]:
    ema12 = _ema(values, 12)
    ema26 = _ema(values, 26)
    dif_series: list[float] = []
    for index in range(len(values)):
        subset = values[: index + 1]
        dif_series.append(_ema(subset, 12) - _ema(subset, 26))
    dif = ema12 - ema26
    dea = _ema(dif_series, 9)
    return dif, dea


def _bias(price: float, ma: float) -> float:
    return (price - ma) / ma * 100 if ma else 0.0


def _upper_shadow_count(bars: list[dict[str, Any]]) -> int:
    count = 0
    for bar in bars:
        high = float(bar.get("high") or 0)
        low = float(bar.get("low") or 0)
        open_ = float(bar.get("open") or 0)
        close = float(bar.get("close") or 0)
        spread = high - low
        if spread > 0 and (high - max(open_, close)) / spread >= 0.45:
            count += 1
    return count


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, (value - peak) / peak * 100)
    return max_dd


def _safe_div(left: float, right: float, default: float = 0.0) -> float:
    return left / right if right else default


def compute_daily_bar_indicators(code: str, bars: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    """Given sorted bars (ascending) for a single code, return {trade_date: {ma5, ma10, ma20, ma60, volume_ratio_5d}}."""
    result: dict[str, dict[str, float | None]] = {}
    closes: list[float] = []
    volumes: list[float] = []

    for bar in bars:
        closes.append(float(bar.get("close") or 0))
        volumes.append(float(bar.get("volume") or 0))

        trade_date = str(bar["trade_date"])
        result[trade_date] = {
            "ma5": round(_ma(closes, 5), 3),
            "ma10": round(_ma(closes, 10), 3),
            "ma20": round(_ma(closes, 20), 3),
            "ma60": round(_ma(closes, 60), 3) if len(closes) >= 60 else round(_ma(closes, len(closes)), 3),
            "volume_ratio_5d": round(_safe_div(_ma(volumes, 5), _ma(volumes, 20), 1.0), 3),
        }

    return result


def compute_daily_indicators(code: str, bars: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Given sorted bars (ascending) for a single code, return {trade_date: {rsi_*, macd_*, bias_*, breakout*, upper_shadow_count20, max_dd*}}."""
    result: dict[str, dict[str, Any]] = {}
    closes: list[float] = []
    highs: list[float] = []

    for index, bar in enumerate(bars):
        closes.append(float(bar.get("close") or 0))
        highs.append(float(bar.get("high") or 0))

        trade_date = str(bar["trade_date"])
        close = closes[-1]

        # MA values (reuse from closes)
        ma5 = _ma(closes, 5)
        ma10 = _ma(closes, 10)
        ma20 = _ma(closes, 20)

        # RSI
        rsi_6 = _rsi(closes, 6)
        rsi_12 = _rsi(closes, 12)
        rsi_24 = _rsi(closes, 24)

        # MACD
        if len(closes) >= 35:
            dif, dea = _macd(closes)
            macd_bar = (dif - dea) * 2
        else:
            dif, dea, macd_bar = None, None, None

        # Bias
        bias_ma5 = round(_bias(close, ma5), 3)
        bias_ma10 = round(_bias(close, ma10), 3)
        bias_ma20 = round(_bias(close, ma20), 3)

        # Breakout
        if len(highs) >= 21:
            previous_high20 = max(highs[-21:-1])
            breakout20 = 1 if close >= previous_high20 * 0.995 else 0
        else:
            breakout20 = 0

        if len(highs) >= 61:
            max_high60 = max(highs[-61:-1] or highs)
            breakout60 = 1 if close >= max_high60 * 0.995 else 0
        else:
            breakout60 = 0

        # Upper shadow count (last 20 bars)
        start = max(0, index - 19)
        upper_shadow_count20 = _upper_shadow_count(bars[start : index + 1])

        # Max drawdown
        window20 = closes[-20:] if len(closes) >= 20 else closes
        window60 = closes[-60:] if len(closes) >= 60 else closes
        max_dd20 = round(_max_drawdown(window20), 3)
        max_dd60 = round(_max_drawdown(window60), 3)

        result[trade_date] = {
            "rsi_6": rsi_6,
            "rsi_12": rsi_12,
            "rsi_24": rsi_24,
            "macd_dif": round(dif, 4) if dif is not None else None,
            "macd_dea": round(dea, 4) if dea is not None else None,
            "macd_bar": round(macd_bar, 4) if macd_bar is not None else None,
            "bias_ma5": bias_ma5,
            "bias_ma10": bias_ma10,
            "bias_ma20": bias_ma20,
            "breakout20": breakout20,
            "breakout60": breakout60,
            "upper_shadow_count20": upper_shadow_count20,
            "max_dd20": max_dd20,
            "max_dd60": max_dd60,
        }

    return result
