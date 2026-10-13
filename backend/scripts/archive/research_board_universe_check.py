"""核实生产选择契约的**板型范围**，以及三窗实际选中/候选池的板型分布。

为什么要单独做：会话中我曾把三窗口径描述为"只做 20cm"，并据此把"放宽 universe
到非 20cm 主板票"列为一个待用户决定的选项。这是错的——`production_selection.v4`
的 `board_universe = None`（=全市场契约），`excluded_20cm_buckets` 只在
`board_universe='20cm'` 时才生效。本脚本把这个断言变成可复核数字，避免同类误述。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_board_universe_check.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy,
    evaluate_selection_policy,
    SelectionPolicyContext,
)
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
    attach_ivol20_pct,
    attach_ivol_dn20_pct,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: F401,E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    OUTPUT_COUNT,
    SCORE_LINE,
)


def board(code: str) -> str:
    c = str(code)
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("688", "689")):
        return "科创板"
    if c.startswith(("8", "4", "920")):
        return "北交所"
    return "主板"


def main() -> None:
    pol = load_production_selection_policy(version=4)
    print(f"policy version        = {pol.version}")
    print(f"board_universe        = {pol.board_universe!r}   ← None 即全市场契约")
    print(f"excluded_20cm_buckets = {pol.excluded_20cm_buckets}   ← 仅 board_universe='20cm' 时生效")
    print()

    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_groups), ("W2", w2_groups), ("W3", w3_groups))
    all_dates = [d for _w, g in windows for d, _c, _n, _day in g]
    with SessionLocal() as s:
        iv20 = load_ivol20_pct_map(s, all_dates)
        ivdn = load_ivol_dn20_pct_map(s, all_dates)

    for w, groups in windows:
        pool, picked = Counter(), Counter()
        for dt, cards, _nm, day in groups:
            ms = dict(getattr(day, "market_snapshot", {}) or {})
            cc = [dict(c) for c in cards]
            attach_ivol20_pct(cc, dt, iv20)
            attach_ivol_dn20_pct(cc, dt, ivdn)
            for c in cards:
                pool[board(str(c["code"]))] += 1
            for c in evaluate_selection_policy(
                cc, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms), pol
            ).recommendations:
                picked[board(str(c.get("code")))] += 1
        total = sum(picked.values())
        share = " ".join(f"{k}={v}({v/max(1,total)*100:.0f}%)" for k, v in picked.most_common())
        print(f"[{w}] 候选池板型 {dict(pool)}")
        print(f"[{w}] 选中板型   {share}  (n={total})")

    print("\n结论：该口径为**全市场**（主板占比最高），不存在'只做 20cm'的锁定；"
          "把'放宽 universe 到非 20cm'当作待决选项是错的。")


if __name__ == "__main__":
    main()
