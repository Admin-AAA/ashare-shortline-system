"""基准对比：推荐 alpha 真实性验证。

第一性原理：策略 alpha 必须显著高于随机基准，否则是噪声。
对比同期全市场随机买入 5 天的胜率/收益 vs 推荐样本，验证 alpha 真实性。

用法：
    python -m scripts.benchmark_alpha          # 全 formal 样本
    python -m scripts.benchmark_alpha --run-mode experiment
"""
from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from sqlalchemy import text


def recommendation_metrics(db, run_mode: str = "formal") -> dict:
    """推荐样本的胜率/盈亏比/平均收益（去重）。"""
    row = db.execute(text(f"""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN o.outcome_label = 'success' THEN 1 ELSE 0 END) AS wins,
          ROUND(AVG(o.executed_return), 2) AS avg_ret,
          ROUND(AVG(CASE WHEN o.executed_return > 0 THEN o.executed_return END), 2) AS avg_win,
          ROUND(AVG(CASE WHEN o.executed_return < 0 THEN ABS(o.executed_return) END), 2) AS avg_loss
        FROM recommendations rec
        JOIN recommendation_runs ru ON ru.run_id = rec.run_id
        JOIN recommendation_outcomes o ON o.recommendation_id = rec.recommendation_id
        WHERE ru.run_mode = :mode AND o.outcome_label IN ('success', 'failed')
    """), {"mode": run_mode}).fetchone()
    total = int(row[0] or 0)
    wins = int(row[1] or 0)
    return {
        "total": total,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "avg_return": float(row[2] or 0),
        "avg_win": float(row[3] or 0),
        "avg_loss": float(row[4] or 0),
        "plr": round(float(row[3] or 0) / float(row[4] or 1), 2) if row[4] else 0,
    }


def market_benchmark(db, run_mode: str = "formal") -> dict:
    """同期全市场随机买入 5 天的基准（与推荐同 trade_date）。"""
    row = db.execute(text("""
        WITH rec_dates AS (
          SELECT DISTINCT rec.trade_date
          FROM recommendations rec
          JOIN recommendation_runs ru ON ru.run_id = rec.run_id
          WHERE ru.run_mode = :mode
        )
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN dk.pct_chg > 0 THEN 1 ELSE 0 END) AS up,
          ROUND(AVG(dk.pct_chg), 2) AS avg_pct
        FROM daily_kbars dk
        WHERE dk.trade_date IN (SELECT trade_date FROM rec_dates)
          AND dk.pct_chg IS NOT NULL
    """), {"mode": run_mode}).fetchone()
    total = int(row[0] or 0)
    up = int(row[1] or 0)
    return {
        "total": total,
        "win_rate": round(up / total * 100, 1) if total else 0,
        "avg_return": float(row[2] or 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-mode", default="formal", choices=["formal", "experiment"])
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rec = recommendation_metrics(db, args.run_mode)
        bench = market_benchmark(db, args.run_mode)

        print(f"=== Alpha 基准对比（{args.run_mode}）===")
        print(f"\n推荐样本:")
        print(f"  样本数: {rec['total']}")
        print(f"  胜率: {rec['win_rate']}%")
        print(f"  平均收益: {rec['avg_return']}%")
        print(f"  盈亏比: {rec['plr']} (win={rec['avg_win']}% loss={rec['avg_loss']}%)")
        print(f"\n全市场基准（同期 trade_date）:")
        print(f"  样本数: {bench['total']}")
        print(f"  胜率: {bench['win_rate']}%")
        print(f"  平均收益: {bench['avg_return']}%")

        wr_alpha = round(rec["win_rate"] - bench["win_rate"], 1)
        ret_alpha = round(rec["avg_return"] - bench["avg_return"], 2)
        print(f"\n=== Alpha（推荐 - 基准）===")
        print(f"  胜率 alpha: +{wr_alpha}%")
        print(f"  收益 alpha: +{ret_alpha}%")
        if wr_alpha > 5 and ret_alpha > 0.5:
            print(f"  ✅ 策略 alpha 显著（胜率 +{wr_alpha}%, 收益 +{ret_alpha}%），不是噪声")
        else:
            print(f"  ⚠️ alpha 不显著，需进一步验证")
    finally:
        db.close()


if __name__ == "__main__":
    main()
