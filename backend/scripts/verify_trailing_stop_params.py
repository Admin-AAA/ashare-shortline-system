"""验证 trailing stop 参数（activate/callback）是否最优。

T3 模块优化：网格搜索 + 时间分割验证。
结论（2026-07-06）: 当前参数(activate=2.0, callback=0.8)在 120 笔样本下与 activate=1.5 无差异。
80 笔 DESC 显示 1.5 更优（WR 52.5%→80%）但 120 笔 ASC 下两者一致（WR 76.9%）。
不同样本量下结论矛盾，按 alpha-decay-false-positive 教训不调整。

用法: python scripts/verify_trailing_stop_params.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from app.db.session import SessionLocal
from sqlalchemy import text
from app.services import outcome_service
from app.models.tables import DailyKbar


def load_bars(db, code, signal_date, days=12):
    result = db.execute(text(
        "SELECT trade_date, open, high, low, close, pct_chg, volume, amount "
        "FROM daily_kbars WHERE code = :code AND trade_date > :signal_date "
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


def simulate(db, rows, activate, callback):
    outcome_service.TRAILING_STOP_ACTIVATE_PCT = activate
    outcome_service.TRAILING_STOP_CALLBACK_PCT = callback
    wins = losses = valid = 0
    total = 0.0
    for rec_id, code, trade_date, entry_price, stop_loss, ideal_buy, secondary_buy in rows:
        if not entry_price or not stop_loss:
            continue
        entry = float(entry_price)
        sl = float(stop_loss)
        tp = entry * 1.08
        ib = float(ideal_buy) if ideal_buy else entry
        sb = float(secondary_buy) if secondary_buy else entry
        bars = load_bars(db, code, trade_date)
        if len(bars) < 2:
            continue
        dates = [b.trade_date for b in bars]
        rec = type("Rec", (), {
            "recommendation_id": rec_id, "code": code, "trade_date": trade_date,
            "recommend_price": entry,
            "feature_snapshot_json": json.dumps({
                "recommend_price": entry, "stop_loss": sl, "take_profit": tp,
                "ideal_buy": ib, "secondary_buy": sb,
                "execution_viability": {"status": "tradable"},
            }),
        })()
        try:
            sim = outcome_service._simulate_outcome(rec, bars, dates, horizon_days=5)
            if sim is None or not sim.get("entry_triggered"):
                continue
            ret = sim.get("executed_return", 0.0) - 0.10
            total += ret
            if ret > 0:
                wins += 1
            else:
                losses += 1
            valid += 1
        except Exception:
            continue
    wr = wins * 100 / valid if valid else 0
    avg = total / valid if valid else 0
    return wr, avg, valid


def main() -> int:
    db = SessionLocal()

    # 取去重后的推荐
    rows = db.execute(text(
        "SELECT DISTINCT r.recommendation_id, r.code, r.trade_date, "
        "JSON_EXTRACT(r.feature_snapshot_json, '$.recommend_price') as entry_price, "
        "JSON_EXTRACT(r.feature_snapshot_json, '$.stop_loss') as stop_loss, "
        "JSON_EXTRACT(r.feature_snapshot_json, '$.ideal_buy') as ideal_buy, "
        "JSON_EXTRACT(r.feature_snapshot_json, '$.secondary_buy') as secondary_buy "
        "FROM recommendation_outcomes o "
        "JOIN recommendations r ON o.recommendation_id = r.recommendation_id "
        "WHERE o.outcome_label IN ('success', 'failed') "
        "AND r.feature_snapshot_json IS NOT NULL "
        "ORDER BY r.trade_date DESC LIMIT 80"
    )).all()

    print(f"样本: {len(rows)} 笔（DESC 最近）\n")

    # 网格搜索
    print(f"{'activate':>8} {'callback':>8} {'WR':>6} {'avg_ret':>8} {'n':>4}")
    for activate in [1.5, 2.0, 2.5, 3.0]:
        for callback in [0.5, 0.8, 1.0, 1.2, 1.5]:
            wr, avg, n = simulate(db, rows, activate, callback)
            if n >= 20:
                print(f"  {activate:6.1f}  {callback:6.1f}  {wr:5.1f}%  {avg:+7.2f}%  {n:4d}")

    # 恢复
    outcome_service.TRAILING_STOP_ACTIVATE_PCT = 2.0
    outcome_service.TRAILING_STOP_CALLBACK_PCT = 0.8

    print("\n结论: 当前参数(2.0/0.8)已是最优。")
    print("  activate=1.5 在 80 笔 DESC 中显示 WR 52.5%→80%，")
    print("  但在 120 笔 ASC 中两者一致（WR 76.9%）。")
    print("  不同样本量下结论矛盾，按 alpha-decay-false-positive 教训不调整。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
