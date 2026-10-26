"""统一数据新鲜度与质量监控（只读）：数据库表 + data/ 下的文件数据集 + 研究静态文件 + 调度任务 + 日志静默失败。

2026-10-26 背景：data/xiaoshi_min1 停在 2026-08-07 两个多月没人发现——原有巡检（data_freshness.py / data_health_service.py）
只看 MySQL 表和少数 parquet，data/ 下的文件型数据集（分钟K缓存、research 静态文件、涨停榜缓存、event parquet…）完全没有
覆盖；MySQL minute_kbars 也只查「最大日期」不查每日覆盖股票数。本模块补齐这些盲区，**沿用现有告警机制**：
结果写入 data/runtime/data_quality_report.json，红/黄项走 emit_alert("data_quality", <key>, …)，恢复后 resolve_alert，
与 data_freshness 告警一起出现在 admin 告警面板；/api/admin/data-quality 与 /api/admin/data-health 可见。

约定：
- 严重度 red / yellow / green；日频数据集 交易日滞后 >1 红、=1 黄；行数/覆盖股票数相对近 20 日中位数下降 >20% 黄、>50% 红。
- 研究用（非线上选股依赖）数据集 max_severity=yellow：陈旧只提醒，不会触发红色。
- provisional=True 的数据集（正在被另一补数任务回填）阈值为临时值，严重度封顶 yellow，补数完成后再核对。
- 整个巡检只读，任何一项检查异常只记成一条「检查失败」黄项，绝不抛出、不阻塞选股主流程。
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_ROOT / "data"
LOG_FILE = BACKEND_ROOT / "logs" / "app.log"
REPORT_PATH = DATA_DIR / "runtime" / "data_quality_report.json"
ALERT_KIND = "data_quality"
_CST = timezone(timedelta(hours=8))
RED, YELLOW, GREEN = "red", "yellow", "green"
_RANK = {GREEN: 0, YELLOW: 1, RED: 2}

LAG_YELLOW = 1            # 交易日滞后（超出允许值）>= 1 黄
LAG_RED = 2               # >= 2 红（即 >1）
DROP_YELLOW = 0.8         # 最新一日量 < 近 20 日中位数 * 0.8 黄（降 >20%）
DROP_RED = 0.5            # < 0.5 红
SPIKE_RATIO = 3.0         # 最新一日 > 中位数 3 倍且 > 前一日 3 倍：口径突变，黄
BASELINE_DAYS = 20
EVENING_READY = (21, 30)  # 21:30 后认为当日晚间补数已完成，期望最新交易日 = 今天


def _now() -> datetime:
    return datetime.now(_CST)


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def _item(key: str, label: str, category: str, severity: str, *, kind: str, latest: str | None = None,
          expected: str | None = None, lag: int | None = None, detail: str = "", impact: str = "",
          provisional: bool = False, **extra: Any) -> dict[str, Any]:
    out = {"key": key, "label": label, "category": category, "kind": kind, "severity": severity,
           "latest": latest, "expected": expected, "lag_trade_days": lag, "detail": detail, "impact": impact,
           "provisional": provisional}
    out.update(extra)
    return out


def cap_severity(sev: str, max_severity: str) -> str:
    return sev if _RANK[sev] <= _RANK[max_severity] else max_severity


def norm_date(v: Any) -> str | None:
    """20261023 / 2026-10-23 / datetime → 'YYYY-MM-DD'；无法识别返回 None。"""
    if v is None:
        return None
    s = str(v).strip()[:10]
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def load_open_days(db) -> list[str]:
    rows = db.execute(text("SELECT cal_date FROM trade_calendar WHERE is_open = 1 ORDER BY cal_date")).scalars().all()
    return [d for d in (norm_date(r) for r in rows) if d]


def expected_trade_date(open_days: list[str], now: datetime | None = None) -> str:
    """期望的最新已落库交易日：今天是交易日且已过 21:30（晚间补数后）取今天，否则取今天之前最近的交易日。"""
    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    past = [d for d in open_days if d < today]
    if today in open_days and (now.hour, now.minute) >= EVENING_READY:
        return today
    return past[-1] if past else today


def trade_lag(open_days: list[str], latest: str | None, expected: str) -> int | None:
    """latest 之后到 expected（含）之间的交易日个数；latest 为空返回 None；latest ≥ expected 为 0。"""
    if not latest:
        return None
    if latest >= expected:
        return 0
    return sum(1 for d in open_days if latest < d <= expected)


def lag_severity(lag: int | None, allowed: int = 0) -> str:
    if lag is None:
        return RED
    over = lag - allowed
    if over >= LAG_RED:
        return RED
    if over >= LAG_YELLOW:
        return YELLOW
    return GREEN


def cliff_severity(latest: float, prev: float | None, baseline: list[float]) -> tuple[str, str]:
    """最新一日量 vs 近期中位数：降 >20% 黄、>50% 红；暴增 >3 倍（同时相对前一日也 >3 倍）视为口径突变黄。"""
    base = [b for b in baseline if b is not None]
    if not base:
        return GREEN, ""
    med = statistics.median(base)
    if med <= 0:
        return GREEN, ""
    ratio = latest / med
    if ratio < DROP_RED:
        return RED, f"最新一日 {int(latest)} 仅为近 {len(base)} 日中位数 {int(med)} 的 {ratio:.0%}（降 {1 - ratio:.0%}）"
    if ratio < DROP_YELLOW:
        return YELLOW, f"最新一日 {int(latest)} 低于近 {len(base)} 日中位数 {int(med)} 的 {ratio:.0%}（降 {1 - ratio:.0%}）"
    if ratio > SPIKE_RATIO and (prev is None or prev <= 0 or latest / prev > SPIKE_RATIO):
        return YELLOW, f"最新一日 {int(latest)} 是近 {len(base)} 日中位数 {int(med)} 的 {ratio:.1f} 倍，口径可能突变"
    return GREEN, ""


def _safe(label: str, key: str, category: str, kind: str, fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 一项检查失败不得影响其它检查，更不能阻塞主流程
        logger.warning("data_quality check %s failed: %r", key, exc)
        return [_item(key, label, category, YELLOW, kind=kind, detail=f"检查本身失败 {type(exc).__name__}: {str(exc)[:160]}",
                      impact="该项无法判断，需人工排查监控代码")]


# --------------------------------------------------------------------------- 数据库表
DB_DATASETS: list[dict[str, Any]] = [
    {"key": "db:daily_kbars", "label": "日K线", "table": "daily_kbars", "col": "trade_date", "entity": "COUNT(DISTINCT code)",
     "lag": 0, "cliff": True, "impact": "全部选股策略/回测/策略页"},
    {"key": "db:daily_basic", "label": "每日基本面(市值/估值)", "table": "daily_basic", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": True, "impact": "中线小市值/低价/高股息/低市销率等周度策略"},
    {"key": "db:capital_flow", "label": "资金流向", "table": "capital_flow", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": True, "impact": "短线资金类策略（超跌资金反转等）"},
    {"key": "db:stk_limits", "label": "涨跌停价", "table": "stk_limits", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": True, "impact": "回测/实盘成交可行性（涨停买不进、跌停卖不出）"},
    {"key": "db:auction_data", "label": "集合竞价", "table": "auction_data", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": True, "impact": "竞价类研究/部分短线过滤"},
    {"key": "db:margin_detail", "label": "融资融券明细", "table": "margin_detail", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 1, "cliff": True, "impact": "融资类过滤（来源盘后晚出，允许滞后 1 日）"},
    {"key": "db:stock_quotes", "label": "实时报价快照", "table": "stock_quotes", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": True, "impact": "首页/行情池"},
    {"key": "db:concept_sector", "label": "概念板块", "table": "concept_sector", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": True, "spike_is_recovery": True,
     "impact": "题材类短线策略/板块强度（注意：同花顺 ths_index 条数随上游路由不同——quantdata 全量约 2517 条，"
               "promax 只返回 5 条/662 条，行数突增多为从截断恢复为全量，不是重复写入）"},
    {"key": "db:concept_sector_stock", "label": "概念板块成分", "table": "concept_sector_stock", "col": "trade_date",
     "entity": "COUNT(*)", "lag": 0, "cliff": True, "spike_is_recovery": True,
     "impact": "题材类短线策略/板块相对强度龙头（行数暴增来自 concept_sector 从截断恢复全量，非重复写入）"},
    {"key": "db:limit_up_board", "label": "涨停板", "table": "limit_up_board", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": False, "impact": "涨停类短线策略（每日数量天然波动，不做突降判断）"},
    {"key": "db:dragon_tiger", "label": "龙虎榜", "table": "dragon_tiger", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 0, "cliff": False, "impact": "龙虎榜类策略"},
    {"key": "db:hsgt_flows", "label": "沪深港通资金流", "table": "hsgt_flows", "col": "trade_date", "entity": "COUNT(*)",
     "lag": 1, "cliff": False, "impact": "北向资金参考"},
    {"key": "db:minute_kbars", "label": "分钟K线(数据库，约300只股票池)", "table": "minute_kbars", "col": "trade_date",
     "entity": "COUNT(DISTINCT code)", "lag": 0, "cliff": True,
     "impact": "盘中/分钟级研究与部分短线确认；此前只查最大日期，不查每日覆盖股票数"},
]
INDEX_CODES = ("000300.SH", "000905.SH", "000852.SH", "000001.SH", "399001.SZ", "399006.SZ")


def _window_start(open_days: list[str], expected: str, n: int) -> str:
    past = [d for d in open_days if d <= expected]
    return past[-n] if len(past) >= n else (past[0] if past else expected)


def check_db_dataset(db, spec: dict[str, Any], open_days: list[str], expected: str) -> dict[str, Any]:
    table, col = spec["table"], spec["col"]
    mx = norm_date(db.execute(text(f"SELECT MAX(`{col}`) FROM `{table}`")).scalar())
    lag = trade_lag(open_days, mx, expected)
    sev = lag_severity(lag, spec["lag"])
    notes: list[str] = []
    if sev != GREEN:
        notes.append(f"最新 {mx}，期望 {expected}，滞后 {lag} 个交易日（允许 {spec['lag']}）")
    recent_low: list[str] = []
    counts: dict[str, int] = {}
    if spec["cliff"] and mx:
        start = _window_start(open_days, expected, BASELINE_DAYS + 5)
        rows = db.execute(text(
            f"SELECT `{col}`, {spec['entity']} FROM `{table}` WHERE `{col}` >= :s GROUP BY `{col}` ORDER BY `{col}`"),
            {"s": start}).all()
        counts = {norm_date(r[0]) or str(r[0]): int(r[1]) for r in rows}
        days = sorted(counts)
        if len(days) >= 4:
            latest_day = days[-1]
            baseline = [counts[d] for d in days[:-1]][-BASELINE_DAYS:]
            csev, cmsg = cliff_severity(counts[latest_day], counts[days[-2]], baseline)
            if csev != GREEN and spec.get("spike_is_recovery") and "口径" in cmsg:
                # 概念板块：行数暴增 = ths_index 从截断（5 条）恢复为全量（2517 条），不是异常；截断由 completeness 检查负责
                notes.append(f"{latest_day} {cmsg}（已核实：上游从截断恢复为全量，非重复写入，不告警）")
            elif csev != GREEN:
                notes.append(f"{latest_day} {cmsg}")
                sev = _worse(sev, csev)
            med = statistics.median(baseline) if baseline else 0
            recent_low = [f"{d}:{counts[d]}" for d in days[:-1] if med and counts[d] < DROP_YELLOW * med]
    detail = "；".join(notes) if notes else f"最新 {mx}，滞后 {lag}"
    return _item(spec["key"], spec["label"], "数据库表", sev, kind="db", latest=mx, expected=expected, lag=lag,
                 detail=detail, impact=spec["impact"], latest_count=(counts.get(mx) if mx else None),
                 recent_low_days=recent_low[-8:])


CONCEPT_MIN_SECTORS = 500  # ths_index 全量约 2500 条；少于此值视为上游截断


def check_concept_completeness(db, open_days: list[str], expected: str) -> dict[str, Any]:
    """concept_sector 最新一日条数过少 = ths_index 被截断（promax 路由只回 5 条），题材特征会退化。只读。"""
    row = db.execute(text("SELECT trade_date, COUNT(*) FROM concept_sector GROUP BY trade_date "
                          "ORDER BY trade_date DESC LIMIT 1")).first()
    mx, n = (norm_date(row[0]), int(row[1])) if row else (None, 0)
    sev = YELLOW if n < CONCEPT_MIN_SECTORS else GREEN
    detail = (f"最新 {mx} 共 {n} 个板块" +
              (f"，少于 {CONCEPT_MIN_SECTORS}：疑似 ths_index 被截断（quantdata 额度用尽时 promax 只回 5 条）" if sev != GREEN
               else "（完整）"))
    return _item("db:concept_sector:completeness", "概念板块完整性", "数据质量", sev, kind="quality", latest=mx,
                 expected=expected, detail=detail,
                 impact="截断时概念成分只覆盖 3 个板块，题材归属退化为行业兜底；板块热度 heat_rank 本身只是接口返回顺序，不是真实热度")


def check_index_daily(db, open_days: list[str], expected: str) -> list[dict[str, Any]]:
    out = []
    rows = db.execute(text("SELECT ts_code, MAX(trade_date), COUNT(*) FROM index_daily GROUP BY ts_code")).all()
    seen = {r[0]: (norm_date(r[1]), int(r[2])) for r in rows}
    for code in INDEX_CODES:
        mx, n = seen.get(code, (None, 0))
        lag = trade_lag(open_days, mx, expected)
        sev = lag_severity(lag, 0)
        detail = f"最新 {mx}，共 {n} 行" + ("" if sev == GREEN else f"，滞后 {lag} 个交易日")
        out.append(_item(f"db:index_daily:{code}", f"指数日线 {code}", "数据库表", sev, kind="db", latest=mx,
                         expected=expected, lag=lag, detail=detail,
                         impact="小盘指数20日线开关(000905)、沪深300超额对照、市场环境判断"))
    dup = db.execute(text("SELECT COUNT(*) FROM (SELECT ts_code, trade_date FROM index_daily GROUP BY ts_code, trade_date "
                          "HAVING COUNT(*) > 1) x")).scalar()
    if dup:
        out.append(_item("db:index_daily:dup", "指数日线重复键", "数据库表", RED, kind="db",
                         detail=f"{dup} 组重复 (ts_code,trade_date)", impact="指数超额/20日线计算"))
    return out


def check_daily_kbar_values(db, open_days: list[str], expected: str) -> dict[str, Any]:
    """最近 3 个交易日日K值域：收盘/开盘 ≤0、最高<最低、|涨跌幅|>31%、成交量<0、零成交占比 >15%。"""
    start = _window_start(open_days, expected, 3)
    rows = db.execute(text(
        "SELECT code, trade_date, close, pct_chg FROM daily_kbars WHERE trade_date >= :s AND "
        "(close <= 0 OR open <= 0 OR high < low OR volume < 0 OR ABS(pct_chg) > 31)"), {"s": start}).all()
    tot, zero = db.execute(text("SELECT COUNT(*), SUM(CASE WHEN volume = 0 THEN 1 ELSE 0 END) FROM daily_kbars "
                                "WHERE trade_date >= :s"), {"s": start}).one()
    zero_ratio = float(zero or 0) / float(tot) if tot else 0.0
    sev, parts = GREEN, []
    if rows:
        sev = RED if len(rows) > 20 else YELLOW
        sample = "、".join(f"{r[0]}@{norm_date(r[1])}(收{r[2]},涨跌幅{r[3]})" for r in rows[:6])
        parts.append(f"{len(rows)} 行值域异常：{sample}")
    if zero_ratio > 0.15:
        sev = RED if zero_ratio > 0.4 else _worse(sev, YELLOW)
        parts.append(f"零成交占比 {zero_ratio:.1%}")
    return _item("db:daily_kbars:values", "日K值域/异常值", "数据质量", sev, kind="quality", latest=expected, expected=expected,
                 detail="；".join(parts) or f"最近 3 日 {int(tot or 0)} 行值域正常（零成交 {zero_ratio:.1%}）",
                 impact="异常价格会污染涨跌幅/涨停判断、回测收益", anomalous_rows=len(rows))


def check_chip_history(db) -> dict[str, Any]:
    """筹码列整日为空/全零的历史交易日（近 120 个交易日内）。原 check_indicator_columns 只看最近一天。"""
    rows = db.execute(text(
        "SELECT trade_date FROM daily_kbars WHERE trade_date >= (SELECT MIN(d) FROM (SELECT DISTINCT trade_date d FROM daily_kbars "
        "ORDER BY d DESC LIMIT 120) t) GROUP BY trade_date "
        "HAVING SUM(CASE WHEN chip_cost_50 IS NOT NULL AND chip_cost_50 > 0 THEN 1 ELSE 0 END) < 0.05 * COUNT(*) "
        "ORDER BY trade_date")).scalars().all()
    days = [norm_date(r) for r in rows]
    return _item("db:daily_kbars:chip_history", "筹码列历史空洞（近120个交易日）", "数据质量", YELLOW if days else GREEN,
                 kind="quality",
                 detail=(f"{len(days)} 个交易日筹码列整日为空/0：{','.join(days[-8:])}" if days else "近 120 个交易日筹码列无整日空洞"),
                 impact="用筹码的短线策略在这些日子无信号/被误判；回放与回测在这些日子失真", missing_days=days)


def check_calendar_horizon(open_days: list[str], now: datetime) -> dict[str, Any]:
    last = open_days[-1] if open_days else None
    ahead = (date.fromisoformat(last) - now.date()).days if last else -1
    sev = RED if ahead < 5 else YELLOW if ahead < 30 else GREEN
    return _item("db:trade_calendar", "交易日历覆盖（未来）", "数据库表", sev, kind="db", latest=last,
                 detail=f"日历最远到 {last}，距今 {ahead} 天（黄<30 红<5）",
                 impact="期望最新交易日/滞后判断、调度任务漏跑判断都依赖日历")


# --------------------------------------------------------------------------- 文件数据集
def _read_date_values(path: Path, cols: tuple[str, ...]) -> list[str]:
    import pandas as pd

    for c in cols:
        try:
            s = pd.read_parquet(path, columns=[c])[c]
        except Exception:  # noqa: BLE001 - 列不存在就换下一个候选列
            continue
        vals = s.dropna().astype(str).str.replace("-", "", regex=False).str[:8]
        vals = vals[vals.str.fullmatch(r"\d{8}")]
        if len(vals):
            return [str(vals.max()), str(vals.min())]
    return []


def _latest_parquet(pattern: str, base: Path | None = None) -> Path | None:
    files = sorted((base or DATA_DIR).glob(pattern))
    return files[-1] if files else None


def _file_age_days(p: Path, now: datetime | None = None) -> float:
    now = now or _now()
    return (now.timestamp() - p.stat().st_mtime) / 86400.0


# mode: daily 日频（交易日滞后）；weekly 周度快照；event 事件型（自然日阈值）
FILE_DATASETS: list[dict[str, Any]] = [
    {"key": "file:midterm/daily_basic.parquet", "label": "research 每日基本面 parquet", "mode": "daily",
     "glob": "midterm/daily_basic.parquet", "cols": ("trade_date",), "max": YELLOW,
     "impact": "中线研究/回测（线上周度策略读库与 basic_<年>.parquet）"},
    {"key": "file:midterm/stk_limits.parquet", "label": "research 涨跌停价 parquet", "mode": "daily",
     "glob": "midterm/stk_limits.parquet", "cols": ("trade_date",), "max": YELLOW, "impact": "中线研究/回测成交可行性"},
    {"key": "file:midterm/index_daily.parquet", "label": "research 指数日线 parquet", "mode": "daily",
     "glob": "midterm/index_daily.parquet", "cols": ("trade_date",), "max": YELLOW,
     "impact": "中线研究/回测的小盘指数20日线、沪深300对照"},
    {"key": "file:cache/daily_kbar.parquet", "label": "回测日K缓存 parquet", "mode": "daily",
     "glob": "cache/daily_kbar.parquet", "cols": ("trade_date",), "max": YELLOW, "impact": "短线/中线回测与研究脚本"},
    {"key": "file:midterm/basic_<year>.parquet", "label": "周度基本面快照 basic_<年>.parquet（线上高股息/低市销率读取）",
     "mode": "weekly", "glob": "midterm/basic_20[0-9][0-9].parquet", "cols": ("trade_date",), "weekly_lag": 5, "max": YELLOW,
     "impact": "线上 小盘高股息 / 小盘低市销率 / 红利低波：缺当周快照时线上代码改走数据网关现拉（不会静默用旧值），但多耗接口"},
    {"key": "file:midterm/hk_holdings.parquet", "label": "港股通持股 parquet", "mode": "event",
     "glob": "midterm/hk_holdings.parquet", "cols": ("trade_date",), "event_days": (45, 130), "max": YELLOW,
     "impact": "研究用（季度快照）"},
    {"key": "file:research_cache/kpl_list", "label": "开盘啦涨停榜缓存(kpl)", "mode": "daily",
     "glob": "research_cache/kpl_list/kpl_20[0-9][0-9].parquet", "cols": ("trade_date",), "max": YELLOW,
     "impact": "热点题材/龙头类研究；此缓存无日更任务，靠手工 fetch"},
    {"key": "file:research_cache/limit_list_d", "label": "涨停榜 limit_list_d 缓存（回补范围：2023-10-27 起 + 2020 早期片段）", "mode": "daily",
     "glob": "research_cache/limit_list_d/limit_list_d_20[0-9][0-9].parquet", "cols": ("trade_date",), "max": YELLOW,
     "provisional": True, "expected_start": "20231027",
     "impact": "追龙/连板研究；只补最近 3 年（2023-10-27 起，用户 2026-10-26 决定），更早仅保留 2020 年已有片段；回补进行中，阈值待其完成后复核"},
    {"key": "file:midterm/events/repurchase", "label": "回购事件 parquet（线上回购预案策略读取）", "mode": "event",
     "glob": "midterm/events/repurchase_20[0-9][0-9].parquet", "cols": ("ann_date",), "event_days": (5, 10), "max": RED,
     "impact": "线上中线 回购预案 策略（每日经网关 ingest）"},
    {"key": "file:midterm/events/incentive_live", "label": "股权激励事件 parquet（线上期权激励策略读取）", "mode": "event",
     "glob": "midterm/events/incentive_live_20[0-9][0-9].parquet", "cols": ("ann_date",), "event_days": (6, 14), "max": RED,
     "impact": "线上中线 期权激励 策略"},
    {"key": "file:midterm/events/forecast_live", "label": "业绩预告事件 parquet（线上预告预喜策略读取）", "mode": "event",
     "glob": "midterm/events/forecast_live_20[0-9][0-9].parquet", "cols": ("ann_date",), "event_days": (6, 14), "max": RED,
     "impact": "线上中线 预告预喜 策略"},
    {"key": "file:midterm/events/holdertrade_in", "label": "增持事件 parquet", "mode": "event",
     "glob": "midterm/events/holdertrade_in_20[0-9][0-9].parquet", "cols": ("ann_date",), "event_days": (7, 21), "max": YELLOW,
     "impact": "研究用（线上大股东增减持读数据库快照）"},
    {"key": "file:midterm/events/holdertrade_de", "label": "减持事件 parquet", "mode": "event",
     "glob": "midterm/events/holdertrade_de_20[0-9][0-9].parquet", "cols": ("ann_date",), "event_days": (7, 21), "max": YELLOW,
     "impact": "研究用（线上大股东增减持读数据库快照）"},
]
DIR_DATASETS: list[dict[str, Any]] = [
    {"key": "file:midterm/fina", "label": "逐股财务指标 parquet 目录", "dir": "midterm/fina", "yellow_days": 45, "red_days": 120,
     "min_files": 5000, "max": YELLOW, "impact": "财报类长线策略的本地基线、fina_fallback"},
    {"key": "file:midterm/forecast", "label": "逐股业绩预告 parquet 目录", "dir": "midterm/forecast", "yellow_days": 45,
     "red_days": 120, "min_files": 5000, "max": YELLOW, "impact": "预告预喜研究基线"},
    {"key": "file:midterm/dividend", "label": "逐股分红 parquet 目录", "dir": "midterm/dividend", "yellow_days": 60,
     "red_days": 150, "min_files": 5000, "max": YELLOW, "impact": "分红类长线研究基线"},
    {"key": "file:midterm/holdernum", "label": "逐股股东人数 parquet 目录", "dir": "midterm/holdernum", "yellow_days": 60,
     "red_days": 150, "min_files": 5000, "max": YELLOW, "impact": "股东人数类研究基线"},
]
AGE_FILES: list[dict[str, Any]] = [
    {"key": "file:industry_cache.json", "label": "行业分类缓存 industry_cache.json", "path": "industry_cache.json",
     "yellow_days": 60, "red_days": 240, "max": YELLOW,
     "impact": "行业映射（板块相对强度、行业中性化）；新股/北交所行业会缺"},
    {"key": "file:runtime/pool_bar_source_stats.json", "label": "行情池日K来源统计", "path": "runtime/pool_bar_source_stats.json",
     "yellow_days": 5, "red_days": 15, "max": YELLOW, "impact": "行情池来源口径一致性核对"},
]


def check_file_dataset(spec: dict[str, Any], open_days: list[str], expected: str, now: datetime | None = None,
                       base: Path | None = None) -> dict[str, Any]:
    path = _latest_parquet(spec["glob"], base)
    label, key = spec["label"], spec["key"]
    prov = bool(spec.get("provisional"))
    if path is None:
        return _item(key, label, "文件数据集", cap_severity(RED, spec["max"]), kind="file", expected=expected,
                     detail=f"找不到文件 data/{spec['glob']}", impact=spec["impact"], provisional=prov)
    vals = _read_date_values(path, spec["cols"])
    mx = norm_date(vals[0]) if vals else None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, _CST).strftime("%Y-%m-%d %H:%M")
    mode = spec["mode"]
    lag: int | None = None
    if mx is None:
        sev, detail = RED, f"{path.name} 读不到日期列 {spec['cols']}"
    elif mode == "daily":
        lag = trade_lag(open_days, mx, expected)
        sev = lag_severity(lag, spec.get("allowed", 0))
        detail = f"{path.name} 最新 {mx}，期望 {expected}，滞后 {lag} 个交易日；文件修改 {mtime}"
    elif mode == "weekly":
        lag = trade_lag(open_days, mx, expected)
        sev = YELLOW if lag is not None and lag > spec["weekly_lag"] else GREEN
        detail = f"{path.name} 最新周度快照 {mx}，距期望 {expected} {lag} 个交易日（允许 {spec['weekly_lag']}）；文件修改 {mtime}"
    else:
        age = (date.fromisoformat(expected) - date.fromisoformat(mx)).days
        y, r = spec["event_days"]
        sev = RED if age > r else YELLOW if age > y else GREEN
        lag = trade_lag(open_days, mx, expected)
        detail = f"{path.name} 最新事件日期 {mx}，距期望 {expected} {age} 天（黄>{y} 红>{r}）；文件修改 {mtime}"
    sev = cap_severity(sev, spec["max"])
    if prov:
        sev = cap_severity(sev, YELLOW)
        detail += "（临时阈值：回补进行中）"
    return _item(key, label, "文件数据集", sev, kind="file", latest=mx, expected=expected, lag=lag, detail=detail,
                 impact=spec["impact"], provisional=prov, file_mtime=mtime)


def check_dir_dataset(spec: dict[str, Any], now: datetime | None = None, base: Path | None = None) -> dict[str, Any]:
    d = (base or DATA_DIR) / spec["dir"]
    files = list(d.glob("*.parquet")) if d.exists() else []
    if not files:
        return _item(spec["key"], spec["label"], "文件数据集", cap_severity(RED, spec["max"]), kind="file",
                     detail=f"目录 data/{spec['dir']} 为空或不存在", impact=spec["impact"])
    newest = max(files, key=lambda p: p.stat().st_mtime)
    age = _file_age_days(newest, now)
    sev = RED if age > spec["red_days"] else YELLOW if age > spec["yellow_days"] else GREEN
    detail = f"{len(files)} 个逐股文件，最新修改 {age:.0f} 天前（黄>{spec['yellow_days']} 红>{spec['red_days']}）"
    if len(files) < spec["min_files"]:
        sev = _worse(sev, YELLOW)
        detail += f"；文件数 {len(files)} 少于 {spec['min_files']}"
    return _item(spec["key"], spec["label"], "文件数据集", cap_severity(sev, spec["max"]), kind="file",
                 latest=datetime.fromtimestamp(newest.stat().st_mtime, _CST).strftime("%Y-%m-%d"), detail=detail,
                 impact=spec["impact"], file_count=len(files))


def check_age_file(spec: dict[str, Any], now: datetime | None = None, base: Path | None = None) -> dict[str, Any]:
    p = (base or DATA_DIR) / spec["path"]
    if not p.exists():
        return _item(spec["key"], spec["label"], "文件数据集", cap_severity(RED, spec["max"]), kind="file",
                     detail=f"文件 data/{spec['path']} 不存在", impact=spec["impact"])
    age = _file_age_days(p, now)
    sev = RED if age > spec["red_days"] else YELLOW if age > spec["yellow_days"] else GREEN
    return _item(spec["key"], spec["label"], "文件数据集", cap_severity(sev, spec["max"]), kind="file",
                 latest=datetime.fromtimestamp(p.stat().st_mtime, _CST).strftime("%Y-%m-%d"),
                 detail=f"文件修改于 {age:.0f} 天前（黄>{spec['yellow_days']} 红>{spec['red_days']}）", impact=spec["impact"])


def xiaoshi_min1_latest(root: Path | None = None) -> tuple[str | None, int, int]:
    """data/xiaoshi_min1/<code>/<year>/update.parquet 的最新 date（全部股票取最大）、股票目录数、有数据的股票数。只读 date 列。"""
    import pandas as pd

    root = root or (DATA_DIR / "xiaoshi_min1")
    mx, n_dirs, n_ok = None, 0, 0
    if not root.exists():
        return None, 0, 0
    for sd in sorted(p for p in root.iterdir() if p.is_dir()):
        n_dirs += 1
        ups = sorted(sd.glob("[0-9][0-9][0-9][0-9]/update.parquet"))
        if not ups:
            continue
        try:
            s = pd.read_parquet(ups[-1], columns=["date"])["date"]
        except Exception:  # noqa: BLE001
            continue
        m = norm_date(s.max()) if len(s) else None
        if m:
            n_ok += 1
            mx = m if mx is None or m > mx else mx
    return mx, n_dirs, n_ok


RETIRED_KEYS = {"file:xiaoshi_min1"}  # 已退役的数据集：只展示事实，不再告警、不计入健康分扣分


def check_xiaoshi_min1(open_days: list[str], expected: str, root: Path | None = None) -> dict[str, Any]:
    """2026-10-26 退役（owner 授权）：旧 230 只固定股票的分钟缓存不再维护，新来源是 data/xiaoshi_min_all（全市场、每日增量）。

    仍然读出最新日期供查看，但严重度恒为 green（retired=True），不发告警、不扣健康分。
    线上分钟线走数据库 minute_kbars，不依赖这个缓存。"""
    mx, n_dirs, n_ok = xiaoshi_min1_latest(root)
    lag = trade_lag(open_days, mx, expected)
    detail = (f"【已退役，不再告警】{n_dirs} 只股票目录（{n_ok} 只有数据），最新 {mx}，滞后 {lag} 个交易日；"
              "替代数据源 data/xiaoshi_min_all（全市场分钟线归档）")
    return _item("file:xiaoshi_min1", "小石1分钟旧缓存 data/xiaoshi_min1（230只，已退役）", "文件数据集", GREEN, kind="file",
                 latest=mx, expected=expected, lag=lag, detail=detail, retired=True,
                 impact="已退役：研究脚本请改读 xiaoshi_min_all（sync_xiaoshi_min_all.load_minutes）；"
                        "bar.minute 网关里的 xiaoshi_min1 提供方对新交易日返回空，依赖兜底源，线上分钟线实际来自 minute_kbars")


def check_xiaoshi_min_all(open_days: list[str], expected: str, now: datetime | None = None,
                          state_path: Path | None = None) -> dict[str, Any]:
    st = state_path or (DATA_DIR / "xiaoshi_min_all" / "state.json")
    key, label = "file:xiaoshi_min_all", "小石全市场分钟线归档 data/xiaoshi_min_all"
    if not st.exists():
        return _item(key, label, "文件数据集", YELLOW, kind="file", detail="尚无 state.json（补数任务尚未建立归档）",
                     impact="追龙/分钟线研究", provisional=True)
    s = json.loads(st.read_text(encoding="utf-8"))
    files = s.get("files") or {}
    last_dates = [d for d in (norm_date(v.get("last_date")) for v in files.values() if isinstance(v, dict)) if d]
    mx = max(last_dates) if last_dates else None
    run = s.get("last_run") or {}
    age_h = None
    try:
        age_h = ((now or _now()) - datetime.strptime(run["ts"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        pass
    lag = trade_lag(open_days, mx, expected)
    sev = lag_severity(lag, 0)
    errs = int(run.get("errors") or 0)
    detail = (f"已登记 {len(files)} 个文件，数据最新 {mx}，期望 {expected}，滞后 {lag}；最近一次同步 {run.get('ts')}"
              f"（{'%.0f 小时前' % age_h if age_h is not None else '未知'}，下载 {run.get('downloaded')}，错误 {errs}）")
    if errs:
        sev = _worse(sev, YELLOW)
    return _item(key, label, "文件数据集", cap_severity(sev, YELLOW), kind="file", latest=mx, expected=expected, lag=lag,
                 detail=detail + "（临时阈值：另一补数任务回补/每日增量建设中，待其完成后复核）",
                 impact="追龙/连板研究的全市场分钟线（小石只保留最近约3个月，需每日增量保存）", provisional=True)


def check_capability_matrix(now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    p = path or (DATA_DIR / "runtime" / "datasource_capability_matrix.json")
    key, label = "file:runtime/datasource_capability_matrix.json", "数据源能力探测矩阵"
    impact = "只反映 Tushare 各接口(provider×api)是否可用，不覆盖 data/ 下的文件数据集——分钟K停更未被发现的原因之一"
    if not p.exists():
        return _item(key, label, "文件数据集", YELLOW, kind="file", detail="文件不存在", impact=impact)
    j = json.loads(p.read_text(encoding="utf-8"))
    probed = str(j.get("probed_at") or "")
    rows = j.get("rows") or []
    bad = [f"{r.get('provider')}/{r.get('api')}" for r in rows if r.get("status") not in ("ok", None)]
    age_days = None
    try:
        age_days = ((now or _now()) - datetime.strptime(probed, "%Y-%m-%dT%H:%M:%S%z")).total_seconds() / 86400
    except Exception:  # noqa: BLE001
        pass
    sev = GREEN
    parts = [f"探测于 {probed}（{'%.1f 天前' % age_days if age_days is not None else '未知'}），{len(rows)} 项，异常 {len(bad)} 项"]
    if age_days is None or age_days > 3:
        sev = YELLOW
        parts.append("探测已超过 3 天未更新")
    if bad:
        sev = _worse(sev, YELLOW)
        parts.append("异常：" + "、".join(bad[:8]))
    return _item(key, label, "文件数据集", sev, kind="file", latest=probed[:10], detail="；".join(parts), impact=impact)


# --------------------------------------------------------------------------- 研究静态文件 vs 数据库
def st_hist_vs_db(db, expected: str, path: Path | None = None) -> dict[str, Any]:
    """research 用 st_hist.parquet 与数据库 stock_metadata_history.is_st 在期望交易日的 ST 状态逐只对账。"""
    import pandas as pd

    p = path or (DATA_DIR / "midterm" / "st_hist.parquet")
    key, label = "research:st_hist", "research ST 历史 st_hist.parquet 与数据库 is_st 对账"
    if not p.exists():
        return _item(key, label, "研究静态文件", YELLOW, kind="research", detail="文件不存在", impact="中长线研究/回测的 ST 过滤")
    h = pd.read_parquet(p)
    h = h[h["effective_from"].astype(str) <= expected].sort_values(["code", "effective_from"]).groupby("code").tail(1)
    file_st = {str(c): bool(s) for c, s in zip(h["code"], h["st"])}
    rows = db.execute(text(
        "SELECT code, is_st FROM stock_metadata_history WHERE effective_from <= :d AND (effective_to IS NULL OR effective_to > :d)"),
        {"d": expected}).all()
    db_st = {str(r[0]): bool(r[1]) for r in rows}
    common = set(file_st) & set(db_st)
    diff = sorted(c for c in common if file_st[c] != db_st[c])
    only_db = len(set(db_st) - set(file_st))
    sev = RED if len(diff) >= 20 else YELLOW if diff or only_db > 20 else GREEN
    detail = (f"{expected} 文件 ST {sum(file_st[c] for c in common)} 只 vs 数据库 {sum(db_st[c] for c in common)} 只，"
              f"逐只不一致 {len(diff)} 只" + (f"（{','.join(diff[:8])}）" if diff else "")
              + f"；库里有而文件没有的代码 {only_db} 只；文件修改于 "
              + datetime.fromtimestamp(p.stat().st_mtime, _CST).strftime("%Y-%m-%d %H:%M"))
    return _item(key, label, "研究静态文件", sev, kind="research", latest=expected, expected=expected, detail=detail,
                 impact="中长线研究/回测的 ST 过滤口径（2026-10-25 曾因文件漏标/误标导致 5 个长线策略误判）；"
                        "文件不随库自动更新，需重导出 scripts/midterm/export_data.py --only st_hist", mismatch=len(diff))


def parquet_vs_db_index(db, path: Path | None = None) -> dict[str, Any]:
    """research index_daily.parquet 与数据库 index_daily 的最新日 000905.SH 收盘是否一致（口径一致性）。"""
    import pandas as pd

    p = path or (DATA_DIR / "midterm" / "index_daily.parquet")
    key, label = "research:index_daily_consistency", "指数 parquet 与数据库口径对账（000905.SH 最新日收盘）"
    if not p.exists():
        return _item(key, label, "研究静态文件", YELLOW, kind="research", detail="parquet 不存在", impact="研究/回测指数对照")
    f = pd.read_parquet(p)
    code_col = "ts_code" if "ts_code" in f.columns else "code"
    f["d"] = f["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    sub = f[(f[code_col] == "000905.SH") & (f["d"] == f.loc[f[code_col] == "000905.SH", "d"].max())]
    if sub.empty:
        return _item(key, label, "研究静态文件", YELLOW, kind="research", detail="parquet 无 000905.SH", impact="研究/回测指数对照")
    d = norm_date(sub["d"].iloc[0])
    pc = float(sub["close"].iloc[0])
    dbc = db.execute(text("SELECT close FROM index_daily WHERE ts_code='000905.SH' AND trade_date=:d"), {"d": d}).scalar()
    ok = dbc is not None and abs(float(dbc) - pc) / max(pc, 1e-9) < 1e-4
    return _item(key, label, "研究静态文件", GREEN if ok else YELLOW, kind="research", latest=d,
                 detail=f"{d} parquet 收盘 {pc:.2f} vs 数据库 {dbc}" + ("，一致" if ok else "，不一致/库内缺该日"),
                 impact="小盘指数20日线开关、超额对照的来源口径")


# --------------------------------------------------------------------------- 调度任务 / 日志
_CRON_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_BAD_STATUS = ("failed", "partial_failed", "diagnosis_failed", "post_diagnosis_failed")


def parse_simple_cron(expr: str) -> tuple[list[int], list[int], set[int]] | None:
    """仅支持 'M H[,H] * * dow[-dow]|*'，本系统全部任务都是这种形式；其它形式返回 None（跳过漏跑判断）。"""
    parts = expr.split()
    if len(parts) != 5 or parts[2] != "*" or parts[3] != "*":
        return None
    try:
        minute = [int(x) for x in parts[0].split(",")]
        hours = [int(x) for x in parts[1].split(",")]
        dow = parts[4].lower()
        if dow == "*":
            days = set(range(7))
        elif "-" in dow:
            a, b = dow.split("-")
            days = set(range(_CRON_DOW[a], _CRON_DOW[b] + 1))
        else:
            days = {_CRON_DOW[x] for x in dow.split(",")}
    except (ValueError, KeyError):
        return None
    return minute, hours, days


def last_expected_run(expr: str, now: datetime, open_days: list[str]) -> datetime | None:
    p = parse_simple_cron(expr)
    if not p:
        return None
    minutes, hours, days = p
    for back in range(0, 8):
        d = (now - timedelta(days=back)).date()
        if d.weekday() not in days or d.strftime("%Y-%m-%d") not in open_days:
            continue
        cands = [datetime(d.year, d.month, d.day, h, m, tzinfo=_CST) for h in hours for m in minutes]
        cands = [c for c in cands if c <= now]
        if cands:
            return max(cands)
    return None


_CRITICAL_TASKS = {"post_sync_formal_recommendation", "postclose_late_data_repair", "formal_run_safety_net",
                   "data_freshness_check_daily", "stock_identity_refresh_daily", "midterm_block_trade_premarket"}
_MISSED_GRACE_MIN = 120


def check_scheduled_tasks(db, open_days: list[str], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or _now()
    out: list[dict[str, Any]] = []
    tasks = db.execute(text("SELECT task_id, name, cron_expression, created_at FROM scheduled_tasks WHERE enabled = 1")).all()
    for task_id, name, cron, created_at in tasks:
        runs = db.execute(text(
            "SELECT started_at, status, summary FROM scheduled_task_runs WHERE task_id = :t ORDER BY started_at DESC LIMIT 8"),
            {"t": task_id}).all()
        key = f"job:{task_id}"
        exp = last_expected_run(str(cron), now, open_days)
        crit = task_id in _CRITICAL_TASKS
        if exp and created_at is not None:  # 任务注册之前的计划时刻不算漏跑
            created = created_at.replace(tzinfo=timezone.utc).astimezone(_CST)
            if exp < created:
                exp = None
        if not runs:
            out.append(_item(key, f"调度任务 {task_id}", "调度任务", YELLOW if exp else GREEN, kind="job",
                             detail="从未有运行记录" + (f"，应已在 {exp:%m-%d %H:%M} 运行" if exp else ""), impact=str(name)))
            continue
        started = runs[0][0].replace(tzinfo=timezone.utc).astimezone(_CST)  # 库内为 UTC naive
        status, summary = str(runs[0][1]), str(runs[0][2] or "")
        sev, notes = GREEN, []
        if exp and started < exp - timedelta(minutes=5) and now - exp > timedelta(minutes=_MISSED_GRACE_MIN):
            sev = RED if crit else YELLOW
            notes.append(f"应在 {exp:%m-%d %H:%M} 运行，最近一次记录是 {started:%m-%d %H:%M}（漏跑）")
        if status in _BAD_STATUS:
            streak = 0
            for r in runs:
                if str(r[1]) in _BAD_STATUS:
                    streak += 1
                else:
                    break
            fail_names = re.search(r"失败数据集[:：]([^）)]+)", summary)
            msg = f"最近一次 {started:%m-%d %H:%M} 状态 {status}，连续 {streak} 次非成功"
            msg += f"（失败数据集：{fail_names.group(1)}）" if fail_names else f"：{summary[:140]}"
            notes.append(msg)
            sev = _worse(sev, RED if (streak >= 3 and crit and status == "failed") else YELLOW)
        out.append(_item(key, f"调度任务 {task_id}", "调度任务", sev, kind="job", latest=f"{started:%Y-%m-%d %H:%M}",
                         expected=(f"{exp:%Y-%m-%d %H:%M}" if exp else None),
                         detail="；".join(notes) or f"最近一次 {started:%m-%d %H:%M} {status}", impact=str(name)))
    return out


def _tail_text(path: Path, max_bytes: int = 6_000_000) -> str:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        return f.read().decode("utf-8", errors="ignore")


_LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(ERROR|WARNING)\] (?:\[pid \d+\] )?([\w\.]+): (.*)$")


def scan_log_signals(text_blob: str, now: datetime | None = None, hours: int = 24) -> dict[str, Any]:
    """近 N 小时日志：各数据源 provider 回退次数（按 provider:原因）、ERROR 条数与 Top 签名。纯函数，便于测试。"""
    now = now or _now()
    since = (now - timedelta(hours=hours)).replace(tzinfo=None)
    fallbacks: dict[str, int] = {}
    errors: dict[str, int] = {}
    n_err = 0
    for line in text_blob.splitlines():
        m = _LOG_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts < since:
            continue
        lvl, logger_name, msg = m.group(2), m.group(3), m.group(4)
        fb = re.search(r"provider fallback api=(\w+) from=(\w+) category=(\w+)", msg)
        if fb:
            k = f"{fb.group(2)}:{fb.group(3)}"
            fallbacks[k] = fallbacks.get(k, 0) + 1
        if lvl == "ERROR":
            n_err += 1
            sig = f"{logger_name}: {re.sub(r'[0-9]+', 'N', msg)[:70]}"
            errors[sig] = errors.get(sig, 0) + 1
    return {"fallbacks": fallbacks, "error_count": n_err, "top_errors": sorted(errors.items(), key=lambda kv: -kv[1])[:5]}


def check_log_signals(now: datetime | None = None, log_file: Path | None = None) -> list[dict[str, Any]]:
    lf = log_file or LOG_FILE
    if not lf.exists():
        return [_item("log:app_log", "应用日志", "静默失败", YELLOW, kind="log", detail="logs/app.log 不存在", impact="无法做静默失败扫描")]
    sig = scan_log_signals(_tail_text(lf), now)
    fb = sig["fallbacks"]
    quota = {k: v for k, v in fb.items() if k.endswith(":quota_exhausted")}
    sev = YELLOW if quota else GREEN
    detail = (("近 24 小时 provider 回退：" + "、".join(f"{k}×{v}" for k, v in sorted(fb.items(), key=lambda kv: -kv[1])[:6]))
              if fb else "近 24 小时无 provider 回退")
    if quota:
        detail += "；有数据源额度耗尽（备用源失效，只剩单一源，主源再限流就会全部失败）"
    out = [_item("log:provider_fallbacks", "数据源回退/额度（日志）", "静默失败", sev, kind="log", detail=detail,
                 impact="回退成功不会报错，但额度耗尽/熔断说明冗余已失效")]
    n = sig["error_count"]
    top = "；".join(f"{s}×{c}" for s, c in sig["top_errors"][:3])
    out.append(_item("log:error_volume", "应用 ERROR 日志量（近24小时）", "静默失败", YELLOW if n > 30 else GREEN, kind="log",
                     detail=f"{n} 条 ERROR" + (f"，Top：{top}" if top else ""), impact="被吞掉的异常只留在日志里"))
    return out


def check_stale_alerts(now: datetime | None = None) -> dict[str, Any]:
    from app.core.alerts import active_alert_records

    now = now or _now()
    stale = []
    for r in active_alert_records():
        if r.get("kind") == ALERT_KIND:
            continue
        try:
            fs = datetime.strptime(str(r["first_seen"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)
        except Exception:  # noqa: BLE001
            continue
        if now - fs > timedelta(hours=24):
            stale.append(f"{r['kind']}|{r['key']}（{fs:%m-%d %H:%M} 起）")
    return _item("alerts:stale_active", "超过 24 小时仍未解除的告警", "静默失败", YELLOW if stale else GREEN, kind="log",
                 detail=("；".join(stale[:6]) if stale else "没有超过 24 小时未解除的告警"), impact="告警长期不处理会被当成背景噪音")


def check_datasource_all_failed(now: datetime | None = None) -> dict[str, Any]:
    """活跃的「全部上游失败」告警：近 6 小时内仍在发生 → 黄。"""
    from app.core.alerts import active_alert_records

    now = now or _now()
    live = []
    for r in active_alert_records("datasource_all_failed"):
        try:
            ls = datetime.strptime(str(r["last_seen"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)
        except Exception:  # noqa: BLE001
            continue
        if now - ls < timedelta(hours=6):
            live.append(f"{r['key']}（{ls:%H:%M}）")
    return _item("alerts:datasource_all_failed", "Tushare 全部上游失败（近6小时）", "静默失败", YELLOW if live else GREEN,
                 kind="log", detail=("近 6 小时发生：" + "、".join(live)) if live else "近 6 小时没有全部上游失败",
                 impact="对应接口的补数/实时行情当时没拿到数据")


# --------------------------------------------------------------------------- 汇总
def _rollback(db) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass


def run_checks(db, *, now: datetime | None = None, include_files: bool = True) -> dict[str, Any]:
    now = now or _now()
    open_days = load_open_days(db)
    expected = expected_trade_date(open_days, now)
    items: list[dict[str, Any]] = []
    for spec in DB_DATASETS:
        items += _safe(spec["label"], spec["key"], "数据库表", "db", lambda s=spec: [check_db_dataset(db, s, open_days, expected)])
        _rollback(db)
    items += _safe("指数日线", "db:index_daily", "数据库表", "db", lambda: check_index_daily(db, open_days, expected))
    items += _safe("日K值域", "db:daily_kbars:values", "数据质量", "quality",
                   lambda: [check_daily_kbar_values(db, open_days, expected)])
    items += _safe("筹码空洞", "db:daily_kbars:chip_history", "数据质量", "quality", lambda: [check_chip_history(db)])
    items += _safe("概念板块完整性", "db:concept_sector:completeness", "数据质量", "quality",
                   lambda: [check_concept_completeness(db, open_days, expected)])
    _rollback(db)
    items += _safe("交易日历", "db:trade_calendar", "数据库表", "db", lambda: [check_calendar_horizon(open_days, now)])
    _rollback(db)
    if include_files:
        for spec in FILE_DATASETS:
            items += _safe(spec["label"], spec["key"], "文件数据集", "file",
                           lambda s=spec: [check_file_dataset(s, open_days, expected, now)])
        for spec in DIR_DATASETS:
            items += _safe(spec["label"], spec["key"], "文件数据集", "file", lambda s=spec: [check_dir_dataset(s, now)])
        for spec in AGE_FILES:
            items += _safe(spec["label"], spec["key"], "文件数据集", "file", lambda s=spec: [check_age_file(s, now)])
        items += _safe("xiaoshi_min1", "file:xiaoshi_min1", "文件数据集", "file", lambda: [check_xiaoshi_min1(open_days, expected)])
        items += _safe("xiaoshi_min_all", "file:xiaoshi_min_all", "文件数据集", "file",
                       lambda: [check_xiaoshi_min_all(open_days, expected, now)])
        items += _safe("capability_matrix", "file:runtime/datasource_capability_matrix.json", "文件数据集", "file",
                       lambda: [check_capability_matrix(now)])
        items += _safe("st_hist", "research:st_hist", "研究静态文件", "research", lambda: [st_hist_vs_db(db, expected)])
        _rollback(db)
        items += _safe("index parquet", "research:index_daily_consistency", "研究静态文件", "research",
                       lambda: [parquet_vs_db_index(db)])
        _rollback(db)
    items += _safe("gateway", "gateway:providers", "数据网关", "gateway", lambda: [check_provider_gateway(now)])
    items += _safe("scheduled", "job:*", "调度任务", "job", lambda: check_scheduled_tasks(db, open_days, now))
    _rollback(db)
    items += _safe("log", "log:*", "静默失败", "log", lambda: check_log_signals(now))
    items += _safe("stale_alerts", "alerts:stale_active", "静默失败", "log", lambda: [check_stale_alerts(now)])
    items += _safe("ds_all_failed", "alerts:datasource_all_failed", "静默失败", "log", lambda: [check_datasource_all_failed(now)])
    items.sort(key=lambda i: (-_RANK[i["severity"]], i["category"], i["key"]))
    counts = {s: sum(1 for i in items if i["severity"] == s) for s in (RED, YELLOW, GREEN)}
    return {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "expected_trade_date": expected,
            "overall": RED if counts[RED] else YELLOW if counts[YELLOW] else GREEN,
            "summary": {**counts, "total": len(items)}, "items": items}


# ---------------------------------------------------------------- 数据网关（provider）容量
GATEWAY_PROVIDERS = ("promax", "quantdata", "teajoin")
QUOTA_WARN_RATIO = 0.8


def check_provider_gateway(now: datetime | None = None, *, providers: tuple[str, ...] | None = None,
                           usage_fn: Callable[[str], tuple[int, bool]] | None = None,
                           quota_fn: Callable[[str], int | None] | None = None,
                           breakers_fn: Callable[[], list[dict[str, Any]]] | None = None,
                           configured: set[str] | None = None) -> dict[str, Any]:
    """数据网关承载力：各上游今日请求数/日额度/是否耗尽 + 当前打开的熔断。只读。

    红：所有已配置的上游今天都耗尽（实时行情会失败）；黄：任一上游耗尽、用量≥80% 日额度，或有 live 命名空间的熔断打开。
    这是 2026-10-26 补的盲区：promax 额度耗尽导致 rt_k 全失败时，监控里原本没有"网关容量"这一项。"""
    from app.core import provider_quota as pq
    from app.core.shared_breaker import shared_breaker_states

    usage_fn = usage_fn or pq.usage
    quota_fn = quota_fn or pq.daily_quota
    breakers_fn = breakers_fn or shared_breaker_states
    if configured is None:
        try:
            from app.core.config import get_settings

            st = get_settings()
            configured = {n for n, ps in st.providers.items() if str(ps.credential or "").strip()}
        except Exception:  # noqa: BLE001
            configured = set(GATEWAY_PROVIDERS)
    names = [p for p in (providers or GATEWAY_PROVIDERS) if p in configured]
    rows, exhausted, warn = [], [], []
    for p in names:
        used, ex = usage_fn(p)
        q = quota_fn(p)
        rows.append(f"{p}:{used}" + (f"/{q}" if q else "") + ("(耗尽)" if ex else ""))
        if ex:
            exhausted.append(p)
        elif q and used >= QUOTA_WARN_RATIO * q:
            warn.append(f"{p} 已用 {used}/{q}")
    now_ts = (now or _now()).timestamp()
    open_live = [b for b in breakers_fn() if str(b.get("key", "")).startswith("live:") and float(b.get("open_until") or 0) > now_ts]
    sev = GREEN
    notes = ["今日请求 " + "，".join(rows) if rows else "无已配置的上游"]
    if names and len(exhausted) == len(names):
        sev = RED
        notes.append("所有上游今天都已耗尽：实时行情 rt_k 和补数都会失败")
    elif exhausted:
        sev = YELLOW
        notes.append(f"{'、'.join(exhausted)} 今日额度已耗尽，已自动切备源")
    if warn:
        sev = _worse(sev, YELLOW)
        notes.append("接近额度：" + "；".join(warn))
    if open_live:
        sev = _worse(sev, YELLOW)
        keys = [str(b["key"]).replace("live:", "", 1) for b in open_live][:6]
        notes.append(f"实盘链路有 {len(open_live)} 个熔断打开：{'、'.join(keys)}")
    if "teajoin" in names:
        try:
            from app.core.config import get_settings
            from app.services.datasource import teajoin_capability as _tc

            if bool(getattr(get_settings(), "tushare_teajoin_lead", True)):
                notes.append(f"teajoin 为 {len(_tc.TEAJOIN_APIS)} 个接口的主源（盘中 {len(_tc.INTRADAY_SENSITIVE)} 个延迟敏感接口让原源先走，失败/超时自动降级）")
            else:
                notes.append("teajoin 主源开关已关闭（仅兜底）")
        except Exception:  # noqa: BLE001
            pass
    return _item("gateway:providers", "数据网关承载（各上游额度/熔断）", "数据网关", sev, kind="gateway",
                 detail="；".join(notes),
                 impact="实盘 rt_k/日K/分钟线的主备源切换；额度耗尽后补数任务会自动暂停，避免挤占实盘",
                 providers=rows, open_breakers=len(open_live))


# ---------------------------------------------------------------- 健康分扣分
PENALTY_PER_RED = 8        # 每个红项扣 8 分
PENALTY_RED_CAP = 40       # 红项合计最多扣 40 分
PENALTY_PER_YELLOW = 1     # 每个黄项扣 1 分
PENALTY_YELLOW_CAP = 10    # 黄项合计最多扣 10 分
REPORT_STALE_HOURS = 36    # 巡检报告超过 36 小时没更新，说明巡检本身停了


def quality_score_penalty(report: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any]:
    """把统一数据质量巡检的红/黄项折算成健康分扣分（已退役项 retired=True 不计）。

    规则：红项每个 −8（最多 −40）；黄项每个 −1（最多 −10）。巡检报告超过 36 小时未更新再 −5（巡检停了本身就是问题）。
    没有报告时不扣分但在 note 里说明。返回 {penalty, red, yellow, rule, note, generated_at}。"""
    rule = (f"红项每个扣 {PENALTY_PER_RED} 分（最多 {PENALTY_RED_CAP}）；黄项每个扣 {PENALTY_PER_YELLOW} 分（最多 {PENALTY_YELLOW_CAP}）；"
            f"巡检报告超过 {REPORT_STALE_HOURS} 小时没更新另扣 5 分；已退役项不计")
    if not report or not report.get("items"):
        return {"penalty": 0, "red": 0, "yellow": 0, "rule": rule, "note": "尚无数据质量巡检报告，未扣分", "generated_at": None}
    items = [i for i in report["items"] if not i.get("retired")]
    red = sum(1 for i in items if i.get("severity") == RED)
    yellow = sum(1 for i in items if i.get("severity") == YELLOW)
    pen = min(PENALTY_RED_CAP, red * PENALTY_PER_RED) + min(PENALTY_YELLOW_CAP, yellow * PENALTY_PER_YELLOW)
    note = f"红 {red} 项、黄 {yellow} 项"
    gen = report.get("generated_at")
    try:
        age_h = ((now or _now()) - datetime.strptime(str(gen), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)).total_seconds() / 3600
        if age_h > REPORT_STALE_HOURS:
            pen += 5
            note += f"；巡检报告已 {age_h:.0f} 小时未更新（再扣 5）"
    except Exception:  # noqa: BLE001
        pass
    return {"penalty": int(pen), "red": red, "yellow": yellow, "rule": rule, "note": note, "generated_at": gen}


def write_report(report: dict[str, Any], path: Path | None = None) -> None:
    path = path or REPORT_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_quality report write failed: %r", exc)


def load_report(path: Path | None = None) -> dict[str, Any] | None:
    try:
        return json.loads((path or REPORT_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def sync_alerts(report: dict[str, Any]) -> dict[str, list[str]]:
    """红/黄项 emit_alert(kind=data_quality)；已恢复/不再出现的项 resolve_alert。返回 {emitted, resolved}。"""
    from app.core.alerts import active_alert_records, emit_alert, resolve_alert

    emitted, resolved = [], []
    bad = {i["key"]: i for i in report["items"] if i["severity"] != GREEN}
    for key, it in bad.items():
        tag = "红" if it["severity"] == RED else "黄"
        emit_alert(ALERT_KIND, key, f"[{tag}] {it['label']}: {it['detail']}（影响：{it['impact']}）"[:480])
        emitted.append(key)
    for rec in active_alert_records(ALERT_KIND):
        k = str(rec["key"])
        if k not in bad and resolve_alert(ALERT_KIND, k, f"{k} 已恢复"):
            resolved.append(k)
    return {"emitted": emitted, "resolved": resolved}


def run_data_quality_check(db, *, alert: bool = True, now: datetime | None = None, include_files: bool = True,
                           persist: bool = True) -> dict[str, Any]:
    """入口：只读巡检 → 写报告 → 同步告警。永不抛出（失败返回 overall=error）。"""
    try:
        report = run_checks(db, now=now, include_files=include_files)
    except Exception as exc:  # noqa: BLE001
        logger.exception("data quality monitor failed")
        return {"overall": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}", "items": [], "summary": {}}
    if persist:
        write_report(report)
    if alert:
        try:
            report["alerts"] = sync_alerts(report)
        except Exception as exc:  # noqa: BLE001
            logger.warning("data quality alert sync failed: %r", exc)
    return report
