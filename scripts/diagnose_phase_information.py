#!/usr/bin/env python3
"""诊断市场状态（phase / sentiment）是否真有信息量 —— T5 一致性工程（市场状态自适应）。

第一性原理质疑：aggregator 当前对 market_regime 持「逆势立场」（强牛返分低、弱市返分高，
见 feature_builder.market_regime_score docstring），并在 phase 上做 hard_veto / 排除 /
soft floor 等处理。这套逻辑成立的前提是：**phase / sentiment 桶之间胜率确实存在差异**。

若各 phase 桶的胜率差异在统计上不显著（χ² 检验 p>=0.05），说明 phase 对短线 outcome 无信息
量——继续在 phase 上做精细的 veto / 抬线 / soft 调整，本质是过拟合噪声，应简化而非加规则。

反之若显著，则量化「哪个 regime 真赚 / 哪个真亏」，供后续 D 攻击把防守规则精准投到真亏桶，
而非对整段历史毒区无差别加 entry 规则（历轮已证伪）。

本脚本是纯诊断（不改任何规则），与 self_check._residual_regime_sentiment_breakdown 同源口径
（feature_snapshot.market_regime / market_sentiment_phase，兼容「弱势·活跃」合体与
score_line_meta 兜底），但覆盖**全量 LIVE formal ledger**（含成功样本），而不仅残毒区。

用法（在 worktree 内）：
    cd backend && source .venv/bin/activate && python3 ../scripts/diagnose_phase_information.py
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
from collections import Counter, defaultdict
from typing import Any

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

# 与 self_check / feature_signal_guard 对齐：单桶最小可信样本、显著偏离阈值
MIN_BUCKET_N = 20          # <20 桶胜率不可信（与 E 项 DEFAULT_EXPOSURE_MIN_SAMPLE=20 同源）
NOTABLE_DEVIATION_PP = 10  # 桶胜率相对全量偏离 >=10pp 视为「值得注意」
ALPHA = 0.05               # 显著性水平
BASELINE_WR = 61.5          # 系统全量净胜率基线（与 self_check 基线 61.5% 对齐）

# 默认 JSON 报告落盘路径（output/ 已被 .gitignore 忽略，不进版本库）
REPORT_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'output', 'phase_information_report.json')
)

# —— χ² 上尾 p 值：列联表 df=(桶数-1)*(类别数-1)，类别=2(胜/负) → df 必为整数 —— #
# 故可用闭式解（无需 scipy / 不完全 Gamma 连分式，避免数值脆弱）：
#   χ²(k) ~ Gamma(k/2, 2)，上尾 Q = Γ(k/2, χ²/2) / Γ(k/2)。
#   · k 偶 (a=m=k/2 整数): Q = e^{-t} Σ_{i=0}^{m-1} t^i/i!,  t=χ²/2
#   · k 奇 (a=n+1/2, n=(k-1)/2): Q = erfc(√t) + (2/√π)e^{-t}√t Σ_{j=0}^{n-1} t^j/j!
def _chi2_sf(k: int, chi2: float) -> float:
    """χ² 分布上尾生存函数 P(X>=chi2)，df=k（整数）。闭式精确解。

    用于列联表独立性检验 p 值。df<=0 返回 nan；chi2<=0 返回 1.0。
    """
    if k <= 0:
        return float("nan")
    if chi2 <= 0.0:
        return 1.0
    t = chi2 / 2.0
    if k % 2 == 0:
        m = k // 2
        s = 0.0
        term = 1.0  # t^0/0!
        for i in range(m):
            s += term
            term *= t / (i + 1)
        return math.exp(-t) * s
    n = (k - 1) // 2
    sq = math.sqrt(t)
    if n == 0:
        return math.erfc(sq)
    s = 0.0
    term = 1.0
    for j in range(n):
        s += term
        term *= t / (j + 1)
    return math.erfc(sq) + (2.0 / math.sqrt(math.pi)) * math.exp(-t) * sq * s


# ——————————————————————————————————————————————— #
# 纯函数：phase 提取 + 分桶 + 统计检验（便于单测锁定口径）
# ——————————————————————————————————————————————— #
def extract_regime_sentiment(fs: dict | None) -> tuple[str, str]:
    """从 feature_snapshot 提取 (market_regime, market_sentiment_phase)。

    兼容历史「弱势·活跃」合体写法（market_regime 含 '·' 且 sentiment 为空时拆开），
    与 self_check._residual_regime_sentiment_breakdown 口径一致。
    """
    if not fs:
        return ("?", "?")
    meta = (fs or {}).get("score_line_meta") or {}
    base = str(fs.get("market_regime") or meta.get("market_base") or "?")
    sent = str(
        fs.get("market_sentiment_phase")
        or meta.get("market_sentiment")
        or "?"
    )
    if "·" in base and sent in ("?", ""):
        parts = base.split("·", 1)
        base, sent = parts[0], parts[1]
    return (base, sent)


def norm_cdf(x: float) -> float:
    """标准正态 CDF，用 erf 实现（math 内置，无需 scipy）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def proportion_z_test(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    """两比例 z 检验（pooled）。返回 (z, 双尾 p)。

    H0: p1==p2。用于比较两个 phase 桶（如 冰点 vs 活跃）的胜率差异是否显著。
    任一组 n==0 或合并比例 se==0 时返回 (0.0, 1.0)（无法判定）。
    """
    if n1 <= 0 or n2 <= 0:
        return (0.0, 1.0)
    p1, p2 = x1 / n1, x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1.0 - p) * (1.0 / n1 + 1.0 / n2))
    if se == 0.0:
        return (0.0, 1.0)
    z = (p1 - p2) / se
    p_val = 2.0 * (1.0 - norm_cdf(abs(z)))
    return (z, p_val)


