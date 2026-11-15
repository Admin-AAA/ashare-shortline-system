"""容量分层初版：按当日成交额分档统计收益，不预设冲击成本参数。

为什么这样做：完整的容量模型需要「资金规模 + 冲击系数」，两者当前都没有可靠来源。
与其拍一个系数编出年化，不如把**可观测的事实**摊开：
- 各成交额档位的笔数、每笔均值、收益分布；
- 每档在 1% / 2% / 5% 参与率下单笔可承载的金额；
- 一个显式的冲击模型接口，参数由使用者自己定。

数据源：backend/output/research/solo_20261026/*/panel/entry_panel_WX.json（同 solo 回放，731 笔）
用法：cd backend && .venv/bin/python scripts/research_capacity_bands.py
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SOLO = BASE / "output" / "research" / "solo_20261026"
OUT = BASE / "output" / "research" / "capacity_bands_20261115"

# 成交额档位（亿元，按当日 amount_yi）
BANDS = [(0.0, 0.5), (0.5, 1.0), (1.0, 3.0), (3.0, 10.0), (10.0, float("inf"))]


def band_of(amount_yi: float | None) -> str | None:
    if amount_yi is None:
        return None
    for lo, hi in BANDS:
        if lo <= amount_yi < hi:
            return f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+"
    return None


def impact_pct(participation_pct: float, coef: float = 1.0) -> float:
    """平方根冲击模型（%）。coef 是待标定参数，默认 1.0 仅作示意。

    参与率 1% 时冲击 = coef × 1.0；4% 时 = 2 × coef。
    真实 coef 需用成交回报标定，本脚本不假装知道它。
    """
    return coef * (max(participation_pct, 0.0) ** 0.5)


def load_rows():
    rows = []
    for d in sorted(SOLO.iterdir()):
        p = d / "panel" / "entry_panel_WX.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        for day in data.get("days") or []:
            net = day.get("net_by_code") or {}
            cards = {c.get("code"): c for c in (day.get("cards") or [])}
            for code in day.get("selected") or []:
                if net.get(code) is None:
                    continue
                c = cards.get(code) or {}
                rows.append(
                    {
                        "skill": d.name,
                        "date": day.get("signal_date"),
                        "code": code,
                        "net": float(net[code]),
                        "amount_yi": c.get("amount_yi"),
                    }
                )
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    if not rows:
        print("no rows")
        return

    by_band: dict[str, list[dict]] = defaultdict(list)
    missing = 0
    for r in rows:
        b = band_of(r["amount_yi"])
        if b is None:
            missing += 1
            continue
        by_band[b].append(r)

    order = [b for b, _ in [(band_of((lo + min(hi, lo + 1)) / 2) or "", None) for lo, hi in BANDS]]
    order = [f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+" for lo, hi in BANDS]

    print("=" * 96)
    print("A. 按当日成交额分档的真实收益（731 笔 solo 回放，2025-10~2026-10）")
    print("=" * 96)
    hdr = f"{'成交额(亿)':>12} {'笔数':>5} {'每笔均值':>9} {'中位数':>8} {'胜率':>7} {'标准差':>8} {'1%参与率下单笔额度':>18}"
    print(hdr)
    print("-" * 96)
    summary, total = [], 0
    for b in order:
        rs = by_band.get(b) or []
        if not rs:
            continue
        nets = [x["net"] for x in rs]
        amts = [x["amount_yi"] for x in rs if x["amount_yi"] is not None]
        med_amt = statistics.median(amts)
        # 1% 参与率：单笔下单额 = 日均成交额 × 1%
        cap_1pct_wan = med_amt * 1e4 * 0.01  # 亿元 → 万元
        total += len(rs)
        summary.append(
            {
                "band_yi": b,
                "n": len(rs),
                "avg_pct": round(statistics.mean(nets), 3),
                "median_pct": round(statistics.median(nets), 3),
                "win_rate_pct": round(100 * sum(1 for x in nets if x > 0) / len(nets), 1),
                "std_pct": round(statistics.pstdev(nets), 3),
                "median_amount_yi": round(med_amt, 3),
                "cap_at_1pct_wan": round(cap_1pct_wan, 0),
            }
        )
        print(
            f"{b:>12} {len(rs):>5} {statistics.mean(nets):>9.3f} {statistics.median(nets):>8.3f} "
            f"{100 * sum(1 for x in nets if x > 0) / len(nets):>6.1f}% {statistics.pstdev(nets):>8.3f} {cap_1pct_wan:>15.0f} 万元"
        )

    print(f"\n合计 {total} 笔，成交额缺失 {missing} 笔")

    # 冲击成本敏感性：不同 coef 下，各档收益被吃掉多少
    print("\n" + "=" * 96)
    print("B. 冲击成本敏感性（平方根模型 impact = coef × √参与率，单位 %）")
    print("=" * 96)
    print("说明：coef 未标定，下表只展示「若 coef 取某值，各档收益会变成多少」，不是收益预测。")
    for coef in (0.3, 0.6, 1.0):
        print(f"\n  coef={coef}（参与率 1% → {impact_pct(1, coef):.2f}%，4% → {impact_pct(4, coef):.2f}%）")
        print(f"  {'档位':>10} {'原始':>8} {'1%参与':>9} {'2%参与':>9} {'5%参与':>9}")
        for s in summary:
            raw = s["avg_pct"]
            cells = [raw - impact_pct(p, coef) for p in (1, 2, 5)]
            print(f"  {s['band_yi']:>10} {raw:>8.3f} {cells[0]:>9.3f} {cells[1]:>9.3f} {cells[2]:>9.3f}")

    (OUT / "capacity_bands.json").write_text(
        json.dumps(
            {
                "source": str(SOLO),
                "n_total": total,
                "n_missing_amount": missing,
                "bands": summary,
                "impact_model": "sqrt: impact_pct = coef * sqrt(participation_pct)",
                "coef_calibration": "uncalibrated",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"\n输出: {OUT / 'capacity_bands.json'}")


if __name__ == "__main__":
    main()
