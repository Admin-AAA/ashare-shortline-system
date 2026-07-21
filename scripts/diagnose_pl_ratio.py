#!/usr/bin/env python3
"""诊断 D 项净盈亏比下滑的根因（全量 2.08 -> 近10交易日 1.65）。

只读诊断：复用 self_check._compute_d_cost 的精确口径
（total P/L = Σ赢 / Σ|亏|，executed_return 单位为百分比，扣 0.07%/笔），
叠加 板块 / 策略 / 出场分布 拆解，定位 P/L 下滑来自「赢家变小」还是
「亏损变大」还是「止损地板占比上升」。不写库、不改策略。
"""
import os
import sys
from collections import defaultdict

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from app.db.session import SessionLocal
from sqlalchemy import text

ADDITIONAL_COST_PCT = 0.07  # outcome_service._ADDITIONAL_COST_PCT，单位=百分比


def _board_of(code: str | None) -> str:
    if not code:
        return 'unknown'
    if code.startswith(('688', '300', '8', '4')):
        return '20cm'
    return '10cm'


def _fetch(db, since_days):
    """返回 [(code, trade_date, label, executed_return)]，LIVE formal 去重(取最差ret)。"""
    sql = text("""
        SELECT r.code, r.trade_date, MAX(o.outcome_label) AS label,
               ROUND(MIN(o.executed_return), 2) AS min_ret
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE o.outcome_label IN ('success', 'failed')
          AND r.recommendation_id LIKE 'rec_%'
          AND r.recommended_at >= DATE_SUB(NOW(), INTERVAL :d DAY)
        GROUP BY r.code, r.trade_date
    """)
    return db.execute(sql, {"d": since_days}).fetchall()


def _metrics(rows):
    rets = [float(r[3]) for r in rows if r[3] is not None]
    if not rets:
        return None
    net = [x - ADDITIONAL_COST_PCT for x in rets]
    n = len(rets)
    g_wins = sum(1 for x in rets if x > 0)
    gp = sum(x for x in rets if x > 0)
    gl = abs(sum(x for x in rets if x < 0))
    np_ = sum(x for x in net if x > 0)
    nl = abs(sum(x for x in net if x < 0))
    return {
        "n": n,
        "wr": round(g_wins * 100 / n, 1),
        "gross_pl": round(gp / gl, 2) if gl else 999.0,
        "net_pl": round(np_ / nl, 2) if nl else 999.0,
        "gross_avg": round(sum(rets) / n, 2),
        "net_avg": round(sum(net) / n, 2),
        "max_loss": round(min(rets), 2),
        "sum_win": gp, "sum_loss": gl,
        "avg_win": round(gp / g_wins, 2) if g_wins else 0,
        "avg_loss": round(-gl / (n - g_wins), 2) if (n - g_wins) else 0,
    }


def _print(tag, m):
    if not m:
        print(f"  {tag}: 样本不足")
        return
    print(f"  {tag}: n={m['n']} 胜率={m['wr']}% 赢均={m['avg_win']}% 亏均={m['avg_loss']}% "
          f"Σ赢={m['sum_win']:.1f}/Σ亏={m['sum_loss']:.1f} 净盈亏比={m['net_pl']} "
          f"净均收益={m['net_avg']}% 最大亏损={m['max_loss']}%")


def _breakdown(db, since_days, keyfn, title):
    rows = _fetch(db, since_days)
    print(f"\n=== {title} (近 {since_days} 天 LIVE formal 去重) ===")
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        _print(f"{k}", _metrics(groups[k]))


def _top_losses(db, since_days, k=15):
    rows = _fetch(db, since_days)
    losses = sorted([r for r in rows if r[3] is not None and r[3] < 0],
                    key=lambda x: x[3])[:k]
    print(f"\n=== 近 {since_days} 天 最大亏损 TOP {k}（定位止损地板集中度） ===")
    for r in losses:
        print(f"  {r[0]} td={r[1]} label={r[2]} ret={r[3]}% board={_board_of(r[0])}")


def main():
    db = SessionLocal()
    try:
        print("=== D 项净盈亏比拆解（精确口径，同 self_check._compute_d_cost）===")
        _print("全量(1年)", _metrics(_fetch(db, 365)))
        _print("近10交易日(~20天)", _metrics(_fetch(db, 20)))
        _print("近3交易日(~7天)", _metrics(_fetch(db, 7)))
        _breakdown(db, 20, lambda r: _board_of(r[0]), "近10交易日 板块拆分")
        _breakdown(db, 20, lambda r: r[2], "近10交易日 出场类型拆分")
        _breakdown(db, 20, lambda r: r[0][:3], "近10交易日 代码前缀(板块)拆分")
        _top_losses(db, 20, 15)
    finally:
        db.close()


if __name__ == "__main__":
    main()
