#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成本感知回测验证工具 (Cost-Aware Backtest Verifier)
===================================================

北极星：用户用的推荐能稳定赚钱。回测的目的不是"证明策略有效"，
而是"证明策略不是噪声"——默认假设无效，必须用数据推翻。

本工具对 recommendation_outcomes 做端到端、扣交易成本的真实回测：

**关键：区分 LIVE 与 BACKTEST 两个总体（2026-07 修正）**
recommendation_outcomes 混存两类记录，必须用 recommendation_id 前缀区分，
绝不能用 (trade_date, code) 去重：
  - `rec_*`  = LIVE 实盘推荐（用户真正看到的，北极星口径）
  - `btrec_*` = BACKTEST 回测生成的模拟交易（策略历史自验证）
回测会在同一 (trade_date, code) 上生成多条 *不同* 的 btrec_* 记录（不同的
历史测试点），它们各自是独立交易，按 (td,code) 去重会非法合并且污染 LIVE
总体。旧版（si434950）即因此把 1433 条回测交易错误并入 LIVE，得出虚高的
"66%"——真实 LIVE 胜率仅 61.5%。

1. 去重：按 recommendation_id 去重（每个 id = 一笔独立交易）。LIVE 与 BACKTEST
   分别统计，绝不互相合并口径。
2. 成本模型：executed_return 已含滑点但未扣佣金+印花税。
   与 outcome_service.py 保持一致：_ADDITIONAL_COST_PCT = 0.10%
   （佣金万2.5 双边 + 印花税0.05% 卖出 ≈ 0.10%/笔）。
   net_return = executed_return - 0.10
3. 指标：胜率(净)、平均净收益、盈亏比(净)、Sharpe(每笔)、最大亏损(净)、
   出场分布、持仓天数。LIVE / BACKTEST / 混合 三套分别输出。
4. 策略归因：从 skill_breakdown_json 提取每只推荐命中的策略，统计各策略
   净胜率与平均净收益，定位拖后腿/贡献大的策略（指导未来 T1/T3）。
5. 相位信息量检验（T5 一致性）：按 market_sentiment_phase 分组看胜率差。
   若各相位胜率差 < 5pp，说明 phase 分类无信息量，是噪声。

纯计算函数 compute_metrics() 与 DB 完全解耦，便于单元测试。
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 与 backend/app/services/outcome_service.py 保持一致
# executed_return 已含滑点但未扣佣金+印花税
ADDITIONAL_COST_PCT = 0.10  # 佣金万2.5双边 + 印花税0.05%卖出 ≈ 0.10%/笔


@dataclass
class BacktestRow:
    """一行去重后的回测样本（纯数据，无 DB 依赖）。"""
    recommendation_id: str
    trade_date: str
    code: str
    executed_return: float          # 含滑点，未扣佣金+印花税
    outcome_label: str              # success / failed
    exit_reason: str
    holding_days: int
    final_score: float
    strategy_ids: List[str] = field(default_factory=list)
    phase: Optional[str] = None
    source: str = "live"            # "live" (rec_*) | "backtest" (btrec_*)

    @property
    def net_return(self) -> float:
        return round(self.executed_return - ADDITIONAL_COST_PCT, 4)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std_pop(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)  # 总体方差 (ddof=0)
    return math.sqrt(var)


