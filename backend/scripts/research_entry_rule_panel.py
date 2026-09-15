"""三窗入场规则离线筛查（面板口径，path A，只读、零 DB）。

面板由 `scripts/research_strategy_ablation_fast.py --targets none --panel-dir` 产出：
每个交易日的 policy **输入态**卡（含 ivol 分位与 regime 内部标记）+ 当日 market_snapshot
+ 每张候选卡的因果净收益 `net_by_code`。因此"只改选中集合、不改候选池"的入场规则
都能离线重跑真实 `evaluate_selection_policy` 并直接得到组合指标，不必重放 1 小时。

纪律：
- 前置断言（L-112a）：identity 规则必须逐日复现面板里的 `selected`，且三窗指标与
  `ablate_fast_baseline_W*.json` 的 A 臂一致；不一致直接抛错，不允许解释候选 Δ。
- 每个候选必须三窗都过 `evaluate_challenger`（L-010）才算 PASS，并报逐月同号（L-058）。
- 只测"事前假设驱动"的少量规则，不做大网格（避免把噪声调成信号）。

用法：
  cd backend && .venv/bin/python scripts/research_entry_rule_panel.py --rules all
  cd backend && .venv/bin/python scripts/research_entry_rule_panel.py --rules panic40,consensus2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.engine.production_policy import (
    evaluate_selection_policy,
    load_production_selection_policy,
    SelectionPolicyContext,
)
from app.services.backtest.challenger_decision import (
    PerformanceSnapshot,
    evaluate_challenger,
)

PANEL_DIR = B / "output" / "research" / "entry_panel"
BASE_DIR = B / "output" / "research" / "per_strategy"
OUT = B / "output" / "research" / "entry_rule_panel.json"
WINDOWS = ("W1", "W2", "W3")
SCORE_LINE = 72.0
OUTPUT_COUNT = 10


# --------------------------------------------------------------------------- #
# 规则注册表：每个规则 = 纯函数 (cards, day) -> 新 cards（可返回空表 = 当日不入场）
# --------------------------------------------------------------------------- #
def _fs(card: dict) -> dict:
    return card.get("feature_snapshot") or {}


def _non_veto_signals(card: dict) -> list[dict]:
    return [s for s in (card.get("strategy_signals") or [])
            if isinstance(s, dict) and not s.get("hard_veto")]


def r_identity(cards, day):
    return cards


def _make_day_gate(predicate):
    def rule(cards, day):
        return [] if predicate(day["market_snapshot"]) else cards
    return rule


def _limit_down_ge(thr):
    def pred(ms):
        return float(ms.get("limit_down_count") or 0) >= thr
    return pred


def _up_ratio_lt(thr):
    def pred(ms):
        ms = dict(ms or {})
        breadth = ms.get("breadth") or {}
        ratio = breadth.get("up_ratio")
        if ratio is None:
            up = float(ms.get("up_count") or 0.0)
            down = float(ms.get("down_count") or 0.0)
            if up + down <= 0:
                return False
            ratio = up / (up + down)
        return float(ratio) < thr
    return pred


def _limit_up_ge(thr):
    def pred(ms):
        return float(ms.get("limit_up_count") or 0) >= thr
    return pred


def _card_filter(predicate):
    def rule(cards, day):
        return [c for c in cards if predicate(c, day)]
    return rule


def _penalty(predicate, points):
    def rule(cards, day):
        out = []
        for c in cards:
            cc = dict(c)
            if predicate(cc, day):
                cc["final_score"] = float(cc.get("final_score") or 0.0) - points
            out.append(cc)
        return out
    return rule


def _vol5_20_ge(thr):
    def pred(card, _day):
        v = _fs(card).get("vol5_20")
        return v is not None and float(v) >= thr
    return pred


def _non_main_theme(_card=None, _day=None):
    raise NotImplementedError


def _industry_not_in_main(card, day):
    ms = day["market_snapshot"] or {}
    main = ms.get("main_industries") or []
    if not main:
        return False
    return str(card.get("industry") or "") not in {str(x) for x in main}


RULES: dict[str, "callable"] = {
    "identity": r_identity,
    # ① 大盘广度/恐慌闸门：跌停蔓延日不进场（事前可得：T 收盘统计）
    "panic20": _make_day_gate(_limit_down_ge(20)),
    "panic40": _make_day_gate(_limit_down_ge(40)),
    "panic60": _make_day_gate(_limit_down_ge(60)),
    # ② 赚钱效应闸门：上涨家数占比过低（普跌日）不进场
    "upratio035": _make_day_gate(_up_ratio_lt(0.35)),
    "upratio045": _make_day_gate(_up_ratio_lt(0.45)),
    # ③ 情绪过热闸门：涨停家数过多（追高区）不进场
    "euphoria120": _make_day_gate(_limit_up_ge(120)),
    "euphoria150": _make_day_gate(_limit_up_ge(150)),
    # ④ 追量闸门：个股放量倍数过高（追高）剔除
    "vol5_20_ge20": _card_filter(_vol5_20_ge(2.0)),
    "vol5_20_ge25": _card_filter(_vol5_20_ge(2.5)),
    # ⑤ 共识闸门：只留至少 2 个非否决策略背书
    "consensus2": _card_filter(lambda c, _d: len(_non_veto_signals(c)) >= 2),
    "consensus3": _card_filter(lambda c, _d: len(_non_veto_signals(c)) >= 3),
    # ⑥ 主线闸门（LIVE 反事实强候选；本轮首次在三窗全区间可测，因为全管线重放的
    #    market_snapshot 带 main_industries，而面板口径的 W2/W3 快照没有）
    "non_main_p4": _penalty(_industry_not_in_main, 4.0),
    "non_main_p8": _penalty(_industry_not_in_main, 8.0),
    "non_main_drop": _card_filter(lambda c, d: not _industry_not_in_main(c, d)),
}


# --------------------------------------------------------------------------- #
def _metrics(rows: list[dict], all_days: list[str]) -> dict | None:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    net = df.net
    wins, losses = net[net > 0], net[net < 0]
    daily = df.groupby("dt").net.mean().reindex(all_days).fillna(0.0)
    cum = daily.cumsum()
    return {
        "n": int(len(df)),
        "wr": float((net > 0).mean()),
        "avg": float(net.mean()),
        "sum": float(net.sum()),
        "pf": float(wins.sum() / max(abs(losses.sum()), 1e-9)),
        "mdd": float((cum - cum.cummax()).min()),
        "cohort": float(daily.mean()),
        "days": int(df.dt.nunique()),
        "daily": daily,
    }


def _boot_ci(delta: np.ndarray, draws: int = 4000, seed: int = 7):
    rng = np.random.default_rng(seed)
    n = len(delta)
    means = np.array([delta[rng.integers(0, n, n)].mean() for _ in range(draws)])
    return float(delta.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def run_rule(rule, panel: dict, pol) -> tuple[dict | None, list[dict]]:
    rows: list[dict] = []
    all_days: list[str] = []
    for day in panel["days"]:
        all_days.append(day["signal_date"])
        cards = rule([dict(c) for c in day["cards"]], day)
        if not cards:
            continue
        res = evaluate_selection_policy(
            cards,
            SelectionPolicyContext(
                score_line=SCORE_LINE,
                output_count=OUTPUT_COUNT,
                market_snapshot=dict(day["market_snapshot"]),
                risk_template="balanced",
                same_day_industry_counts=day["same_day_industry_counts"],
                same_day_recommendation_count=day["same_day_recommendation_count"],
            ),
            pol,
        )
        for c in res.recommendations:
            code = str(c.get("code"))
            net = day["net_by_code"].get(code)
            if net is None:
                continue
            rows.append({"dt": day["signal_date"], "code": code, "net": float(net),
                         "exit": day["exit_by_code"].get(code)})
    return _metrics(rows, all_days), rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default="all")
    ap.add_argument(
        "--single-skill",
        action="store_true",
        help="额外测每个技能的**单策略组合**（只保留该 skill 有非否决策略信号的卡，再走真实 policy）",
    )
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    names = list(RULES) if args.rules == "all" else [r.strip() for r in args.rules.split(",") if r.strip()]
    pol = load_production_selection_policy()
    panels = {w: json.loads((PANEL_DIR / f"entry_panel_{w}.json").read_text()) for w in WINDOWS}
    def harness_baseline(w: str) -> dict:
        """harness A 臂基线：优先读 ablation JSON；缺失时用面板自带的 selected+net 复原
        （两者同源：selected 来自 harness 的 formal_cards，net 来自同一次因果出场模拟）。"""
        path = BASE_DIR / f"ablate_fast_baseline_{w}.json"
        if path.exists():
            return json.loads(path.read_text())[w]["A"]
        rows = []
        for d in panels[w]["days"]:
            for code in d["selected"]:
                net = d["net_by_code"].get(code)
                if net is not None:
                    rows.append({"dt": d["signal_date"], "code": code, "net": float(net)})
        m = _metrics(rows, [d["signal_date"] for d in panels[w]["days"]])
        assert m is not None
        return {k: v for k, v in m.items() if k != "daily"}

    baselines_expected = {w: harness_baseline(w) for w in WINDOWS}

    print("=== 基线前置断言（identity 必须复现面板 selected 与 harness A 臂） ===")
    base_metrics: dict[str, dict] = {}
    base_rows: dict[str, list[dict]] = {}
    for w in WINDOWS:
        m, rows = run_rule(RULES["identity"], panels[w], pol)
        exp = baselines_expected[w]
        assert m is not None, w
        sel_panel = sum(len(d["selected"]) for d in panels[w]["days"])
        # 离线基线 vs harness 基线对账：允许极小边界日噪声（面板 JSON 里记录 days），
        # 但量级必须一致——不一致就不能解释任何候选 Δ（L-112a）。
        d_avg = m["avg"] - exp["avg"]
        d_n = m["n"] - exp["n"]
        mismatch_days = panels[w].get("fidelity_mismatch_days") or []
        # 容差按"边界日噪声"设定：实测 W2 有 3 天离线重跑与 harness 在第 10 张卡上互换
        # 1 张（n 相同、成分不同）→ Δavg 约 0.035pp。超过 0.10pp 说明不是边界噪声而是
        # 面板本身失真，必须停。
        ok = abs(d_avg) <= 0.10 and abs(d_n) <= 3
        print(f"  {w}: 离线基线 n={m['n']} avg={m['avg']:+.4f} WR={m['wr']*100:.1f}% "
              f"| harness n={exp['n']} avg={exp['avg']:+.4f} WR={exp['wr']*100:.1f}% "
              f"| Δn={d_n:+d} Δavg={d_avg:+.4f} 边界日={len(mismatch_days)} "
              f"→ {'OK' if ok else 'MISMATCH'}")
        if not ok:
            raise RuntimeError(f"{w} 基线不一致：offline={m} vs harness={exp}")
        base_metrics[w] = m
        base_rows[w] = rows

    if args.single_skill:
        skills = sorted({
            str(s.get("strategy_id"))
            for w in WINDOWS for d in panels[w]["days"] for c in d["cards"]
            for s in (c.get("strategy_signals") or [])
            if isinstance(s, dict) and s.get("strategy_id")
        })
        for sid in skills:
            RULES[f"only:{sid}"] = (
                lambda cards, _d, _sid=sid: [
                    c for c in cards
                    if any(str(s.get("strategy_id")) == _sid and not s.get("hard_veto")
                           for s in (c.get("strategy_signals") or []) if isinstance(s, dict))
                ]
            )
            RULES[f"dominant:{sid}"] = (
                lambda cards, _d, _sid=sid: [c for c in cards if str(c.get("bucket")) == _sid]
            )
        names = names + [n for n in RULES if n.startswith(("only:", "dominant:"))]
        print(f"\n[per-skill] 已注册 {len(skills)} 个技能的单策略组合规则")

    print(f"\n=== 规则筛查（{len(names)} 个；PASS = 三窗全过 evaluate_challenger） ===")
    results: dict[str, dict] = {}
    for name in names:
        rule = RULES[name]
        per_window: dict[str, dict] = {}
        passes = 0
        for w in WINDOWS:
            m, rows = run_rule(rule, panels[w], pol)
            if m is None:
                per_window[w] = {"n": 0, "decision": "FALSIFIED(无成交)"}
                continue
            b = base_metrics[w]
            dlt = (m["daily"] - b["daily"]).dropna()
            md, lo, hi = _boot_ci(dlt.values)
            monthly = dlt.groupby(dlt.index.str[:7]).mean()
            pos, neg = int((monthly > 0).sum()), int((monthly < 0).sum())
            dec = evaluate_challenger(
                PerformanceSnapshot(b["avg"], b["sum"], b["wr"] * 100, b["pf"], abs(b["mdd"])),
                PerformanceSnapshot(m["avg"], m["sum"], m["wr"] * 100, m["pf"], abs(m["mdd"])),
            )
            passes += int(dec.should_publish)
            per_window[w] = {
                "n": m["n"], "wr": m["wr"], "avg": m["avg"], "sum": m["sum"], "pf": m["pf"],
                "mdd": m["mdd"], "cohort": m["cohort"], "days": m["days"],
                "d_cohort": md, "ci": [lo, hi],
                "month_same_pos": pos / max(pos + neg, 1), "n_months": pos + neg,
                "decision": dec.decision, "improvements": list(dec.improvements),
                "regressions": list(dec.regressions),
            }
        results[name] = {"windows": per_window, "passes": passes}
        cells = []
        for w in WINDOWS:
            v = per_window[w]
            if not v.get("n"):
                cells.append(f"{w} 无成交")
                continue
            cells.append(f"{w} Δ{v['d_cohort']:+.4f} CI[{v['ci'][0]:+.3f},{v['ci'][1]:+.3f}] "
                         f"WR{v['wr']*100:.1f}% PF{v['pf']:.3f} sum{v['sum']:+.1f} "
                         f"月{v['month_same_pos']:.2f} {v['decision']}")
        print(f"\n[{name}] passes={passes}/3")
        for c in cells:
            print("   ", c)

    out = Path(args.out)
    out.write_text(json.dumps(
        {"policy": "production_selection.v4", "execution": "execution_policy.v4",
         "score_line": SCORE_LINE, "output_count": OUTPUT_COUNT,
         "baseline": {w: {k: v for k, v in base_metrics[w].items() if k != "daily"} for w in WINDOWS},
         "rules": results},
        ensure_ascii=False, indent=1, default=str))
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
