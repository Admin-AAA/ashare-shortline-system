"""§282 收益来源指标与新达标标准的纯函数（中线/长线/短线共用）。定义见 docs/strategy-research.md §282（预注册）。输入收益单位：%（已扣成本）。"""
from __future__ import annotations
import statistics

LABEL_ROBUST = "稳健型：多数交易都赚一点"
LABEL_LOTTERY = "彩票型：靠少数暴涨，多数时候原地踏步"


def tail_metrics(rets: list[float]) -> dict:
    """rets：按入场/出场时间排序的逐笔收益（%）。返回 n/avg/median/trim5(只去最赚5%)/trim_both5(两头各去5%)/top10_share/top10_red/label/streak。"""
    n = len(rets)
    if n == 0:
        return {"n": 0}
    xs = sorted(rets)
    k = max(1, round(0.05 * n))
    trim5 = statistics.fmean(xs[: n - k]) if n > k else None          # 压力测试：只去最赚 5%
    trim_both5 = statistics.fmean(xs[k: n - k]) if n > 2 * k else None  # 正常水平：两头各去 5%（§285，标签依据）
    tot = sum(xs)
    top10 = sum(xs[-10:])
    share = (top10 / tot) if tot > 0 else None
    med = statistics.median(xs)
    streak = best = 0
    for x in rets:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    robust = trim_both5 is not None and trim_both5 > 0 and med > 0  # §285：标签按两头各去 5%
    return {"n": n, "avg": statistics.fmean(xs), "median": med, "trim5": trim5, "trim_both5": trim_both5, "top10_share": share,
            "top10_red": bool(share is not None and share > 0.5), "streak": best,
            "label": LABEL_ROBUST if robust else LABEL_LOTTERY, "robust": robust}


def overall_label(r12: dict, n3: dict) -> str:
    return LABEL_ROBUST if (r12.get("robust") and n3.get("robust")) else LABEL_LOTTERY


MDD_CAP = {"mid": -25.0, "long": -20.0, "short": -35.0}


def verdict(horizon: str, r12: dict, n3: dict, phases: dict) -> dict:
    """r12/n3: {n,avg,pf,mdd}；phases: {阶段名: avg}（中长线 8 段；短线 4 窗口 EXT/12m/W0/W00）。返回各条款与是否达标。"""
    cap = MDD_CAP[horizon]
    pos = sum(1 for v in phases.values() if v is not None and v > 0)
    need = 6 if horizon != "short" else 3
    bears = [phases[k] for k in phases if k == "W0" or k.startswith("W0 ") or k == "W00" or k.startswith("W00")]
    c = {
        "1_avg_pos": r12["avg"] > 0 and n3["avg"] > 0,
        "2a_pf": r12["pf"] >= 1.3 and n3["pf"] >= 1.3,
        "2b_mdd": r12["mdd"] >= cap and n3["mdd"] >= cap,
        "3a_phases": pos >= need,
        "3b_bear": len(bears) >= 2 and all(b >= -5.0 for b in bears),
        "4_n": r12["n"] >= 30 and n3["n"] >= 30,
    }
    return {**c, "pos_phases": pos, "n_phases": len(phases), "pass": all(c.values()),
            "fail": [k for k, v in c.items() if not v]}
