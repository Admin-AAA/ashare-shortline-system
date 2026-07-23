#!/usr/bin/env python3
"""资金流因子预测力分析（T1 新因子研发储备）。

capital_flow 数据 2026-07-24 修复后（单位 /1e4、ratio 改 NULL、upsert 修复），
本脚本验证主力资金净流入对 LIVE formal outcome 的预测力，输出：
  - 各资金流字段 vs outcome 的 point-biserial 相关系数
  - 流入>0 vs <=0 的分组胜率（A 股语义，正流入应预示更高胜率）
  - feature_signal_guard 判定（跨日可分才可信，防单日伪影）

只读分析，不改策略、不写库。D 路径穷尽时不直接加 strategy.yaml（过拟合风险），
本脚本产出「待前瞻验证的因子候选」——等前瞻样本积累后用扣费后样本外验证。

用法: cd backend && source .venv/bin/activate && python3 ../scripts/analyze_capital_flow_factor.py
"""
import os
import sys
import math

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))
SCRIPTS_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

from app.db.session import SessionLocal
from sqlalchemy import text
from feature_signal_guard import evaluate_feature_signal


def _point_biserial(x: list[float], y: list[int]) -> float | None:
    """point-biserial 相关系数：y 是 0/1 二值，x 是连续值。"""
    n = len(x)
    if n < 20:
        return None
    n1 = sum(y)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return None
    mean = sum(x) / n
    sd = math.sqrt(sum((xi - mean) ** 2 for xi in x) / n)
    if sd == 0:
        return None
    x_s = [xi for xi, yi in zip(x, y) if yi == 1]
    x_f = [xi for xi, yi in zip(x, y) if yi == 0]
    return (sum(x_s) / n1 - sum(x_f) / n0) / sd * math.sqrt(n1 * n0 / (n * n))


