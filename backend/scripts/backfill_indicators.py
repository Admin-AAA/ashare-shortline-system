"""
补齐 daily_kbars 中本地可计算的指标（MA/量比/乖离/突破/回撤等）。

参数化日期：默认补最近 N 天缺失指标的交易日。
同时处理 ma5 IS NULL 和 ma5 = 0 两种情况（批量同步写入 0，逐股票写入 NULL）。

仅补本地可算指标；MACD/RSI/KDJ/BOLL/CCI 来自 Tushare stk_factor，需要完整同步补齐。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from sqlalchemy import text


def _dates_needing_indicators(db, days: int) -> list[str]:
    """找出最近 days 天内指标缺失（ma5 IS NULL 或 = 0）比例 > 50% 的交易日。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = db.execute(text("""
        SELECT trade_date,
               COUNT(*) AS total,
               SUM(CASE WHEN ma5 IS NULL OR ma5 = 0 THEN 1 ELSE 0 END) AS missing
        FROM daily_kbars
        WHERE trade_date >= :cutoff
        GROUP BY trade_date
        HAVING missing > total * 0.5
        ORDER BY trade_date DESC
    """), {"cutoff": cutoff}).fetchall()
    return [r[0] for r in rows]


def backfill_date(db, trade_date: str) -> dict:
    """补单个交易日的本地指标。返回修复统计。"""
    codes = db.execute(text(
        "SELECT DISTINCT code FROM daily_kbars WHERE trade_date = :d"
    ), {"d": trade_date}).scalars().all()

    fixed = {"ma": 0, "vol": 0, "bias": 0, "breakout": 0, "dd": 0}

    for code in codes:
        bars = db.query(DailyKbar).filter(
            DailyKbar.code == code,
            DailyKbar.trade_date <= trade_date
        ).order_by(DailyKbar.trade_date.desc()).limit(60).all()
        bars = list(reversed(bars))
        if len(bars) < 3:
            continue

        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]
        target = bars[-1]
        n = len(closes)

        # MA：同时处理 NULL 和 0
        if n >= 5 and (target.ma5 is None or target.ma5 == 0):
            target.ma5 = round(sum(closes[-5:]) / 5, 4)
            fixed["ma"] += 1
        if n >= 10 and (target.ma10 is None or target.ma10 == 0):
            target.ma10 = round(sum(closes[-10:]) / 10, 4)
        if n >= 20 and (target.ma20 is None or target.ma20 == 0):
            target.ma20 = round(sum(closes[-20:]) / 20, 4)
        if n >= 60 and (target.ma60 is None or target.ma60 == 0):
            target.ma60 = round(sum(closes[-60:]) / 60, 4)

        # Volume ratio
        if n >= 5 and sum(volumes[-5:]) > 0 and (target.vol_ratio_5 is None or target.vol_ratio_5 == 0):
            target.vol_ratio_5 = round(volumes[-1] / (sum(volumes[-5:]) / 5), 4)
            fixed["vol"] += 1
        if n >= 20 and sum(volumes[-20:]) > 0 and (target.vol_ratio_20 is None or target.vol_ratio_20 == 0):
            target.vol_ratio_20 = round(volumes[-1] / (sum(volumes[-20:]) / 20), 4)

        # Bias
        if n >= 5 and target.ma5 and (target.bias_5 is None or target.bias_5 == 0):
            target.bias_5 = round((closes[-1] - target.ma5) / target.ma5 * 100, 4)
            fixed["bias"] += 1
        if n >= 20 and target.ma20 and (target.bias_20 is None or target.bias_20 == 0):
            target.bias_20 = round((closes[-1] - target.ma20) / target.ma20 * 100, 4)

        # Breakout
        if target.ma20 and closes[-1] > target.ma20 and not target.breakout20:
            target.breakout20 = 1
            fixed["breakout"] += 1
        if target.ma60 and closes[-1] > target.ma60 and not target.breakout60:
            target.breakout60 = 1

        # Max drawdown
        if n >= 20 and (target.max_dd20 is None or target.max_dd20 == 0):
            max20 = max(closes[-20:])
            dd20 = min((c - max20) / max20 * 100 for c in closes[-20:])
            target.max_dd20 = round(dd20, 4)
            fixed["dd"] += 1

        # Holder pct
        if target.ma60 and closes[-1] > 0 and (target.holder_pct is None or target.holder_pct == 0):
            pct = (closes[-1] - target.ma60) / target.ma60 * 100
            target.holder_pct = round(pct, 2)

    db.commit()
    return fixed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="回看天数")
    parser.add_argument("--date", type=str, default=None, help="指定单日 (YYYY-MM-DD)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.date:
            dates = [args.date]
        else:
            dates = _dates_needing_indicators(db, args.days)

        if not dates:
            print(f"最近 {args.days} 天指标完整，无需补齐")
            return

        print(f"需补齐指标的交易日: {len(dates)} 个")
        for d in dates:
            stats = backfill_date(db, d)
            print(f"  {d}: ma={stats['ma']} vol={stats['vol']} bias={stats['bias']} dd={stats['dd']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
