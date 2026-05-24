"""
一次性全量回填日K线脚本（Tushare-only）。
遍历全A股，每只拉取最近 N 个交易日的日K线（含技术指标和筹码分布），写入 DailyKbar 表。
支持断点续传、速率控制。

Usage: python scripts/seed_daily_bars.py
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, StockQuote
from app.services.tushare_client import fetch_daily_kbars, fetch_stock_list
from app.services.market_pool import _latest_allowed_bar_date, _persist_normalized_bars

COMMIT_INTERVAL = 50
RATE_LIMIT_SECONDS = 0.08
SEED_KLINE_COUNT = 400
PROGRESS_FILE = Path(__file__).resolve().parent / ".seed_daily_bars_progress.txt"


def load_progress() -> set[str]:
    if not PROGRESS_FILE.exists():
        return set()
    return set(PROGRESS_FILE.read_text(encoding="utf-8").strip().splitlines())


def save_progress(code: str) -> None:
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{code}\n")


def main() -> None:
    settings = get_settings()
    seed_days = getattr(settings, "market_kline_seed_days", 250)

    print(f"种子日K回填脚本启动")
    print(f"  交易日数目标: {seed_days} 天")
    print(f"  每只拉取: {SEED_KLINE_COUNT} 条K线（Tushare）")
    print(f"  速率限制: {RATE_LIMIT_SECONDS}s / 只")
    print()

    codes: list[tuple[str, str]] = []
    print("正在获取股票代码列表...")
    try:
        stocks = fetch_stock_list()
        codes = [(s["code"], s.get("name", "")) for s in stocks if s.get("code")]
        print(f"  从Tushare获取到 {len(codes)} 只")
    except Exception as exc:
        print(f"  Tushare不可用: {exc}")

    if not codes:
        print("  尝试从本地数据库获取...")
        db_temp = SessionLocal()
        try:
            db_codes = db_temp.scalars(
                select(StockQuote.code).distinct().order_by(StockQuote.code)
            ).all()
            codes = [(str(c), "") for c in db_codes if c]
            print(f"  从本地数据库获取到 {len(codes)} 只")
        finally:
            db_temp.close()

    if not codes:
        print("无法获取股票代码列表，退出。")
        return
    total = len(codes)
    print(f"获取到 {total} 只股票")
    print()

    done_codes = load_progress()
    if done_codes:
        print(f"已从进度文件恢复: {len(done_codes)} 只已完成")
        print()

    db = SessionLocal()
    try:
        success_count = 0
        skip_count = 0
        fail_count = 0
        start_time = time.perf_counter()

        for idx, (code, name) in enumerate(codes):
            if code in done_codes:
                skip_count += 1
                continue

            existing_count = db.scalar(
                select(func.count()).select_from(DailyKbar).where(DailyKbar.code == code)
            ) or 0
            if existing_count >= seed_days * 0.8:
                done_codes.add(code)
                save_progress(code)
                skip_count += 1
                continue

            try:
                bars = fetch_daily_kbars(code, days=SEED_KLINE_COUNT)
            except Exception:
                bars = []

            if not bars or len(bars) < 30:
                fail_count += 1
                continue

            try:
                _persist_normalized_bars(db, bars)
                success_count += 1
            except Exception:
                fail_count += 1
                db.rollback()
                continue

            save_progress(code)
            done_codes.add(code)

            if (idx + 1) % COMMIT_INTERVAL == 0:
                db.commit()
                elapsed = time.perf_counter() - start_time
                done = success_count + skip_count + fail_count
                pct = done / total * 100 if total else 0
                rate = done / elapsed if elapsed > 0 else 0
                print(
                    f"进度: {done}/{total} ({pct:.1f}%) | "
                    f"成功 {success_count} 跳过 {skip_count} 失败 {fail_count} | "
                    f"{rate:.1f} 只/秒 | "
                    f"{elapsed:.0f}s"
                )

            time.sleep(RATE_LIMIT_SECONDS)

        db.commit()
        elapsed = time.perf_counter() - start_time
        print()
        print(f"种子回填完成！用时 {elapsed:.0f}s")
        print(f"  总计: {total} 只")
        print(f"  成功: {success_count} 只")
        print(f"  跳过: {skip_count} 只")
        print(f"  失败: {fail_count} 只")

        code_count = db.scalar(select(func.count(func.distinct(DailyKbar.code)))) or 0
        bar_count = db.scalar(select(func.count()).select_from(DailyKbar)) or 0
        latest_bar = _latest_allowed_bar_date()
        print()
        print(f"DailyKbar 表现状:")
        print(f"  去重代码数: {code_count}")
        print(f"  总行数: {bar_count}")
        print(f"  今日最新交易日: {latest_bar}")

        if success_count + skip_count == total:
            PROGRESS_FILE.unlink(missing_ok=True)
            print()
            print("全部完成，进度文件已清除。")

    finally:
        db.close()


if __name__ == "__main__":
    main()
