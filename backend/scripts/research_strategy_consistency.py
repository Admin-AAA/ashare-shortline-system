"""策略存活判定的「R12 vs 历史一致性」诊断（只读）。

背景：当前现役策略的去留规则是「近 12 个月每笔均值/总收益/MDD」（L-170、L-177、L-191）。
本脚本检验该单一窗口是否会把「只在最近一年有效」的策略留成赢家、把「只是赶上逆风」的策略误杀。

指标：
- r12_avg：近 12 个月每笔均值
- pos_phase_ratio：分阶段（P19/P20/P21/W0/W00/W1/W2）中各段 avg>0 的占比
- worst_phase_avg：历史最差一段的每笔均值
- n3_avg：N3（2024-01~2026-10）每笔均值
- consistency_gap：R12 均值 − 历史阶段中位数（正值越大＝近期越强于历史常态）

输出：backend/output/research/strategy_consistency_20261115/consistency.json + 控制台表
用法：cd backend && .venv/bin/python scripts/research_strategy_consistency.py
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "app" / "services" / "midterm_backtest_metrics.json"
OUT = BASE / "output" / "research" / "strategy_consistency_20261115"

HIST_PHASES = ("P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09")
MIN_PHASE_N = 10  # 阶段样本低于 10 笔不判定该段正负


def f(x):
    return None if x is None else float(x)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(SRC.read_text())
    rows = []
    for sid, v in data.items():
        if not isinstance(v, dict):
            continue
        r12 = v.get("recent_12m") or {}
        if not r12 or r12.get("avg") is None:
            continue
        phases = v.get("phases") or {}
        hist = []
        for name, pr in phases.items():
            if name.startswith("R12") or name.startswith("N3"):
                continue
            if not isinstance(pr, dict):
                continue
            avg, n = f(pr.get("avg")), f(pr.get("n"))
            if avg is not None and n is not None and n >= MIN_PHASE_N:
                hist.append((name, avg, n, f(pr.get("mdd"))))
        if len(hist) < 4:
            continue
        avgs = [h[1] for h in hist]
        pos = sum(1 for a in avgs if a > 0)
        med = statistics.median(avgs)
        n3 = phases.get("N3 2024-01~2026-10") or {}
        rows.append(
            {
                "id": sid,
                "r12_n": f(r12.get("n")),
                "r12_avg": f(r12.get("avg")),
                "r12_pf": f(r12.get("pf")),
                "r12_mdd": f(r12.get("mdd")),
                "r12_excess": f(r12.get("excess")),
                "n3_avg": f(n3.get("avg")),
                "hist_phases": len(hist),
                "pos_phase_ratio": round(pos / len(hist), 3),
                "worst_phase_avg": round(min(avgs), 3),
                "hist_median_avg": round(med, 3),
                "consistency_gap": round(f(r12.get("avg")) - med, 3),
                "worst_phase": min(hist, key=lambda h: h[1])[0],
            }
        )

    rows.sort(key=lambda x: -x["r12_avg"])
    (OUT / "consistency.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))

    hdr = f"{'strategy':34s} {'R12n':>5} {'R12avg':>7} {'PF':>6} {'N3avg':>7} {'正段比':>6} {'最差段':>7} {'历史中位':>8} {'R12-中位':>8}"
    print(hdr)
    print("-" * len(hdr))
    for x in rows[:20]:
        print(
            f"{x['id'][:34]:34s} {x['r12_n']:>5.0f} {x['r12_avg']:>7.2f} {x['r12_pf'] if x['r12_pf'] is not None else 0:>6.2f} "
            f"{x['n3_avg'] if x['n3_avg'] is not None else 0:>7.2f} {x['pos_phase_ratio']:>6.2f} "
            f"{x['worst_phase_avg']:>7.2f} {x['hist_median_avg']:>8.2f} {x['consistency_gap']:>8.2f}"
        )

    print(f"\n共 {len(rows)} 个策略有 ≥4 个可用历史阶段")
    # 统计口径差异
    top20 = rows[:20]
    weak = [x for x in top20 if x["pos_phase_ratio"] < 0.6]
    print(f"R12 均值 TOP20 中，历史正阶段占比 <60% 的有 {len(weak)} 个：")
    for x in weak:
        print(f"  - {x['id']}: R12 {x['r12_avg']:+.2f}% 但正段比 {x['pos_phase_ratio']:.0%}，最差段 {x['worst_phase']} {x['worst_phase_avg']:+.2f}%")
    # 反向：历史稳但 R12 差
    stable = [x for x in rows if x["pos_phase_ratio"] >= 0.86 and x["r12_avg"] < 1.0]
    print(f"\n历史 ≥6/7 段为正、但 R12 均值 <1% 的策略 {len(stable)} 个（可能被 R12 规则误杀）：")
    for x in stable[:10]:
        print(f"  - {x['id']}: 正段比 {x['pos_phase_ratio']:.0%}，历史中位 {x['hist_median_avg']:+.2f}%，R12 {x['r12_avg']:+.2f}%")
    print(f"\n输出: {OUT / 'consistency.json'}")


if __name__ == "__main__":
    main()
