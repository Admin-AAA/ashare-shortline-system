"""把三窗回测结果导出为前端可消费的 artifact（策略清单页展示用）。

输入：`output/research/entry_rule_panel_single.json`
      （由 `scripts/research_entry_rule_panel.py --single-skill` 生成：对每个技能单独跑
       "只保留该 skill 有非否决策略信号的卡 → 真实 production_selection v4 选择 →
        execution_policy v4 因果出场" 的三窗组合回测）
输出：`app/policies/strategy_backtest_metrics.json`（随代码提交，API 直接读取）

为什么要落地成 repo 内 artifact：`/api/strategies` 的 canonical 回测画像要求
`backtest_runs_v2` 有当前版本匹配行，多数技能因此显示 `pending_backtest`（页面上看不到
任何收益）；把本机跑出的三窗回测指标落到 repo 内，前端就能把"我确实回测过的收益率"
展示出来，且口径与复现命令一并写进 artifact，避免"数字来源不明"（L-111）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

SRC = B / "output" / "research" / "entry_rule_panel_single_prod.json"
DST = B / "app" / "policies" / "strategy_backtest_metrics.json"
WINDOW_LABELS = {
    "W1": ("2025-01-15", "2026-01-16", "W1 holdout 冻结窗"),
    "W2": ("2026-01-17", "2026-07-30", "W2 前瞻窗"),
    "W3": ("2026-08-01", "2026-09-13", "W3 当前窗"),
}


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def _metrics_block(win: dict) -> dict | None:
    if not win or not win.get("n"):
        return None
    n = int(win["n"])
    pf = float(win["pf"])
    # 盈亏比在"窗口内没有亏损笔"时是无穷（筛查脚本用 wins/1e-9 兜底 → 天文数字），
    # n<5 时也没有统计意义：两种都置 None，前端显示 "—"，避免出现 2851728271.70 这种噪声。
    pf_out = None if (pf > 1000.0 or n < 5) else _round(pf, 3)
    return {
        "n": n,
        "win_rate_pct": _round(float(win["wr"]) * 100, 2),
        "avg_net_pct": _round(win["avg"], 4),
        "sum_net_pct": _round(win["sum"], 2),
        "profit_factor": pf_out,
        "profit_factor_note": (
            None if pf_out is not None
            else ("样本不足 5 笔" if n < 5 else "窗口内无亏损笔，盈亏比无定义")
        ),
        "max_drawdown_pct": _round(abs(float(win["mdd"])), 2),
        "trade_days": int(win.get("days") or 0),
        "delta_cohort_vs_combined": _round(win.get("d_cohort"), 4),
        "ci95": [_round(win["ci"][0], 4), _round(win["ci"][1], 4)] if win.get("ci") else None,
        "month_same_sign_ratio": _round(win.get("month_same_pos"), 2),
        "challenger": win.get("decision"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", default=str(DST))
    ap.add_argument(
        "--champion-runs",
        default=None,
        help="冠军（当前生产默认 + 出场策略）的三窗端到端重放产物，逗号分隔 W1=<json>,W2=<json>,W3=<json>。"
             "给定后用它覆盖页面的『全策略组合』行（否则该行是 v4 面板基线，与当前生产不符），"
             "并额外导出逐技能贡献（按 dominant 归因）。",
    )
    args = ap.parse_args()
    from app.engine.production_policy import load_production_selection_policy

    current_policy = load_production_selection_policy()
    src = Path(args.src)
    data = json.loads(src.read_text())

    combined = {
        w: {
            "n": int(b["n"]),
            "win_rate_pct": _round(float(b["wr"]) * 100, 2),
            "avg_net_pct": _round(b["avg"], 4),
            "sum_net_pct": _round(b["sum"], 2),
            "profit_factor": _round(b["pf"], 3),
            "max_drawdown_pct": _round(abs(float(b["mdd"])), 2),
            "trade_days": int(b.get("days") or 0),
        }
        for w, b in data["baseline"].items()
    }
    champion_runs: dict[str, dict] = {}
    champion_contrib: dict[str, dict] = {}
    if args.champion_runs:
        for item in str(args.champion_runs).split(","):
            if not item.strip():
                continue
            win, _, path = item.partition("=")
            payload = json.loads(Path(path.strip()).read_text())
            block = payload.get(win) or next(iter(payload.values()))
            a = block["A"]
            champion_runs[win] = {
                "n": int(a["n"]),
                "win_rate_pct": _round(float(a["wr"]) * 100, 2),
                "avg_net_pct": _round(a["avg"], 4),
                "sum_net_pct": _round(a["sum"], 2),
                "profit_factor": _round(a["pf"], 3),
                "max_drawdown_pct": _round(abs(float(a["mdd"])), 2),
                "trade_days": int(a.get("days") or 0),
            }
            for t in block.get("provenance") or []:
                sid = str(t.get("dominant") or "unknown")
                bucket = champion_contrib.setdefault(sid, {}).setdefault(
                    win, {"n": 0, "wins": 0, "sum_net_pct": 0.0}
                )
                bucket["n"] += 1
                bucket["wins"] += 1 if float(t.get("net") or 0) > 0 else 0
                bucket["sum_net_pct"] += float(t.get("net") or 0)
        for sid, per_win in champion_contrib.items():
            for win, agg in per_win.items():
                agg["win_rate_pct"] = _round(agg["wins"] / agg["n"] * 100, 2) if agg["n"] else None
                agg["sum_net_pct"] = _round(agg["sum_net_pct"], 2)
                del agg["wins"]

    live_skill_ids = {p.name for p in (B / "skills").iterdir() if p.is_dir()}
    skills: dict[str, dict] = {}
    for name, payload in data["rules"].items():
        if not name.startswith("only:"):
            continue
        sid = name.split(":", 1)[1]
        # 已退役技能（面板里还留着历史卡）不再上页面
        if sid not in live_skill_ids:
            print(f"[skip] 已退役技能 {sid}")
            continue
        windows = {w: _metrics_block(payload["windows"].get(w) or {}) for w in WINDOW_LABELS}
        present = [v for v in windows.values() if v]
        passes = int(payload.get("passes") or 0)
        # 三窗一致度：三窗 avg 是否同号（正=全部为正），以及三窗都要有样本
        signs = [v["avg_net_pct"] > 0 for v in present]
        skills[sid] = {
            "windows": windows,
            "windows_with_trades": len(present),
            "passes": passes,
            "avg_net_pct_mean": _round(
                sum(v["avg_net_pct"] for v in present) / len(present), 4
            ) if present else None,
            "all_windows_positive": bool(present) and all(signs) and len(present) == 3,
            "any_window_positive": any(signs) if present else False,
        }

    out = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "caliber": {
            "name": "single_skill_portfolio",
            "label": "单策略组合回测（三窗）",
            "description": (
                "只保留该技能有非否决策略信号的候选卡，再走真实 production_selection v4 选择"
                "（score_line=72 / Top10）与 execution_policy v4 因果出场（T+1 入场、止损/跟踪止盈、"
                "跳空成交约束）；逐笔等权，不含仓位与资金模型。"
            ),
            "combined_label": "全策略组合（10 技能全开，全管线重放）",
            "selection_policy": f"production_selection.v{current_policy.version}",
            "signal_weights": "preset_formal_opening（生产逐技能权重：DSA 0.5 / 陈小群 1.5 / 其余 1.0）",
            "baseline_selection_policy": "production_selection.v4（Δ 与闸门判定相对该基线）",
            "execution_policy": "execution_policy.v4",
            "score_line": 72.0,
            "output_count": 10,
            "caveat": (
                "候选池来自 10 技能全量重放的面板（按该技能信号筛卡），未做单技能独立 prefilter 重放；"
                "选择用当前生产选择策略（见 selection_policy），组合基线为 v4（基线_selection_policy）。"
                "两类指标都属 path A 诊断证据，不是 live_forward 发布证据。"
                "盈亏比在窗口内无亏损笔或样本<5 时置空（显示 —），不做伪精度展示。"
            ),
            "source_artifact": f"backend/output/research/{src.name}",
            "reproduce": (
                "cd backend && .venv/bin/python scripts/research_entry_rule_panel.py --rules '' "
                f"--single-skill --baseline-version 4 --out output/research/{src.name} && "
                ".venv/bin/python scripts/export_strategy_backtest_metrics.py"
            ),
        },
        "windows": {
            w: {"from": d0, "to": d1, "label": label}
            for w, (d0, d1, label) in WINDOW_LABELS.items()
        },
        "combined": combined,
        "champion": champion_runs or None,
        "champion_contributions": champion_contrib or None,
        "skills": skills,
    }
    if champion_runs:
        # 页面『全策略组合』行改用冠军（当前生产默认 + 出场策略）的三窗端到端结果
        out["combined"] = champion_runs
        out["caliber"]["combined_label"] = "全策略组合（冠军：当前生产默认选择 + 出场策略，全管线端到端）"
        out["caliber"]["combined_note"] = (
            "该行来自 `research_strategy_ablation_fast.py --policy-version <当前默认> --trail-act <当前出场>` "
            "的全管线重放（含全部横截面闸门与每日输出上限），是唯一与 LIVE 同口径的组合行；"
            "skills 各行仍是单技能面板口径（selection v4 / Top10 / 出场 v4）。"
        )
        out["caliber"]["baseline_for_combined"] = "production_selection.v7 + execution_policy.v4（发布前冠军）"
    dst = Path(args.out)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"[saved] {dst}（{len(skills)} 个技能）")
    for sid, v in sorted(skills.items(), key=lambda kv: -(kv[1]["avg_net_pct_mean"] or -9e9)):
        wins = " ".join(
            f"{w}:{'—' if not v['windows'][w] else format(v['windows'][w]['avg_net_pct'], '+.3f')}"
            for w in WINDOW_LABELS
        )
        print(f"  {sid:34s} 均{v['avg_net_pct_mean']:+.3f}  三窗正={v['all_windows_positive']}  {wins}")


if __name__ == "__main__":
    main()