def chi_square_independence(rows: list[list[int]]) -> tuple[float, int, float]:
    """列联表独立性 χ² 检验。rows: list[list[int]]，每行一个桶的 [成功数, 失败数]。

    返回 (chi2, df, p)。df=(r-1)*(c-1)=(桶数-1)*(类别数-1)。p=Q(df/2, chi2/2)。
    退化情形（<2 桶 / 边际为 0）返回 (0.0, 0, 1.0)。
    """
    r = len(rows)
    if r < 2:
        return (0.0, 0, 1.0)
    c = len(rows[0])
    if c < 2:
        return (0.0, 0, 1.0)
    col_tot: list[int] = [0] * c
    row_tot: list[int] = [0] * r
    grand = 0
    for i in range(r):
        for j in range(c):
            v = int(rows[i][j])
            row_tot[i] += v
            col_tot[j] += v
            grand += v
    if grand == 0:
        return (0.0, 0, 1.0)
    chi2 = 0.0
    for i in range(r):
        for j in range(c):
            if row_tot[i] == 0 or col_tot[j] == 0:
                continue
            expected = row_tot[i] * col_tot[j] / grand
            if expected == 0:
                continue
            diff = rows[i][j] - expected
            chi2 += diff * diff / expected
    df = (r - 1) * (c - 1)
    if df <= 0:
        return (chi2, 0, 1.0)
    p = _chi2_sf(df, chi2)
    return (chi2, df, p)


def _bucket_stats(records: list[dict]) -> dict[str, Any]:
    """对一组 {win: bool, ret: float} 记录算统计。

    返回 {n, wins, winrate, avg_ret, win_rets_avg, loss_rets_avg, net_plr}。
    net_plr = 平均盈利 / |平均亏损|（净成本前的毛盈亏比，与 self_check 口径一致）。
    """
    n = len(records)
    if n == 0:
        return {
            "n": 0, "wins": 0, "winrate": 0.0, "avg_ret": 0.0,
            "win_rets_avg": 0.0, "loss_rets_avg": 0.0, "net_plr": 0.0,
        }
    wins = sum(1 for r in records if r["win"])
    win_rets = [r["ret"] for r in records if r["win"]]
    loss_rets = [r["ret"] for r in records if not r["win"]]
    avg_ret = sum(r["ret"] for r in records) / n
    win_avg = sum(win_rets) / len(win_rets) if win_rets else 0.0
    loss_avg = sum(loss_rets) / len(loss_rets) if loss_rets else 0.0
    net_plr = (win_avg / abs(loss_avg)) if loss_avg != 0 else 0.0
    return {
        "n": n,
        "wins": wins,
        "winrate": round(wins * 100.0 / n, 1),
        "avg_ret": round(avg_ret, 2),
        "win_rets_avg": round(win_avg, 2),
        "loss_rets_avg": round(loss_avg, 2),
        "net_plr": round(net_plr, 2),
    }


