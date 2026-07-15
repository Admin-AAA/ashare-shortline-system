"""技术指标数学：全系统唯一的指标计算深模块（deep module）。

此前 `_ma/_ema/_rsi/_macd/_macd_status/_bias/_efficiency/_max_drawdown/`
`_upper_shadow_count/_safe_div` 等指标数学在 market_data、feature_builder、
agents/tools 等处平行复制，任一处改动都要同步多份、极易漂移。这里把它们收敛为
单一实现：对外暴露一组窄而稳定的纯函数接口（见 __all__），实现体承担全部边界
处理（空/单元素序列、window 超长度、除零保护、增量 MACD 等）。

- 纯计算，无数据库 / 网络 / Tushare 依赖，可 in-process 单测。
- 调用方经 `from app.services.indicator_computer import <name> as _<name>` 复用，
  不再保留本地副本（重构基线：相同输入相同输出）。
"""
from __future__ import annotations

import math
import statistics
from typing import Any

__all__ = [
    "ma",
    "ema",
    "rsi",
    "macd",
    "macd_status",
    "bias",
    "efficiency",
    "upper_shadow_count",
    "strict_upper_shadow_count",
    "max_drawdown",
    "safe_div",
    "compute_daily_bar_indicators",
    "compute_daily_indicators",
]


def _num(value: Any, default: float = 0.0) -> float:
    """健壮数值兜底：None/""/"-"/NaN/inf/非数字串一律归为 default。

    收口自 market_data 的同名助手，供 upper_shadow_count 对异常行情字段做兜底。
    """
    if value is None or value == "" or value == "-":
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(number) or math.isinf(number):
        return float(default)
    return number


def ma(values: list[float], window: int, *, short: str = "mean") -> float:
    """window 内均值；样本不足 window 时按 short 口径兜底。

    - ``len(values) >= window``：取末 window 个的均值（与历史一致）。
    - ``len(values) < window`` 且 ``short == "mean"``（默认）：取全量均值，空序列 → 0.0。
    - ``len(values) < window`` 且 ``short == "last"``：取最后一个值，空序列 → 0.0。

    默认 ``"mean"`` 使既有调用点（market_data、compute_daily_* 等）行为零变化；
    agents/tools 历史上短序列取最后值，经 ``short="last"`` 复用同一实现。
    """
    if len(values) >= window:
        sample = values[-window:]
        return statistics.fmean(sample) if sample else 0.0
    if short == "last":
        return values[-1] if values else 0.0
    return statistics.fmean(values) if values else 0.0


def ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def rsi(values: list[float], window: int) -> float:
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


def macd(values: list[float]) -> tuple[float, float]:
    if len(values) < 26:
        ema12 = ema(values, 12)
        ema26 = ema(values, 26)
        return ema12 - ema26, 0.0
    alpha12 = 2 / 13
    alpha26 = 2 / 27
    alpha9 = 2 / 10
    ema12 = values[0]
    ema26 = values[0]
    dif_series: list[float] = []
    for value in values:
        ema12 = alpha12 * value + (1 - alpha12) * ema12
        ema26 = alpha26 * value + (1 - alpha26) * ema26
        dif_series.append(ema12 - ema26)
    dif = dif_series[-1]
    dea = dif_series[0]
    for d in dif_series[1:]:
        dea = alpha9 * d + (1 - alpha9) * dea
    return dif, dea


def macd_status(values: list[float]) -> str:
    if len(values) < 35:
        return "上穿零轴"
    dif, dea = macd(values)
    if dif > 0 and dif > dea:
        return "零轴上金叉"
    if dif > dea:
        return "金叉"
    if dif < 0 and dif < dea:
        return "空头"
    return "多头" if dif > 0 else "下穿零轴"


def bias(price: float, ma_value: float) -> float:
    return (price - ma_value) / ma_value * 100 if ma_value else 0.0


def efficiency(values: list[float], window: int) -> float:
    if len(values) <= window:
        return 0.0
    direction = abs(values[-1] - values[-window - 1])
    noise = sum(abs(values[index] - values[index - 1]) for index in range(len(values) - window, len(values)))
    return direction / noise if noise else 0.0


def upper_shadow_count(bars: list[dict[str, Any]]) -> int:
    """上影线偏长的 bar 数（上影线 / 全幅 ≥ 0.45），入参为 dict 序列。

    数值兜底用健壮的 `_num`（None/""/"-"/NaN/inf/非数字串 → 0）。对真实行情
    （有限数值、缺键 None）与旧的 ``float(x or 0)`` 口径逐位一致；仅对异常字符串
    与 NaN/inf 入参更健壮（唯一的、已记录的微差：旧口径遇 "-"/非数字串会抛错、
    遇 NaN/inf 会透传，新口径统一归零）。
    """
    count = 0
    for bar in bars:
        high = _num(bar.get("high"))
        low = _num(bar.get("low"))
        open_ = _num(bar.get("open"))
        close = _num(bar.get("close"))
        spread = high - low
        if spread > 0 and (high - max(open_, close)) / spread >= 0.45:
            count += 1
    return count


