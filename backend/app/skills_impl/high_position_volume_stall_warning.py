"""「高位放量滞涨预警」命令式策略。

该实现只计算 ``StrategyDecision``。策略身份、版本、运行模式、权重和独立性均由
manifest 装配的 ``StrategyDescriptor`` 持有，adapter 统一投影为 ``Signal``。
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.engine.imperative_strategy import StrategyDecision
from app.engine.strategy import StrategyContext

_BIAS_MA20_HIGH_THRESHOLD = 8.0
_VOL_EXPANSION_THRESHOLD = 1.3
_STALL_PCT_CEILING = 1.0
_EXTREME_TURNOVER_THRESHOLD = 18.0


class HighPositionVolumeStallWarning:
    """识别「量在放、价不涨」的高位顶部风险。"""

    # The sole optional implementation-side declaration: execution-time
    # universe filtering. Identity and all declared metadata remain in manifest.
    universe: ClassVar[dict[str, Any]] = {
        "include": ["amount_yi >= 1"],
        "exclude": ["is_st == true"],
    }

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> StrategyDecision:
        bias_ma20 = float(stock.get("bias_ma20") or 0.0)
        vol5_20 = float(stock.get("vol5_20") or 0.0)
        pct = float(stock.get("pct") or 0.0)
        turnover_rate = float(stock.get("turnover_rate") or 0.0)

        is_high_position = bias_ma20 >= _BIAS_MA20_HIGH_THRESHOLD
        is_volume_expanding = vol5_20 >= _VOL_EXPANSION_THRESHOLD
        is_stalling = pct <= _STALL_PCT_CEILING
        stall_warning_triggered = is_high_position and is_volume_expanding and is_stalling
        evidence = [
            f"乖离率(bias_ma20)={round(bias_ma20, 2)}",
            f"量比(vol5_20)={round(vol5_20, 2)}",
            f"当日涨幅(pct)={round(pct, 2)}",
        ]

        if stall_warning_triggered and turnover_rate >= _EXTREME_TURNOVER_THRESHOLD:
            return StrategyDecision(
                score=15.0,
                phase="高位放量滞涨-换手极端",
                score_cap=25.0,
                hard_veto=True,
                veto_reason="高位放量滞涨且换手率极端，筹码严重分歧，规避追高",
                risk_flags=["放量滞涨", f"换手率极端({round(turnover_rate, 1)}%)"],
                setup_quality=20.0,
                evidence=evidence + [f"换手率(turnover_rate)={round(turnover_rate, 2)}"],
            )
        if stall_warning_triggered:
            return StrategyDecision(
                score=32.0,
                phase="高位放量滞涨预警",
                score_cap=45.0,
                risk_flags=["放量滞涨，警惕高位资金派发"],
                setup_quality=35.0,
                evidence=evidence,
            )
        if is_high_position and is_volume_expanding and not is_stalling:
            return StrategyDecision(
                score=62.0,
                phase="放量上涨未滞涨",
                score_cap=80.0,
                setup_match="高位放量且价涨同步",
                setup_quality=65.0,
                evidence=evidence + ["量价同步上涨，暂无滞涨信号"],
            )
        return StrategyDecision(
            score=45.0,
            phase="未触发预警",
            score_cap=60.0,
            setup_quality=45.0,
            evidence=evidence + [f"乖离率未达预警阈值({_BIAS_MA20_HIGH_THRESHOLD})"],
        )
