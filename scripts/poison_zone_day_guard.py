"""毒区单日集中度护栏：判定一组失败样本是否被单一交易日主导。

第一性原理（见 docs/lessons/LESSONS.md，历史第187轮）：单日（尤其情绪异常日）集中驱动的毒区，
个股 entry 侧不可解——任何据此加的 entry 规则都是对一个交易日噪声的过拟合。
加 entry 规则前必须验证「该毒区跨 >=3 个独立交易日」，否则禁止加规则。

self_check 已打印「单日集中」提示，但只看不判。本模块把它升级为可复用判定：
给定失败笔的 (trade_date) 列表，返回是否单日主导 + 主导日占比 + 独立日数。
纯函数，便于单测锁定判定阈值。
"""
from __future__ import annotations

from collections import Counter

# 单日主导判定阈值：
# - 独立交易日 < MIN_INDEPENDENT_DAYS：样本天然不足，禁止下规则
# - 或 最大单日占比 >= SINGLE_DAY_DOMINANCE：单一日吃掉多数样本，禁止下规则
MIN_INDEPENDENT_DAYS = 3
SINGLE_DAY_DOMINANCE = 0.5


def single_day_concentration(trade_dates: list[str]) -> dict:
    """判定一组失败笔是否被单一交易日主导。

    参数: trade_dates —— 每个失败笔的 trade_date（'YYYY-MM-DD' 或 'YYYYMMDD'）。
    返回: {
        'n': 总笔数,
        'independent_days': 独立交易日数,
        'top_day': 样本最多的交易日,
        'top_day_count': 该日笔数,
        'top_day_ratio': 该日占比,
        'single_day_driven': 是否单日主导（True=禁止据此加 entry 规则）,
        'reason': 判定理由,
    }
    """
    n = len(trade_dates)
    if n == 0:
        return {
            "n": 0, "independent_days": 0, "top_day": None, "top_day_count": 0,
            "top_day_ratio": 0.0, "single_day_driven": True,
            "reason": "无样本",
        }
    cnt = Counter(trade_dates)
    independent_days = len(cnt)
    top_day, top_count = cnt.most_common(1)[0]
    top_ratio = top_count / n

    if independent_days < MIN_INDEPENDENT_DAYS:
        driven = True
        reason = (f"独立交易日仅 {independent_days} 天 (<{MIN_INDEPENDENT_DAYS})，"
                  f"样本不足以支撑可泛化规则")
    elif top_ratio >= SINGLE_DAY_DOMINANCE:
        driven = True
        reason = (f"单日 {top_day} 占 {top_ratio:.0%} (>={SINGLE_DAY_DOMINANCE:.0%})，"
                  f"疑似单日 regime 风险，非个股因子可解")
    else:
        driven = False
        reason = (f"跨 {independent_days} 个独立交易日，最大单日占比 {top_ratio:.0%} "
                  f"<{SINGLE_DAY_DOMINANCE:.0%}，可考虑 entry 规则（仍需分段胜率验证）")

    return {
        "n": n,
        "independent_days": independent_days,
        "top_day": top_day,
        "top_day_count": top_count,
        "top_day_ratio": round(top_ratio, 4),
        "single_day_driven": driven,
        "reason": reason,
    }


def report_poison_zone_guard(name: str, trade_dates: list[str]) -> str:
    """对某毒区打印护栏判定，返回人类可读摘要字符串。"""
    r = single_day_concentration(trade_dates)
    verdict = "🚫 禁止加 entry 规则" if r["single_day_driven"] else "✅ 可进一步验证"
    line = (f"[{name}] n={r['n']} 独立日={r['independent_days']} "
            f"top_day={r['top_day']}({r['top_day_ratio']:.0%}) → {verdict}\n"
            f"    {r['reason']}")
    return line


def subcluster_day_concentration(
    items: list[tuple[str, str]],
    min_subcluster: int = 3,
) -> dict | None:
    """子簇级单日集中检测：模式级分散时，仍可能存在「特征子簇集中单日」。

    背景（见 docs/lessons/LESSONS.md，历史第187轮）：no_follow_through 18笔模式级 独立日=5 top=07-16(44%)
    → 模式级"可验证"，但按 vol5_20>=3 细分后，high_vol 子簇 8/9 集中 07-16（89%）——
    模式级绿灯掩盖了子簇级单日集中。据此对 high_vol 加 entry 规则仍是单日过拟合。

    参数: items —— [(cluster_key, trade_date)]，cluster_key 为子簇标签（如 'high_vol'）。
    返回: 最大单日占比 >= SINGLE_DAY_DOMINANCE 且样本 >= min_subcluster 的子簇里，
        集中度最高者的 {cluster_key, n, top_day, top_day_ratio}；无则 None。
    """
    from collections import defaultdict
    by_cluster: dict[str, list[str]] = defaultdict(list)
    for key, d in items:
        by_cluster[key].append(d)

    worst: dict | None = None
    for key, dates in by_cluster.items():
        if len(dates) < min_subcluster:
            continue
        r = single_day_concentration(dates)
        if r["top_day_ratio"] >= SINGLE_DAY_DOMINANCE:
            entry = {
                "cluster_key": key,
                "n": r["n"],
                "top_day": r["top_day"],
                "top_day_ratio": r["top_day_ratio"],
            }
            if worst is None or entry["top_day_ratio"] > worst["top_day_ratio"]:
                worst = entry
    return worst
