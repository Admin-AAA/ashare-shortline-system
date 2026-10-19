"""
补齐 daily_kbars 中本地可计算的指标（MA/量比/乖离/突破/回撤等）。

参数化日期：默认补最近 N 天缺失指标的交易日。
同时处理 ma5 IS NULL 和 ma5 = 0 两种情况（批量同步写入 0，逐股票写入 NULL）。

仅补本地可算指标；MACD/RSI/KDJ/BOLL/CCI 来自 Tushare stk_factor，需要完整同步补齐。

历史坑（2026-07-21 发现）：scheduler 只补近 7 天 → 2023-2025 ma5/ma20 全 NULL。
生产 formal 走 market_data 读时兜底不受影响；但 matrix/backtest/T1 脚本直接读列会静默失真。
本脚本现支持 --all / --start/--end 高效按 code 批量回填（用 indicator_computer）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
from datetime import date, timedelta

from sqlalchemy import bindparam, text

from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.services.indicator_computer import (
    bias,
    compute_daily_bar_indicators,
    max_drawdown,
    upper_shadow_count,
)


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

    fixed = {"ma": 0, "vol": 0, "bias": 0, "breakout": 0, "dd": 0, "upper_shadow": 0}

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

        # 20 日长上影次数：同步侧永远写 0（Tushare 不返回本地派生指标），且 L-144 已把它
        # 从 upsert 的 update 列表摘掉交给本脚本独占写入。它是近 20 根 OHLC 的确定性函数，
        # 0 也是合法值（无长上影），因此无条件重算覆盖，不做「缺失才补」判定（L-146）。
        target.upper_shadow_count20 = upper_shadow_count(
            [
                {"open": b.open, "high": b.high, "low": b.low, "close": b.close}
                for b in bars[-20:]
            ]
        )
        fixed["upper_shadow"] += 1

    db.commit()
    return fixed


def backfill_history(
    db,
    *,
    start: str | None = None,
    end: str | None = None,
    code_limit: int | None = None,
    batch_codes: int = 50,
) -> dict:
    """按 code 批量回填历史本地指标（ma/vol_ratio/bias/breakout/max_dd）。

    比逐日 backfill_date 快一个数量级：每只股票一次读全历史 OHLCV，内存算完全部日期，
    再 executemany 写回。适合修 2023-2025 ma 全 NULL 的存量坑。
    """
    where = ["1=1"]
    params: dict = {}
    if start:
        where.append("trade_date >= :start")
        params["start"] = start
    if end:
        where.append("trade_date <= :end")
        params["end"] = end
    # 只挑在范围内有 ma20 缺失的 code，避免全市场空跑
    code_sql = f"""
        SELECT DISTINCT code FROM daily_kbars
        WHERE {' AND '.join(where)}
          AND (ma20 IS NULL OR ma20 = 0)
        ORDER BY code
    """
    codes = list(db.execute(text(code_sql), params).scalars().all())
    if code_limit:
        codes = codes[:code_limit]
    if not codes:
        return {"codes": 0, "rows_updated": 0, "message": "无缺失 code"}

    total_updated = 0
    for i in range(0, len(codes), batch_codes):
        chunk = codes[i:i + batch_codes]
        # 读这些 code 的全量 K 线（含 lookback 窗口：start 前 60 天，保证 ma60 有样本）
        lookback_start = start
        if start:
            # 粗略往前多取 120 日历天；不足时 ma 会用短序列兜底
            from datetime import datetime as _dt
            d0 = _dt.strptime(start, "%Y-%m-%d").date() - timedelta(days=120)
            lookback_start = d0.isoformat()
        load_stmt = text("""
            SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover_rate,
                   ma5, ma10, ma20, ma60, vol_ratio_5, vol_ratio_20,
                   bias_5, bias_20, breakout20, breakout60, max_dd20, holder_pct
            FROM daily_kbars
            WHERE code IN :codes
              AND (:lookback_start IS NULL OR trade_date >= :lookback_start)
              AND (:end IS NULL OR trade_date <= :end)
            ORDER BY code, trade_date
        """).bindparams(bindparam("codes", expanding=True))
        rows = db.execute(load_stmt, {
            "codes": list(chunk),
            "lookback_start": lookback_start,
            "end": end,
        }).mappings().all()

        by_code: dict[str, list[dict]] = {}
        for r in rows:
            by_code.setdefault(r["code"], []).append(dict(r))

        updates: list[dict] = []
        for code, bars in by_code.items():
            # 转 float 供 compute
            dict_bars = []
            for b in bars:
                dict_bars.append({
                    "trade_date": str(b["trade_date"]),
                    "open": float(b["open"] or 0),
                    "high": float(b["high"] or 0),
                    "low": float(b["low"] or 0),
                    "close": float(b["close"] or 0),
                    "volume": float(b["volume"] or 0),
                })
            computed = compute_daily_bar_indicators(code, dict_bars)
            closes = [float(b["close"] or 0) for b in bars]
            for idx, b in enumerate(bars):
                td = str(b["trade_date"])
                # 只写目标窗口内的缺失行
                if start and td < start:
                    continue
                if end and td > end:
                    continue
                need = (
                    b["ma20"] is None or b["ma20"] == 0
                    or b["ma5"] is None or b["ma5"] == 0
                )
                # upper_shadow_count20 由本脚本独占写入（L-144 把它从同步 upsert 的
                # update 列表摘掉）。它是近 20 根 OHLC 的确定性函数，且同步侧永远写 0，
                # 因此窗口内每一行都重算覆盖，不受 need 门控（L-146）。
                shadow_window = dict_bars[max(0, idx - 19):idx + 1]
                upper_shadow20 = upper_shadow_count(shadow_window)
                if not need:
                    updates.append({
                        "code": code,
                        "td": td,
                        "ma5": None,
                        "ma10": None,
                        "ma20": None,
                        "ma60": None,
                        "vol_ratio_5": None,
                        "bias_5": None,
                        "bias_20": None,
                        "breakout20": None,
                        "breakout60": None,
                        "max_dd20": None,
                        "holder_pct": None,
                        "upper_shadow_count20": upper_shadow20,
                    })
                    continue
                c = computed.get(td) or {}
                ma5 = c.get("ma5")
                ma10 = c.get("ma10")
                ma20 = c.get("ma20")
                ma60 = c.get("ma60")
                vol5 = c.get("volume_ratio_5d")
                # bias / breakout / max_dd 从 ma 派生
                close = closes[idx]
                b5 = round(bias(close, ma5), 4) if ma5 else None
                b20 = round(bias(close, ma20), 4) if ma20 else None
                br20 = 1 if ma20 and close > ma20 else 0
                br60 = 1 if ma60 and close > ma60 else 0
                window = closes[max(0, idx - 19):idx + 1]
                dd20 = round(max_drawdown(window), 4) if len(window) >= 5 else None
                holder = round(bias(close, ma60), 2) if ma60 else None
                updates.append({
                    "code": code,
                    "td": td,
                    "ma5": ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "ma60": ma60,
                    "vol_ratio_5": vol5,
                    "bias_5": b5,
                    "bias_20": b20,
                    "breakout20": br20,
                    "breakout60": br60,
                    "max_dd20": dd20,
                    "holder_pct": holder,
                    "upper_shadow_count20": upper_shadow20,
                })

        if updates:
            upd_stmt = text("""
                UPDATE daily_kbars SET
                  ma5 = COALESCE(:ma5, ma5),
                  ma10 = COALESCE(:ma10, ma10),
                  ma20 = COALESCE(:ma20, ma20),
                  ma60 = COALESCE(:ma60, ma60),
                  vol_ratio_5 = COALESCE(:vol_ratio_5, vol_ratio_5),
                  bias_5 = COALESCE(:bias_5, bias_5),
                  bias_20 = COALESCE(:bias_20, bias_20),
                  breakout20 = COALESCE(:breakout20, breakout20),
                  breakout60 = COALESCE(:breakout60, breakout60),
                  max_dd20 = COALESCE(:max_dd20, max_dd20),
                  holder_pct = COALESCE(:holder_pct, holder_pct),
                  upper_shadow_count20 = COALESCE(:upper_shadow_count20, upper_shadow_count20),
                  updated_at = UTC_TIMESTAMP()
                WHERE code = :code AND trade_date = :td
            """)
            for u in updates:
                db.execute(upd_stmt, u)
            db.commit()
            total_updated += len(updates)
        print(f"  codes {i+1}-{min(i+batch_codes, len(codes))}/{len(codes)} updated_rows={total_updated}")

    return {"codes": len(codes), "rows_updated": total_updated}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="回看天数（默认模式）")
    parser.add_argument("--date", type=str, default=None, help="指定单日 (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="全历史回填（按 code 批量）")
    parser.add_argument("--start", type=str, default=None, help="历史回填起点 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="历史回填终点 YYYY-MM-DD")
    parser.add_argument("--code-limit", type=int, default=None, help="最多处理 N 只 code（试跑）")
    parser.add_argument("--batch-codes", type=int, default=50, help="每批 code 数")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.all or args.start or args.end:
            print(f"历史批量回填 start={args.start} end={args.end} code_limit={args.code_limit}")
            stats = backfill_history(
                db,
                start=args.start,
                end=args.end,
                code_limit=args.code_limit,
                batch_codes=args.batch_codes,
            )
            print(f"完成: {stats}")
            return

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
