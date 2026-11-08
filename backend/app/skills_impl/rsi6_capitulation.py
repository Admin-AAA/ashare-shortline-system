"""RSI6 恐慌出清（命令式策略，观察身份）。

信号日 T 收盘同时满足：
- 等权全市场 RSI(6) < 20（市场急跌、情绪超卖）；
- 涨停家数 / max(跌停家数, 1) <= 0.34（跌停家数占优，真正的恐慌出清）；
- 非 ST、非涨停收盘、当日成交额 >= 1 亿。

选股：按当日成交额从大到小取前 10（score 随成交额单调，由单策略选股政策截取）。
出场：+5% 止盈、-4.5% 止损、关闭移动止损、持 5 个交易日（strategy.yaml exit_overrides）。
研究依据：docs/research/rsi_exit_s1051_capitulation_filter.md，
§1053/§1054 回测 R12 +1.77%/笔 PF1.90、N3 +1.21%/笔 PF1.55，4 窗口 4/4、8 段 6/8。
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.engine.imperative_strategy import StrategyDecision
from app.engine.signal import ENTRY_MODE_NEXT_OPEN
from app.engine.strategy import StrategyContext


class Rsi6Capitulation:
    """市场级 RSI6 超卖 + 跌停家数占优时，买入流动性最好的 10 只。"""

    universe: ClassVar[dict[str, Any]] = {
        "include": ["amount_yi >= 1", "is_limit_up == false"],
        "exclude": ["is_st == true"],
    }

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> StrategyDecision:
        rsi6 = float(stock.get("mkt_rsi6") or 0.0)
        limit_up = float(stock.get("limit_up_count") or 0.0)
        limit_down = float(stock.get("limit_down_count") or 0.0)
        lu_ld = float(stock.get("mkt_lu_ld") or (limit_up / max(1.0, limit_down)))
        amount_yi = float(stock.get("amount_yi") or 0.0)
        evidence = [
            f"等权指数RSI6={round(rsi6, 2)}",
            f"涨停/跌停={limit_up:.0f}/{limit_down:.0f}（lu_ld={round(lu_ld, 3)}）",
            f"当日成交额={round(amount_yi, 2)}亿",
        ]

        # 市场条件不满足：不给分，选股政策按 72 分线自然排除。
        if not (0.0 < rsi6 < 20.0 and lu_ld <= 0.34):
            return StrategyDecision(
                score=0.0,
                phase="市场未触发",
                score_cap=100.0,
                setup_quality=0.0,
                evidence=evidence,
            )

        # score 随成交额单调（80~100），让单策略选股按成交额取前 10。
        score = 80.0 + min(20.0, amount_yi * 0.1)
        return StrategyDecision(
            score=score,
            phase="RSI6恐慌出清",
            score_cap=100.0,
            setup_match="RSI6超卖+跌停家数占优",
            setup_quality=85.0,
            entry_rule="T+1 开盘买；+5% 止盈、-4.5% 止损、持 5 日",
            invalid_rule="信号日跌停家数不再占优或市场 RSI6 回升",
            entry_mode=ENTRY_MODE_NEXT_OPEN,
            risk_flags=["熊市回撤风险（低频策略）"],
            evidence=evidence,
        )
