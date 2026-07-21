#!/usr/bin/env python3
"""T12 质量保真召回 — formal 0 推荐漏斗审计（只读诊断，绝不改写任何数据）。

A 项失衡（最近 5 交易日 <2 交易日有 formal 推荐，或连续 2 交易日 completed=0）触发 T12。
self_check 的「保护性 0 推荐」只是初步分类，**不能作为跳过 T12 的结论**——必须先做漏斗审计，
区分四类根因：
  1. 运行/数据故障        → 修复故障，不改策略
  2. 市场级已验证风险      → 保留 formal 防线（恐慌日抬线+执行惩罚，非误拦）
  3. 全局门线/聚合器误拦   → 只有跨策略、跨≥3交易日且去污染的生产等价反事实表明误拦净正候选才可改
  4. 单策略 universe/评分失配 → 仅在该策略内最小改动，且须给 OOS 净胜率不降对照；证据不足保留为 experiment

本工具把一次 completed=0 的 formal run 的 filter_audit_json 还原成可读漏斗，并自动分类根因：
  - 只要「最高候选最终分 < 名义地板 72.0」→ legitimate_empty（市场缺乏合格候选，主动空仓；
    即便把 regime 抬线撤到地板，最高候选仍不过线，故 regime 线是二层放大器而非主因）
  - 最高候选 ≥ 地板 但 < regime 有效线 → gate_misblock_suspect（regime 线拦下了本可过地板的候选，
    需进一步反事实复核，本工具仅标记不判）
  - 漏斗异常（prefilter 几乎全卡、数据缺失）→ data_fault（疑似运行/数据故障，优先排查）

纯函数（parse_funnel / classify_zero_run / extract_*）不依赖 DB，可单测；main/db 函数才连库。

用法:
  python3 backend/scripts/diagnose_funnel_audit.py --days 10          # 扫描近 10 天 completed=0 formal run
  python3 backend/scripts/diagnose_funnel_audit.py --run-id run_xxx   # 审计单 run
  python3 backend/scripts/diagnose_funnel_audit.py --trade-date 2026-07-21
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, replace

# 名义地板：与 app/services/runs/contracts.py:FORMAL_SCORE_LINE_FLOOR=72.0 对齐
NOMINAL_FLOOR = 72.0
# 高于地板不足 MARGIN_OK 分（72→80）视为「边际合格」：恐慌日 regime 抬线拦下边际候选属正确防守，
# 不算误拦；只有明显高于地板（≥80）的强候选被 regime 线拦下才标记 suspect。
MARGIN_OK = 8.0
# universe < 该阈值的 run 视为「范围受限 run」（非全市场 formal），单独评估、不计入全市场 0 推荐主因。
SCOPED_UNIVERSE_THRESHOLD = 200

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

_SCORE_LINE_RE = re.compile(r"最终分\s*([\d.]+)\s*低于有效线\s*([\d.]+)")


@dataclass
class FunnelStage:
    stage: str
    label: str
    input_count: int
    pass_count: int
    filtered_count: int


@dataclass
class NearMiss:
    code: str
    name: str
    final_score: float
    effective_line: float
    gap: float
    best_skill_id: str | None
    best_gate_score: float | None
    reason: str


@dataclass
class RunFunnel:
    run_id: str
    trade_date: str
    universe_count: int
    recommended_count: int
    stages: list[FunnelStage] = field(default_factory=list)
    effective_line: float | None = None
    best_final_score: float | None = None
    best_gap: float | None = None
    near_misses: list[NearMiss] = field(default_factory=list)
    classification: str = "unknown"
    classification_note: str = ""


def extract_stages(audit: dict) -> list[FunnelStage]:
    """从 stage_summary 还原漏斗各阶段计数。"""
    out: list[FunnelStage] = []
    for s in audit.get("stage_summary", []) or []:
        out.append(
            FunnelStage(
                stage=s.get("stage", ""),
                label=s.get("label", ""),
                input_count=int(s.get("input_count", 0) or 0),
                pass_count=int(s.get("pass_count", 0) or 0),
                filtered_count=int(s.get("filtered_count", 0) or 0),
            )
        )
    return out


def extract_effective_line(audit: dict) -> tuple[float | None, float | None, float | None]:
    """从 reason_counts.score_line 解析「最高候选最终分 / regime 有效线 / 缺口」。

    返回 (best_final_score, effective_line, gap)；无 score_line 拦截时三者皆 None。
    """
    rc = audit.get("reason_counts", {}) or {}
    sl = rc.get("score_line", {}) or {}
    best_fs: float | None = None
    best_line: float | None = None
    for reason in sl.keys():
        m = _SCORE_LINE_RE.search(reason or "")
        if not m:
            continue
        fs, line = float(m.group(1)), float(m.group(2))
        if best_fs is None or fs > best_fs:
            best_fs, best_line = fs, line
    gap = (best_fs - best_line) if (best_fs is not None and best_line is not None) else None
    return best_fs, best_line, gap


def extract_near_misses(audit: dict, top_k: int = 3) -> list[NearMiss]:
    """取 final_stage=='score_line' 的候选，按 score_line.final_score 降序取 top_k。"""
    rows = audit.get("rows", []) or []
    cands: list[NearMiss] = []
    for row in rows:
        if row.get("final_stage") != "score_line":
            continue
        sl = row.get("score_line") or {}
        fs = sl.get("final_score")
        line = sl.get("effective_score_line")
        if fs is None:
            continue
        rg = row.get("rule_gate") or {}
        cands.append(
            NearMiss(
                code=row.get("code", ""),
                name=row.get("name", ""),
                final_score=float(fs),
                effective_line=float(line) if line is not None else 0.0,
                gap=float(sl.get("score_gap", 0.0) or 0.0),
                best_skill_id=rg.get("best_skill_id"),
                best_gate_score=float(rg.get("best_gate_score")) if rg.get("best_gate_score") is not None else None,
                reason=sl.get("reason", "") or row.get("final_reason", ""),
            )
        )
    cands.sort(key=lambda c: c.final_score, reverse=True)
    return cands[:top_k]


def extract_blocked_candidates(audit: dict) -> list[NearMiss]:
    """取 final_stage=='score_line' 的全部被拦候选（不限 top_k），供 T+1 反事实用。

    与 extract_near_misses 同源数据，但不截断、不要求 final_score 非空——被 score_line
    拦下的候选一定有 final_score（否则不会进 score_line 阶段），此处保留全部以做反事实统计。
    """
    rows = audit.get("rows", []) or []
    cands: list[NearMiss] = []
    for row in rows:
        if row.get("final_stage") != "score_line":
            continue
        sl = row.get("score_line") or {}
        fs = sl.get("final_score")
        line = sl.get("effective_score_line")
        if fs is None:
            # 兜底从 final_reason 解析（部分 run 的 score_line 子结构未落 final_score）
            m = _SCORE_LINE_RE.search(row.get("final_reason", "") or "")
            if not m:
                continue
            fs = float(m.group(1))
            line = float(m.group(2)) if line is None else float(line)
        rg = row.get("rule_gate") or {}
        cands.append(
            NearMiss(
                code=row.get("code", ""),
                name=row.get("name", ""),
                final_score=float(fs),
                effective_line=float(line) if line is not None else 0.0,
                gap=float(sl.get("score_gap", 0.0) or 0.0),
                best_skill_id=rg.get("best_skill_id"),
                best_gate_score=float(rg.get("best_gate_score")) if rg.get("best_gate_score") is not None else None,
                reason=sl.get("reason", "") or row.get("final_reason", ""),
            )
        )
    cands.sort(key=lambda c: c.final_score, reverse=True)
    return cands


def classify_zero_run(
    recommended_count: int,
    best_final_score: float | None,
    stages: list[FunnelStage],
    universe_count: int = 0,
    floor: float = NOMINAL_FLOOR,
) -> tuple[str, str]:
    """对一次 0 推荐 run 分类根因。

    返回 (classification, note)。
    classification ∈ {produced, scoped_run, legitimate_empty, gate_misblock_suspect, data_fault, unknown}
    """
    if recommended_count and recommended_count > 0:
        return "produced", "该 run 实际产出推荐，非 0 推荐，无需 T12 审计。"

    # 范围受限 run（universe 极小，非全市场 formal）：单独评估，不计入全市场 0 推荐主因。
    if universe_count < SCOPED_UNIVERSE_THRESHOLD:
        return (
            "scoped_run",
            f"universe={universe_count} < {SCOPED_UNIVERSE_THRESHOLD}，范围受限 run（非全市场 formal），"
            f"单独评估，不计入全市场 0 推荐主因。",
        )

    # 数据/运行故障迹象：prefilter 几乎全卡（范围候选→预过滤大量 filtered）
    prefilter_filtered = 0
    for s in stages:
        if s.stage in ("prefilter", "prefilter_skill"):
            prefilter_filtered += s.filtered_count
    if prefilter_filtered > 4000 and best_final_score is None:
        return (
            "data_fault",
            f"prefilter 拦截 {prefilter_filtered} 只（范围候选→预过滤近乎全卡）且无可解析最高候选分，"
            f"疑似运行/数据故障（快照缺失/范围异常），优先排查而非调策略。",
        )

    if best_final_score is None:
        return "unknown", "无可解析的 score_line 拦截信息，无法分类；人工核查 filter_audit_json。"

    if best_final_score < floor:
        return (
            "legitimate_empty",
            f"最高候选最终分 {best_final_score:.2f} < 名义地板 {floor:.1f}——即便撤掉 regime 抬线、"
            f"仅用名义地板也过不了线。市场缺乏合格候选，为主动空仓；regime 线是二层放大器而非主因，"
            f"保留 formal 防线，不降线凑票。",
        )

    if best_final_score < floor + MARGIN_OK:
        return (
            "legitimate_empty",
            f"最高候选最终分 {best_final_score:.2f} 仅略高于地板（边际合格，缺口 {best_final_score - floor:.2f} 分）；"
            f"恐慌日 regime 抬线拦下「边际合格」候选属正确防守，非误拦。保留 formal 防线，不降线凑票。",
        )

    # best_final_score >= floor + MARGIN_OK：真正强候选被 regime 有效线拦下
    return (
        "gate_misblock_suspect",
        f"最高候选最终分 {best_final_score:.2f} 明显高于地板（≥{floor + MARGIN_OK:.1f}），却被 regime 有效线拦下；"
        f"存在「强候选被抬线误拦」的嫌疑。须用跨策略、跨≥3交易日且去污染的生产等价反事实"
        f"证明误拦净正候选，才可下沉到对应 strategy.yaml 或下调该 regime 抬线；证据不足保留为 experiment。",
    )


def parse_funnel(
    run_id: str,
    trade_date: str,
    universe_count: int,
    recommended_count: int,
    audit_json: str | dict,
    floor: float = NOMINAL_FLOOR,
    top_k: int = 3,
) -> RunFunnel:
    """解析单 run 的 filter_audit_json → RunFunnel（含分类）。纯函数，不依赖 DB。"""
    audit = audit_json if isinstance(audit_json, dict) else (json.loads(audit_json) if audit_json else {})
    stages = extract_stages(audit)
    best_fs, eff_line, gap = extract_effective_line(audit)
    near = extract_near_misses(audit, top_k=top_k)
    cls, note = classify_zero_run(recommended_count, best_fs, stages, universe_count=int(universe_count or 0), floor=floor)
    return RunFunnel(
        run_id=run_id,
        trade_date=trade_date,
        universe_count=int(universe_count or 0),
        recommended_count=int(recommended_count or 0),
        stages=stages,
        effective_line=eff_line,
        best_final_score=best_fs,
        best_gap=gap,
        near_misses=near,
        classification=cls,
        classification_note=note,
    )


def render_funnel(rf: RunFunnel) -> str:
    lines = []
    lines.append(f"=== {rf.trade_date} {rf.run_id} ===")
    lines.append(f"  universe={rf.universe_count}  recommended={rf.recommended_count}")
    # 漏斗
    prev = None
    for s in rf.stages:
        arrow = f"{prev}→" if prev is not None else ""
        lines.append(f"  {arrow}{s.stage:14s} in={s.input_count:5d} pass={s.pass_count:5d} filt={s.filtered_count:5d}  ({s.label})")
        prev = s.stage
    # 拦截点
    lines.append(f"  --- 拦截点 ---")
    lines.append(f"  regime 有效线={rf.effective_line}  最高候选最终分={rf.best_final_score}  缺口={rf.best_gap}")
    if rf.near_misses:
        lines.append(f"  近失候选 top{len(rf.near_misses)} (按最终分降序):")
        for nm in rf.near_misses:
            skill = nm.best_skill_id or "?"
            gscore = f"{nm.best_gate_score:.1f}" if nm.best_gate_score is not None else "?"
            lines.append(f"    {nm.code} {nm.name} final={nm.final_score:.2f} 线={nm.effective_line:.2f} 缺口={nm.gap:.2f} 最佳策略={skill}(门分{gscore})")
    lines.append(f"  >>> 分类: {rf.classification}")
    lines.append(f"      {rf.classification_note}")
    return "\n".join(lines)


def _connect_db():
    from app.db.session import SessionLocal

    return SessionLocal()


# T+1 反事实分桶边界：被拦候选按 final_score 分桶，衡量「被有效线拦下的候选 T+1 真实收益」。
# - near_floor：final∈[FLOOR-4, FLOOR)，边际未过地板——若 T+1 净正，说明地板边缘候选有 alpha，
#   但裸 T+1 不含交易成本/止损/滑点，只能提示「值得用 P0 backtest 正式复核」，不能直接降地板。
# - at_floor_plus：final≥FLOOR，本该过地板却被 regime 有效线拦——若 T+1 净正，才是 gate_misblock
#   的生产等价反事实验证（跨 run 聚合后≥3 交易日且去污染才可据以下沉规则）。
T1_NEAR_FLOOR_LOW = NOMINAL_FLOOR - 4.0


@dataclass
class T1Bucket:
    label: str
    n: int
    wins: int
    avg: float
    pl: float


def _bucketize(label: str, subset: list[tuple[float, float]]) -> T1Bucket:
    """把 (final_score, t1_ret%) 列表汇总成一个 T1Bucket。纯函数，可单测。"""
    n = len(subset)
    if n == 0:
        return T1Bucket(label, 0, 0, 0.0, 0.0)
    wins = sum(1 for _, r in subset if r > 0)
    avg = sum(r for _, r in subset) / n
    wsum = sum(r for _, r in subset if r > 0)
    lsum = sum(-r for _, r in subset if r <= 0)
    pl = wsum / lsum if lsum > 0 else (float("inf") if wsum > 0 else 0.0)
    return T1Bucket(label, n, wins, avg, pl)


def compute_t1_counterfactual(db, run_funnels: list[RunFunnel]) -> list[T1Bucket]:
    """对全部被拦候选查 T+1 真实 pct_chg（次日收盘/当日收盘 - 1），按 final 分桶汇总。

    只读 daily_kbars，绝不写库。T+1 = 当日之后的最近一个有 K 线的交易日。
    返回分桶列表（near_floor / at_floor_plus / all）。样本 <1 的桶 n=0、avg/pl=0。
    """
    from sqlalchemy import text
    from collections import defaultdict

    # 收集 (trade_date, code, final_score)，同日同 code 去重
    seen: set[tuple[str, str]] = set()
    cands: list[tuple[str, str, float]] = []
    for rf in run_funnels:
        td = rf.trade_date
        # 直接从 filter_audit 重新提取全部被拦候选（near_misses 被 top_k 截断）
        # 这里 rf 已有 near_misses，但为拿全量重新查库一次
        row = db.execute(
            text("SELECT filter_audit_json FROM recommendation_runs WHERE run_id=:rid"),
            {"rid": rf.run_id},
        ).fetchone()
        if not row or not row[0]:
            continue
        try:
            audit = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        for nm in extract_blocked_candidates(audit):
            k = (td, nm.code)
            if k in seen or not nm.code:
                continue
            seen.add(k)
            cands.append((td, nm.code, nm.final_score))

    if not cands:
        return [_bucketize(f"near_floor([{T1_NEAR_FLOOR_LOW:.0f},{NOMINAL_FLOOR:.0f})", []),
                _bucketize(f"at_floor_plus(>={NOMINAL_FLOOR:.0f})", []),
                _bucketize("all_blocked", [])]

    by_td: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for td, code, fin in cands:
        by_td[td].append((code, fin))

    results: list[tuple[float, float]] = []  # (final_score, t1_ret%)
    for td, items in by_td.items():
        codes = [c for c, _ in items]
        ph = ",".join([":c%d" % i for i in range(len(codes))])
        params: dict[str, object] = {"td": td}
        for i, c in enumerate(codes):
            params["c%d" % i] = c
        # T+1 交易日
        nt = db.execute(
            text("SELECT DISTINCT trade_date FROM daily_kbars WHERE trade_date > :td ORDER BY trade_date LIMIT 1"),
            {"td": td},
        ).fetchone()
        if not nt:
            continue
        t1 = nt[0]
        params["t1"] = t1
        px: dict[str, dict[str, float]] = defaultdict(dict)
        rows = db.execute(
            text(f"SELECT trade_date, code, close FROM daily_kbars "
                 f"WHERE trade_date IN (:td, :t1) AND code IN ({ph})"),
            params,
        ).fetchall()
        for r in rows:
            px[r[1]][str(r[0])] = float(r[2])
        fin_map = {c: f for c, f in items}
        for code in codes:
            t0c = px[code].get(td)
            t1c = px[code].get(str(t1))
            if t0c and t1c and t0c > 0:
                results.append((fin_map[code], (t1c - t0c) / t0c * 100.0))

    near = [x for x in results if T1_NEAR_FLOOR_LOW <= x[0] < NOMINAL_FLOOR]
    over = [x for x in results if x[0] >= NOMINAL_FLOOR]
    return [
        _bucketize(f"near_floor([{T1_NEAR_FLOOR_LOW:.0f},{NOMINAL_FLOOR:.0f})", near),
        _bucketize(f"at_floor_plus(>={NOMINAL_FLOOR:.0f})", over),
        _bucketize("all_blocked", results),
    ]


def render_t1_counterfactual(buckets: list[T1Bucket]) -> str:
    """渲染 T+1 反事实报告。裸 T+1 不含交易成本/止损/滑点，仅作误拦判定的辅助证据。"""
    lines = ["=== T+1 反事实（被拦候选次日真实收盘收益，裸收益未扣费）==="]
    for b in buckets:
        if b.n == 0:
            lines.append(f"  {b.label}: n=0")
            continue
        wr = b.wins / b.n * 100.0
        pl = b.pl if b.pl != float("inf") else float("inf")
        pl_str = "inf" if b.pl == float("inf") else f"{b.pl:.2f}"
        lines.append(f"  {b.label}: n={b.n} WR={wr:.1f}% avg={b.avg:+.2f}% PL={pl_str}")
    lines.append("  说明：裸 T+1 收益未扣 0.07% 双边费用/止损/滑点，且弱势/恐慌日含超跌反弹——")
    lines.append("        at_floor_plus 桶净正（跨≥3交易日去污染）才构成 gate_misblock 下沉规则的生产等价反事实；")
    lines.append("        near_floor 桶净正仅提示「地板边缘候选有 alpha 候选」，须 P0 backtest 正式复核，不能直接降地板。")
    return "\n".join(lines)


def resolve_suspects_with_t1(results: list[RunFunnel], buckets: list[T1Bucket]) -> list[RunFunnel]:
    """用 T+1 反事实 at_floor_plus 桶（final≥地板、被 regime 有效线拦下的候选）判定 per-run 的
    gate_misblock_suspect 是否误报。纯函数，不连库、不改输入，返回新列表。

    规则（与 render_t1_counterfactual 说明一致）：
      - at_floor_plus 桶 T+1 净正（avg>0 且 WR>=50%）才构成 gate_misblock 下沉规则的生产等价反事实；
      - 若 at_floor_plus 桶净负（avg<=0 或 WR<50%），说明 regime 线整体上是在保护而非误拦，
        per-run 的 gate_misblock_suspect 属误报，下调为 gate_misblock_resolved_false（不改规则、不改结论）；
      - 若 at_floor_plus 无样本（n=0，T+1 数据缺失），证据不足，保守保留 suspect 不自动清除。
    """
    at_floor = next((b for b in buckets if b.label.startswith("at_floor_plus")), None)
    protective = False
    if at_floor and at_floor.n > 0:
        wr = at_floor.wins / at_floor.n
        protective = (at_floor.avg <= 0) or (wr < 0.5)
    if not protective:
        return results  # 净正或证据不足：保留 suspect，需更深 per-run 复核

    out: list[RunFunnel] = []
    for rf in results:
        if rf.classification == "gate_misblock_suspect":
            rf2 = replace(
                rf,
                classification="gate_misblock_resolved_false",
                classification_note=(
                    rf.classification_note
                    + f" T+1 反事实复核(at_floor_plus 桶 n={at_floor.n} "
                    f"WR={at_floor.wins / at_floor.n * 100:.1f}% avg={at_floor.avg:+.2f}%)："
                    f"regime 线拦下的 final≥地板候选 T+1 净负，证实整体保护性，"
                    f"本 suspect 为误报——保留 formal 防线，不降线凑票。"
                ),
            )
            out.append(rf2)
        else:
            out.append(rf)
    return out


def audit_run(db, run_id: str, floor: float = NOMINAL_FLOOR, top_k: int = 3) -> RunFunnel | None:
    from sqlalchemy import text

    row = db.execute(
        text(
            "SELECT run_id, trade_date, universe_count, recommended_count, filter_audit_json "
            "FROM recommendation_runs WHERE run_id=:rid"
        ),
        {"rid": run_id},
    ).fetchone()
    if not row:
        return None
    return parse_funnel(row.run_id, row.trade_date, row.universe_count, row.recommended_count, row.filter_audit_json, floor=floor, top_k=top_k)


def scan_recent_zero_runs(db, days: int = 10, floor: float = NOMINAL_FLOOR, top_k: int = 3) -> list[RunFunnel]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT run_id, trade_date, universe_count, recommended_count, filter_audit_json "
            "FROM recommendation_runs "
            "WHERE run_mode='formal' AND status='completed' AND recommended_count=0 "
            "  AND trade_date >= DATE_SUB(CURDATE(), INTERVAL :d DAY) "
            "ORDER BY trade_date DESC, run_id DESC"
        ),
        {"d": days},
    ).fetchall()
    out = []
    for r in rows:
        out.append(parse_funnel(r.run_id, r.trade_date, r.universe_count, r.recommended_count, r.filter_audit_json, floor=floor, top_k=top_k))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="T12 formal 0 推荐漏斗审计")
    ap.add_argument("--run-id", help="审计单个 run")
    ap.add_argument("--trade-date", help="审计该交易日所有 completed=0 formal run")
    ap.add_argument("--days", type=int, default=10, help="扫描近 N 天 completed=0 formal run（默认 10）")
    ap.add_argument("--floor", type=float, default=NOMINAL_FLOOR, help=f"名义地板（默认 {NOMINAL_FLOOR}）")
    ap.add_argument("--top-k", type=int, default=3, help="每个 run 列出 top-K 近失候选（默认 3）")
    ap.add_argument("--t1", action="store_true", help="对被拦候选做 T+1 真实收益反事实（查 daily_kbars，只读）")
    args = ap.parse_args()

    db = _connect_db()
    try:
        results: list[RunFunnel] = []
        if args.run_id:
            rf = audit_run(db, args.run_id, floor=args.floor, top_k=args.top_k)
            if rf:
                results = [rf]
        elif args.trade_date:
            from sqlalchemy import text

            rows = db.execute(
                text(
                    "SELECT run_id, trade_date, universe_count, recommended_count, filter_audit_json "
                    "FROM recommendation_runs WHERE run_mode='formal' AND status='completed' "
                    "  AND recommended_count=0 AND trade_date=:td ORDER BY run_id DESC"
                ),
                {"td": args.trade_date},
            ).fetchall()
            results = [parse_funnel(r.run_id, r.trade_date, r.universe_count, r.recommended_count, r.filter_audit_json, floor=args.floor, top_k=args.top_k) for r in rows]
        else:
            results = scan_recent_zero_runs(db, days=args.days, floor=args.floor, top_k=args.top_k)

        if not results:
            print("无 completed=0 formal run 可审计。")
            return

        # T+1 反事实：把「须用反事实证明误拦净正」从口号变成自动化证据。
        # 即便全部 legitimate_empty（最高候选<地板），T+1 反事实也能揭示「被拦候选整体 T+1 是否净负」——
        # 净负=防线正确，净正=地板边缘有 alpha 候选（须 P0 backtest 复核，不直接降地板）。
        # 计算后即刻用 at_floor_plus 桶把 per-run 的 gate_misblock_suspect 自动复核/降级，
        # 让汇总不再 perpetual 标记已证伪的嫌疑（证据不足时保守保留 suspect）。
        buckets = None
        if args.t1:
            buckets = compute_t1_counterfactual(db, results)
            results = resolve_suspects_with_t1(results, buckets)

        for rf in results:
            print(render_funnel(rf))
            print()

        # 汇总分类
        from collections import Counter

        cnt = Counter(r.classification for r in results)
        print(f"=== 汇总: 审计 {len(results)} 个 0 推荐 run ===")
        for k, v in cnt.most_common():
            print(f"  {k}: {v}")
        legit = cnt.get("legitimate_empty", 0)
        suspect = cnt.get("gate_misblock_suspect", 0)
        fault = cnt.get("data_fault", 0)
        scoped = cnt.get("scoped_run", 0)
        resolved = cnt.get("gate_misblock_resolved_false", 0)
        print(f"\n结论速读: legitimate_empty={legit}（市场缺合格候选/边际合格, 保留防线） "
              f"gate_misblock_suspect={suspect}（需反事实复核） "
              f"gate_misblock_resolved_false={resolved}（T+1 证实误报） "
              f"data_fault={fault}（先查故障） scoped_run={scoped}（范围受限, 不计入主因）")
        if fault == 0 and suspect == 0 and resolved > 0 and legit > 0:
            print(f"→ {resolved} 个 gate_misblock_suspect 经 T+1 反事实证实为误报（regime 线整体保护性，"
                  f"拦下的 final≥地板候选 T+1 净负），本轮 0 推荐为合理主动空仓，保留 formal 防线，不降线凑票。")
        elif fault == 0 and suspect == 0 and legit > 0:
            print("→ 本轮 0 推荐为合理主动空仓（市场级已验证风险），保留 formal 防线，不降线凑票。")
        elif suspect > 0:
            print("→ 存在强候选被 regime 抬线误拦嫌疑，须用生产等价反事实证明误拦净正候选后方可下沉/下调，证据不足保留为 experiment。")
        elif fault > 0:
            print("→ 存在疑似运行/数据故障，优先排查快照/范围异常，修复后仅验证故障消失，不顺势改策略。")

        if args.t1 and buckets is not None:
            print()
            print(render_t1_counterfactual(buckets))
    finally:
        db.close()


if __name__ == "__main__":
    main()