def compute_metrics(rows: List[BacktestRow]) -> Dict[str, Any]:
    """对去重后的样本计算全部回测指标。纯函数，可被单测覆盖。"""
    if not rows:
        return {"n": 0}

    net = [r.net_return for r in rows]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    n = len(rows)

    win_rate = len(wins) / n if n else 0.0
    avg_net = _mean(net)
    avg_win = _mean(wins) if wins else 0.0
    avg_loss_abs = _mean([abs(x) for x in losses]) if losses else 0.0
    pl_ratio = (avg_win / avg_loss_abs) if avg_loss_abs > 0 else float("inf")
    max_loss = min(net) if net else 0.0
    sharpe = (avg_net / _std_pop(net)) if _std_pop(net) > 0 else 0.0

    exit_dist = Counter(r.exit_reason for r in rows)
    holding = [r.holding_days for r in rows if r.holding_days is not None]
    holding_avg = _mean([float(h) for h in holding]) if holding else 0.0

    # ---- 策略归因 ----
    strat_net: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        for sid in r.strategy_ids:
            strat_net[sid].append(r.net_return)
    strategy_stats = {}
    for sid, ns in strat_net.items():
        w = [x for x in ns if x > 0]
        strategy_stats[sid] = {
            "n": len(ns),
            "win_rate": round(len(w) / len(ns), 4) if ns else 0.0,
            "avg_net": round(_mean(ns), 4),
        }
    strategy_stats = dict(sorted(strategy_stats.items(), key=lambda kv: kv[1]["avg_net"], reverse=True))

    # ---- 相位信息量检验 ----
    phase_groups: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        key = r.phase or "unknown"
        phase_groups[key].append(r.net_return)
    phase_stats = {}
    for ph, ns in phase_groups.items():
        w = [x for x in ns if x > 0]
        phase_stats[ph] = {
            "n": len(ns),
            "win_rate": round(len(w) / len(ns), 4) if ns else 0.0,
            "avg_net": round(_mean(ns), 4),
        }
    phase_stats = dict(sorted(phase_stats.items(), key=lambda kv: kv[1]["n"], reverse=True))
    phase_wrs = [v["win_rate"] for v in phase_stats.values()]
    phase_discrimination = (max(phase_wrs) - min(phase_wrs)) if phase_wrs else 0.0

    return {
        "n": n,
        "win_rate": round(win_rate, 4),
        "avg_net_return": round(avg_net, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss_abs": round(avg_loss_abs, 4),
        "pl_ratio": round(pl_ratio, 4),
        "sharpe_per_trade": round(sharpe, 4),
        "max_loss_net": round(max_loss, 4),
        "exit_distribution": dict(exit_dist),
        "holding_days_avg": round(holding_avg, 2),
        "strategy_stats": strategy_stats,
        "phase_stats": phase_stats,
        "phase_winrate_spread_pp": round(phase_discrimination * 100, 2),
        "phase_has_information": phase_discrimination * 100 >= 5.0,
        "cost_pct_per_trade": ADDITIONAL_COST_PCT,
    }


def dedup_rows(rows: List[BacktestRow]) -> List[BacktestRow]:
    """按 recommendation_id 去重（每个 id = 一笔独立交易）。

    关键修正（2026-07）：绝不能用 (trade_date, code) 去重。BACKTEST 会在同一
    (td,code) 上生成多条 *不同* 的 btrec_* 记录（各自是独立历史测试点），按
    (td,code) 去重会非法合并、污染 LIVE 总体。这里按 recommendation_id 去重，
    仅当同一 id 出现多条 outcome 时保留 final_score 最高的一条。
    """
    best: Dict[str, BacktestRow] = {}
    for r in rows:
        key = r.recommendation_id
        if key not in best or r.final_score > best[key].final_score:
            best[key] = r
    return list(best.values())


def load_rows_from_db(db) -> List[BacktestRow]:
    """从 DB 读取已标注 outcome，构造 BacktestRow 列表（未去重）。"""
    from sqlalchemy import text

    sql = text("""
        SELECT o.recommendation_id, r.trade_date, r.code,
               o.executed_return, o.outcome_label, o.exit_reason,
               o.holding_days, r.final_score,
               r.skill_breakdown_json, r.feature_snapshot_json
        FROM recommendation_outcomes o
        JOIN recommendations r ON o.recommendation_id = r.recommendation_id
        WHERE o.outcome_label IN ('success', 'failed')
          AND o.executed_return IS NOT NULL
    """)
    out: List[BacktestRow] = []
    for rec in db.execute(sql).fetchall():
        (rid, tdate, code, exret, label, reason, hdays, score, sb_json, fs_json) = rec
        strategy_ids: List[str] = []
        if sb_json:
            try:
                sb = json.loads(sb_json)
                if isinstance(sb, list):
                    strategy_ids = [it.get("strategy_id") for it in sb if isinstance(it, dict) and it.get("strategy_id")]
            except Exception:
                pass
        phase = None
        if fs_json:
            try:
                fs = json.loads(fs_json)
                phase = fs.get("market_sentiment_phase")
            except Exception:
                pass
        # 区分 LIVE(rec_*) 与 BACKTEST(btrec_*) 两个总体
        source = "backtest" if (rid or "").startswith("btrec_") else "live"
        out.append(BacktestRow(
            recommendation_id=rid, trade_date=tdate, code=code,
            executed_return=float(exret or 0.0), outcome_label=label or "",
            exit_reason=reason or "", holding_days=int(hdays or 0),
            final_score=float(score or 0.0), strategy_ids=strategy_ids,
            phase=phase, source=source,
        ))
    return out


def render_report(metrics: Dict[str, Any], raw_n: int, dedup_n: int, title: str = "成本感知回测报告") -> str:
    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 样本(原始 outcome 行): {raw_n}")
    lines.append(f"- 样本(去重后 recommendation_id): {dedup_n}")
    lines.append(f"- 单笔交易成本: {metrics['cost_pct_per_trade']}% (佣金万2.5双边+印花税0.05%卖出)")
    lines.append("")
    lines.append("## 核心指标 (净收益=executed_return-成本)")
    lines.append("")
    lines.append(f"| 指标 | 值 | 目标 |")
    lines.append(f"|------|-----|------|")
    lines.append(f"| 胜率(净) | {metrics['win_rate']*100:.1f}% | ≥50% |")
    lines.append(f"| 平均净收益 | {metrics['avg_net_return']:+.2f}% | — |")
    lines.append(f"| 盈亏比(净) | {metrics['pl_ratio']:.2f} | ≥1.4 |")
    lines.append(f"| Sharpe(每笔) | {metrics['sharpe_per_trade']:.3f} | — |")
    lines.append(f"| 最大亏损(净) | {metrics['max_loss_net']:+.2f}% | — |")
    lines.append(f"| 平均持仓天数 | {metrics['holding_days_avg']:.1f} | — |")
    lines.append("")
    lines.append("## 出场分布")
    lines.append("")
    for reason, cnt in sorted(metrics["exit_distribution"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: {cnt} ({cnt/metrics['n']*100:.1f}%)")
    lines.append("")
    lines.append("## 策略归因 (按平均净收益排序)")
    lines.append("")
    lines.append("| 策略 | 命中数 | 净胜率 | 平均净收益 |")
    lines.append("|------|--------|--------|------------|")
    for sid, st in metrics["strategy_stats"].items():
        lines.append(f"| {sid} | {st['n']} | {st['win_rate']*100:.1f}% | {st['avg_net']:+.2f}% |")
    lines.append("")
    lines.append("## 相位信息量检验 (T5 一致性)")
    lines.append("")
    lines.append(f"- 各相位胜率极差: {metrics['phase_winrate_spread_pp']:.1f}pp")
    lines.append(f"- 相位是否携带信息量: {'是' if metrics['phase_has_information'] else '否（疑似噪声，需重新标定）'}")
    lines.append("")
    lines.append("| 相位 | 样本数 | 净胜率 | 平均净收益 |")
    lines.append("|------|--------|--------|------------|")
    for ph, st in metrics["phase_stats"].items():
        lines.append(f"| {ph} | {st['n']} | {st['win_rate']*100:.1f}% | {st['avg_net']:+.2f}% |")
    lines.append("")
    return "\n".join(lines)


def main() -> Dict[str, Any]:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.db.session import SessionLocal

    db = SessionLocal()
    raw = load_rows_from_db(db)
    rows = dedup_rows(raw)

    live = [r for r in rows if r.source == "live"]
    bt = [r for r in rows if r.source == "backtest"]

    m_all = compute_metrics(rows)
    m_bt = compute_metrics(bt)
    m_live = compute_metrics(live)

    raw_all = len(raw)
    raw_bt = sum(1 for r in raw if r.source == "backtest")
    raw_live = sum(1 for r in raw if r.source == "live")

    report_parts = [
        render_report(m_all, raw_all, len(rows), "混合回测报告（live+backtest，口径不同仅作整体参考）"),
        "",
        render_report(m_bt, raw_bt, len(bt), "BACKTEST 回测样本 (btrec_*)"),
        "",
        render_report(m_live, raw_live, len(live), "LIVE 实盘样本 (rec_*) — 北极星口径"),
    ]
    report = "\n".join(report_parts)
    print(report)

    out_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(out_dir, "backtest_report_latest.json"), "w", encoding="utf-8") as f:
        json.dump({"live": m_live, "backtest": m_bt, "all": m_all}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "backtest_report_latest.md"), "w", encoding="utf-8") as f:
        f.write(report)

    db.close()
    return {"live": m_live, "backtest": m_bt, "all": m_all}


if __name__ == "__main__":
    main()
