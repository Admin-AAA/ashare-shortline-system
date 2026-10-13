"""三窗口的**交易日覆盖**核验：既有 canonical 候选池是否真的覆盖了目标区间。

为什么要单独做：会话中我发现 W3 只覆盖 19/30 个交易日，W2 的覆盖区间与目标区间
并不一致（起点晚 15 个交易日、终点越界 3 个交易日）。验收目标按"窗口"定义，
如果底层候选池没有覆盖该窗口，那么指标描述的是**另一个区间**，必须在结论里
显式披露，而不是默认它覆盖了。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_window_coverage_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402

WINDOWS = {
    "W1": ("2025-01-15", "2026-01-16"),
    "W2": ("2026-01-17", "2026-07-30"),
    "W3": ("2026-08-01", "2026-09-13"),
}


def main() -> None:
    with SessionLocal() as s:
        calendar = {
            w: [r[0] for r in s.execute(
                text("select distinct trade_date from daily_kbars where trade_date between :a and :b order by trade_date"),
                {"a": a, "b": b},
            ).all()]
            for w, (a, b) in WINDOWS.items()
        }

    f_dates, _fg = frozen_groups()
    w2_dates, _w2g = canon_groups("forward")
    w3_dates, _w3g = canon_groups("third")
    actual = {"W1": list(f_dates), "W2": list(w2_dates), "W3": list(w3_dates)}

    print(f"{'窗口':4s} {'日历交易日':>10s} {'实际覆盖':>8s} {'覆盖率':>8s}  {'实际区间':<26s} 缺失")
    for w in ("W1", "W2", "W3"):
        cal, act = calendar[w], actual[w]
        miss = [d for d in cal if d not in set(act)]
        outside = [d for d in act if d not in set(cal)]
        rate = len(act) / max(1, len(cal)) * 100
        print(f"{w:4s} {len(cal):>10d} {len(act):>8d} {rate:>7.1f}%  "
              f"{act[0]}..{act[-1]}  {len(miss)} 个缺失；{len(outside)} 个越界")
        if miss:
            print(f"      缺失日: {miss}")
        if outside:
            print(f"      越界日（属于其它窗口）: {outside}")

    print("\n结论：三窗口的既有 canonical 候选池**并非完整覆盖**目标区间，且 W2 实际区间与目标区间不一致。")
    print("指标必须按'实际覆盖区间'解读；补覆盖需用同一 canonical runner 重跑缺失区间，")
    print("不得与 historical_replay 重建口径混用（两者在共同日上 Δcohort −0.7250，已测）。")


if __name__ == "__main__":
    main()
