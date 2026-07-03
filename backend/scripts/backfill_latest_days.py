"""
快速回填最近缺失交易日 K 线。
使用 Tushare batch 接口按日期拉取，比逐股票快 1000 倍。
后接 outcome labeling + 基线指标计算。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.services.data_gap_remediation_service import _backfill_daily_kbar_date, _daily_kbar_target_codes
from app.services.data_health_service import (
    _MIN_RECENT_DAILY_KBAR_CODES,
    _fmt_date,
    _incomplete_daily_kbar_dates,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_latest")


def get_missing_trade_dates(db) -> list[str]:
    """检测本地缺了哪些交易日。"""
    latest_local = db.execute(select(func.max(DailyKbar.trade_date))).scalar()
    logger.info("本地最新日K日期: %s", latest_local)

    from app.services.tushare_client import fetch_latest_trade_date, get_pro
    tushare_latest_compact = fetch_latest_trade_date()
    if not tushare_latest_compact:
        logger.warning("无法获取 Tushare 最新交易日")
        return []
    tushare_latest = f"{tushare_latest_compact[:4]}-{tushare_latest_compact[4:6]}-{tushare_latest_compact[6:]}"
    logger.info("Tushare 最新交易日: %s", tushare_latest)

    pro = get_pro()
    start_compact = _start_trade_date(latest_local)
    end_compact = tushare_latest_compact
    cal = pro.trade_cal(exchange="SSE", start_date=start_compact, end_date=end_compact, is_open="1")
    if cal is None or cal.empty:
        return []

    all_dates = sorted(set(
        f"{str(row['cal_date'])[:4]}-{str(row['cal_date'])[4:6]}-{str(row['cal_date'])[6:]}"
        for _, row in cal.iterrows()
    ))

    existing = {
        day for day in (
            _fmt_date(row.trade_date) for row in db.execute(
            select(DailyKbar.trade_date).where(
                DailyKbar.trade_date >= f"{start_compact[:4]}-{start_compact[4:6]}-{start_compact[6:]}",
                DailyKbar.trade_date <= tushare_latest,
            ).distinct()
        ).all()
        ) if day
    }

    missing = [day for day in all_dates if day not in existing]
    incomplete = _incomplete_daily_kbar_dates(db, all_dates)
    gaps = sorted(set(missing + incomplete))
    if incomplete:
        logger.info("日K覆盖不足交易日(<%d只): %s", _MIN_RECENT_DAILY_KBAR_CODES, incomplete)
    logger.info("缺失/不完整交易日: %s", gaps if gaps else "无")
    return gaps


def _start_trade_date(latest_local: str | None) -> str:
    normalized = _fmt_date(latest_local)
    if not normalized:
        return "20260501"
    # 回看最近几天，捕获“最新日期存在但只写入少量股票”的半同步状态。
    try:
        start = date.fromisoformat(normalized) - timedelta(days=10)
    except ValueError:
        return normalized.replace("-", "")
    return start.strftime("%Y%m%d")


def batch_backfill_date(db, trade_date: str) -> int:
    """拉取单日全市场 K 线并写入 daily_kbars，然后补齐缺失的本地指标。"""
    codes = _daily_kbar_target_codes(db, trade_date, limit=6000)
    result = _backfill_daily_kbar_date(db, trade_date, codes)
    logger.info(
        "%s: 写入 %d 条 / 请求 %d 只 / Tushare 返回 %d 条",
        trade_date,
        result.get("row_count", 0),
        result.get("requested_code_count", 0),
        result.get("provider_row_count", 0),
    )
    # 补齐批量路径丢失的本地指标（MA/量比/乖离/突破/回撤等）
    # stk_factor API 失败或返回空时指标为 0，需要从历史 K 线本地计算
    from app.services.data_gap_remediation_service import _backfill_missing_indicators
    _backfill_missing_indicators(db)
    return int(result.get("row_count") or 0)


def run_outcome_labeling(db):
    """标注 pending outcomes 并输出基线指标。"""
    from app.services.outcome_service import (
        label_pending_outcomes, overview_metrics,
        metrics_by_strategy, precision_at_k, metrics_by_score_line,
    )

    result = label_pending_outcomes(db, horizon_days=20)
    logger.info("结果标注: %s", result)

    for mode in ("formal",):
        m = overview_metrics(db, run_mode=mode)
        logger.info("=== 基线指标 (mode=%s) ===", mode)
        logger.info("总推荐: %d", m["total_recommendations"])
        logger.info("已标注: %d", m["labeled_count"])
        logger.info("已触发: %d | 触发率 %.1f%%", m["triggered_count"], m["trigger_rate"] * 100)
        logger.info("命中率: %.1f%% (%d/%d)", m["hit_rate"] * 100, m["success_count"], m["triggered_count"])
        logger.info("平均收益: %.2f%% | 平均权重收益: %.2f%%", m["avg_return"], m["avg_weighted_return"])
        logger.info("平均持仓: %.1f天 | 平均回撤: %.2f%%", m["avg_holding_days"], m["avg_drawdown"])
        logger.info("成功:%d 失败:%d 未触发:%d 待处理:%d",
                    m["success_count"], m["failed_count"], m["missed_count"], m["pending_count"])

        strategies = metrics_by_strategy(db, run_mode=mode)
        for s in strategies:
            logger.info("  Skill %s: n=%d triggered=%d hit=%.1f%% avg_ret=%.2f%% score=%.2f",
                       s["strategy_id"], s["sample_count"], s["triggered_count"],
                       s["hit_rate"] * 100, s["avg_return"], s["avg_score"])

        pk = precision_at_k(db, run_mode=mode)
        for p in pk:
            logger.info("  P@%d: prec=%.1f%% (runs=%d, eval=%d, success=%d)",
                       p["k"], p["precision"] * 100, p["sample_runs"],
                       p["evaluated_count"], p["success_count"])

        sl = metrics_by_score_line(db, run_mode=mode)
        for s in sl:
            logger.info("  Score>=%d: n=%d activated=%.0f%% triggered=%d hit=%.1f%% avg_ret=%.2f%% drawdown=%.2f%%",
                       int(s["score_line"]), s["sample_count"], s["activation_rate"] * 100,
                       s["triggered_count"], s["hit_rate"] * 100, s["avg_return"], s["avg_drawdown"])


def main():
    db = SessionLocal()
    try:
        missing = get_missing_trade_dates(db)
        for td in missing:
            logger.info("回填 %s ...", td)
            batch_backfill_date(db, td)

        logger.info("运行结果标注...")
        run_outcome_labeling(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
