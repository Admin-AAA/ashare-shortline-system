"""特征信号过拟合护栏（feature-signal overfit guard）。

第 7 轮 T3.2 实证：在 n≈200 的 LIVE formal 样本上用 point-biserial 找「预测力最强的
特征」挖 D 项毒区，连续 3 个头部候选全部被证伪为**小样本/单日集中/混杂伪影**：

- holder_trade_count（预示 no_follow_through，r=+0.20）：93% 票 holder_trade_count=0，
  >=1 仅 3 笔且全部集中 07-16 → 重尾单日伪影。
- dragon_tiger_net_buy_ratio（10cm 负向 r=-0.41）：全部 37 样本落在 [0,20] 窄桶、无 >20
  样本，且 86% 集中 07-06/07/08 → 窄范围噪声相关。
- roe（10cm 负向 r=-0.27）：roe>10 仅 8 笔全集中 07-08（WR0%），控制 free_mv 后小盘子集
  方向反转（roe<0 WR50% vs 0-5 WR84%）→ free_mv 混杂伪影。

教训：n≈200 的 LIVE 样本上，单特征 point-biserial 的「头部」相关几乎都是「稀疏特征 ×
单日 regime」的乘积。若不显式检验，self-improve 会每轮把这些噪声当成「高预测力因子」
去加 entry/exit 规则，反复过拟合同一个交易日的噪声（与 lessons #187 / #350 同构）。

本模块提供 `evaluate_feature_signal`：给定「信号正例」的 (trade_date) 列表，输出该信号
是否**可信到足以为之加规则**。判定两条（任一命中即不可信）：
1. 正例样本 < MIN_SUPPORT —— 统计噪声，无法区分真伪。
2. 正例 top 单日占比 >= TOP_DAY_RATIO —— 单日 regime 驱动，个股因子不可解。

与 poison_zone_day_guard.single_day_concentration 互补：后者检验「失败毒区模式」，
本模块检验「point-biserial 挖出的特征候选」。两者共用同一套单日集中度思想，但输入不同
（一个是失败模式的日期列表，一个是特征命中样本的日期列表）。
"""

from __future__ import annotations

from collections import Counter

# 与 self_check / poison_zone_day_guard 口径对齐：单日主导阈值 40%（单日 regime 噪声判定），
# 最小支撑样本与「样本 <20 不可信」的 E 项下限（DEFAULT_EXPOSURE_MIN_SAMPLE=20）同源思路，
# 但特征信号更宽松——特征命中本身已是全样本的子集，放宽到 12 笔即可做方向参考（仍不足以下规则）。
MIN_SUPPORT = 12          # 正例样本 <12 → 统计噪声，不足以为之加规则
TOP_DAY_RATIO = 0.40      # 正例 top 单日占比 >=40% → 单日 regime 驱动
INDEPENDENT_DAYS_MIN = 3  # 正例独立日 <3 → 样本时间跨度过窄，即使 top<40% 也不可信

# point-biserial 最小样本量（<20 算不出可信相关）
POINT_BISERIAL_MIN_N = 20


def point_biserial(x: list[float], y: list[int]) -> float | None:
    """point-biserial 相关系数：y 是 0/1 二值（如 outcome success=1/failed=0），x 是连续因子值。

    返回 [-1, +1] 的相关系数；样本 <20 或全 0/全 1 或 sd=0 时返回 None。
    提取为共享纯函数，self_check 资金流因子摘要 + analyze_capital_flow_factor.py 共用，
    避免两处内联计算漂移。负值=因子高对应失败，正值=因子高对应成功。
    """
    n = len(x)
    if n < POINT_BISERIAL_MIN_N:
        return None
    n1 = sum(y)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return None
    mean = sum(x) / n
    sd = (sum((xi - mean) ** 2 for xi in x) / n) ** 0.5
    if sd == 0:
        return None
    return (sum(xi for xi, yi in zip(x, y) if yi == 1) / n1 -
            sum(xi for xi, yi in zip(x, y) if yi == 0) / n0) / sd * ((n1 * n0) ** 0.5 / n)


def evaluate_feature_signal(dates: list[str]) -> dict:
    """评估一个 point-biserial 特征候选是否可信到足以为之加规则。

    参数 dates: 该特征「信号正例」样本的交易日列表（如命中 holder_trade_count>=1 的
    推荐的 trade_date，或 10cm 内 dragon_tiger_net_buy_ratio 高段的失败样本日期）。
    调用方负责按自己的信号定义筛出正例子集，本函数只判时间分布是否过拟合。

    返回 {support, independent_days, top_day, top_day_ratio, trustworthy, reason}。
    trustworthy=False 时 reason 给出拦截理由（供 self-improve 日志/护栏输出引用）。
    """
    n = len(dates)
    if n == 0:
        return {
            "support": 0, "independent_days": 0, "top_day": None,
            "top_day_ratio": 0.0, "trustworthy": False,
            "reason": "无信号正例样本",
        }
    counter = Counter(str(d) for d in dates)
    independent_days = len(counter)
    top_day, top_n = counter.most_common(1)[0]
    top_ratio = top_n / n

    if n < MIN_SUPPORT:
        verdict = False
        reason = f"正例样本过少(n={n}<{MIN_SUPPORT})，point-biserial 属统计噪声，禁止据此加规则"
    elif independent_days < INDEPENDENT_DAYS_MIN:
        verdict = False
        reason = (
            f"正例独立日过少({independent_days}<{INDEPENDENT_DAYS_MIN})，"
            f"时间跨度过窄（top={top_day} 占 {top_ratio:.0%}），疑似单日/双日 regime 噪声"
        )
    elif top_ratio >= TOP_DAY_RATIO:
        verdict = False
        reason = (
            f"正例单日集中(top={top_day} 占 {top_ratio:.0%}>={TOP_DAY_RATIO:.0%})，"
            f"单日 regime 驱动，个股因子不可解，禁止据此加规则"
        )
    else:
        verdict = True
        reason = (
            f"正例 n={n} 跨 {independent_days} 日、top={top_day}({top_ratio:.0%})，"
            f"未被单日主导——可进入前瞻/回测验证（仍非充分条件，需扣费后样本外有效）"
        )

    return {
        "support": n,
        "independent_days": independent_days,
        "top_day": top_day,
        "top_day_ratio": round(top_ratio, 4),
        "trustworthy": verdict,
        "reason": reason,
    }
