"""诊断每个策略的 prefilter 通过率 + 零通过策略的逐条 universe 规则诊断。

用法: python3 scripts/diagnose_skill_prefilter.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.evaluator import evaluate  # noqa: E402
from app.engine.skill_loader import load_skills  # noqa: E402
from app.engine.strategy_runner import evaluate_skill_prefilter, _rule_expr  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.market_pool import get_market_universe_from_pool  # noqa: E402


def diagnose_universe_rules(skill, stocks: list[dict]) -> None:
    """对零通过策略，逐条 universe.include / prefilter.market 规则统计通过率。"""
    contract = skill.contract or {}
    universe = contract.get("universe", {}) or {}
    pf = (contract.get("prefilter") or {}).get("market") or {}

    print(f"  --- universe.include 逐条 ---")
    for rule in universe.get("include", []) or []:
        expr = _rule_expr(rule)
        passed = sum(1 for s in stocks if evaluate(expr, s))
        print(f"    [{passed:>4}/{len(stocks)} {passed/len(stocks)*100:5.1f}%] {expr}")
    print(f"  --- prefilter.market.include 逐条 ---")
    for rule in pf.get("include", []) or []:
        expr = _rule_expr(rule)
        passed = sum(1 for s in stocks if evaluate(expr, s))
        print(f"    [{passed:>4}/{len(stocks)} {passed/len(stocks)*100:5.1f}%] {expr}")
    ia = pf.get("include_any", []) or []
    if ia:
        print(f"  --- prefilter.market.include_any (需满足任一) ---")
        for rule in ia:
            expr = _rule_expr(rule)
            passed = sum(1 for s in stocks if evaluate(expr, s))
            print(f"    [{passed:>4}/{len(stocks)} {passed/len(stocks)*100:5.1f}%] {expr}")
        any_passed = sum(1 for s in stocks if any(evaluate(_rule_expr(r), s) for r in ia))
        print(f"    => 满足任一: {any_passed}/{len(stocks)} ({any_passed/len(stocks)*100:.1f}%)")


def main() -> None:
    db = SessionLocal()
    try:
        stocks, snapshot = get_market_universe_from_pool(db, refresh=False)
        trade_date = snapshot.get("trade_date")
        print(f"交易日={trade_date} 股票池={len(stocks)} 只")
        print("=" * 80)

        settings = get_settings()
        skills = load_skills(settings.skills_dir)
        print(f"加载 {len(skills)} 个策略\n")

        zero_pass_skills = []
        for skill in skills:
            sid = skill.skill_id
            pass_count = 0
            excl = Counter()
            for stock in stocks:
                passed, reason = evaluate_skill_prefilter(skill, stock, stage="market")
                if passed:
                    pass_count += 1
                else:
                    excl[reason] += 1
            total = len(stocks)
            rate = pass_count / total * 100 if total else 0
            marker = " ❌零通过" if pass_count == 0 else ""
            print(f"[{sid}] pass={pass_count}/{total} ({rate:.1f}%){marker}")
            if pass_count == 0:
                print(f"  排除原因 top5:")
                for r, c in excl.most_common(5):
                    print(f"    {r}: {c} ({c/total*100:.1f}%)")
                zero_pass_skills.append(skill)
            else:
                print(f"  排除原因 top3: {dict(excl.most_common(3))}")
            print()

        print("=" * 80)
        print(f"零通过策略 {len(zero_pass_skills)} 个，逐条诊断 universe:\n")
        for skill in zero_pass_skills:
            print(f"========== {skill.skill_id} ==========")
            diagnose_universe_rules(skill, stocks)
            print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
