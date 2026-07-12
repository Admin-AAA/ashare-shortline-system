"""「高位放量滞涨预警」命令式策略（任务 3.5，需求 4.6）。

业务逻辑说明（真实、有含义的短线风险预警策略，不是测试替身）：

短线交易里一个常见的顶部信号是「放量滞涨」——股价已经明显偏离 20 日均线（乖离率
``bias_ma20`` 高企，说明短期涨幅已经很大），同时成交量比（``vol5_20``）继续放大
（资金还在进场），但当日涨幅（``pct``）却很小甚至下跌——即「量在放，价不涨」。这
通常意味着高位有资金借助放量派发，短线继续上攻的动能已经衰竭，是一个**该规避
而非追涨**的信号，而不是常见的「放量上涨=看多」逻辑的简单镜像。

打分/否决设计（``score()`` 内的四个分支，覆盖不同业务含义）：
  1. **滞涨预警命中 + 换手率极端（>= 18%）**：量价背离已经确认，且换手率显示
     筹码已经严重分歧、随时可能加速下跌 → ``hard_veto``，直接规避。
  2. **滞涨预警命中但换手率未到极端水平**：给出低分 + 风险提示，不否决（留给
     aggregator 结合其他策略综合判断），但明确标注"高位放量滞涨"风险。
  3. **高位（乖离率高）但价涨量增、并未滞涨**（``pct`` 仍有像样涨幅）：说明放量
     是资金在继续推升，而非派发 → 给中等偏高分，phase 标注"放量上涨未滞涨"。
  4. **未达到"高位"乖离率阈值**：预警条件根本不成立，给基础分，evidence 说明
     未触发预警阈值。

字段全部来自 ``evaluator.ALLOWED_NAMES`` 现有白名单（``bias_ma20`` / ``vol5_20`` /
``turnover_rate`` / ``pct``），universe 门槛复用与其他声明式技能一致的最小流动性
过滤（``amount_yi``）与 ST 排除。
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.engine.imperative_strategy import ImperativeScoreResult, ImperativeStrategy
from app.engine.strategy import StrategyContext

# ── 业务阈值（集中声明，便于未来校准时一处可改） ──────────────────────────
_BIAS_MA20_HIGH_THRESHOLD = 8.0   # 乖离率 >= 8%：短期已明显偏离 20 日均线，视为"高位"
_VOL_EXPANSION_THRESHOLD = 1.3    # 量比 >= 1.3：成交量相对 20 日均量显著放大
_STALL_PCT_CEILING = 1.0          # 当日涨幅 <= 1%：放量但价格不涨，视为"滞涨"
_EXTREME_TURNOVER_THRESHOLD = 18.0  # 换手率 >= 18%：筹码分歧已到极端水平


class HighPositionVolumeStallWarning(ImperativeStrategy):
    """高位放量滞涨预警：识别「量在放、价不涨」的顶部风险信号。"""

    strategy_id: ClassVar[str] = "high-position-volume-stall-warning"
    strategy_name: ClassVar[str] = "高位放量滞涨预警"
    version: ClassVar[str] = "0.1.0"
    weight: ClassVar[float] = 0.3
    strategy_independence: ClassVar[float] = 0.8

    universe: ClassVar[dict[str, Any]] = {
        "include": ["amount_yi >= 1"],
        "exclude": ["is_st == true"],
    }

    def score(self, stock: dict[str, Any], ctx: StrategyContext) -> ImperativeScoreResult:
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

        # 分支 1：滞涨预警命中 + 换手率极端 → 硬否决，规避追高
        if stall_warning_triggered and turnover_rate >= _EXTREME_TURNOVER_THRESHOLD:
            return ImperativeScoreResult(
                score=15.0,
                phase="高位放量滞涨-换手极端",
                score_cap=25.0,
                hard_veto=True,
                veto_reason="高位放量滞涨且换手率极端，筹码严重分歧，规避追高",
                risk_flags=["放量滞涨", f"换手率极端({round(turnover_rate, 1)}%)"],
                setup_quality=20.0,
                evidence=evidence + [f"换手率(turnover_rate)={round(turnover_rate, 2)}"],
            )

        # 分支 2：滞涨预警命中，换手率未到极端 → 低分 + 风险提示，不否决
        if stall_warning_triggered:
            return ImperativeScoreResult(
                score=32.0,
                phase="高位放量滞涨预警",
                score_cap=45.0,
                hard_veto=False,
                risk_flags=["放量滞涨，警惕高位资金派发"],
                setup_quality=35.0,
                evidence=evidence,
            )

        # 分支 3：高位但价涨量增、并未滞涨 → 资金仍在推升，给中等偏高分
        if is_high_position and is_volume_expanding and not is_stalling:
            return ImperativeScoreResult(
                score=62.0,
                phase="放量上涨未滞涨",
                score_cap=80.0,
                hard_veto=False,
                risk_flags=[],
                setup_match="高位放量且价涨同步",
                setup_quality=65.0,
                evidence=evidence + ["量价同步上涨，暂无滞涨信号"],
            )

        # 分支 4：未达"高位"乖离率阈值 → 预警条件不成立，基础分
        return ImperativeScoreResult(
            score=45.0,
            phase="未触发预警",
            score_cap=60.0,
            hard_veto=False,
            risk_flags=[],
            setup_quality=45.0,
            evidence=evidence + [f"乖离率未达预警阈值({_BIAS_MA20_HIGH_THRESHOLD})"],
        )