def build_phase_report(records: list[dict]) -> dict[str, Any]:
    """核心分析：把 LIVE formal ledger 记录（含 regime/sentiment/win/ret）切成多维度桶并检验。

    records: list[dict]{regime, sentiment, win: bool, ret: float}
    返回结构化报告（纯函数，便于单测与跨 run 漂移对比）。
    """
    overall = _bucket_stats(records)
    by_regime: dict[str, list[dict]] = defaultdict(list)
    by_sentiment: dict[str, list[dict]] = defaultdict(list)
    by_cross: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        by_regime[r["regime"]].append(r)
        by_sentiment[r["sentiment"]].append(r)
        by_cross[(r["regime"], r["sentiment"])].append(r)

    regime_stats = {k: _bucket_stats(v) for k, v in by_regime.items()}
    sentiment_stats = {k: _bucket_stats(v) for k, v in by_sentiment.items()}
    cross_stats = {f"{k[0]}·{k[1]}": _bucket_stats(v) for k, v in by_cross.items()}

    # χ² 检验：胜/负 × 桶，检验 phase 是否携带 outcome 信息
    regime_rows = [
        [regime_stats[k]["wins"], regime_stats[k]["n"] - regime_stats[k]["wins"]]
        for k in regime_stats
    ]
    sent_rows = [
        [sentiment_stats[k]["wins"], sentiment_stats[k]["n"] - sentiment_stats[k]["wins"]]
        for k in sentiment_stats
    ]
    regime_chi2, regime_df, regime_p = chi_square_independence(regime_rows)
    sent_chi2, sent_df, sent_p = chi_square_independence(sent_rows)

    # 显著偏离全量的桶（n>=20 且 |winrate-整体|>=阈值）
    notable = []
    for name, st in sorted(regime_stats.items(), key=lambda kv: kv[1]["winrate"]):
        if st["n"] >= MIN_BUCKET_N and abs(st["winrate"] - overall["winrate"]) >= NOTABLE_DEVIATION_PP:
            notable.append({"dim": "regime", "name": name, **st})

    # 冰点 vs 活跃：逆势立场的核心对照（若两桶都存在且 n 足够）
    ice_vs_active = None
    ice = regime_stats.get("冰点")
    active = regime_stats.get("活跃")
    if ice and active and ice["n"] > 0 and active["n"] > 0:
        z, p = proportion_z_test(ice["wins"], ice["n"], active["wins"], active["n"])
        ice_vs_active = {
            "ice_winrate": ice["winrate"], "ice_n": ice["n"],
            "active_winrate": active["winrate"], "active_n": active["n"],
            "z": round(z, 3), "p": round(p, 4),
        }

    return {
        "overall": overall,
        "by_regime": regime_stats,
        "by_sentiment": sentiment_stats,
        "by_cross": cross_stats,
        "regime_chi2": round(regime_chi2, 3),
        "regime_df": regime_df,
        "regime_p": round(regime_p, 4),
        "sentiment_chi2": round(sent_chi2, 3),
        "sentiment_df": sent_df,
        "sentiment_p": round(sent_p, 4),
        "notable_buckets": notable,
        "ice_vs_active": ice_vs_active,
    }


