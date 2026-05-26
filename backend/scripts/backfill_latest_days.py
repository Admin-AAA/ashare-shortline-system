"""
快速回填最近缺失交易日 K 线。
使用 Tushare batch 接口按日期拉取，比逐股票快 1000 倍。
后接 outcome labeling + 基线指标计算。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import DailyKbar

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

    if latest_local and latest_local >= tushare_latest:
        logger.info("本地数据已是最新")
        return []

    pro = get_pro()
    start_compact = (latest_local or "2026-05-01").replace("-", "")
    end_compact = tushare_latest_compact
    cal = pro.trade_cal(exchange="SSE", start_date=start_compact, end_date=end_compact, is_open="1")
    if cal is None or cal.empty:
        return []

    all_dates = sorted(set(
        f"{str(row['cal_date'])[:4]}-{str(row['cal_date'])[4:6]}-{str(row['cal_date'])[6:]}"
        for _, row in cal.iterrows()
    ))

    existing = set(
        row.trade_date for row in db.execute(
            select(DailyKbar.trade_date).where(
                DailyKbar.trade_date >= (latest_local or "2020-01-01"),
                DailyKbar.trade_date <= tushare_latest,
            ).distinct()
        ).all()
    )

    missing = [d for d in all_dates if d not in existing]
    logger.info("缺失交易日: %s", missing if missing else "无")
    return missing


def batch_backfill_date(db, trade_date: str) -> int:
    """拉取单日全市场 K 线并写入 daily_kbars。"""
    from app.services.tushare_client import get_pro, _f
    pro = get_pro()
    compact = trade_date.replace("-", "")

    # 1. 批量拉取 daily
    daily = pro.daily(trade_date=compact)
    if daily is None or daily.empty:
        logger.warning("daily 为空: %s", trade_date)
        return 0
    logger.info("daily 返回 %d 行", len(daily))

    # 2. 批量拉取 daily_basic
    basic_map: dict[str, dict] = {}
    try:
        basic = pro.daily_basic(trade_date=compact)
        if basic is not None and not basic.empty:
            for _, r in basic.iterrows():
                basic_map[str(r.get("ts_code", "")).split(".")[0]] = dict(r)
            logger.info("daily_basic 返回 %d 行", len(basic))
    except Exception as e:
        logger.warning("daily_basic 失败: %s", e)

    # 构建 bar 数据
    new_bars: list[dict[str, Any]] = []
    for _, row in daily.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        bas = basic_map.get(code, {})
        amount = _f(row.get("amount")) * 1000
        new_bars.append({
            "code": code,
            "trade_date": trade_date,
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "volume": _f(row.get("vol")),
            "amount": amount,
            "pct_chg": _f(row.get("pct_chg")),
            "turnover_rate": _f(bas.get("turnover_rate")),
            "pe_ttm": _f(bas.get("pe_ttm")),
            "total_mv": _f(bas.get("total_mv")) / 1e8 if bas.get("total_mv") is not None else 0.0,
            "source": "tushare",
        })

    if not new_bars:
        return 0

    # 3. 加载历史 K 线，逐股票计算本地指标
    codes = list(set(b["code"] for b in new_bars))
    historical = db.scalars(
        select(DailyKbar).where(
            DailyKbar.code.in_(codes),
            DailyKbar.trade_date <= trade_date,
        ).order_by(DailyKbar.code, DailyKbar.trade_date.asc())
    ).all()

    hist_by_code: dict[str, list[dict]] = defaultdict(list)
    for h in historical:
        hist_by_code[h.code].append({
            "trade_date": h.trade_date,
            "open": h.open, "high": h.high, "low": h.low, "close": h.close,
            "volume": h.volume or 0, "amount": h.amount or 0,
            "pct_chg": h.pct_chg or 0, "turnover_rate": h.turnover_rate or 0,
        })

    from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators
    for bar in new_bars:
        code = bar["code"]
        # Merge historical + new bar for indicator computation
        all_bars = list(hist_by_code.get(code, []))
        all_bars.append(bar)
        bar_inds = compute_daily_bar_indicators(code, all_bars)
        if bar["trade_date"] in bar_inds:
            bar.update(bar_inds[bar["trade_date"]])
        full_inds = compute_daily_indicators(code, all_bars)
        if bar["trade_date"] in full_inds:
            bar.update(full_inds[bar["trade_date"]])

    # 4. Upsert
    inserted = 0
    for bar in new_bars:
        try:
            existing_bar = db.execute(
                select(DailyKbar).where(
                    DailyKbar.code == bar["code"],
                    DailyKbar.trade_date == trade_date,
                )
            ).scalar_one_or_none()
            if existing_bar:
                for key, val in bar.items():
                    if key not in ("code", "trade_date"):
                        setattr(existing_bar, key, val)
            else:
                bar_copy = dict(bar)
                db.add(DailyKbar(**bar_copy))
            inserted += 1
        except Exception as e:
            logger.warning("写入失败 %s %s: %s", bar["code"], trade_date, e)
            db.rollback()

    db.commit()
    logger.info("%s: 写入 %d 条 / %d 候选", trade_date, inserted, len(new_bars))
    return inserted


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
