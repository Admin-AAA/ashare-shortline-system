"""
补齐 daily_kbars 中本地可计算的指标（MA/量比/乖离/突破/回撤等）。

仅补本地可算指标；MACD/RSI/KDJ/BOLL/CCI 来自 Tushare stk_factor，需要下一次完整同步补齐。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from sqlalchemy import text


def main():
    db = SessionLocal()
    try:
        r = db.execute(text("""
            SELECT trade_date, COUNT(*) as total,
                   SUM(CASE WHEN ma5 IS NOT NULL THEN 1 ELSE 0 END) as with_ma5
            FROM daily_kbars
            WHERE trade_date = '2026-05-29'
            GROUP BY trade_date
        """)).fetchone()

        if not r or r.with_ma5 >= r.total * 0.5:
            print(f"May 29: {r.with_ma5}/{r.total} have ma5 → OK")
            return

        trade_date, total, with_ma5 = r
        print(f"May 29: {with_ma5}/{total} ({round(with_ma5/total*100,1)}%) have ma5")

        # Get all codes for this date
        codes = db.execute(text("""
            SELECT DISTINCT code FROM daily_kbars WHERE trade_date = '2026-05-29'
        """)).scalars().all()

        fixed_ma = 0
        fixed_vol = 0
        fixed_bias = 0

        for i, code in enumerate(codes):
            # Get bars for this code (last 60 days)
            bars = db.query(DailyKbar).filter(
                DailyKbar.code == code,
                DailyKbar.trade_date <= '2026-05-29'
            ).order_by(DailyKbar.trade_date.desc()).limit(60).all()
            bars = list(reversed(bars))  # ascending order for calculation

            if len(bars) < 3:
                continue

            closes = [b.close for b in bars]
            volumes = [b.volume for b in bars]

            target = bars[-1]
            n = len(closes)

            # MA
            if n >= 5 and target.ma5 is None:
                target.ma5 = round(sum(closes[-5:]) / 5, 4)
                fixed_ma += 1
            if n >= 10 and target.ma10 is None:
                target.ma10 = round(sum(closes[-10:]) / 10, 4)
            if n >= 20 and target.ma20 is None:
                target.ma20 = round(sum(closes[-20:]) / 20, 4)
            if n >= 60 and target.ma60 is None:
                target.ma60 = round(sum(closes[-60:]) / 60, 4)

            # Volume ratio
            if n >= 5 and target.vol_ratio_5 is None and sum(volumes[-5:]) > 0:
                target.vol_ratio_5 = round(volumes[-1] / (sum(volumes[-5:]) / 5), 4)
                fixed_vol += 1
            if n >= 20 and target.vol_ratio_20 is None and sum(volumes[-20:]) > 0:
                target.vol_ratio_20 = round(volumes[-1] / (sum(volumes[-20:]) / 20), 4)

            # Bias
            if n >= 5 and target.ma5 and target.bias_5 is None:
                target.bias_5 = round((closes[-1] - target.ma5) / target.ma5 * 100, 4)
                fixed_bias += 1
            if n >= 20 and target.ma20 and target.bias_20 is None:
                target.bias_20 = round((closes[-1] - target.ma20) / target.ma20 * 100, 4)

            # Breakout
            if target.ma20 and closes[-1] > target.ma20:
                target.breakout20 = 1
            if target.ma60 and closes[-1] > target.ma60:
                target.breakout60 = 1

            # Max drawdown
            if n >= 20 and target.max_dd20 is None:
                max20 = max(closes[-20:])
                dd20 = min((c - max20) / max20 * 100 for c in closes[-20:])
                target.max_dd20 = round(dd20, 4)

            # Holder pct
            if target.ma60 and closes[-1] > 0:
                pct = (closes[-1] - target.ma60) / target.ma60 * 100
                target.holder_pct = round(pct, 2)

            if (i + 1) % 100 == 0:
                db.commit()
                print(f"  [{i+1}/{len(codes)}] ma:{fixed_ma} vol:{fixed_vol} bias:{fixed_bias}")

        db.commit()
        print(f"Done: ma={fixed_ma} vol={fixed_vol} bias={fixed_bias}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
