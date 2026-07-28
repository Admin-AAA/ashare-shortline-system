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

BACKEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, os.path.abspath(BACKEND_DIR))
SCRIPTS_DIR = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

from app.db.session import SessionLocal
from feature_signal_guard import evaluate_feature_signal, point_biserial
from app.services.capital_flow_factor_evidence import (
    CAPITAL_FLOW_DISCOVERY_DATE,
    CAPITAL_FLOW_FIELDS,
    fetch_dedup_live_formal_capital_flow,
    summarize_discovery_oos,
    summarize_factor_groups,
)

def main() -> None:
    db = SessionLocal()
    try:
        rows = fetch_dedup_live_formal_capital_flow(db)

        print("资金流因子预测力分析（LIVE formal 已结算；按 code+trade_date 去重）")
        print(f"独立样本: {len(rows)} 笔，覆盖 {len({str(r['trade_date']) for r in rows})} 个交易日\n")
        if len(rows) < 20:
            print("样本不足 20，不可分析")
            return

        best_field = None
        best_rpb = 0
        for field, name in CAPITAL_FLOW_FIELDS:
            pairs = [(float(r[field] or 0), 1 if r['outcome_label'] == 'success' else 0)
                     for r in rows if r[field] is not None and abs(float(r[field] or 0)) > 0.001]
            if len(pairs) < 20:
                print(f"  {name:18}: n={len(pairs)} 样本不足")
                continue
            x = [p[0] for p in pairs]
            y = [p[1] for p in pairs]
            rpb = point_biserial(x, y)
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
            pos_dates = sorted([
                str(r['trade_date']) for r in rows
                if r[field] is not None and float(r[field] or 0) > 0.001 and r['outcome_label'] == 'success'
            ])
            guard = evaluate_feature_signal(pos_dates)
            if guard["trustworthy"]:
                print(f"  feature_signal_guard: ✅ trustworthy（跨 {guard['independent_days']} 日，"
                      f"top={guard['top_day']}({guard['top_day_ratio']*100:.0f}%）")
                print(f"  → 可进入前瞻验证（扣费后样本外有效才加 strategy.yaml；D 路径穷尽时勿直接加 entry）")
            else:
                print(f"  feature_signal_guard: ❌ {guard['reason']}")
                print(f"  → 禁止据此加规则（单日伪影风险）")

            groups = summarize_factor_groups(rows, field)
            all_metrics = groups["sample"]
            kept_metrics = groups["positive"]
            dropped_metrics = groups["non_positive"]
            if all_metrics["n"] and kept_metrics["n"]:
                drop_pct = dropped_metrics["n"] * 100 / all_metrics["n"]
                print(f"\n  反事实（剔除 {name}<=0 的票）:")
                for label, metrics in (("全量", all_metrics), ("剔除后(>0)", kept_metrics), ("被剔除(<=0)", dropped_metrics)):
                    print(f"    {label:12} n={metrics['n']} days={metrics['trade_days']} "
                          f"WR={metrics['win_rate']}% 净期望={metrics['net_expectancy']}% "
                          f"PF={metrics['profit_factor']}")
                print(f"    代价=损失{drop_pct:.0f}%样本；此处仅为探索窗口，不能作为晋级证据")

        oos = summarize_discovery_oos(rows)
        signal = oos["positive"]
        print(f"\n独立 OOS（因子发现于 {CAPITAL_FLOW_DISCOVERY_DATE} 收盘后，{oos['window']}）:")
        print(f"  3日资金流>0: n={signal['n']} days={signal['trade_days']} "
              f"WR={signal['win_rate']}% 净期望={signal['net_expectancy']}% PF={signal['profit_factor']}")
        print(f"  结论: {oos['verdict']}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
