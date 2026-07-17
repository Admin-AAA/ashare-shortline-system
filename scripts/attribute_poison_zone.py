"""T3.4 失败归因：导出 22 笔真残留毒区逐笔详情，做深度归因。

不改规则（避免前移 FORWARD_EPOCH）。产出归因文档供后续一次性加规则用样本外验证。
纯查询，不改 DB。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import SessionLocal
from sqlalchemy import text

sys.path.insert(0, str(ROOT / "scripts"))
from self_check import _collect_uncovered_failures, _classify_failure_mode_inline, _load_skills_once  # noqa: E402


def _fs_get(fs: dict, key: str, default=None):
    v = fs.get(key, default)
    return v if v is not None else default


def main() -> None:
    db = SessionLocal()
    skills = _load_skills_once()
    rows = db.execute(text("""
        SELECT r.recommendation_id, o.outcome_label, o.executed_return,
               r.feature_snapshot_json, r.skill_breakdown_json,
               o.exit_reason, o.max_return,
               r.trade_date, r.code, r.name, r.setup_type,
               r.bucket, r.final_score
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
    """)).fetchall()

    uncovered = _collect_uncovered_failures(rows, skills)
    print(f"真残留毒区：{len(uncovered)} 笔\n")

    # 建 rid → 元信息映射
    meta = {}
    for row in rows:
        rid = row[0]
        meta[rid] = {
            "trade_date": row[7], "code": row[8], "name": row[9],
            "setup_type": row[10], "bucket": row[11], "final_score": float(row[12] or 0),
        }

    # 拉每笔的 regime / 关键因子
    print(f"{'#':>2} {'date':>10} {'code':>8} {'name':<10} {'setup':<20} {'mode':<16} {'exit':<18} {'maxR':>6} {'execR':>7} {'regime':<12} {'rsi12':>6} {'chg20':>6} {'pos20':>6} {'pos60':>6} {'strategy'}")
    print("-" * 160)

    mode_counts: dict[str, int] = {}
    mode_rets: dict[str, list[float]] = {}
    regime_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    detail_rows = []

    for i, u in enumerate(uncovered, 1):
        rid = u["rid"]
        fs = u["fs"]
        m = meta.get(rid, {})
        exit_reason = u["exit_reason"] or ""
        max_return = u["max_return"]
        executed_return = u["executed_return"]

        rsi_12 = _fs_get(fs, "rsi_12")
        chg20 = _fs_get(fs, "chg20")
        pos20 = _fs_get(fs, "pos20")
        pos60 = _fs_get(fs, "pos60")
        regime = _fs_get(fs, "market_regime") or _fs_get(fs, "regime") or "?"

        # skill_breakdown 取策略
        sb_raw = None
        for row in rows:
            if row[0] == rid:
                sb_raw = row[4]
                break
        sb = json.loads(sb_raw) if sb_raw else []
        strategies = [s.get("strategy_id") for s in sb if isinstance(s, dict) and s.get("strategy_id")]

        mode = _classify_failure_mode_inline(
            exit_reason=exit_reason, executed_return=executed_return, max_return=max_return,
            rsi_12=rsi_12, chg20=chg20, pos60=pos60, setup_type=m.get("setup_type"),
        )
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        mode_rets.setdefault(mode, []).append(executed_return or 0)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        for s in strategies:
            strategy_counts[s] = strategy_counts.get(s, 0) + 1

        print(f"{i:>2} {str(m.get('trade_date','')):>10} {str(m.get('code','')):>8} {str(m.get('name','')):<10.10} {str(m.get('setup_type','')):<20.20} {mode:<16} {str(exit_reason):<18.18} {max_return if max_return is not None else 0:>6.2f} {executed_return if executed_return is not None else 0:>7.2f} {str(regime):<12.12} {_fmt(rsi_12):>6} {_fmt(chg20):>6} {_fmt(pos20):>6} {_fmt(pos60):>6} {','.join(strategies)[:50]}")
        detail_rows.append({
            "rid": rid, "date": str(m.get("trade_date")), "code": m.get("code"),
            "name": m.get("name"), "setup": m.get("setup_type"), "mode": mode,
            "exit_reason": exit_reason, "max_return": max_return, "executed_return": executed_return,
            "regime": regime, "rsi_12": rsi_12, "chg20": chg20, "pos20": pos20, "pos60": pos60,
            "strategies": strategies, "final_score": m.get("final_score"),
        })

    print("\n" + "=" * 80)
    print("模式分布:")
    for mode, cnt in sorted(mode_counts.items(), key=lambda x: -x[1]):
        rets = mode_rets[mode]
        avg = sum(rets) / len(rets) if rets else 0
        print(f"  {mode:<16} {cnt:>3} 笔  平均净收益 {avg:>+6.2f}%")

    print("\nRegime 分布:")
    for r, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
        print(f"  {r:<20} {cnt:>3} 笔")

    print("\n策略分布（一笔可能多策略）:")
    for s, cnt in sorted(strategy_counts.items(), key=lambda x: -x[1]):
        print(f"  {s:<40} {cnt:>3} 笔")

    # 导出 JSON 供后续分析
    out_path = ROOT / "backend" / "logs" / "poison_zone_attribution.json"
    out_path.write_text(json.dumps(detail_rows, ensure_ascii=False, indent=2, default=str))
    print(f"\n详情已导出: {out_path}")


def _fmt(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return str(v)


if __name__ == "__main__":
    main()
