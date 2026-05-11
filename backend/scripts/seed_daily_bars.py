"""
一次性全量回填日K线脚本。

遍历全A股，每只拉取最近 N 个交易日的日K线，写入 DailyBar 表并自动计算技术指标。
支持断点续传、速率控制、多源 fallback。

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
from app.models.tables import DailyBar, UnifiedQuote
from app.services.market_data import (
    _fetch_akshare_kline,
    _fetch_baostock_kline,
    _fetch_eastmoney_kline,
    _fetch_eastmoney_quotes,
    _fetch_tencent_kline,
    _exchange_from_code,
)
from app.services.market_pool import _latest_allowed_bar_date, _persist_normalized_bars

COMMIT_INTERVAL = 50
RATE_LIMIT_SECONDS = 0.35
SEED_KLINE_COUNT = 400  # 腾讯API每次请求的K线条数（约覆盖250个交易日）
PROGRESS_FILE = Path(__file__).resolve().parent / ".seed_daily_bars_progress.txt"


def load_progress() -> set[str]:
    if not PROGRESS_FILE.exists():
        return set()
    return set(PROGRESS_FILE.read_text(encoding="utf-8").strip().splitlines())


def save_progress(code: str) -> None:
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{code}\n")


def fetch_bars(code: str, name: str, settings: object) -> list[dict]:
    """拉取日K线，腾讯优先（最稳定），失败则 fallback 到其他源。"""
    exchange = _exchange_from_code(code)
    quote = {"code": code, "name": name, "exchange": exchange}

    # 1. 腾讯 — 最可靠的免费源
    try:
        saved_days = settings.market_kline_days
        settings.market_kline_days = SEED_KLINE_COUNT
        bars = _fetch_tencent_kline(quote, settings)
        settings.market_kline_days = saved_days
        if len(bars) >= 30:
            return bars
    except Exception:
        pass

    # 2. 东方财富
    try:
        start_date = (date.today() - timedelta(days=int(SEED_KLINE_COUNT * 1.5))).strftime("%Y%m%d")
        bars = _fetch_eastmoney_kline(quote, settings, start_date=start_date)
        if len(bars) >= 30:
            return bars
    except Exception:
        pass

    # 3. Akshare
    try:
        start_date = (date.today() - timedelta(days=int(SEED_KLINE_COUNT * 1.5))).strftime("%Y%m%d")
        bars = _fetch_akshare_kline(quote, settings, start_date=start_date)
        if len(bars) >= 30:
            return bars
    except Exception:
        pass

    # 4. Baostock
    try:
        start_date = (date.today() - timedelta(days=int(SEED_KLINE_COUNT * 1.5))).strftime("%Y%m%d")
        bars = _fetch_baostock_kline(quote, settings, start_date=start_date)
        if len(bars) >= 30:
            return bars
    except Exception:
        pass

    return []


def main() -> None:
    settings = get_settings()
    seed_days = getattr(settings, "market_kline_seed_days", 250)

    print(f"种子日K回填脚本启动")
    print(f"  交易日数目标: {seed_days} 天")
    print(f"  每只拉取: {SEED_KLINE_COUNT} 条K线（腾讯优先）")
    print(f"  速率限制: {RATE_LIMIT_SECONDS}s / 只")
    print()

    # 获取全A股代码列表 — 优先东财API，失败则用本地数据库
    codes: list[tuple[str, str]] = []
    print("正在获取股票代码列表...")
    try:
        quotes = _fetch_eastmoney_quotes(settings)
        codes = [(q["code"], q.get("name", "")) for q in quotes if q.get("code")]
        print(f"  从东财API获取到 {len(codes)} 只")
    except Exception as exc:
        print(f"  东财API不可用: {exc}")

    if not codes:
        print("  尝试从本地数据库获取...")
        db_temp = SessionLocal()
        try:
            db_codes = db_temp.scalars(
                select(UnifiedQuote.code).distinct().order_by(UnifiedQuote.code)
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

    # 加载进度
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

            # 检查本地已有条数
            existing_count = db.scalar(
                select(func.count()).select_from(DailyBar).where(DailyBar.code == code)
            ) or 0
            if existing_count >= seed_days * 0.8:
                done_codes.add(code)
                save_progress(code)
                skip_count += 1
                continue

            # 拉取（腾讯优先，自动 fallback）
            bars = fetch_bars(code, name, settings)
            if not bars:
                fail_count += 1
                continue

            # 持久化
            try:
                _persist_normalized_bars(db, bars)
                success_count += 1
            except Exception as exc:
                fail_count += 1
                # 回滚失败的这只会话，继续下一只
                db.rollback()
                continue

            save_progress(code)
            done_codes.add(code)

            # 每 COMMIT_INTERVAL 只提交一次
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

            # 速率限制
            time.sleep(RATE_LIMIT_SECONDS)

        # 最终提交
        db.commit()
        elapsed = time.perf_counter() - start_time
        print()
        print(f"种子回填完成！用时 {elapsed:.0f}s")
        print(f"  总计: {total} 只")
        print(f"  成功: {success_count} 只")
        print(f"  跳过: {skip_count} 只")
        print(f"  失败: {fail_count} 只")

        # 统计最终覆盖
        code_count = db.scalar(select(func.count(func.distinct(DailyBar.code)))) or 0
        bar_count = db.scalar(select(func.count()).select_from(DailyBar)) or 0
        latest_bar = _latest_allowed_bar_date()
        print()
        print(f"DailyBar 表现状:")
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
