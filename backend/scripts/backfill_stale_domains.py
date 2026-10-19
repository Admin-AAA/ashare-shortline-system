#!/usr/bin/env python3
"""
按交易日全市场批量回填历史缺口数据。

覆盖数据健康 tab 标记为 stale 的域：
  - cyq_perf   筹码峰 → daily_kbar.chip_cost_* （只更新筹码列，不覆盖其他列）
  - stk_limit  涨跌停价 → stk_limits
  - margin_detail 融资融券明细 → margin_detail
  - hk_hold    港股通持股 → hk_holdings
  - broker_recommend 券商金股（按 month 批量）→ broker_recommends

所有接口均按 trade_date（或 month）全市场批量调用，比逐股票快几个数量级。
用 MySQL INSERT ... ON DUPLICATE KEY UPDATE upsert 去重。

用法:
    python scripts/backfill_stale_domains.py --days 60 --domains chip,stk_limit,margin,hk_hold,broker
    python scripts/backfill_stale_domains.py --domains chip --days 40
    python scripts/backfill_stale_domains.py --domains broker  # broker 不受 --days 影响，按月补
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import BrokerRecommend, DailyKbar, HkHolding, MarginDetail, StkLimit
from scripts.tushare_init import get_pro

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_stale")

_ALL_DOMAINS = ["chip", "stk_limit", "margin", "margin_data", "hk_hold", "broker"]


def _trade_days(pro, days: int) -> list[str]:
    """最近 days 个自然日内的交易日列表（compact YYYYMMDD，升序）。"""
    end = date.today()
    start = end - timedelta(days=days)
    for attempt in range(3):
        try:
            cal = pro.trade_cal(exchange="SSE",
                                start_date=start.strftime("%Y%m%d"),
                                end_date=end.strftime("%Y%m%d"),
                                is_open="1")
            if cal is not None and not cal.empty:
                return sorted(str(d) for d in cal.sort_values("cal_date")["cal_date"].tolist())
        except Exception as exc:
            logger.warning("trade_cal 第%d次失败: %s，重试", attempt + 1, exc)
            time.sleep(2)
    raise RuntimeError("无法获取交易日历")


def _safe_float(val: Any) -> float | None:
    try:
        if val is None:
            return None
        f = float(val)
        import math
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fmt_date(compact: str) -> str:
    s = str(compact).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


def _compact_date(value: Any) -> str:
    """compact YYYYMMDD（margin_data/hk_holdings 历史用此格式）。"""
    return _fmt_date(value).replace("-", "")


# ── 各域回填 ──────────────────────────────────────────────────────────────

def backfill_chip(db, pro, trade_days: list[str]) -> int:
    """筹码峰 cyq_perf → daily_kbar.chip_* 列（只更新筹码列）。"""
    total = 0
    for td in trade_days:
        try:
            df = pro.cyq_perf(trade_date=td)
            from app.services.tushare_client_pure import keep_rows_for_trade_date
            df = keep_rows_for_trade_date(df, td, api_name="cyq_perf")
        except Exception as exc:
            logger.warning("cyq_perf(%s) 异常: %s", td, str(exc)[:80])
            continue
        if df is None or df.empty:
            logger.info("cyq_perf(%s) 空", td)
            continue
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("ts_code", "")).split(".")[0]
            if len(code) != 6:
                continue
            rows.append({
                "code": code,
                "trade_date": _fmt_date(td),
                "chip_cost_15": _safe_float(r.get("cost_15pct")),
                "chip_cost_50": _safe_float(r.get("cost_50pct")),
                "chip_cost_85": _safe_float(r.get("cost_85pct")),
                "chip_weight_avg": _safe_float(r.get("weight_avg")),
                "chip_winner_rate": _safe_float(r.get("winner_rate")),
            })
        if not rows:
            continue
        stmt = mysql_insert(DailyKbar).values(rows)
        # 只更新筹码列，不覆盖其他已存在的列
        update_cols = {
            "updated_at": func.utc_timestamp(),
            "chip_cost_15": stmt.inserted.chip_cost_15,
            "chip_cost_50": stmt.inserted.chip_cost_50,
            "chip_cost_85": stmt.inserted.chip_cost_85,
            "chip_weight_avg": stmt.inserted.chip_weight_avg,
            "chip_winner_rate": stmt.inserted.chip_winner_rate,
        }
        stmt = stmt.on_duplicate_key_update(**update_cols)
        db.execute(stmt)
        db.commit()
        total += len(rows)
        logger.info("cyq_perf(%s) 写入 %d 行", td, len(rows))
        time.sleep(0.3)
    return total


def backfill_stk_limit(db, pro, trade_days: list[str]) -> int:
    """涨跌停价 stk_limit → stk_limits。"""
    total = 0
    for td in trade_days:
        try:
            df = pro.stk_limit(trade_date=td)
        except Exception as exc:
            logger.warning("stk_limit(%s) 异常: %s", td, str(exc)[:80])
            continue
        if df is None or df.empty:
            logger.info("stk_limit(%s) 空", td)
            continue
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("ts_code", "")).split(".")[0]
            if len(code) != 6:
                continue
            rows.append({
                "code": code,
                "trade_date": _fmt_date(td),
                "up_limit": _safe_float(r.get("up_limit")),
                "down_limit": _safe_float(r.get("down_limit")),
            })
        if not rows:
            continue
        stmt = mysql_insert(StkLimit).values(rows)
        stmt = stmt.on_duplicate_key_update(
            up_limit=stmt.inserted.up_limit,
            down_limit=stmt.inserted.down_limit,
        )
        db.execute(stmt)
        db.commit()
        total += len(rows)
        logger.info("stk_limit(%s) 写入 %d 行", td, len(rows))
        time.sleep(0.3)
    return total


def backfill_margin(db, pro, trade_days: list[str]) -> int:
    """融资融券明细 margin_detail → margin_detail。"""
    total = 0
    for td in trade_days:
        try:
            df = pro.margin_detail(trade_date=td)
        except Exception as exc:
            logger.warning("margin_detail(%s) 异常: %s", td, str(exc)[:80])
            continue
        if df is None or df.empty:
            logger.info("margin_detail(%s) 空（可能 T+1 未出）", td)
            continue
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("ts_code", "")).split(".")[0]
            if len(code) != 6:
                continue
            rows.append({
                "code": code,
                "trade_date": _fmt_date(td),
                # 与 fetch_margin_detail_batch 一致存「亿元」(/1e8)。此前这里写原始「元」，
                # 与 state_adapters/margin_detail 的亿元契约不符（07-16 前 17.9 万行数据单位错，
                # 07-16 起 fetch_margin_detail_batch 写亿元才正确）。
                "rzye": _safe_float(r.get("rzye")) / 1e8 if r.get("rzye") is not None else None,
                "rqye": _safe_float(r.get("rqye")) / 1e8 if r.get("rqye") is not None else None,
                "rzmre": _safe_float(r.get("rzmre")) / 1e8 if r.get("rzmre") is not None else None,
                "rzche": _safe_float(r.get("rzche")) / 1e8 if r.get("rzche") is not None else None,
                "rzrqye": _safe_float(r.get("rzrqye")) / 1e8 if r.get("rzrqye") is not None else None,
                "rqyl": _safe_float(r.get("rqyl")),
            })
        if not rows:
            continue
        stmt = mysql_insert(MarginDetail).values(rows)
        stmt = stmt.on_duplicate_key_update(
            rzye=stmt.inserted.rzye,
            rqye=stmt.inserted.rqye,
            rzmre=stmt.inserted.rzmre,
            rzche=stmt.inserted.rzche,
            rzrqye=stmt.inserted.rzrqye,
            rqyl=stmt.inserted.rqyl,
        )
        db.execute(stmt)
        db.commit()
        total += len(rows)
        logger.info("margin_detail(%s) 写入 %d 行", td, len(rows))
        time.sleep(0.3)
    return total


def backfill_margin_data(db, pro, trade_days: list[str]) -> int:
    """融资融券汇总 margin → margin_data（样本股逐股票，单位：亿）。
    MarginData 表是市值前 N 只两融标的的逐股票汇总，只能按 ts_code 逐个拉。
    每次调用返回该股区间内多日数据，所以一只股票一次即可覆盖整个 trade_days 窗口。
    """
    from app.models.tables import MarginData
    # 取现有 MarginData 的样本股 code（保持样本一致）；若空则取市值前 300 两融标的
    existing_codes = db.execute(
        text("SELECT DISTINCT code FROM margin_data ORDER BY code")
    ).all()
    codes = [str(r[0]) for r in existing_codes if r[0]]
    if not codes:
        # 回退：取最近有 daily_kbar 的市值前 300 只
        rows = db.execute(text(
            "SELECT code FROM daily_kbars WHERE trade_date = (SELECT MAX(trade_date) FROM daily_kbars) "
            "ORDER BY amount DESC LIMIT 300"
        )).all()
        codes = [str(r[0]) for r in rows if r[0]]
    if not codes:
        logger.warning("margin_data: 无样本股，跳过")
        return 0

    start = trade_days[0] if trade_days else date.today().strftime("%Y%m%d")
    end = trade_days[-1] if trade_days else start
    total = 0
    for i, code in enumerate(codes):
        tsc = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
        try:
            df = pro.margin(ts_code=tsc, start_date=start, end_date=end)
        except Exception as exc:
            logger.warning("margin(%s) 异常: %s", tsc, str(exc)[:60])
            continue
        if df is None or df.empty:
            continue
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "code": code,
                "trade_date": _compact_date(r.get("trade_date")),
                "rzye": _safe_float(r.get("rzye")) / 1e8 if _safe_float(r.get("rzye")) is not None else None,
                "rqye": _safe_float(r.get("rqye")) / 1e8 if _safe_float(r.get("rqye")) is not None else None,
                "rzmre": _safe_float(r.get("rzmre")) / 1e8 if _safe_float(r.get("rzmre")) is not None else None,
                "rzche": _safe_float(r.get("rzche")) / 1e8 if _safe_float(r.get("rzche")) is not None else None,
                "rzrqye": _safe_float(r.get("rzrqye")) / 1e8 if _safe_float(r.get("rzrqye")) is not None else None,
            })
        if not rows:
            continue
        stmt = mysql_insert(MarginData).values(rows)
        stmt = stmt.on_duplicate_key_update(
            rzye=stmt.inserted.rzye, rqye=stmt.inserted.rqye,
            rzmre=stmt.inserted.rzmre, rzche=stmt.inserted.rzche,
            rzrqye=stmt.inserted.rzrqye,
        )
        db.execute(stmt)
        db.commit()
        total += len(rows)
        if (i + 1) % 50 == 0:
            logger.info("margin_data 进度 %d/%d", i + 1, len(codes))
        time.sleep(0.15)
    logger.info("margin_data 完成: %d 只股票，%d 行", len(codes), total)
    return total


def backfill_hk_hold(db, pro, trade_days: list[str]) -> int:
    """港股通持股 hk_hold → hk_holdings。
    hk_hold 接口返回的 code 是港股代码，但本表 code 是 A 股代码，
    故按 ts_code 逐 A 股拉，用 ts_code 解析 A 股 code 写入。
    trade_days 不适用（hk_hold 按季末日更新，非每个交易日），直接拉每只股票全历史。
    """
    # 取现有 HkHolding 样本股；若空则取市值前 300
    existing_codes = db.execute(text("SELECT DISTINCT code FROM hk_holdings ORDER BY code")).all()
    codes = [str(r[0]) for r in existing_codes if r[0]]
    if not codes:
        rows = db.execute(text(
            "SELECT code FROM daily_kbars WHERE trade_date = (SELECT MAX(trade_date) FROM daily_kbars) "
            "ORDER BY amount DESC LIMIT 300"
        )).all()
        codes = [str(r[0]) for r in rows if r[0]]
    if not codes:
        logger.warning("hk_hold: 无样本股，跳过")
        return 0

    total = 0
    for i, code in enumerate(codes):
        tsc = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
        try:
            df = pro.hk_hold(ts_code=tsc)
        except Exception as exc:
            logger.warning("hk_hold(%s) 异常: %s", tsc, str(exc)[:60])
            continue
        if df is None or df.empty:
            continue
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "code": code,
                "trade_date": _compact_date(r.get("trade_date")),
                "vol": _safe_float(r.get("vol")),
                "ratio": _safe_float(r.get("ratio")),
            })
        if not rows:
            continue
        stmt = mysql_insert(HkHolding).values(rows)
        stmt = stmt.on_duplicate_key_update(vol=stmt.inserted.vol, ratio=stmt.inserted.ratio)
        db.execute(stmt)
        db.commit()
        total += len(rows)
        if (i + 1) % 50 == 0:
            logger.info("hk_hold 进度 %d/%d", i + 1, len(codes))
        time.sleep(0.15)
    logger.info("hk_hold 完成: %d 只股票，%d 行", len(codes), total)
    return total


def backfill_broker(db, pro) -> int:
    """券商金股 broker_recommend（按月，最近 4 个月）。"""
    today = date.today()
    months = [(today.replace(day=1) - timedelta(days=30 * i)).strftime("%Y%m") for i in range(4)]
    months = sorted(set(months))
    total = 0
    for m in months:
        try:
            df = pro.broker_recommend(month=m)
        except Exception as exc:
            logger.warning("broker_recommend(%s) 异常: %s", m, str(exc)[:80])
            continue
        if df is None or df.empty:
            logger.info("broker_recommend(%s) 空", m)
            continue
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("ts_code", "")).split(".")[0]
            if len(code) != 6:
                continue
            rows.append({
                "code": code,
                "name": str(r.get("name", ""))[:80] or None,
                "month": m,
                "broker": str(r.get("broker", ""))[:80] or None,
            })
        if not rows:
            continue
        # 唯一约束 (code, month, broker) — 去重已存在的不重复插入
        stmt = mysql_insert(BrokerRecommend).values(rows)
        stmt = stmt.on_duplicate_key_update(name=stmt.inserted.name)
        db.execute(stmt)
        db.commit()
        total += len(rows)
        logger.info("broker_recommend(%s) 写入 %d 行", m, len(rows))
        time.sleep(0.3)
    return total


# ── 概念板块成分 ──────────────────────────────────────────────────────────

def backfill_concept_members(db, pro, trade_days: list[str]) -> int:
    """概念板块成分 ths_member → concept_sector_stock。
    ths_member 按板块代码逐个拉，取最近交易日补一次即可（成分变化慢）。"""
    from app.models.tables import ConceptSectorStock
    td = trade_days[-1] if trade_days else date.today().strftime("%Y%m%d")
    trade_date = _fmt_date(td)
    sectors = db.execute(text(
        "SELECT sector_code FROM concept_sector "
        "WHERE trade_date = (SELECT MAX(trade_date) FROM concept_sector)"
    )).all()
    total = 0
    n_sectors = len(sectors)
    logger.info("concept_member: 待补 %d 个板块，目标日期 %s", n_sectors, trade_date)
    for idx, (sector_code,) in enumerate(sectors):
        try:
            df = pro.ths_member(ts_code=sector_code)
        except Exception as exc:
            logger.warning("ths_member(%s) 异常: %s", sector_code, str(exc)[:60])
            continue
        if df is None or df.empty:
            continue
        rows = []
        seen_codes = set()
        for _, r in df.iterrows():
            code = str(r.get("con_code", "") or r.get("ts_code", "") or r.get("code", "")).split(".")[0]
            if len(code) != 6 or code in seen_codes:
                continue
            seen_codes.add(code)
            rows.append({
                "code": code,
                "trade_date": trade_date,
                "sector_code": sector_code,
                "name": str(r.get("con_name", "") or r.get("name", ""))[:80] or None,
            })
        if not rows:
            continue
        # 先删该板块当天的旧成分再插（成分会变动）
        db.execute(text("DELETE FROM concept_sector_stock WHERE sector_code = :sc AND trade_date = :td"),
                   {"sc": sector_code, "td": trade_date})
        db.bulk_save_objects([ConceptSectorStock(**r) for r in rows])
        db.commit()
        total += len(rows)
        if (idx + 1) % 100 == 0:
            logger.info("concept_member 进度 %d/%d，已写入 %d 条", idx + 1, n_sectors, total)
        time.sleep(0.2)
    logger.info("concept_member 补到 %s，共 %d 条成分", trade_date, total)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="按交易日批量回填 stale 数据域")
    parser.add_argument("--days", type=int, default=60, help="回填最近 N 个自然日内的交易日（broker 不受此限）")
    parser.add_argument("--domains", type=str, default=",".join(_ALL_DOMAINS),
                        help=f"逗号分隔的域，可选: {','.join(_ALL_DOMAINS)} + concept_member")
    args = parser.parse_args()

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    unknown = set(domains) - set(_ALL_DOMAINS + ["concept_member"])
    if unknown:
        logger.error("未知域: %s", unknown)
        return 1

    pro = get_pro()
    need_days = domains != ["broker"] or "concept_member" in domains
    trade_days = _trade_days(pro, args.days) if need_days else []
    if trade_days:
        logger.info("交易日区间: %s ~ %s（共 %d 天）", trade_days[0], trade_days[-1], len(trade_days))

    db = SessionLocal()
    try:
        results: dict[str, int] = {}
        if "chip" in domains:
            results["chip"] = backfill_chip(db, pro, trade_days)
        if "stk_limit" in domains:
            results["stk_limit"] = backfill_stk_limit(db, pro, trade_days)
        if "margin" in domains:
            results["margin"] = backfill_margin(db, pro, trade_days)
        if "margin_data" in domains:
            results["margin_data"] = backfill_margin_data(db, pro, trade_days)
        if "hk_hold" in domains:
            results["hk_hold"] = backfill_hk_hold(db, pro, trade_days)
        if "broker" in domains:
            results["broker"] = backfill_broker(db, pro)
        if "concept_member" in domains:
            results["concept_member"] = backfill_concept_members(db, pro, trade_days)

        logger.info("── 回填汇总 ──")
        for k, v in results.items():
            logger.info("  %s: %d 行", k, v)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
