"""恐慌出清（命令式策略，观察身份）。

信号日 T 收盘满足任一恐慌触发器（§1058 OR 扩展）：
- PRICE_CAP：等权全市场 RSI(6) < 20 且 涨停家数/max(跌停家数,1) <= 0.34（价格恐慌出清）；
- QV_SPIKE：QVIX50 期权隐含波动率五日涨幅 > 25%（mkt_qvix50_chg5 > 0.25，期权市场恐慌）。
股池：非 ST、非涨停收盘、当日成交额 >= 1 亿。

选股：按当日成交额从大到小取前 10（score 随成交额单调，由单策略选股政策截取）。
出场：+5% 止盈、-4.5% 止损、关闭移动止损、持 5 个交易日（strategy.yaml exit_overrides）。
研究依据：§1051–§1054（价格门，R12 +1.60/PF1.65、N3 +1.79/PF2.00，当日成交额口径）；
§1058 OR 扩展过 §282（8/8 段正、4/4 窗正，N3 322 笔 +1.54/PF1.61/MDD−8.8，总利润≈2.2×价格门），
用户 2026-11-16 批准以总收益优先部署。QVIX-only 日熊市负期望，价格门 lu_ld 过滤兜底（L-205）。
QVIX 字段缺失（0）时降级为仅价格门，不阻断。
"""
from __future__ import annotations

from typing import Any, ClassVar

from app.engine.imperative_strategy import StrategyDecision
from app.engine.signal import ENTRY_MODE_NEXT_OPEN
from app.engine.strategy import StrategyContext


class Rsi6Capitulation:
    """市场级恐慌出清（价格 RSI6+跌停占优 OR QVIX 尖峰）时，买入流动性最好的 10 只。"""

    universe: ClassVar[dict[str, Any]] = {
        "include": ["amount_yi >= 1", "is_limit_up == false"],
        "exclude": ["is_st == true"],
    }

    def evaluate(self, stock: dict[str, Any], ctx: StrategyContext) -> StrategyDecision:
        rsi6 = float(stock.get("mkt_rsi6") or 0.0)
        limit_up = float(stock.get("limit_up_count") or 0.0)
        limit_down = float(stock.get("limit_down_count") or 0.0)
        lu_ld = float(stock.get("mkt_lu_ld") or (limit_up / max(1.0, limit_down)))
        qvix_chg5 = float(stock.get("mkt_qvix50_chg5") or 0.0)
        amount_yi = float(stock.get("amount_yi") or 0.0)
        evidence = [
            f"等权指数RSI6={round(rsi6, 2)}",
            f"涨停/跌停={limit_up:.0f}/{limit_down:.0f}（lu_ld={round(lu_ld, 3)}）",
            f"QVIX50五日涨幅={round(qvix_chg5 * 100, 1)}%",
            f"当日成交额={round(amount_yi, 2)}亿",
        ]

        price_cap = 0.0 < rsi6 < 20.0 and lu_ld <= 0.34
        qv_spike = qvix_chg5 > 0.25
        # 两个恐慌触发器都不满足：不给分，选股政策按 72 分线自然排除。
        if not (price_cap or qv_spike):
            return StrategyDecision(
                score=0.0,
                phase="市场未触发",
                score_cap=100.0,
                setup_quality=0.0,
                evidence=evidence,
            )

        if price_cap and qv_spike:
            phase, setup = "恐慌出清(价格+QVIX)", "RSI6超卖+跌停占优+QVIX尖峰"
        elif price_cap:
            phase, setup = "RSI6恐慌出清", "RSI6超卖+跌停家数占优"
        else:
            phase, setup = "QVIX恐慌尖峰", "QVIX50五日飙升>25%"

        # score 随成交额单调（80~100），让单策略选股按成交额取前 10。
        score = 80.0 + min(20.0, amount_yi * 0.1)
        return StrategyDecision(
            score=score,
            phase=phase,
            score_cap=100.0,
            setup_match=setup,
            setup_quality=85.0,
            entry_rule="T+1 开盘买；+5% 止盈、-4.5% 止损、持 5 日",
            invalid_rule="价格恐慌消退且 QVIX 五日涨幅回落 <=25%",
            entry_mode=ENTRY_MODE_NEXT_OPEN,
            risk_flags=["熊市回撤风险（低频策略）", "QVIX-only 日熊市负期望（L-205）"],
            evidence=evidence,
        )
