"""
数据质量自动检测：报告各表最新日期、缺口、覆盖率。

用于 cron 定时任务或手动巡检。

使用方法:
    python scripts/check_data_health.py
    python scripts/check_data_health.py --json
"""

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

from app.db.session import SessionLocal
from app.models.tables import (
    DailyKbar, MinuteKbar, Stock, StockQuote, LimitUpBoard,
    DragonTiger, ConceptSector, CapitalFlow, NewsAnnouncement,
    IndexDaily, Recommendation, RecommendationOutcome,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("data_health")

TODAY = date.today().isoformat()

# 全局缓存：总交易日数（取 daily_kbar 的全量覆盖范围）
_total_trading_days: int | None = None


def _load_total_trading_days(db) -> int:
    """加载参考交易日总数（从 daily_kbar 表中取 distinct trade_date 数）。"""
    global _total_trading_days
    if _total_trading_days is None:
        _total_trading_days = db.scalar(
            select(func.count(func.distinct(DailyKbar.trade_date)))
        ) or 1
    return _total_trading_days


def check_table(db, name: str, model, date_col: str, critical: bool = False) -> dict:
    """检查单表健康状态，含覆盖率百分比。"""
    try:
        total = db.scalar(select(func.count()).select_from(model)) or 0

        if date_col:
            latest = db.scalar(select(func.max(getattr(model, date_col))))
            earliest = db.scalar(select(func.min(getattr(model, date_col))))
            date_cnt = db.scalar(
                select(func.count(func.distinct(getattr(model, date_col))))
            ) or 0

            # 覆盖率 = 该表 distinct 日期数 / 总交易日数
            total_days = _load_total_trading_days(db)
            coverage_pct = round(date_cnt / total_days * 100, 1) if total_days > 0 else 0.0

            # 计算缺口天数
            days_behind = None
            if latest:
                latest_dt = date.fromisoformat(latest) if isinstance(latest, str) else latest
                days_behind = (date.today() - latest_dt).days if isinstance(latest_dt, date) else None
        else:
            latest = earliest = "N/A"
            date_cnt = 0
            days_behind = None
            coverage_pct = None

        # 健康判定
        if total == 0:
            health = "empty" if critical else "empty (non-critical)"
        elif days_behind is not None and days_behind <= 1:
            health = "healthy"
        elif days_behind is not None and days_behind <= 3:
            health = "stale (behind)"
        elif days_behind is not None:
            health = "stale (critical)"
        else:
            health = "healthy"

        return {
            "table": name,
            "critical": critical,
            "total_rows": total,
            "latest_date": str(latest) if latest else None,
            "earliest_date": str(earliest) if earliest else None,
            "date_count": date_cnt,
            "coverage_pct": coverage_pct,
            "days_behind": days_behind,
            "health": health,
        }
    except Exception as e:
        return {
            "table": name,
            "critical": critical,
            "error": str(e)[:100],
            "health": "error",
        }


def main():
    parser = argparse.ArgumentParser(description="数据质量检测")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        checks = [
            ("daily_kbar", DailyKbar, "trade_date", True),
            ("minute_kbar_1min", MinuteKbar, "trade_date", True),
            ("minute_kbar_15min", MinuteKbar, "trade_date", True),
            ("minute_kbar_30min", MinuteKbar, "trade_date", True),
            ("index_daily", IndexDaily, "trade_date", True),
            ("stock_quote", StockQuote, "trade_date", False),
            ("limit_up_board", LimitUpBoard, "trade_date", True),
            ("dragon_tiger", DragonTiger, "trade_date", False),
            ("concept_sector", ConceptSector, "trade_date", False),
            ("capital_flow", CapitalFlow, "trade_date", False),
            ("news", NewsAnnouncement, "announce_date", False),
            ("recommendation", Recommendation, "trade_date", True),
            ("recommendation_outcome", RecommendationOutcome, None, True),
            ("stocks", Stock, None, False),
        ]

        results = []
        for name, model, date_col, critical in checks:
            # 对于 MinuteKbar 带 freq 过滤
            if name.startswith("minute_kbar_"):
                freq = name.split("_")[-1]  # "1min", "15min", "30min"
                r = check_table_minute(db, name, model, freq, critical)
            else:
                r = check_table(db, name, model, date_col, critical)
            results.append(r)

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(f"{'表名':<22} {'状态':<20} {'总行数':>10} {'最新日期':>12} {'覆盖率':>8} {'落后天数':>8}")
            print("-" * 85)
            critical_alerts = []
            for r in results:
                health_icon = {
                    "healthy": "🟢",
                    "stale (behind)": "🟡",
                    "stale (critical)": "🔴",
                    "empty": "⚫",
                    "empty (non-critical)": "⚪",
                    "error": "❌",
                }.get(r.get("health", ""), "❓")

                cov = r.get("coverage_pct")
                cov_str = f"{cov:.1f}%" if cov is not None else " —  "

                print(f"{r['table']:<22} {health_icon} {r.get('health',''):<18} "
                      f"{r.get('total_rows',0):>10,} {str(r.get('latest_date','N/A')):>12} "
                      f"{cov_str:>8} {str(r.get('days_behind','')):>8}")

                if r.get("critical") and r.get("health", "").startswith("stale"):
                    critical_alerts.append(r)

            if critical_alerts:
                print(f"\n⚠️  关键表数据滞后 {len(critical_alerts)} 项：")
                for r in critical_alerts:
                    print(f"  - {r['table']}: 最新 {r['latest_date']}, 落后 {r['days_behind']} 天")

        # 汇总
        healthy = sum(1 for r in results if r.get("health") == "healthy")
        stale = sum(1 for r in results if r.get("health", "").startswith("stale"))
        errors = sum(1 for r in results if r.get("health") == "error")
        logger.info("数据健康: %d 健康 / %d 滞后 / %d 错误", healthy, stale, errors)

    finally:
        db.close()


def check_table_minute(db, name: str, model, freq: str, critical: bool) -> dict:
    """检查带 freq 过滤的分钟表，含覆盖率。"""
    try:
        total = db.scalar(
            select(func.count()).select_from(model).where(model.freq == freq)
        ) or 0

        latest = db.scalar(
            select(func.max(model.trade_date)).where(model.freq == freq)
        )
        earliest = db.scalar(
            select(func.min(model.trade_date)).where(model.freq == freq)
        )
        date_cnt = db.scalar(
            select(func.count(func.distinct(model.trade_date)))
            .where(model.freq == freq)
        ) or 0
        stocks = db.scalar(
            select(func.count(func.distinct(model.code)))
            .where(model.freq == freq)
        ) or 0

        # 覆盖率
        total_days = _load_total_trading_days(db)
        coverage_pct = round(date_cnt / total_days * 100, 1) if total_days > 0 else 0.0

        days_behind = None
        if latest:
            latest_dt = date.fromisoformat(latest) if isinstance(latest, str) else latest
            days_behind = (date.today() - latest_dt).days

        if total == 0:
            health = "empty" if critical else "empty (non-critical)"
        elif days_behind is not None and days_behind <= 1:
            health = "healthy"
        elif days_behind is not None and days_behind <= 3:
            health = "stale (behind)"
        elif days_behind is not None:
            health = "stale (critical)"
        else:
            health = "healthy"

        return {
            "table": f"{name} ({stocks} stocks)",
            "critical": critical,
            "total_rows": total,
            "latest_date": str(latest) if latest else None,
            "earliest_date": str(earliest) if earliest else None,
            "date_count": date_cnt,
            "coverage_pct": coverage_pct,
            "days_behind": days_behind,
            "health": health,
        }
    except Exception as e:
        return {"table": name, "critical": critical, "error": str(e)[:100], "health": "error"}


if __name__ == "__main__":
    main()
