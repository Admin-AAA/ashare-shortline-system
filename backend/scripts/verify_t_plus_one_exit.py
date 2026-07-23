"""T+1 离场口径验证（改码后）。

A 股 T+1 交收：买入当日不能卖出。本脚本验证改码后的真实口径表现：
  - 盘中推荐（recommended_at 落在交易时段）：T 当天入场、T+1 起可离场
  - 盘后推荐：T+1 入场、T+2 起可离场
  - 入场日当天不触发任何离场（含跳空止损），从入场日次日起扫止损/止盈/trailing

对比改后模拟 vs 库里原始 outcome_label（库里是改前的旧口径），输出：
  - 改后整体胜率/均收益/最大亏损/盈亏比（拆分盘后/盘中）
  - 与库里旧标签的翻转数

不写库，不重算历史 outcome；只读 recommendations + daily_kbars 做模拟。

用法: python scripts/verify_t_plus_one_exit.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from app.db.session import SessionLocal
from sqlalchemy import text
from app.services import outcome_service
from app.models.tables import DailyKbar

# 北京时间 09:30–15:00 = UTC 01:30–07:00（recommended_at 以 UTC 存储）
INTRADAY_UTC_START = time(1, 30)
INTRADAY_UTC_END = time(7, 0)


def is_intraday_recommendation(recommended_at) -> bool:
    if recommended_at is None:
        return False
    t = recommended_at.time() if hasattr(recommended_at, "time") else None
    if t is None:
        return False
    return INTRADAY_UTC_START <= t <= INTRADAY_UTC_END


def load_bars(db, code, signal_date, days=12):
    result = db.execute(text(
        "SELECT trade_date, open, high, low, close, pct_chg, volume, amount "
        "FROM daily_kbars WHERE code = :code AND trade_date >= :signal_date "
        "ORDER BY trade_date ASC LIMIT :limit"
    ), {"code": code, "signal_date": signal_date, "limit": days})
    bars = []
    for row in result:
        bars.append(DailyKbar(
            code=code, trade_date=str(row[0]),
            open=float(row[1]), high=float(row[2]), low=float(row[3]),
            close=float(row[4]), pct_chg=float(row[5]) if row[5] else 0.0,
            volume=float(row[6]) if row[6] else 1000000.0,
            amount=float(row[7]) if row[7] else 10000000.0,
        ))
    return bars


def simulate(db, rows, only_intraday=None):
    """跑改后真实口径，返回聚合指标 + 与库里 orig_label 的翻转数。"""
    cost = outcome_service._ADDITIONAL_COST_PCT
    rets = []
    wins = losses = 0
    flips = 0
    for rec_id, code, trade_date, recommended_at, orig_label, fs_json in rows:
        intr = is_intraday_recommendation(recommended_at)
        if only_intraday is True and not intr:
            continue
        if only_intraday is False and intr:
            continue
        fs = json.loads(fs_json) if fs_json else {}
        bars = load_bars(db, code, trade_date)
        if len(bars) < 2:
            continue
        dates = [b.trade_date for b in bars]
        rec = type("Rec", (), {
            "recommendation_id": rec_id, "code": code, "trade_date": trade_date,
            "recommended_at": recommended_at,
            "recommend_price": float(fs.get("recommend_price") or 0),
            "feature_snapshot_json": fs_json,
        })()
        try:
            sim = outcome_service._simulate_outcome(rec, bars, dates, horizon_days=5)
        except Exception:
            continue
        if sim is None or not sim.get("entry_triggered"):
            continue
        ret = float(sim.get("executed_return", 0.0)) - cost
        rets.append(ret)
        if ret > 0:
            wins += 1
        else:
            losses += 1
        new_label = "success" if ret > 0 else "failed"
        if new_label != orig_label:
            flips += 1
    n = len(rets)
    if not n:
        return None
    losses_vals = [abs(r) for r in rets if r < 0]
    plr = (sum(r for r in rets if r > 0) / sum(losses_vals)) if losses_vals else None
    return {
        "n": n, "win_rate": wins * 100 / n, "avg_return": sum(rets) / n,
        "max_loss": min(rets), "plr": plr, "label_flips": flips,
    }


def fmt(x):
    return f"{x:.2f}" if x is not None else "  —"


def main() -> int:
    db = SessionLocal()
    rows = db.execute(text(
        "SELECT r.recommendation_id, r.code, r.trade_date, r.recommended_at, "
        "o.outcome_label as orig_label, r.feature_snapshot_json "
        "FROM recommendation_outcomes o "
        "JOIN recommendations r ON o.recommendation_id = r.recommendation_id "
        "JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal' "
        "WHERE o.outcome_label IN ('success', 'failed') "
        "AND r.recommendation_id LIKE 'rec_%' "
        "AND r.feature_snapshot_json IS NOT NULL "
        "ORDER BY r.trade_date DESC LIMIT 200"
    )).all()

    print(f"样本: {len(rows)} 笔（LIVE formal 已结算，DESC 最近 200）\n")

    overall = simulate(db, rows)
    intra = simulate(db, rows, only_intraday=True)
    post = simulate(db, rows, only_intraday=False)

    def show(label, m):
        if m is None:
            print(f"  {label:<14} 无样本")
            return
        print(f"  {label:<14} n={m['n']:>3}  胜率 {m['win_rate']:5.1f}%  均收益 {m['avg_return']:+7.2f}%  "
              f"最大亏损 {m['max_loss']:+7.2f}%  盈亏比 {fmt(m['plr']):>5}  翻转 {m['label_flips']}")

    print("改后口径（T+1 交收，盘中推荐 T 当天入场/T+1 离场，盘后 T+1 入场/T+2 离场）:")
    show("整体", overall)
    show("盘中推荐", intra)
    show("盘后推荐", post)

    if intra:
        print(f"\n盘中推荐 {intra['n']} 笔（recommended_at 落在交易时段，手动盘中触发）")
    if post:
        print(f"盘后推荐 {post['n']} 笔（17:35 cron 生成）")
    print(f"\n标签翻转: 整体 {overall['label_flips']}/{overall['n']} 笔 vs 库里旧口径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
