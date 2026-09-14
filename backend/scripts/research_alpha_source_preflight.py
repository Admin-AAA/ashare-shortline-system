"""新 alpha 数据源的可行性预检（三窗覆盖率 + 对现有 20cm 选择的触发率）。

用户规则：`expected_n_per_window < 20` 的候选直接放弃（L-009 统计门槛）。
本脚本把"能不能做"从口头判断变成可复现数字，避免为一个必然触发不足的数据源
写完整 skill。

输出两部分：
1. 数据覆盖：每个候选表在 W1/W2/W3 的行数与覆盖天数；
2. 触发率：把每个源当作"当日触发条件"，统计现有 v4 生产选择里有多少笔
   同时命中（W1 冻结快照 244d / W2 前瞻 117d / W3 第三窗 19d）。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_alpha_source_preflight.py
"""
from __future__ import annotations

import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import inspect, text  # noqa: E402

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
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    OUTPUT_COUNT,
    SCORE_LINE,
    _main_industries_from_db,
    _attach_main_industries,
)

WINDOWS = {
    "W1": ("2025-01-15", "2026-01-16"),
    "W2": ("2026-01-17", "2026-07-30"),
    "W3": ("2026-08-01", "2026-09-13"),
}
# 候选源：(表名, 日期列, 用于触发率匹配的股票列)
SOURCES = (
    ("auction_data", "trade_date", "code"),
    ("dragon_tiger", "trade_date", "code"),
    ("capital_flow", "trade_date", "code"),
    ("hsgt_top10", "trade_date", "code"),
    ("news_announcement", "announce_date", "code"),
)


def _selected_codes(windows, iv20map, ivdnmap):
    pol = load_production_selection_policy(version=4)
    picks = {}
    for w, _dates, groups in windows:
        sel = []
        for dt, cards, _nm, day in groups:
            ms = dict(getattr(day, "market_snapshot", {}) or {})
            cc = [dict(c) for c in cards]
            attach_ivol20_pct(cc, dt, iv20map)
            attach_ivol_dn20_pct(cc, dt, ivdnmap)
            for c in evaluate_selection_policy(
                cc, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms), pol
            ).recommendations:
                sel.append((dt, str(c.get("code"))))
        picks[w] = sel
    return picks


def main() -> None:
    with SessionLocal() as s:
        have = set(inspect(s.get_bind()).get_table_names())
        print("=== 1. 数据覆盖（行数 / 覆盖天数）===")
        for table, dcol, _ccol in SOURCES:
            if table not in have:
                print(f"{table}: 表不存在")
                continue
            parts = []
            for w, (a, b) in WINDOWS.items():
                n = s.execute(
                    text(f"select count(*) from {table} where {dcol} between :a and :b"),
                    {"a": a, "b": b},
                ).scalar()
                d = s.execute(
                    text(f"select count(distinct {dcol}) from {table} where {dcol} between :a and :b"),
                    {"a": a, "b": b},
                ).scalar()
                parts.append(f"{w} {n:>7}行/{d:>3}天")
            print(f"{table:20s} " + " | ".join(parts))

    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    mi = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi)
    with SessionLocal() as s:
        iv20map = load_ivol20_pct_map(s, all_dates)
        ivdnmap = load_ivol_dn20_pct_map(s, all_dates)
    picks = _selected_codes(windows, iv20map, ivdnmap)

    print("\n=== 2. 触发率（现有 v4 选择里当日命中该源的比例）===")
    window_days = {w: len({dt for dt, _c in picks[w]}) for w in ("W1", "W2", "W3")}
    with SessionLocal() as s:
        for table, dcol, ccol in SOURCES:
            if table not in have:
                continue
            rows = s.execute(
                text(f"select distinct {dcol}, {ccol} from {table} where {dcol} between :a and :b"),
                {"a": WINDOWS["W1"][0], "b": WINDOWS["W3"][1]},
            ).all()
            byday: dict = {}
            for d, c in rows:
                byday.setdefault(str(d), set()).add(str(c))
            parts, verdict, cover = [], [], []
            for w in ("W1", "W2", "W3"):
                sel = picks[w]
                hit = sum(1 for dt, code in sel if code in byday.get(dt, set()))
                rate = hit / max(1, len(sel)) * 100
                days_cov = len({dt for dt, _c in sel if dt in byday})
                total_days = window_days[w]
                c_rate = days_cov / max(1, total_days) * 100
                parts.append(f"{w} {hit:>4}/{len(sel):<4}({rate:4.1f}%) 天覆盖{days_cov}/{total_days}")
                verdict.append(hit)
                cover.append((days_cov, total_days))
            # 两个门槛都要过：每窗触发>=20（L-009），且每窗数据覆盖>=90%（L-112）
            ok = min(verdict) >= 20 and all(c / max(1, t) >= 0.90 for c, t in cover)
            if not ok and min(verdict) >= 20:
                gate = "FAIL(覆盖不足：规则会在缺数据的日子静默失效)"
            else:
                gate = "PASS" if ok else "FAIL(触发<20)"
            print(f"{table:20s} " + " | ".join(parts) + f"  → {gate}")


if __name__ == "__main__":
    main()
