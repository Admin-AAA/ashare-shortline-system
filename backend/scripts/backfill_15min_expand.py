"""
15分钟K线批量扩充：从现有 ~173 只扩展到 500+ 只。

策略：
  - 读取所有有日K数据的活跃股票 (>=2026-04-01)
  - 跳过已存在 15min 数据的股票
  - 按 --batch-size (默认 50) 分批回填
  - 每批结束输出进度，支持 ctrl+c 后断点续跑
  - 跑完后打印覆盖率对比

使用方法:
    python scripts/backfill_15min_expand.py --batch-size 50 --delay 0.5
    python scripts/backfill_15min_expand.py --batch-size 50 --resume   # 启用断点续跑
"""

import argparse
import logging
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import DailyKbar, MinuteKbar
from scripts.tushare_init import get_pro

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_15min_expand")


def get_all_active_codes(db, min_date="2026-04-01") -> list[str]:
    """获取有日K数据的活跃股票代码，按 code 排序以保证顺序稳定。"""
    rows = db.execute(
        select(DailyKbar.code)
        .where(DailyKbar.trade_date >= min_date)
        .distinct()
        .order_by(DailyKbar.code)
    ).scalars().all()
    logger.info("活跃股票总数: %d (>= %s)", len(rows), min_date)
    return list(rows)


def get_existing_15min_codes(db) -> set[str]:
    """获取已有 15min 数据的股票 code 集合。"""
    rows = db.execute(
        select(MinuteKbar.code)
        .where(MinuteKbar.freq == "15min")
        .distinct()
    ).scalars().all()
    return set(rows)


def stock_ts_code(code: str) -> str:
    if code.startswith("6"):
        return f"{code}.SH"
    elif code.startswith("8") or code.startswith("4"):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def backfill_one(pro, db, code: str) -> dict:
    """回填单只股票的 15min 数据。返回 {fetched, stored, errors}。"""
    ts_code = stock_ts_code(code)
    result = {"code": code, "fetched": 0, "stored": 0, "errors": 0}

    try:
        r = pro.stk_mins(ts_code=ts_code, freq="15min")
        if r is None or r.empty:
            return result

        result["fetched"] = len(r)
        records = []
        for _, row in r.iterrows():
            try:
                trade_time = row["trade_time"]
                if isinstance(trade_time, str):
                    dt = datetime.fromisoformat(trade_time) if "T" in trade_time or len(trade_time) == 19 else datetime.strptime(trade_time, "%Y-%m-%d %H:%M:%S")
                else:
                    dt = trade_time.to_pydatetime()

                def safe_float(v, default=0.0) -> float:
                    """安全转 float，NaN/None 用 default。"""
                    if v is None:
                        return default
                    f = float(v)
                    return default if math.isnan(f) else f

                records.append({
                    "code": code,
                    "freq": "15min",
                    "trade_date": dt.strftime("%Y-%m-%d"),
                    "trade_time": dt,
                    "open": safe_float(row["open"]),
                    "close": safe_float(row["close"]),
                    "high": safe_float(row["high"]),
                    "low": safe_float(row["low"]),
                    "volume": safe_float(row.get("vol")),
                    "amount": safe_float(row.get("amount")),
                })
            except Exception:
                result["errors"] += 1

        if records:
            stmt = mysql_insert(MinuteKbar).values(records)
            stmt = stmt.on_duplicate_key_update(id=MinuteKbar.id)
            db.execute(stmt)
            db.commit()
            result["stored"] = len(records)

    except Exception as e:
        logger.error("  %s 失败: %s", code, str(e)[:80])
        db.rollback()
        result["errors"] += 1

    return result


def get_current_coverage(db) -> dict:
    """获取当前 15min 数据覆盖统计。"""
    stocks = db.scalar(
        select(func.count(func.distinct(MinuteKbar.code)))
        .where(MinuteKbar.freq == "15min")
    ) or 0
    rows = db.scalar(
        select(func.count()).select_from(MinuteKbar)
        .where(MinuteKbar.freq == "15min")
    ) or 0
    dates = db.scalar(
        select(func.count(func.distinct(MinuteKbar.trade_date)))
        .where(MinuteKbar.freq == "15min")
    ) or 0
    return {"stocks": stocks, "rows": rows, "dates": dates}


def progress_file_path() -> Path:
    return Path(__file__).resolve().parent / ".backfill_15min_progress"


