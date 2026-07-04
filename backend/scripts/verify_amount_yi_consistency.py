"""校验并修复 stock_quotes.amount_yi 与 daily_kbars.amount 的一致性。

背景：
    stock_quotes.amount_yi 应为成交额（亿元），与 daily_kbars.amount（元）/1e8 一致。
    但多数据源（realtime vs daily）单位假设不一致，导致约 45% 行的 amount_yi 比真实值少 10 倍。
    这让 limit-up-reseal-relay 等策略的 `amount_yi >= 3` 门槛错误地挡掉大成交额涨停股。

用法：
    python -m scripts.verify_amount_yi_consistency          # 仅诊断
    python -m scripts.verify_amount_yi_consistency --fix    # 修复差 10 倍的行
"""
from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from sqlalchemy import text


def diagnose(db) -> dict:
    """统计 amount_yi 与 daily.amount/1e8 的一致性。"""
    row = db.execute(
        text(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN ABS(sq.amount_yi - dk.amount/1e8) < 0.1 THEN 1 ELSE 0 END) AS consistent,
              SUM(CASE WHEN ABS(sq.amount_yi*10 - dk.amount/1e8) < 0.1 AND sq.amount_yi > 0
                       AND ABS(sq.amount_yi - dk.amount/1e8) >= 0.1 THEN 1 ELSE 0 END) AS off_by_10,
              SUM(CASE WHEN sq.amount_yi = 0 AND dk.amount > 0 THEN 1 ELSE 0 END) AS zero
            FROM stock_quotes sq
            JOIN daily_kbars dk ON dk.code=sq.code AND dk.trade_date=sq.trade_date
            WHERE dk.amount > 0
            """
        )
    ).fetchone()
    return {"total": row[0], "consistent": row[1], "off_by_10": row[2], "zero": row[3]}


def fix(db) -> int:
    """修复差 10 倍的行：用 daily.amount/1e8 覆盖 stock_quotes.amount_yi。"""
    result = db.execute(
        text(
            """
            UPDATE stock_quotes sq
            JOIN daily_kbars dk ON dk.code=sq.code AND dk.trade_date=sq.trade_date
            SET sq.amount_yi = ROUND(dk.amount/1e8, 4)
            WHERE dk.amount > 0
              AND sq.amount_yi > 0
              AND ABS(sq.amount_yi*10 - dk.amount/1e8) < 0.1
              AND ABS(sq.amount_yi - dk.amount/1e8) >= 0.1
            """
        )
    )
    db.commit()
    return result.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="修复差 10 倍的行（默认仅诊断）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        before = diagnose(db)
        print(f"修复前: 共 {before['total']}, 一致 {before['consistent']}, 差10倍 {before['off_by_10']}, 零值 {before['zero']}")

        if args.fix:
            n = fix(db)
            print(f"修复行数: {n}")
            after = diagnose(db)
            print(f"修复后: 共 {after['total']}, 一致 {after['consistent']}, 差10倍 {after['off_by_10']}, 零值 {after['zero']}")
        else:
            print("（仅诊断，加 --fix 执行修复）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