def main() -> None:
    db = SessionLocal()
    try:
        rows = [dict(r._mapping) for r in db.execute(text("""
            SELECT r.code, r.trade_date, o.outcome_label, o.executed_return,
                   cf.main_net_inflow_yi, cf.super_large_net_inflow_yi,
                   cf.large_net_inflow_yi, cf.cumulative_inflow_3d_yi,
                   cf.cumulative_inflow_5d_yi
            FROM recommendations r
            JOIN recommendation_runs rr ON r.run_id = rr.run_id
            JOIN recommendation_outcomes o ON o.recommendation_id = r.recommendation_id
            JOIN capital_flow cf ON cf.code = r.code AND cf.trade_date = r.trade_date
            WHERE rr.run_mode = 'formal' AND o.outcome_label IN ('success', 'failed')
              AND cf.main_net_inflow_yi IS NOT NULL AND ABS(cf.main_net_inflow_yi) > 0.001
        """)).fetchall()]

        print(f"资金流因子预测力分析（LIVE formal 已结算，匹配推荐日当天资金流）")
        print(f"样本: {len(rows)} 笔\n")
        if len(rows) < 20:
            print("样本不足 20，不可分析")
            return

        fields = [
            ("main_net_inflow_yi", "当日主力净流入"),
            ("super_large_net_inflow_yi", "超大单净流入"),
            ("large_net_inflow_yi", "大单净流入"),
            ("cumulative_inflow_3d_yi", "3日累计主力净流入"),
            ("cumulative_inflow_5d_yi", "5日累计主力净流入"),
        ]
        best_field = None
        best_rpb = 0
        for field, name in fields:
            pairs = [(float(r[field] or 0), 1 if r['outcome_label'] == 'success' else 0)
                     for r in rows if r[field] is not None and abs(float(r[field] or 0)) > 0.001]
            if len(pairs) < 20:
                print(f"  {name:18}: n={len(pairs)} 样本不足")
                continue
            x = [p[0] for p in pairs]
            y = [p[1] for p in pairs]
            rpb = _point_biserial(x, y)
            pos = [yi for xi, yi in pairs if xi > 0]
            neg = [yi for xi, yi in pairs if xi <= 0]
            wr_pos = sum(pos) * 100 / len(pos) if pos else 0
            wr_neg = sum(neg) * 100 / len(neg) if neg else 0
            print(f"  {name:18}: n={len(pairs):3} r_pb={rpb:+.4f} | "
                  f"流入>0 WR={wr_pos:.1f}%(n={len(pos)}) 流入<=0 WR={wr_neg:.1f}%(n={len(neg)}) Δ{wr_pos-wr_neg:+.1f}pp")
            if rpb is not None and rpb > best_rpb:
                best_rpb = rpb
                best_field = (field, name)

        if best_field:
            field, name = best_field
            print(f"\n最强因子: {name} (r_pb={best_rpb:+.4f})")
            # feature_signal_guard 判正例时间分布（防单日伪影）
            pos_dates = sorted({
                str(r['trade_date']) for r in rows
                if r[field] is not None and float(r[field] or 0) > 0.001 and r['outcome_label'] == 'success'
            })
            guard = evaluate_feature_signal(pos_dates)
            if guard["trustworthy"]:
                print(f"  feature_signal_guard: ✅ trustworthy（跨 {guard['independent_days']} 日，"
                      f"top={guard['top_day']}({guard['top_day_ratio']*100:.0f}%）")
                print(f"  → 可进入前瞻验证（扣费后样本外有效才加 strategy.yaml；D 路径穷尽时勿直接加 entry）")
            else:
                print(f"  feature_signal_guard: ❌ {guard['reason']}")
                print(f"  → 禁止据此加规则（单日伪影风险）")

            # 反事实：剔除最强因子<=0 的票后 D 项净盈亏比变化（量化因子边际价值）
            # 只读分析，不加规则。代价=损失被剔除的样本量，需权衡信息增益 vs 样本损失。
            def _net_pl(lst):
                rets = [float(r['executed_return'] or 0) - 0.07 for r in lst if r['executed_return'] is not None]
                if not rets: return None, 0, 0
                win = sum(x for x in rets if x > 0)
                lose = abs(sum(x for x in rets if x < 0))
                pl = win / lose if lose > 0 else float('inf')
                wr = sum(1 for r in lst if r['outcome_label'] == 'success') * 100 / len(lst)
                avg = sum(rets) / len(rets)
                return pl, wr, avg

            all_rows = [r for r in rows if r[field] is not None and abs(float(r[field] or 0)) > 0.001]
            kept = [r for r in all_rows if float(r[field] or 0) > 0]
            dropped = [r for r in all_rows if float(r[field] or 0) <= 0]
            pl_all, wr_all, avg_all = _net_pl(all_rows)
            pl_kept, wr_kept, avg_kept = _net_pl(kept)
            pl_drop, wr_drop, avg_drop = _net_pl(dropped)
            if pl_all and pl_kept:
                drop_pct = len(dropped) * 100 / len(all_rows) if all_rows else 0
                print(f"\n  反事实（剔除 {name}<=0 的票）:")
                print(f"    全量:        n={len(all_rows)} WR={wr_all:.1f}% avg={avg_all:.2f}% 净PL={pl_all:.2f}")
                print(f"    剔除后(>0):  n={len(kept)} WR={wr_kept:.1f}% avg={avg_kept:.2f}% 净PL={pl_kept:.2f}")
                print(f"    被剔除(<=0): n={len(dropped)} WR={wr_drop:.1f}% avg={avg_drop:.2f}% 净PL={pl_drop:.2f}")
                print(f"    边际: WR {wr_all:.1f}→{wr_kept:.1f} ({wr_kept-wr_all:+.1f}pp) | "
                      f"净PL {pl_all:.2f}→{pl_kept:.2f} ({pl_kept-pl_all:+.2f}) | 代价=损失{drop_pct:.0f}%样本")
    finally:
        db.close()


if __name__ == '__main__':
    main()
