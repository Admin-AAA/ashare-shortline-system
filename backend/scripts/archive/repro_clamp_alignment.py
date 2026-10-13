"""对齐 clamp 后的分数线复现。

背景：repo 的 resolve_score_line_context 曾用 min(95.0) 钳制 effective_score_line，
而 2026-07-03 生产矩阵批次的有效分数线到了 119（生产版本无此硬上限）。两处不一致
会让回测无法复现生产打分。

本脚本用代表性强牛·高潮 snapshot，对照各 preset 的 base 分数线，分别计算：
  - 旧 clamp (min 95) 下的 effective
  - 对齐后 clamp (min 120，即当前 repo) 下的 effective
并与本次批次候选最高分 (86.35) 比较，验证「0 推荐」与 clamp 取值无关。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine.aggregator import resolve_score_line_context

# 代表性强牛·高潮：up_ratio≈0.70 -> 强牛；limit_ratio≈0.015 -> 高潮
SNAPSHOT = {
    "sample_count": 5300,
    "up_count": 3700,
    "limit_up_count": 80,
    "limit_down_count": 5,
    "trade_date": "2026-07-03",
}

# 来自 2026-07-03 矩阵批次报告的有效分数线（effective），反推 base = effective - 19
# （强牛 +6 / 高潮 +13 = +19，risk_template=balanced 不调整）
PRESETS = [
    ("趋势龙头组合", 112.5),
    ("陈小群", 115.0),
    ("主线相对强度", 85.0),
    ("基础短线策略", 91.0),
    ("涨停回封接力", 82.0),
    ("隔夜持股", 91.0),
    ("阶段新高突破", 85.0),
    ("强趋势缩量回踩", 82.0),
]

TOP_CANDIDATE = 86.35  # 本次批次候选最高分（深南电路 002916）


def main() -> None:
    ctx = resolve_score_line_context(PRESETS[0][1] - 19, market_snapshot=SNAPSHOT, risk_template="balanced")
    regime = f"{ctx['market_base']}·{ctx['market_sentiment']}"
    adj = ctx["market_adjustment"] + ctx["risk_template_adjustment"]

    print(f"行情状态: {regime}  (market_adjustment = {ctx['market_adjustment']})")
    print(f"候选最高分: {TOP_CANDIDATE}")
    print()
    print(f"{'preset':<14}{'base':>8}{'eff(旧 clamp95)':>16}{'eff(对齐120)':>14}{'门槛状态':>22}")
    print("-" * 86)

    for name, eff in PRESETS:
        base = round(eff - 19, 2)
        eff_old = round(max(35.0, min(95.0, base + adj)), 2)
        eff_new_ctx = resolve_score_line_context(base, market_snapshot=SNAPSHOT, risk_template="balanced")
        eff_new = eff_new_ctx["effective_score_line"]
        if eff_new > TOP_CANDIDATE:
            status = "线>最高分·必0推荐"
        else:
            status = "线≤最高分·需内部分数"
        print(f"{name:<14}{base:>8}{eff_old:>16}{eff_new:>14}{status:>22}")

    print()
    print("说明: '最高分'指本次批次全局已知候选最高分 86.35（深南电路）。同一股票在不同 preset 得分不同，")
    print("      各 preset 内部实际最高分需真实批次数据；趋势龙头/陈小群线(112.5/115)远高于 86.35，必然 0 推荐。")
    print()
    print("结论: 将 repo 上限从 95 对齐到 120 后，强牛·高潮阶段分数线可复现到生产批次的 119 量级；")
    print("      高分 preset 的 0 推荐与 clamp 取值无关（线始终高于候选分）。clamp 对齐使回测与生产打分一致。")


if __name__ == "__main__":
    main()