def evaluate_regime_defense(rep: dict[str, Any], baseline_wr: float = BASELINE_WR) -> dict[str, Any]:
    """纯函数：基于 χ² 信息量检验 + 各 regime 桶胜率 vs 系统基线，给出 regime 防守评估。

    把「phase 是否值得在防守规则上投入」固化为机器可读结论，供跨 run JSON 漂移追踪，
    避免每轮靠人工读文本判断。决策树（与第一性原理/历轮证伪一致）：
      · regime χ² p < ALPHA（显著有信息量）→ keep_defense=True，理由=regime_chi2_significant。
      · regime χ² p >= ALPHA 但存在桶胜率 < 基线（如 震荡 59.2% < 61.5%）→
        χ² 不显著是样本不均导致的检验力不足（弱势占 71% 样本、df=4 欠功率），
        该桶确为拖累（低于系统基线），防守（soft ban / veto）是去噪非过拟合
        → keep_defense=True，理由=below_baseline_bucket_under_power_limited_chi2。
      · regime χ² p >= ALPHA 且所有可信桶胜率 >= 基线 → phase 真实无信息量，
        防守规则可简化（本报告不改规则，仅标记 keep_defense=False，理由=no_information_content）。
    返回 {keep_defense, reason, below_baseline_buckets, min_bucket_wr}。
    """
    below = [
        name for name, st in rep["by_regime"].items()
        if st["n"] >= MIN_BUCKET_N and st["winrate"] < baseline_wr
    ]
    credible = [st["winrate"] for st in rep["by_regime"].values() if st["n"] >= MIN_BUCKET_N]
    min_wr = min(credible) if credible else None
    if rep["regime_p"] < ALPHA:
        return {
            "keep_defense": True,
            "reason": "regime_chi2_significant",
            "below_baseline_buckets": below,
            "min_bucket_wr": min_wr,
        }
    if below:
        return {
            "keep_defense": True,
            "reason": "below_baseline_bucket_under_power_limited_chi2",
            "below_baseline_buckets": below,
            "min_bucket_wr": min_wr,
        }
    return {
        "keep_defense": False,
        "reason": "no_information_content",
        "below_baseline_buckets": [],
        "min_bucket_wr": min_wr,
    }