def save_progress(idx: int, total: int):
    """保存当前处理到的索引位置。"""
    pf = progress_file_path()
    pf.write_text(f"{idx}\n{total}\n{datetime.now().isoformat()}\n")


def load_progress() -> int | None:
    """读取上次保存的处理位置。返回 None 代表无进度或格式无效。"""
    pf = progress_file_path()
    if not pf.exists():
        return None
    try:
        lines = pf.read_text().strip().split("\n")
        idx = int(lines[0])
        total = int(lines[1])
        logger.info("发现断点进度: %d/%d (保存于 %s)", idx, total, lines[2])
        return idx
    except (IndexError, ValueError, OSError):
        return None


def main():
    parser = argparse.ArgumentParser(description="15分钟K线批量扩充")
    parser.add_argument("--batch-size", type=int, default=50, help="每批股票数 (默认 50)")
    parser.add_argument("--delay", type=float, default=0.5, help="API 调用间隔 (秒)")
    parser.add_argument("--resume", action="store_true", help="从断点续跑")
    args = parser.parse_args()

    pro = get_pro()
    db = SessionLocal()

    try:
        # 1. 获取目标股票列表（排重）
        all_codes = get_all_active_codes(db)
        existing = get_existing_15min_codes(db)
        todo = [c for c in all_codes if c not in existing]
        logger.info("已有 15min 数据: %d 只, 待处理: %d 只", len(existing), len(todo))

        if not todo:
            logger.info("没有需要处理的股票，全部已完成！")
            return

        # 2. 确定起始位置
        start_idx = 0
        if args.resume:
            saved = load_progress()
            if saved is not None and saved < len(todo):
                start_idx = saved
                logger.info("从第 %d 只股票 (code=%s) 继续", start_idx, todo[start_idx])

        # 3. 分批回填
        batch_size = args.batch_size
        total = len(todo)
        t0 = time.time()
        grand = {"fetched": 0, "stored": 0, "errors": 0}

        for batch_start in range(start_idx, total, batch_size):
            batch = todo[batch_start:batch_start + batch_size]
            batch_fetched = 0

            for j, code in enumerate(batch):
                idx = batch_start + j
                r = backfill_one(pro, db, code)
                grand["fetched"] += r["fetched"]
                grand["stored"] += r["stored"]
                grand["errors"] += r["errors"]
                batch_fetched += r["fetched"]

                # 保存进度
                save_progress(idx + 1, total)

                elapsed = time.time() - t0
                rate = (idx + 1) / elapsed if elapsed > 0 else 0
                remaining = total - idx - 1
                eta = remaining / rate if rate > 0 else 0

                print(
                    f"\r  进度 {idx+1}/{total} ({100*(idx+1)/total:.1f}%) "
                    f"| {r['code']} rows={r['fetched']} "
                    f"| 累计: 命中{grand['fetched']} 入库{grand['stored']} 错误{grand['errors']} "
                    f"| {rate:.1f}只/s | ETA {eta:.0f}s",
                    end="", flush=True
                )

                time.sleep(args.delay)

            print()

            # 每批结束打印当前覆盖率
            cov = get_current_coverage(db)
            logger.info(
                "批完成 (idx %d-%d) | 当前 15min 覆盖: %d 只, %d 行, %d 天",
                batch_start + 1, min(batch_start + batch_size, total),
                cov["stocks"], cov["rows"], cov["dates"],
            )

        # 4. 最终状态
        elapsed = time.time() - t0
        final = get_current_coverage(db)
        logger.info("=" * 60)
        logger.info("回填完成！耗时 %.0fs (%.1f 分钟)", elapsed, elapsed / 60)
        logger.info("  处理股票: %d 只", min(total, final["stocks"] - len(existing)))
        logger.info("  拉取行数: %d | 入库: %d | 错误: %d",
                     grand["fetched"], grand["stored"], grand["errors"])
        logger.info("  最终 15min 覆盖: %d 只, %d 行, %d 天",
                     final["stocks"], final["rows"], final["dates"])
        logger.info("  增长率: +%d 只 (%.1f%%)",
                     final["stocks"] - len(existing),
                     (final["stocks"] - len(existing)) / len(existing) * 100 if existing else 0)

        # 清理进度文件
        pf = progress_file_path()
        if pf.exists():
            pf.unlink()

    finally:
        db.close()


if __name__ == "__main__":
    main()
