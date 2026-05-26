"""
每日数据维护 cron 脚本。

工作流：
  1. 回填最近 5 天缺失的辅助数据（资金流向、概念板块、涨停板、指数日线）
  2. 标注待处理推荐结果
  3. 输出数据健康报告

安装 crontab（每天 9:30 开盘前执行）：
    30 9 * * 1-5 cd /opt/ashare-shortline-system/backend && python scripts/daily_data_cron.py >> logs/daily_cron.log 2>&1
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from datetime import date, timedelta

from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from scripts.check_data_health import main as check_health

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/daily_cron.log", mode="a"),
    ],
)
logger = logging.getLogger("daily_cron")


def backfill_auxiliary_data(db, pro):
    """回填辅助数据。"""
    from sqlalchemy import func, select
    from app.models.tables import CapitalFlow, LimitUpBoard, IndexDaily, ConceptSector
    from scripts.backfill_moneyflow_concept import backfill_moneyflow, backfill_concept_sector, compute_cumulative_inflows

    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=10)).isoformat()
    results = {}

    # 1. 资金流向
    r = backfill_moneyflow(pro, db, start_date, end_date)
    if r.get("stored", 0) > 0:
        compute_cumulative_inflows(db)
    results["capital_flow"] = r
    logger.info("  capital_flow: +%d", r.get("stored", 0))

    # 2. 概念板块（每周刷新一次，用当前日期作为 trade_date）
    latest = db.scalar(select(func.max(ConceptSector.trade_date)))
    if not latest or latest < end_date:
        r = backfill_concept_sector(pro, db)
        results["concept_sector"] = r
        logger.info("  concept_sector: +%d", r.get("stored", 0))

    # 3. 涨停板（Tushare limit_list_d）
    latest_lu = db.scalar(select(func.max(LimitUpBoard.trade_date)))
    if not latest_lu or latest_lu < end_date:
        from scripts.backfill_auxiliary import backfill_limit_up
        end_d = end_date
        start_d = (date.today() - timedelta(days=5)).isoformat()
        r = backfill_limit_up(pro, db, start_d, end_d)
        results["limit_up_board"] = r
        logger.info("  limit_up_board: +%d", r.get("stored", 0))

    # 4. 指数日线
    latest_ix = db.scalar(select(func.max(IndexDaily.trade_date)))
    if not latest_ix or latest_ix < end_date:
        from scripts.backfill_auxiliary import backfill_index_daily
        r = backfill_index_daily(pro, db, start_date, end_date)
        results["index_daily"] = r
        logger.info("  index_daily: +%d", r.get("stored", 0))

    return results


def main():
    logger.info("=" * 60)
    logger.info("每日数据维护开始: %s", date.today())

    # 检查今天是否交易日
    db = SessionLocal()
    pro = None
    try:
        recent = db.scalar(
            select(DailyKbar.trade_date)
            .order_by(DailyKbar.trade_date.desc())
            .limit(1)
        )
        if not recent:
            logger.warning("本地无日K数据，跳过")
            return

        from scripts.tushare_init import get_pro
        pro = get_pro()

        # Step 1: 回填
        logger.info("[Step 1/3] 回填辅助数据...")
        results = backfill_auxiliary_data(db, pro)

        # Step 2: 结果标注
        logger.info("[Step 2/3] 标注推荐结果...")
        from app.services.outcome_service import label_pending_outcomes
        r = label_pending_outcomes(db, horizon_days=20)
        logger.info("  标注: 新增 %d", r.get("new_labeled", r.get("labeled", 0)))

        # Step 3: 健康检查
        logger.info("[Step 3/3] 数据健康检查...")
        check_health()

        logger.info("每日数据维护完成")
    except Exception as e:
        logger.exception("维护异常: %s", e)
    finally:
        if pro and hasattr(pro, 'close'):
            pro.close()
        db.close()


if __name__ == "__main__":
    main()
