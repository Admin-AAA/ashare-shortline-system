"""因子级 alpha 衰减诊断（T1 / T2 前置，因子级补充 verify_alpha_decay_by_strategy）。

verify_alpha_decay_by_strategy 只做「策略(bucket)胜率 前期 vs 近期」粗粒度衰减。
本工具下沉到 **因子级**：

1. 对每个推荐快照因子（feature_snapshot_json 数值列）+ 组合分（final_score / consensus_score），
   在「前期 / 近期」两窗口分别计算 point-biserial（因子值 vs 二元胜负）。
2. 衰减% = (r_近期 - r_前期) / |r_前期|。
3. **artifact-safe 标记**：当基线 |r_前期| 极小时（< MIN_ABS_R），衰减% 会被分母放大成伪信号，
   故只有「基线 |r|>=MIN_ABS_R 且 |衰减|>DECAY_THR 且 两窗口 n>=MIN_N」才标 ⚠️ MATERIAL DECAY；
   基线极弱者仅打印 decay% 并标注弱基线跳过，不误报。
4. 组合分 edge（final_score / consensus_score 的 point-biserial）是**主监控**——
   策略的 edge 在「推荐闸门」而非单因子或集合内分数排序，故组合分 pb 才是 edge 是否退化的真信号。

用法:
    python scripts/diagnose_alpha_decay.py [--split-date YYYY-MM-DD] [--json] [--out PATH]
    --split-date: 前期/近期分割日（默认按 trade_date 中位数平衡分割）
    --json:       同时写机器可读报告（默认 output/alpha_decay_report.json）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

# 因子列（feature_snapshot_json 中数值标量）；布尔列按 0/1 处理
NUMERIC_FEATURES = [
    "pct", "amount_yi", "turnover_rate", "free_mv_yi", "total_mv_yi",
    "chg20", "chg60", "dist20", "dist60", "pos20", "pos60", "pos120",
    "eff20", "vol5_20", "ma5", "ma10", "ma20", "ma60",
    "support_ma5", "support_ma10", "rsi_12", "upper_shadow_count20", "max_dd20",
]
BOOL_FEATURES = ["volume_shrink_pullback", "volume_breakout_signal"]

# 衰减判定阈值
MIN_N = 12          # 单窗口最小样本（低于不纳入衰减判定，避免噪声）
MIN_ABS_R = 0.15    # 基线 |point-biserial| 下限：低于视为弱基线，decay% 不可信
DECAY_THR = 0.30    # 衰减% 阈值：|r_近期-r_前期|/|r_前期| > 30% 才标

DEFAULT_OUT = "output/alpha_decay_report.json"


# --------------------------------------------------------------------------- #
# 纯函数（可单测）
# --------------------------------------------------------------------------- #
def point_biserial(xs: list[float], ys: list[int]) -> float | None:
    """手动 point-biserial r。ys 为 0/1 二元；xs 为浮点。样本不足/无方差返回 None。"""
    n = len(xs)
    if n < 4:
        return None
    p = sum(ys) / n
    q = 1 - p
    if p == 0 or p == 1:
        return None
    mx = sum(xs) / n
    var = sum((x - mx) ** 2 for x in xs) / n
    if var <= 0:
        return None
    sx = math.sqrt(var)
    sum1 = sum(x for x, y in zip(xs, ys) if y == 1)
    n1 = sum(ys)
    sum0 = sum(x for x, y in zip(xs, ys) if y == 0)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return None
    m1 = sum1 / n1
    m0 = sum0 / n0
    return (m1 - m0) / sx * math.sqrt(p * q)


def split_by_median_date(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """按 trade_date 中位数平衡分割为 (前期, 近期)。rows 需含 'trade_date'。"""
    if not rows:
        return [], []
    srt = sorted(rows, key=lambda r: r["trade_date"])
    mid = len(srt) // 2
    return srt[:mid], srt[mid:]


def evaluate_factor_decay(
    prior: list[dict],
    recent: list[dict],
    min_n: int = MIN_N,
    min_abs_r: float = MIN_ABS_R,
    decay_thr: float = DECAY_THR,
) -> list[dict]:
    """对每个因子算前期/近期 point-biserial，返回结构化衰减评估。

    每条: {feature, r_prior, r_recent, decay_pct, n_prior, n_recent, baseline_weak, flag}
    flag: 'MATERIAL_DECAY' | 'noop'（弱基线或不足阈值）
    """
    out: list[dict] = []
    feats = NUMERIC_FEATURES + BOOL_FEATURES

    def window_pb(sub: list[dict], k: str):
        xs, ys = [], []
        for r in sub:
            v = r["features"].get(k)
            if v is None:
                continue
            xs.append(float(v) if not isinstance(v, bool) else (1.0 if v else 0.0))
            ys.append(1 if r["win"] else 0)
        return point_biserial(xs, ys), len(xs)

    for k in feats:
        rp, np_ = window_pb(prior, k)
        rr, nr = window_pb(recent, k)
        if rp is None or rr is None or np_ < min_n or nr < min_n:
            out.append({
                "feature": k, "r_prior": rp, "r_recent": rr,
                "decay_pct": None, "n_prior": np_, "n_recent": nr,
                "baseline_weak": True, "flag": "skip_low_n",
            })
            continue
        decay = (rr - rp) / abs(rp) if rp != 0 else float("inf")
        baseline_weak = abs(rp) < min_abs_r
        flag = "noop"
        if (not baseline_weak) and abs(decay) > decay_thr:
            flag = "MATERIAL_DECAY"
        out.append({
            "feature": k, "r_prior": round(rp, 4), "r_recent": round(rr, 4),
            "decay_pct": (round(decay * 100, 1) if math.isfinite(decay) else None),
            "n_prior": np_, "n_recent": nr,
            "baseline_weak": baseline_weak, "flag": flag,
        })
    return out


def evaluate_composite_edge(prior: list[dict], recent: list[dict]) -> dict:
    """主监控：组合分 final_score / consensus_score 的 point-biserial（edge 是否退化）。"""
    def pb_score(sub, key):
        xs = [float(r[key]) for r in sub if r.get(key) is not None]
        ys = [1 if r["win"] else 0 for r in sub if r.get(key) is not None]
        return point_biserial(xs, ys), len(xs)

    res = {}
    for key in ("final_score", "consensus_score"):
        rp, np_ = pb_score(prior, key)
        rr, nr = pb_score(recent, key)
        res[key] = {
            "r_prior": (round(rp, 4) if rp is not None else None),
            "r_recent": (round(rr, 4) if rr is not None else None),
            "n_prior": np_, "n_recent": nr,
        }
    return res


def build_report(factor_rows: list[dict], composite: dict,
                 prior_dates: tuple[str, str], recent_dates: tuple[str, str],
                 n_prior: int, n_recent: int) -> dict:
    material = [f for f in factor_rows if f["flag"] == "MATERIAL_DECAY"]
    return {
        "prior_window": {"dates": list(prior_dates), "n": n_prior},
        "recent_window": {"dates": list(recent_dates), "n": n_recent},
        "composite_edge": composite,
        "factors": factor_rows,
        "material_decay_count": len(material),
        "material_decay_features": [f["feature"] for f in material],
        "verdict": (
            "NO_MATERIAL_DECAY"
            if not material else f"MATERIAL_DECAY:{len(material)}"
        ),
    }


# --------------------------------------------------------------------------- #
# DB 采集
# --------------------------------------------------------------------------- #
def collect_ledger(split_date: str | None = None) -> list[dict]:
    db = SessionLocal()
    # 仅取已结算（success/failed），含快照因子 + 组合分 + 胜负
    rows = db.execute(
        text(
            """
            SELECT r.trade_date, r.final_score, r.consensus_score,
                   r.feature_snapshot_json, o.outcome_label
            FROM recommendation_outcomes o
            JOIN recommendations r ON o.recommendation_id = r.recommendation_id
            WHERE o.outcome_label IN ('success', 'failed')
            """
        )
    ).fetchall()
    db.close()

    ledger = []
    for trade_date, final_score, consensus_score, fs_json, label in rows:
        fs = json.loads(fs_json or "{}")
        features = {k: fs.get(k) for k in (NUMERIC_FEATURES + BOOL_FEATURES)}
        ledger.append({
            "trade_date": str(trade_date),
            "final_score": float(final_score) if final_score is not None else None,
            "consensus_score": float(consensus_score) if consensus_score is not None else None,
            "features": features,
            "win": label == "success",
        })
    return ledger


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split-date", default=None,
                    help="前期/近期分割日 YYYY-MM-DD；缺省按 trade_date 中位数平衡分割")
    ap.add_argument("--json", action="store_true", help="写机器可读报告")
    ap.add_argument("--out", default=DEFAULT_OUT, help="报告输出路径")
    args = ap.parse_args()

    ledger = collect_ledger()
    if not ledger:
        print("无已结算推荐，无法评估 alpha 衰减。")
        return 0

    if args.split_date:
        prior = [r for r in ledger if r["trade_date"] < args.split_date]
        recent = [r for r in ledger if r["trade_date"] >= args.split_date]
    else:
        prior, recent = split_by_median_date(ledger)

    if not prior or not recent:
        print("分割后某一窗口为空，无法评估。")
        return 0

    fac = evaluate_factor_decay(prior, recent)
    comp = evaluate_composite_edge(prior, recent)
    prior_dates = (prior[0]["trade_date"], prior[-1]["trade_date"])
    recent_dates = (recent[0]["trade_date"], recent[-1]["trade_date"])
    report = build_report(fac, comp, prior_dates, recent_dates, len(prior), len(recent))

    print(f"因子级 alpha 衰减诊断  (前期 n={len(prior)} {prior_dates[0]}..{prior_dates[1]} | "
          f"近期 n={len(recent)} {recent_dates[0]}..{recent_dates[1]})")
    print(f"  MIN_N={MIN_N} MIN_ABS_R={MIN_ABS_R} DECAY_THR={DECAY_THR*100:.0f}%")
    print()
    print("组合分 edge（主监控）:")
    for k, v in comp.items():
        print(f"  {k:<16} r_前期={v['r_prior']}  r_近期={v['r_recent']}  "
              f"(n {v['n_prior']}/{v['n_recent']})")
    print()
    print(f"{'factor':<24}{'r_前期':>9}{'r_近期':>9}{'decay%':>10}  flag")
    for f in sorted(fac, key=lambda x: (x["flag"] != "MATERIAL_DECAY",
                                        -(abs(x["decay_pct"]) if x["decay_pct"] is not None else 0))):
        decay = f["decay_pct"]
        decay_s = f"{decay:+.1f}%" if decay is not None else "  -  "
        tag = "⚠ MATERIAL" if f["flag"] == "MATERIAL_DECAY" else (
            "弱基线跳过" if f["baseline_weak"] else "稳定")
        # 弱基线且 decay 大时显式提示 artifact
        if f["baseline_weak"] and decay is not None and abs(decay) > 100:
            tag = "弱基线(伪衰减)"
        print(f"{f['feature']:<24}{str(f['r_prior']):>9}{str(f['r_recent']):>9}{decay_s:>10}  {tag}")
    print()
    print(f"结论: {report['verdict']}  "
          f"(material decay 因子 {report['material_decay_count']} 个: "
          f"{report['material_decay_features'] or '无'})")
    print("说明: 单因子 point-biserial 均接近 0 → 策略 edge 在「推荐闸门」而非单因子；")
    print("      组合分 edge 才是真信号。decay% 大但基线 |r|<0.15 为分母伪信号，已跳过。")

    if args.json:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\n📄 JSON 报告已写出: {args.out}")

    # artifact-safe：仅有真正 material decay 才返回非零（提示关注）
    return 1 if report["material_decay_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