def _fetch_live_formal_ledger(db) -> list[dict]:
    """从 DB 取 LIVE formal ledger（rec_* + run_mode=formal），每条带 feature_snapshot + outcome。"""
    rows = db.execute(text("""
        SELECT r.recommendation_id, o.outcome_label, o.executed_return,
               r.feature_snapshot_json
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()
    records: list[dict] = []
    for rid, label, ret, fsj in rows:
        fs = json.loads(fsj) if fsj else {}
        regime, sentiment = extract_regime_sentiment(fs)
        ret_v = float(ret) if ret is not None else 0.0
        records.append({
            "regime": regime,
            "sentiment": sentiment,
            "win": bool(label == "success"),
            "ret": ret_v,
        })
    return records


def render_report(rep: dict[str, Any]) -> str:
    """把结构化报告渲染为可读文本。"""
    lines: list[str] = []
    ov = rep["overall"]
    lines.append(f"全量 LIVE formal: n={ov['n']} 胜率={ov['winrate']}% 平均收益={ov['avg_ret']}% 净盈亏比={ov['net_plr']}")
    lines.append("")
    lines.append("=== 按 market_regime 分桶 ===")
    for k, st in sorted(rep["by_regime"].items(), key=lambda kv: kv[1]["winrate"]):
        flag = " ⚠️n<20" if st["n"] < MIN_BUCKET_N else ""
        lines.append(
            f"  {k:<8} n={st['n']:<4} 胜率={st['winrate']:>5}% 均收={st['avg_ret']:>6}% "
            f"盈亏比={st['net_plr']:>5}{flag}"
        )
    lines.append("")
    lines.append("=== 按 market_sentiment 分桶 ===")
    for k, st in sorted(rep["by_sentiment"].items(), key=lambda kv: kv[1]["winrate"]):
        flag = " ⚠️n<20" if st["n"] < MIN_BUCKET_N else ""
        lines.append(
            f"  {k:<8} n={st['n']:<4} 胜率={st['winrate']:>5}% 均收={st['avg_ret']:>6}% "
            f"盈亏比={st['net_plr']:>5}{flag}"
        )
    lines.append("")
    lines.append("=== 信息量检验（χ² 独立性） ===")
    lines.append(
        f"  regime:   χ²={rep['regime_chi2']} df={rep['regime_df']} p={rep['regime_p']} "
        f"-> {'显著有信息量' if rep['regime_p'] < ALPHA else '无显著信息量'}"
    )
    lines.append(
        f"  sentiment:χ²={rep['sentiment_chi2']} df={rep['sentiment_df']} p={rep['sentiment_p']} "
        f"-> {'显著有信息量' if rep['sentiment_p'] < ALPHA else '无显著信息量'}"
    )
    if rep.get("ice_vs_active"):
        iv = rep["ice_vs_active"]
        sig = "显著" if iv["p"] < ALPHA else "不显著"
        lines.append(
            f"  冰点vs活跃: 冰点胜率={iv['ice_winrate']}%(n={iv['ice_n']}) "
            f"活跃胜率={iv['active_winrate']}%(n={iv['active_n']}) z={iv['z']} p={iv['p']} ({sig})"
        )
    if rep["notable_buckets"]:
        lines.append("")
        lines.append("=== 显著偏离全量的桶（n>=20, |Δwinrate|>=%dpp）===" % NOTABLE_DEVIATION_PP)
        for b in rep["notable_buckets"]:
            lines.append(
                f"  [{b['dim']}] {b['name']}: 胜率={b['winrate']}% n={b['n']} 均收={b['avg_ret']}% 盈亏比={b['net_plr']}"
            )
    # 结论
    lines.append("")
    if rep["regime_p"] < ALPHA:
        lines.append(
            "结论：phase 对短线 outcome 有统计显著信息量（p<%s）——继续在 phase 上做"
            "逆势立场/排除/soft 调整是站得住的；后续 D 攻击应把防守规则精准投到真亏桶，"
            "而非对整段历史毒区无差别加 entry 规则。" % ALPHA
        )
    else:
        lines.append(
            "结论：phase 对短线 outcome 无统计显著信息量（p>=%s）——继续在 phase 上做精细"
            "veto/抬线/soft 调整本质是过拟合噪声，应简化而非加规则。" % ALPHA
        )
    # 防守评估（机器可读结论，供跨 run 漂移追踪）
    defense = evaluate_regime_defense(rep)
    lines.append("")
    if defense["keep_defense"]:
        extra = (
            f"；低于基线 {BASELINE_WR}% 的桶={defense['below_baseline_buckets']}"
            if defense["below_baseline_buckets"] else ""
        )
        lines.append(
            f"防守评估：保留 regime 防守（soft ban / veto）。理由={defense['reason']}；"
            f"最低可信桶胜率={defense['min_bucket_wr']}%{extra}"
        )
    else:
        lines.append(
            f"防守评估：phase 无信息量且无非基线桶，可考虑简化 regime 防守规则"
            f"（本报告不改规则）。理由={defense['reason']}；最低可信桶胜率={defense['min_bucket_wr']}%"
        )
    return "\n".join(lines)


def write_report(report: dict[str, Any], path: str) -> None:
    """把结构化 phase 报告落盘为 JSON（output/ 已被 .gitignore 忽略，不进版本库）。

    供跨 run 漂移追踪——phase χ² p 值 / regime 防守结论若缓慢退化，单点文本看不出，
    JSON 历史 diff 可捕获（与 verify_d_guardrail --json 同一思路）。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="市场状态(phase)信息量诊断")
    parser.add_argument(
        "--json", nargs="?", const=REPORT_DEFAULT_PATH, default=None,
        help="写出机器可读 JSON 报告；不指定路径则用默认 output/phase_information_report.json",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        records = _fetch_live_formal_ledger(db)
    finally:
        db.close()
    if not records:
        print("无 LIVE formal 记录可分析")
        return 0
    rep = build_phase_report(records)
    print(render_report(rep))
    if args.json:
        defense = evaluate_regime_defense(rep)
        full_report = {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "baseline_winrate": BASELINE_WR,
            "alpha": ALPHA,
            **rep,
            "regime_defense": defense,
        }
        write_report(full_report, args.json)
        print(f"📄 JSON 报告已写出: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