def _bar_field(bar: Any, name: str) -> Any:
    """读取一根 bar 的字段，兼容 dict（下标）与 ORM 行对象（属性，如 DailyKbar）。

    以鸭子类型代替对 ORM 类的 import，使本模块保持纯计算、无 DB / ORM 依赖。
    """
    if isinstance(bar, dict):
        return bar[name]
    return getattr(bar, name)


def strict_upper_shadow_count(bars: list[Any]) -> int:
    """严格口径上影线计数：与 `upper_shadow_count` 是**不同**指标。

    判定：上影线 / 全幅 ≥ 0.55 且 实体 / 全幅 ≤ 0.4 且 实体 > 0。兼容 dict 与
    ORM 行对象（DailyKbar 等）两种入参。此前平行存在于 agents/tools.py，等价迁入
    此处收口（计算逐字保留；ORM/dict 取值改为鸭子类型，避免耦合 ORM）。
    """
    count = 0
    for bar in bars:
        high = float(_bar_field(bar, "high"))
        close = float(_bar_field(bar, "close"))
        open_ = float(_bar_field(bar, "open"))
        low = float(_bar_field(bar, "low"))
        body = abs(close - open_)
        upper = high - max(close, open_)
        shadow_range = high - low
        if shadow_range > 0 and body > 0 and (upper / shadow_range) >= 0.55 and body / shadow_range <= 0.4:
            count += 1
    return count


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, (value - peak) / peak * 100)
    return max_dd


def safe_div(left: float, right: float, default: float = 0.0) -> float:
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
            "ma5": round(ma(closes, 5), 3),
            "ma10": round(ma(closes, 10), 3),
            "ma20": round(ma(closes, 20), 3),
            "ma60": round(ma(closes, 60), 3) if len(closes) >= 60 else round(ma(closes, len(closes)), 3),
            "volume_ratio_5d": round(safe_div(ma(volumes, 5), ma(volumes, 20), 1.0), 3),
        }

    return result


def compute_daily_indicators(code: str, bars: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Given sorted bars (ascending) for a single code, return {trade_date: {rsi_*, macd_*, bias_*, breakout*, upper_shadow_count20, max_dd*}}."""
    result: dict[str, dict[str, Any]] = {}
    closes: list[float] = []
    highs: list[float] = []

    # 增量 MACD 状态（避免每 bar 重算 O(n²)）
    alpha12 = 2 / 13
    alpha26 = 2 / 27
    alpha9 = 2 / 10
    ema12 = 0.0
    ema26 = 0.0
    dea = 0.0
    macd_initialized = False

    for index, bar in enumerate(bars):
        close = float(bar.get("close") or 0)
        closes.append(close)
        highs.append(float(bar.get("high") or 0))

        trade_date = str(bar["trade_date"])

        # MA values (reuse from closes)
        ma5 = ma(closes, 5)
        ma10 = ma(closes, 10)
        ma20 = ma(closes, 20)

        # RSI
        rsi_6 = rsi(closes, 6)
        rsi_12 = rsi(closes, 12)
        rsi_24 = rsi(closes, 24)

        # MACD — 增量计算（O(1) per bar，总计 O(n)）
        dif: float | None = None
        dea_out: float | None = None
        macd_bar: float | None = None
        if len(closes) >= 35:
            if not macd_initialized:
                # 一次性初始化：处理前 34 个 close，得到 ema/dea 状态
                ema12 = closes[0]
                ema26 = closes[0]
                dea = 0.0
                for c in closes[:-1]:
                    ema12 = alpha12 * c + (1 - alpha12) * ema12
                    ema26 = alpha26 * c + (1 - alpha26) * ema26
                    dif_prev = ema12 - ema26
                    dea = alpha9 * dif_prev + (1 - alpha9) * dea
                macd_initialized = True
            # 当前 bar 增量更新
            ema12 = alpha12 * close + (1 - alpha12) * ema12
            ema26 = alpha26 * close + (1 - alpha26) * ema26
            dif = ema12 - ema26
            dea = alpha9 * dif + (1 - alpha9) * dea
            dea_out = dea
            macd_bar = (dif - dea) * 2

        # Bias
        bias_ma5 = round(bias(close, ma5), 3)
        bias_ma10 = round(bias(close, ma10), 3)
        bias_ma20 = round(bias(close, ma20), 3)

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
        upper_shadow_count20 = upper_shadow_count(bars[start : index + 1])

        # Max drawdown
        window20 = closes[-20:] if len(closes) >= 20 else closes
        window60 = closes[-60:] if len(closes) >= 60 else closes
        max_dd20 = round(max_drawdown(window20), 3)
        max_dd60 = round(max_drawdown(window60), 3)

        result[trade_date] = {
            "rsi_6": rsi_6,
            "rsi_12": rsi_12,
            "rsi_24": rsi_24,
            "macd_dif": round(dif, 4) if dif is not None else None,
            "macd_dea": round(dea_out, 4) if dea_out is not None else None,
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
