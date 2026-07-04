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
    """统计 amount_yi 与 daily.amount/1e8 的一致性。

    用相对误差（5%）判定，避免小成交额股票的绝对误差误判。
    只对大成交额（>=0.5亿）判定差 10 倍，小成交额单独统计。
    """
    row = db.execute(
        text(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN dk.amount/1e8 >= 0.5
                       AND ABS(sq.amount_yi - dk.amount/1e8)/NULLIF(dk.amount/1e8,0) < 0.05
                       THEN 1 ELSE 0 END) AS consistent_big,
              SUM(CASE WHEN dk.amount/1e8 >= 0.5
                       AND ABS(sq.amount_yi*10 - dk.amount/1e8)/NULLIF(dk.amount/1e8,0) < 0.05
                       AND ABS(sq.amount_yi - dk.amount/1e8)/NULLIF(dk.amount/1e8,0) >= 0.05
                       THEN 1 ELSE 0 END) AS off_by_10_big,
              SUM(CASE WHEN dk.amount/1e8 < 0.5 THEN 1 ELSE 0 END) AS small_amount
            FROM stock_quotes sq
            JOIN daily_kbars dk ON dk.code=sq.code AND dk.trade_date=sq.trade_date
            WHERE dk.amount > 0 AND sq.amount_yi > 0
            """
        )
    ).fetchone()
    return {"total": row[0], "consistent_big": row[1], "off_by_10_big": row[2], "small_amount": row[3]}


def fix(db) -> int:
    """修复差 10 倍的行（大成交额，相对误差判定）：用 daily.amount/1e8 覆盖。"""
    result = db.execute(
        text(
            """
            UPDATE stock_quotes sq
            JOIN daily_kbars dk ON dk.code=sq.code AND dk.trade_date=sq.trade_date
            SET sq.amount_yi = ROUND(dk.amount/1e8, 4)
            WHERE dk.amount > 0
              AND sq.amount_yi > 0
              AND dk.amount/1e8 >= 0.5
              AND ABS(sq.amount_yi*10 - dk.amount/1e8)/NULLIF(dk.amount/1e8,0) < 0.05
              AND ABS(sq.amount_yi - dk.amount/1e8)/NULLIF(dk.amount/1e8,0) >= 0.05
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
        print(f"修复前: 共 {before['total']}, 大额一致 {before['consistent_big']}, 差10倍 {before['off_by_10_big']}, 小额 {before['small_amount']}")

        if args.fix:
            n = fix(db)
            print(f"修复行数: {n}")
            after = diagnose(db)
            print(f"修复后: 共 {after['total']}, 大额一致 {after['consistent_big']}, 差10倍 {after['off_by_10_big']}, 小额 {after['small_amount']}")
        else:
            print("（仅诊断，加 --fix 执行修复）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
