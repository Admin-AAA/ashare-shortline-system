"""L-086 证据口径审计 + 分层复算（T-measurement）。

被审计对象
----------
L-086（commit 83b98b1，2026-09-12）在 12 个 `strategy.yaml` 前置三层 soft score_cap：
  1. 强势多头发散毒区  `dsa_trend_score == 30`            cap 58（8 策略）
  2. 长周期 RSI 低位毒区 `rsi_24 > 0 and rsi_24 < 48`      cap 58（7 策略）
  3. 短线龙头低分降权   `shortline_leader_score > 0 and <= 35` cap 62（全部 12 策略）
其头条证据表述为「**LIVE 去重 241（已结算 205）** 金标准反事实：top30 WR 48.6% → 56.9%
（+8.3pp）」，并据此删除了 DSA「资金背离」hard_veto。该改动**未合入 main**。

本脚本回答两个问题（只读、0 LLM 调用、不改生产）：
  Q1 population 的 evidence_class 构成是什么？其中多少是 canonical `LIVE_FORWARD`
     （§2 真实性硬门禁唯一承认的前瞻口径）？
  Q2 把同一金标准反事实**按 evidence_class 分层**复算，头条增量来自哪一层？

方法
----
- population 重建：`formal` + `completed` + `rec_*`，按 `(trade_date, code)` 去重；
  与 L-086 的 `/tmp/live_dedup_data.json`（241 对）交叉核对覆盖率（若文件存在）。
- 金标准：对每笔推荐 × 每个背书策略，用**当前加载到的 strategy.yaml** 真实调用
  `run_strategy` 取新鲜分，逐策略权重取返回值（L-088）。
- 三层毒区 phase 由本脚本**内联注入**：`treat` = 把三层插到每个成员策略 phases 最前
  （L-087：首命中即 break，必须放最前），when 追加对各既有 hard_veto 的 `not(...)` 让位，
  并加 `> 0` 数据有效性守卫；`base` = 磁盘 yaml 且剥离同名 phase（若磁盘已有）。
- **分支依赖的边界（重要）**：内联只保证 `treat` 侧注入的三层在所有分支上一致；
  `base` 侧是该分支自己的 yaml 减去这三层，而 main 的 strategy.yaml 里已经躺着大量
  其它批次的毒区/降权 phase（弱市超买、弱市降权、高位追高、T3 第185轮低换手等）。
  所以 base **随分支变化**，本脚本回答的是"在某分支既有策略组合上再加这三层的边际效果"。
  实测：`ALL` top30 的 dWR 在特征分支 +3.9pp、在 main +13.7pp（3.5× 摆动）→ 非 canonical
  层的"增益"不稳健；而 canonical `live_forward` 层的 `dWR=+0.0pp / dAvg=+0.000` 与
  毒区命中数（dsa=0 / rsi=3 / leader=0）在两种基线下**完全一致** —— 这才是可引用的结论。
- 排名反事实：按聚合分降序取 top-half / top40 / top30，统计已实现净收益的 WR/均值；
  hard_veto 命中票照 L-086 口径整票剔除并计数。

用法
----
  backend/.venv/bin/python backend/scripts/si_l086_caliber_audit.py

结果固定在 `output/l086_caliber_audit.json`；分层与分支对照的解读见同目录
`si_l086_caliber_audit_report.md`。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.engine.skill_loader import load_skills  # noqa: E402
from app.engine.strategy import build_registry_from_skills  # noqa: E402
from app.engine.strategy_runner import run_strategy  # noqa: E402
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402

SKILLS_DIR = BACKEND_ROOT / "skills"
OUTPUT_DIR = BACKEND_ROOT.parent / "output"
L086_PAYLOAD = Path("/tmp/live_dedup_data.json")

NET_COST_PCT = 0.07
CANONICAL_CLASS = "live_forward"
SETTLED = ("success", "failed")
TOP_FRACTIONS = (("top_half", 0.5), ("top40", 0.4), ("top30", 0.3))

# ---- L-086 三层毒区声明（见 L-086 / L-087 与 rebuild_poison_front.py）----
POISON_ORDER = ["强势多头发散毒区", "长周期RSI低位毒区", "短线龙头低分降权"]
S8 = {
    "chao-gu-yang-jia-skill",
    "daily-stock-analysis-skill",
    "sector-relative-strength-leader",
    "trend-pullback-continuation",
    "dragon-tiger-smart-money",
    "chen-xiaoqun-skill",
    "chip-single-peak-breakout",
    "high-range-breakout-momentum",
}
R7 = {
    "chao-gu-yang-jia-skill",
    "daily-stock-analysis-skill",
    "chen-xiaoqun-skill",
    "small-cap-thematic-momentum",
    "chip-single-peak-breakout",
    "sector-relative-strength-leader",
    "trend-pullback-continuation",
}
L12 = {
    "chao-gu-yang-jia-skill",
    "chen-xiaoqun-skill",
    "chip-single-peak-breakout",
    "daily-stock-analysis-skill",
    "dragon-tiger-smart-money",
    "high-range-breakout-momentum",
    "limit-up-reseal-relay",
    "overnight-holding-method",
    "sector-relative-strength-leader",
    "small-cap-thematic-momentum",
    "trend-pullback-continuation",
}
POISON_DEF = {
    "强势多头发散毒区": ("dsa_trend_score == 30", 58, S8),
    "长周期RSI低位毒区": ("rsi_24 > 0 and rsi_24 < 48", 58, R7),
    "短线龙头低分降权": ("shortline_leader_score > 0 and shortline_leader_score <= 35", 62, L12),
}


def _strip_poison(contract: dict) -> dict:
    con = copy.deepcopy(contract)
    phases = con.get("phases")
    if isinstance(phases, list):
        con["phases"] = [
            p for p in phases
            if not (isinstance(p, dict) and p.get("name") in POISON_ORDER)
        ]
    return con


def _guarded_when(skill_contract: dict, base_when: str) -> str:
    guards = []
    for p in skill_contract.get("phases") or []:
        if isinstance(p, dict) and bool(p.get("hard_veto", False)):
            w = str(p.get("when") or "").strip()
            if w:
                guards.append(f"not ({w})")
    return " and ".join([base_when] + guards)


def build_banks(skills: dict) -> tuple[dict, dict]:
    """返回 (base_bank, treat_bank)。base 剥离三层毒区；treat 前置三层毒区。"""
    base, treat = {}, {}
    for sid, sk in skills.items():
        stripped = _strip_poison(sk.contract or {})
        base[sid] = dataclasses.replace(sk, contract=stripped)

        injected = copy.deepcopy(stripped)
        phases = injected.get("phases") if isinstance(injected.get("phases"), list) else []
        mine = []
        for name in POISON_ORDER:
            when, cap, members = POISON_DEF[name]
            if sid not in members:
                continue
            mine.append({
                "name": name,
                "when": _guarded_when(stripped, when),
                "score_cap": cap,
                "hard_veto": False,
                "risk_flags": [f"{name}（L-086 软降权）"],
            })
        injected["phases"] = mine + list(phases)
        treat[sid] = dataclasses.replace(sk, contract=injected)
    return base, treat


# --------------------------------------------------------------------------
# population 重建（DB 持久可复现）
# --------------------------------------------------------------------------
def load_population(db) -> dict[tuple[str, str], dict]:
    rows = db.execute(
        select(
            Recommendation.recommendation_id,
            Recommendation.trade_date,
            Recommendation.code,
            Recommendation.bucket,
            Recommendation.final_score,
            Recommendation.feature_snapshot_json,
            Recommendation.skill_breakdown_json,
            RecommendationOutcome.outcome_label,
            RecommendationOutcome.executed_return,
            RecommendationOutcome.exit_reason,
            RecommendationRun.run_id,
            RecommendationRun.evidence_class,
        )
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
            Recommendation.recommendation_id.like("rec_%"),
        )
    ).all()

    by_pair: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        by_pair[(r.trade_date, r.code)].append(r)

    pop: dict[tuple[str, str], dict] = {}
    for pair, group in by_pair.items():
        settled = [g for g in group if g.outcome_label in SETTLED]
        chosen = sorted(settled or group, key=lambda g: g.recommendation_id)[0]
        classes = {str(g.evidence_class) for g in group}
        pop[pair] = {
            "date": pair[0],
            "code": pair[1],
            "recommendation_id": chosen.recommendation_id,
            "bucket": chosen.bucket,
            "final_score": float(chosen.final_score or 0.0),
            "f": json.loads(chosen.feature_snapshot_json or "{}"),
            "brk": json.loads(chosen.skill_breakdown_json or "[]"),
            "label": chosen.outcome_label,
            "ret": (
                round(float(chosen.executed_return) - NET_COST_PCT, 4)
                if chosen.outcome_label in SETTLED and chosen.executed_return is not None
                else None
            ),
            "exit": chosen.exit_reason,
            "evidence_class": CANONICAL_CLASS if CANONICAL_CLASS in classes else sorted(classes)[0],
            "all_classes": sorted(classes),
            "run_id": chosen.run_id,
            "settled": chosen.outcome_label in SETTLED,
        }
    return pop


def payload_pair_coverage(pop: dict) -> dict:
    if not L086_PAYLOAD.exists():
        return {"available": False, "note": "L-086 /tmp payload absent; DB rebuild only"}
    payload = json.loads(L086_PAYLOAD.read_text())
    pairs = {(r["date"], r["code"]) for r in payload}
    matched = sum(1 for p in pairs if p in pop)
    return {
        "available": True,
        "payload_n": len(payload),
        "payload_unique_pairs": len(pairs),
        "rebuilt_matches": matched,
        "match_rate_pct": round(matched / len(pairs) * 100, 1) if pairs else 0.0,
    }


# --------------------------------------------------------------------------
# 金标准反事实
# --------------------------------------------------------------------------
def _score_pair(row: dict, bank: dict) -> tuple[float, bool] | None:
    out = []
    veto = False
    for sig in row.get("brk") or []:
        sk = bank.get(sig.get("strategy_id"))
        if sk is None:
            continue
        try:
            r = run_strategy(sk, row["f"], [], skip_universe_check=True)
        except Exception:
            continue
        if r.get("hard_veto"):
            veto = True
        out.append((float(r.get("score") or 0.0), float(r.get("weight") or 1.0)))
    if not out:
        return None
    den = sum(w for _, w in out)
    if den == 0:
        return None
    return sum(s * w for s, w in out) / den, veto


def counterfactual(rows: list[dict], bank: dict) -> dict:
    scored, vetoed = [], 0
    for row in rows:
        if row.get("ret") is None:
            continue
        got = _score_pair(row, bank)
        if got is None:
            continue
        score, veto = got
        if veto:
            vetoed += 1
            continue
        scored.append((score, float(row["ret"])))
    scored.sort(key=lambda z: -z[0])
    out = {"n": len(scored), "vetoed": vetoed, "windows": {}}
    for name, frac in TOP_FRACTIONS:
        k = max(1, int(len(scored) * frac))
        top = [v for _, v in scored[:k]]
        if not top:
            out["windows"][name] = {"n": 0}
            continue
        out["windows"][name] = {
            "n": len(top),
            "win_rate_pct": round(100 * sum(1 for v in top if v > 0) / len(top), 1),
            "avg_net_pct": round(sum(top) / len(top), 3),
        }
    return out


def poison_firing(rows: list[dict]) -> dict:
    """三层毒区条件在各层的命中数与已实现净收益（判断改动是否能作用于该层）。"""
    checks = {
        "dsa_trend_score==30": lambda f: f.get("dsa_trend_score") == 30,
        "rsi_24 in (0,48)": lambda f: (f.get("rsi_24") or 0) > 0 and (f.get("rsi_24") or 0) < 48,
        "leader in (0,35]": lambda f: 0 < (f.get("shortline_leader_score") or 0) <= 35,
    }
    out = {}
    for name, fn in checks.items():
        hit = [r for r in rows if r.get("ret") is not None and fn(r.get("f") or {})]
        rets = [float(r["ret"]) for r in hit]
        out[name] = {
            "n": len(hit),
            "win_rate_pct": round(100 * sum(1 for v in rets if v > 0) / len(rets), 1) if rets else None,
            "avg_net_pct": round(sum(rets) / len(rets), 3) if rets else None,
        }
    return out


def main() -> None:
    db = SessionLocal()
    try:
        skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
        build_registry_from_skills(list(skills.values()))
        base_bank, treat_bank = build_banks(skills)

        pop = load_population(db)
        coverage = payload_pair_coverage(pop)

        settled_total = sum(1 for r in pop.values() if r["settled"])
        print("=== population ===")
        print(f"  rebuilt pairs (formal completed rec_*, dedup (date,code)): {len(pop)}")
        print(f"  settled (success/failed): {settled_total}")
        print(f"  L-086 payload cross-check: {coverage}")

        by_class: dict[str, list[dict]] = defaultdict(list)
        for row in pop.values():
            by_class[row["evidence_class"]].append(row)

        print("\n=== evidence_class composition ===")
        for cls, rows in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
            settled = [r for r in rows if r["settled"]]
            dates = sorted({r["date"] for r in rows})
            print(
                f"  {cls}: pairs={len(rows)} settled={len(settled)} "
                f"dates={dates[0]}..{dates[-1]} ({len(dates)} days)"
            )
        canonical_settled = by_class.get(CANONICAL_CLASS, [])
        n_canon = sum(1 for r in canonical_settled if r["settled"])
        print(
            f"  --> canonical LIVE_FORWARD share of settled population: "
            f"{n_canon}/{settled_total} = {100 * n_canon / max(1, settled_total):.1f}%"
        )

        print("\n=== gold-standard counterfactual (base=磁盘yaml剥离三层 / treat=前置三层) ===")
        results, firing = {}, {}
        strata = [("ALL", list(pop.values()))] + sorted(by_class.items(), key=lambda kv: -len(kv[1]))
        for name, rows in strata:
            base = counterfactual(rows, base_bank)
            treat = counterfactual(rows, treat_bank)
            firing[name] = poison_firing(rows)
            if base["n"] == 0:
                print(f"  [{name}] n=0 settled-scorable, skip")
                continue
            results[name] = {"base": base, "treat": treat, "poison_firing": firing[name]}
            print(
                f"\n  [{name}] scorable={base['n']} veto(base)={base['vetoed']} "
                f"veto(treat)={treat['vetoed']}"
            )
            for wname, _ in TOP_FRACTIONS:
                b, t = base["windows"][wname], treat["windows"][wname]
                if not b.get("n") or not t.get("n"):
                    continue
                print(
                    f"    {wname:9s} base WR {b['win_rate_pct']:5.1f}% avg {b['avg_net_pct']:+.3f} "
                    f"-> treat WR {t['win_rate_pct']:5.1f}% avg {t['avg_net_pct']:+.3f} "
                    f"| dWR {t['win_rate_pct'] - b['win_rate_pct']:+5.1f}pp "
                    f"dAvg {t['avg_net_pct'] - b['avg_net_pct']:+.3f}"
                )
            print(f"    poison firing: {firing[name]}")

        os.makedirs(str(OUTPUT_DIR), exist_ok=True)
        out_path = OUTPUT_DIR / "l086_caliber_audit.json"
        out_path.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "kind": "l086_caliber_audit",
            "population": {
                "rebuilt_pairs": len(pop),
                "settled": settled_total,
                "canonical_live_forward_settled": n_canon,
                "canonical_share_pct": round(100 * n_canon / max(1, settled_total), 1),
                "composition": {k: len(v) for k, v in by_class.items()},
                "payload_cross_check": coverage,
            },
            "counterfactual": results,
        }, ensure_ascii=False, indent=2))
        print(f"\n[persist] -> {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
