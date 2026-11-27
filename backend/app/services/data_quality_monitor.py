"""统一数据新鲜度与质量监控（只读）：数据库表 + data/ 下的文件数据集 + 研究静态文件 + 调度任务 + 日志静默失败。

2026-10-26 背景（该数据源已于 2026-11-15 整源退役并删除本地存档，相关检查项已移除）：data/xiaoshi_min1 停在 2026-08-07 两个多月没人发现——原有巡检（data_freshness.py / data_health_service.py）
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
import time
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
# 巡检告警的稳态重复冷却。默认 10 分钟的全局冷却对「每 2 小时一次的巡检」毫无抑制：
# 同一批红/黄项每轮都重发一条 [ALERT:data_quality] ERROR，9 个坏项一天能刷出几十条，
# 反过来把 log:error_volume（近 24h ERROR > 30）顶成黄项，黄项又新增一轮告警——自激。
# 稳态重复项按日级冷却；首次出现、红↔黄状态变化仍然立即告警，不掩盖新增/恶化。
# 根因侧另见 scan_log_signals：告警通知本身是 logger.error 落盘的，必须从「应用异常」计数里剔除。
QUALITY_ALERT_COOLDOWN_SECONDS = float(os.environ.get("ASHARE_QUALITY_ALERT_COOLDOWN_SECONDS", "") or 24 * 3600)
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
# 能力矩阵由 launchd 每周六 10:00 重测一次（deploy/launchd/com.wangao.ashare-datasource-probe.plist，
# 刻意避开交易时段以免占配额）。阈值必须 ≥ 探测周期：原来写死 3 天，配上周频探测等于每周有 4 天
# 必然显示「超期」，把一个周期正常的文件报成常态化黄项（2026-11-18 实测 age=4.0 天，见 L-207）。
# 给 1 天宽限（7+1）：漏跑一次周六才报警。
CAPABILITY_MATRIX_MAX_AGE_DAYS = 8


def _now() -> datetime:
    return datetime.now(_CST)


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def _item(key: str, label: str, category: str, severity: str, *, kind: str, latest: str | None = None,
          expected: str | None = None, lag: int | None = None, detail: str = "", impact: str = "",
          provisional: bool = False, **extra: Any) -> dict[str, Any]:
    out = {"key": key, "label": label, "category": category, "kind": kind, "severity": severity,
           "latest": latest, "expected": expected, "lag_trade_days": lag, "detail": detail, "impact": impact,
           "provisional": provisional, "scope": item_scope(key)}
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


SLOW_CHECK_SECONDS = 5.0
_CHECK_TIMINGS: list[tuple[str, float]] = []


def _safe(label: str, key: str, category: str, kind: str, fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    started = time.monotonic()
    try:
        items = fn()
    except Exception as exc:  # noqa: BLE001 - 一项检查失败不得影响其它检查，更不能阻塞主流程
        logger.warning("data_quality check %s failed: %r", key, exc)
        items = [_item(key, label, category, YELLOW, kind=kind, detail=f"检查本身失败 {type(exc).__name__}: {str(exc)[:160]}",
                       impact="该项无法判断，需人工排查监控代码")]
    elapsed = time.monotonic() - started
    _CHECK_TIMINGS.append((key, elapsed))
    if elapsed >= SLOW_CHECK_SECONDS:
        # 2026-11-18：早间巡检实测跑了 92 分钟（单独跑只要 60 秒），原因是 09:00 机器唤醒后
        # 五个重任务同时抢库。没有分项计时就只能看到「一整块慢」，无法判断被谁拖住。
        logger.warning("data_quality check %s 耗时 %.1fs（慢检查：多为数据库/IO 争用）", key, elapsed)
    return items


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
     "lag": 1, "cliff": True, "source_lag_prone": True, "impact": "融资类过滤（来源盘后晚出，允许滞后 1 日）"},
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
                if spec.get("source_lag_prone") and latest_day == expected:
                    # 两融这类盘后 T+1 才全量发布的源：最新日行数腰斩通常是上游还没发完，
                    # 不是系统漏拉。只补上下文方便判断，severity 照常判红、不改口径。
                    cmsg += "（该源 T+1 全量发布，当日行数腰斩多为上游未发完；系统按待补清单在下一周期重试）"
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


FINA_COV_YELLOW = 0.97  # 披露截止日已过的报告期，可交易 A 股（0/3/6 开头）覆盖率低于此值黄
FINA_COV_RED = 0.90
_FINA_DEADLINE = {"0331": ("04", "30", 0), "0630": ("08", "31", 0), "0930": ("10", "31", 0), "1231": ("04", "30", 1)}


def report_deadline(period: str) -> str:
    m, d, plus = _FINA_DEADLINE[period[4:]]
    return f"{int(period[:4]) + plus}{m}{d}"


def due_report_periods(today: str, n: int = 3) -> list[str]:
    """披露截止日（一季报 4/30、半年报 8/31、三季报 10/31、年报次年 4/30）已过的最近 n 个报告期，新到旧。"""
    y = int(today[:4])
    cands = [f"{yy}{md}" for yy in (y, y - 1, y - 2) for md in ("1231", "0930", "0630", "0331")]
    return sorted((p for p in cands if report_deadline(p) < today.replace("-", "")), reverse=True)[:n]


FINA_GW_MISS_YELLOW = 10  # 库里有、网关最近一次按期拉取却没有的可交易股票数（完整时 0~1 只；11-05 截断时 119 只）
FINA_GW_MISS_RED = 50


def fina_coverage_item(periods: list[str], expected: dict[str, int], db_codes: dict[str, set[str]],
                       snap: dict[str, Any]) -> dict[str, Any]:
    """expected：截止日前已上市的可交易 A 股数；db_codes：库里该期的可交易股票（∩ 截止日前已上市）；snap：网关快照。"""
    sev, parts = GREEN, []
    for p in periods:
        exp = max(1, int(expected.get(p) or 0))
        dset = db_codes.get(p) or set()
        r = len(dset) / exp
        s = RED if r < FINA_COV_RED else YELLOW if r < FINA_COV_YELLOW else GREEN
        txt = f"{p} 库 {len(dset)}/{exp}（{min(r, 1):.0%}）"
        g = snap.get(p)
        if g:
            miss = sorted(dset - set(g.get("tradable") or ()))
            gs = RED if len(miss) >= FINA_GW_MISS_RED else YELLOW if len(miss) >= FINA_GW_MISS_YELLOW else GREEN
            if g.get("row_cap_multiple"):
                gs = RED
            s = _worse(s, gs)
            txt += (f"，网关最近一次（{g.get('fetched_at')}，{g.get('source')}，{g.get('rows')} 行）比库少 {len(miss)} 只"
                    + (f"（{miss[0]}…{miss[-1]}）" if miss else "")
                    + ("，行数正好是 10000 的整数倍，疑似被截断" if g.get("row_cap_multiple") else ""))
        sev = _worse(sev, s)
        parts.append(txt)
    return _item("db:fina_period_coverage", "按报告期财报覆盖率", "数据质量", sev, kind="quality",
                 latest=periods[0] if periods else None,
                 detail="；".join(parts) + f"（可交易 A 股口径，黄<{FINA_COV_YELLOW:.0%} 红<{FINA_COV_RED:.0%}）",
                 impact="财报类中线策略（业绩加速、SUE 家族、连续双增、费用率等）漏股票就漏信号；"
                        "2026-11-05 曾因 teajoin 单次 10000 行上限截断半年报，科创板尾部 121 只缺失")


def check_fina_period_coverage(db, now: datetime | None = None, snap_path: Path | None = None) -> dict[str, Any]:
    """最近 3 个已过披露截止日的报告期：库 stock_fina_indicator 与网关最近一次按期拉取的覆盖率。只读。"""
    from app.services.datasource import fina_coverage

    now = now or _now()
    periods = due_report_periods(now.strftime("%Y%m%d"))
    expected, db_codes = {}, {}
    for p in periods:
        dl = report_deadline(p)
        expected[p] = int(db.execute(text(
            "SELECT COUNT(*) FROM stocks WHERE LEFT(code,1) IN ('0','3','6') AND REPLACE(list_date,'-','')<=:d"),
            {"d": dl}).scalar() or 0)
        db_codes[p] = {str(r[0]) for r in db.execute(text(
            "SELECT DISTINCT f.code FROM stock_fina_indicator f JOIN stocks s ON s.code=f.code "
            "WHERE f.end_date=:p AND LEFT(f.code,1) IN ('0','3','6') AND REPLACE(s.list_date,'-','')<=:d"),
            {"p": p, "d": dl})}
    return fina_coverage_item(periods, expected, db_codes, fina_coverage.load(snap_path))


LOCAL_FINA_MISS_YELLOW = 10  # 库里有、本地逐股文件没有的可交易股票数（刷新后 0~2 只；11-05 修前三季报缺 5186 只）
LOCAL_FINA_MISS_RED = 50


def local_fina_item(periods: list[str], db_codes: dict[str, set[str]], local_codes: dict[str, set[str]],
                    covered_through: str | None, lag: int | None) -> dict[str, Any]:
    sev = GREEN if covered_through else YELLOW
    parts = [f"刷新清单覆盖到 {covered_through}（落后 {lag} 个交易日）" if covered_through
             else "没有刷新清单 _manifest.json（从未增量刷新过，重算按最大公告日判断覆盖）"]
    if covered_through:
        sev = _worse(sev, lag_severity(lag, allowed=0))
    for p in periods:
        miss = sorted((db_codes.get(p) or set()) - (local_codes.get(p) or set()))
        s = RED if len(miss) >= LOCAL_FINA_MISS_RED else YELLOW if len(miss) >= LOCAL_FINA_MISS_YELLOW else GREEN
        sev = _worse(sev, s)
        parts.append(f"{p} 本地 {len(local_codes.get(p) or ())} 只，比库少 {len(miss)} 只" + (f"（{miss[0]}…{miss[-1]}）" if miss else ""))
    return _item("file:midterm/fina:coverage", "本地逐股财务指标库覆盖", "数据质量", sev, kind="quality",
                 latest=norm_date(covered_through), detail="；".join(parts),
                 impact="近12个月滚动重算按本地覆盖日决定读本地还是走网关；SUE 家族的历史 SUE 全靠它，缺期就算不出、静默少票；"
                        "fina_indicator_vip 不可用时的备用基线")


def check_local_fina(db, open_days: list[str], expected: str, now: datetime | None = None,
                     fina_dir: Path | None = None) -> dict[str, Any]:
    """本地 data/midterm/fina：刷新清单落后几个交易日 + 最近 3 个已过截止日的报告期逐只比库。只读。"""
    from app.services import local_fina_refresh as lfr

    now = now or _now()
    d = fina_dir or lfr.FINA_DIR
    periods = due_report_periods(now.strftime("%Y%m%d"))
    db_codes = {}
    for p in periods:
        db_codes[p] = {str(r[0]) for r in db.execute(text(
            "SELECT DISTINCT f.code FROM stock_fina_indicator f JOIN stocks s ON s.code=f.code "
            "WHERE f.end_date=:p AND LEFT(f.code,1) IN ('0','3','6') AND REPLACE(s.list_date,'-','')<=:d"),
            {"p": p, "d": report_deadline(p)})}
    local = lfr.local_period_index(d, periods)
    ct = str(lfr.load_manifest(d).get("covered_through") or "") or None
    lag = trade_lag(open_days, norm_date(ct), expected) if ct else None
    return local_fina_item(periods, db_codes, local, ct, lag)


def check_index_daily(db, open_days: list[str], expected: str) -> list[dict[str, Any]]:
    out = []
    rows = db.execute(text("SELECT ts_code, MAX(trade_date), COUNT(*) FROM index_daily GROUP BY ts_code")).all()
    seen = {r[0]: (norm_date(r[1]), int(r[2])) for r in rows}
    for code in INDEX_CODES:
        mx, n = seen.get(code, (None, 0))
        lag = trade_lag(open_days, mx, expected)
        sev = lag_severity(lag, 0)
        detail = f"最新 {mx}，共 {n} 行" + ("" if sev == GREEN else f"，滞后 {lag} 个交易日")
        impact = "小盘指数20日线开关(000905)、沪深300超额对照、市场环境判断"
        out.append(_item(f"db:index_daily:{code}", f"指数日线 {code}", "数据库表", sev, kind="db", latest=mx,
                         expected=expected, lag=lag, detail=detail, impact=impact))
    out.extend(_check_qvix_freshness(seen, open_days, expected))
    dup = db.execute(text("SELECT COUNT(*) FROM (SELECT ts_code, trade_date FROM index_daily GROUP BY ts_code, trade_date "
                          "HAVING COUNT(*) > 1) x")).scalar()
    if dup:
        out.append(_item("db:index_daily:dup", "指数日线重复键", "数据库表", RED, kind="db",
                         detail=f"{dup} 组重复 (ts_code,trade_date)", impact="指数超额/20日线计算"))
    return out


# QVIX（期权隐含波动率）写 index_daily，由 qvix_daily_refresh 每日 21:10 增量。
# rsi6-capitulation 的 QVIX 尖峰分支在字段缺失时静默降级为「仅价格门」，所以它一旦停更
# 必须有人知道——2026-11-23 体检时已停更 8 个交易日（QVIX50/QVIX300 最新 11-13，主指数已到 11-23）。
# 上游本身有 2~3 个交易日发布延迟，2026-11-19 起上游只给空行；按 5 个交易日容忍，超过才黄。
# QVIX 单独检查，不并入 INDEX_CODES：真实指数按 0 日滞后判，QVIX 需要 5 日容忍，两套口径不能共用 key。
_QVIX_CODES = ("QVIX50.SH", "QVIX300.SH")
_QVIX_MAX_LAG_TRADE_DAYS = 5


def _check_qvix_freshness(seen: dict[str, tuple[str | None, int]], open_days: list[str],
                          expected: str) -> list[dict[str, Any]]:
    out = []
    for code in _QVIX_CODES:
        mx, n = seen.get(code, (None, 0))
        lag = trade_lag(open_days, mx, expected)
        sev = YELLOW if (mx is None or lag > _QVIX_MAX_LAG_TRADE_DAYS) else GREEN
        detail = f"最新 {mx}，共 {n} 行" + ("" if sev == GREEN else f"，滞后 {lag} 个交易日")
        if sev != GREEN:
            detail += "；rsi6-capitulation 的 QVIX 分支会静默降级为仅价格门，跑 scripts/backfill_qvix.py 补"
        out.append(_item(f"db:index_daily:{code}", f"波动率指数 {code}", "数据库表", sev, kind="db", latest=mx,
                         expected=expected, lag=lag, detail=detail,
                         impact="rsi6-capitulation 恐慌出清策略的 QVIX 尖峰条件（缺失时静默降级）"))
    return out


# 注册制新股上市首 5 个交易日不设涨跌幅限制（主板/创业板/科创板/北交所均如此），
# 首日 +397% 是合法行情而非数据错误（2026-11-03 001381 C皇冠新材即如此）。
# 不剔除的话，每上一只新股就把这条检查刷黄，真异常反而被噪音淹掉。
# 按自然日给 10 天窗口（5 个交易日≈7 个自然日，留余量），只豁免「涨跌幅越界」，
# 价格类硬错误（≤0、最高<最低、负成交量）无论是否新股都要报。
_NEW_LISTING_GRACE_DAYS = 10


def _parse_day(v: Any) -> date | None:
    """'20261023' / '2026-10-23' / date → date；无法识别返回 None。"""
    s = norm_date(v)
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _new_listing_codes(db, codes: list[str], start: str) -> set[str]:
    """返回 start 之后上市不满 _NEW_LISTING_GRACE_DAYS 个自然日的股票代码。

    查不到 stocks（表缺失/字段缺失/查询报错）时返回空集：豁免是减噪音的增强项，
    不能因为它把整条值域检查拖成「检查失败」。
    """
    out: set[str] = set()
    start_day = _parse_day(start)
    if start_day is None:
        return out
    for code in codes:
        try:
            raw = db.scalar(text("SELECT list_date FROM stocks WHERE code = :c"), {"c": code})
        except Exception:  # noqa: BLE001 - 豁免查不到就退回「照常报异常」
            return set()
        ld = _parse_day(raw or "")
        if ld is not None and (start_day - ld).days <= _NEW_LISTING_GRACE_DAYS:
            out.add(code)
    return out


def check_daily_kbar_values(db, open_days: list[str], expected: str) -> dict[str, Any]:
    """最近 3 个交易日日K值域：收盘/开盘 ≤0、最高<最低、|涨跌幅|>31%、成交量<0、零成交占比 >15%。

    新股上市初期的涨跌幅越界不算异常（见 _NEW_LISTING_GRACE_DAYS），剔除条数单独回报以便审计。
    """
    start = _window_start(open_days, expected, 3)
    rows = db.execute(text(
        "SELECT code, trade_date, close, pct_chg, "
        "(close <= 0 OR open <= 0 OR high < low OR volume < 0) AS price_bad "
        "FROM daily_kbars WHERE trade_date >= :s AND "
        "(close <= 0 OR open <= 0 OR high < low OR volume < 0 OR ABS(pct_chg) > 31)"), {"s": start}).all()
    tot, zero = db.execute(text("SELECT COUNT(*), SUM(CASE WHEN volume = 0 THEN 1 ELSE 0 END) FROM daily_kbars "
                                "WHERE trade_date >= :s"), {"s": start}).one()
    zero_ratio = float(zero or 0) / float(tot) if tot else 0.0
    sev, parts = GREEN, []
    pct_only = [r for r in rows if not int(r[4] or 0)]
    new_listing = _new_listing_codes(db, sorted({r[0] for r in pct_only}), start) if pct_only else set()
    exempted = [r for r in pct_only if r[0] in new_listing]
    rows = [r for r in rows if not (r[0] in new_listing and not int(r[4] or 0))]
    if rows:
        sev = RED if len(rows) > 20 else YELLOW
        sample = "、".join(f"{r[0]}@{norm_date(r[1])}(收{r[2]},涨跌幅{r[3]})" for r in rows[:6])
        parts.append(f"{len(rows)} 行值域异常：{sample}")
    if zero_ratio > 0.15:
        sev = RED if zero_ratio > 0.4 else _worse(sev, YELLOW)
        parts.append(f"零成交占比 {zero_ratio:.1%}")
    if exempted:
        parts.append(f"已排除 {len(exempted)} 行新股上市初期涨跌幅（无涨跌幅限制）："
                     + "、".join(f"{r[0]}@{norm_date(r[1])}" for r in exempted[:4]))
    detail = "；".join(parts) or f"最近 3 日 {int(tot or 0)} 行值域正常（零成交 {zero_ratio:.1%}）"
    return _item("db:daily_kbars:values", "日K值域/异常值", "数据质量", sev, kind="quality", latest=expected, expected=expected,
                 detail=detail,
                 impact="异常价格会污染涨跌幅/涨停判断、回测收益",
                 anomalous_rows=len(rows), new_listing_exempted=len(exempted))


def check_margin_columns(db, open_days: list[str], expected: str) -> dict[str, Any]:
    """两融明细的金额列缺口：行数齐全也可能整批列为 NULL。

    2026-11-24 体检：11 个交易日行数完整（4490 行）而全部深市行的 rzche 为 NULL，沪市完整。
    行数口径的 db:margin_detail 全绿，缺口存在 6 周无人发现。rzche 缺失会让
    「融资净买入 = 买入 − 偿还」把买入额整个当成净流入。
    """
    from app.services.margin_detail_fill import detect_column_gaps

    key, label = "db:margin_detail:columns", "两融金额列完整性"
    impact = "偿还额缺失会把融资买入额整个当成净买入，凭空造出一倍净流入"
    start = _window_start(open_days, expected, 5)
    days = [d for d in open_days if start <= d <= expected]  # open_days 含未来日期，必须同时卡上界
    bad: list[str] = []
    for d in days:
        gaps = detect_column_gaps(db, d)
        if gaps:
            bad.append(f"{d}(" + "、".join(f"{f} 缺{n}行" for f, n in sorted(gaps.items())) + ")")
    if not bad:
        return _item(key, label, "数据质量", GREEN, kind="quality", latest=expected, expected=expected,
                     detail=f"最近 {len(days)} 个交易日金额列无整批缺口", impact=impact)
    return _item(key, label, "数据质量", RED, kind="quality", latest=expected, expected=expected,
                 detail=f"{len(bad)} 个交易日金额列整批为 NULL：{'；'.join(bad[:4])}",
                 impact=impact, anomalous_days=len(bad))


def check_share_float_ratio(db) -> dict[str, Any]:
    """解禁比例口径：share_floats.float_ratio 是占总股本 %，超过 100 即口径错误。

    2026-11-24 体检：xiaoshi 回退源写入的 958 行里 160 行 >100%（最大 1546.97%），
    因为上游 unlock_ratio 的分母是流通股本而非总股本。这些值经 feature_builder 汇总后
    在推荐里输出「占总股本 524.2%」这种不可能的风险提示，且长期没有任何检查覆盖。
    """
    key, label = "db:share_floats:ratio", "解禁比例口径（占总股本%）"
    impact = "比例超 100% 会让解禁风险提示输出不可能的数字，并抬高回避门槛"
    rows = db.execute(text(
        "SELECT code, float_date, float_ratio, holder_name FROM share_floats "
        "WHERE float_ratio > 100 ORDER BY float_ratio DESC LIMIT 200"
    )).all()
    if not rows:
        return _item(key, label, "数据质量", GREEN, kind="quality", detail="全部解禁比例在 0~100% 区间", impact=impact)
    sample = "、".join(f"{r[0]}@{norm_date(r[1])}({float(r[2]):.1f}%)" for r in rows[:6])
    return _item(key, label, "数据质量", RED, kind="quality",
                 detail=f"{len(rows)} 行解禁比例 >100%（占总股本口径不可能）：{sample}",
                 impact=impact, anomalous_rows=len(rows))


CHIP_HOLE_WINDOW_DAYS = 30


def check_chip_history(db) -> dict[str, Any]:
    """筹码列整日为空/全零的交易日（近 CHIP_HOLE_WINDOW_DAYS 个交易日）。

    原版窗口 120 天、且用 `trade_date >= (SELECT MIN(d) FROM (... DISTINCT ... LIMIT 120))` 子查询，
    在 600 万行 / 3GB 的 daily_kbars 上实测 106 秒（整轮巡检 2m33s 的主要来源，`refresh=true` 的 HTTP
    请求会直接超时）。新空洞只可能出现在最近的交易日，历史空洞一旦存在也不会自愈，因此把窗口收到
    30 个交易日、并先走 trade_date 索引取日期列表，扫描量降一个数量级。
    """
    days = [norm_date(r[0]) for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars ORDER BY trade_date DESC LIMIT :n"),
        {"n": CHIP_HOLE_WINDOW_DAYS}).all()]
    if not days:
        return _item("db:daily_kbars:chip_history", "筹码列空洞（近30个交易日）", "数据质量", GREEN, kind="quality",
                     detail="daily_kbars 无数据", impact="用筹码的短线策略在这些日子无信号/被误判")
    rows = db.execute(text(
        "SELECT trade_date FROM daily_kbars WHERE trade_date >= :s GROUP BY trade_date "
        "HAVING SUM(CASE WHEN chip_cost_50 IS NOT NULL AND chip_cost_50 > 0 THEN 1 ELSE 0 END) < 0.05 * COUNT(*) "
        "ORDER BY trade_date"), {"s": min(d for d in days if d)}).scalars().all()
    holes = [norm_date(r) for r in rows]
    return _item("db:daily_kbars:chip_history", "筹码列空洞（近30个交易日）", "数据质量", YELLOW if holes else GREEN,
                 kind="quality",
                 detail=(f"{len(holes)} 个交易日筹码列整日为空/0：{','.join(holes[-8:])}" if holes
                         else f"近 {len(days)} 个交易日筹码列无整日空洞"),
                 impact="用筹码的短线策略在这些日子无信号/被误判；回放与回测在这些日子失真",
                 missing_days=holes, window_days=len(days))


def check_calendar_horizon(open_days: list[str], now: datetime) -> dict[str, Any]:
    last = open_days[-1] if open_days else None
    ahead = (date.fromisoformat(last) - now.date()).days if last else -1
    sev = RED if ahead < 5 else YELLOW if ahead < 30 else GREEN
    return _item("db:trade_calendar", "交易日历覆盖（未来）", "数据库表", sev, kind="db", latest=last,
                 detail=f"日历最远到 {last}，距今 {ahead} 天（黄<30 红<5）",
                 impact="期望最新交易日/滞后判断、调度任务漏跑判断都依赖日历")


# --------------------------------------------------------------------------- 文件数据集
def _normalize_date_values(values) -> list[str]:
    out = []
    for value in values:
        text = str(value).replace("-", "")[:8]
        if len(text) == 8 and text.isdigit():
            out.append(text)
    return out


def _read_date_values(path: Path, cols: tuple[str, ...]) -> list[str]:
    """读 parquet 日期列的 min/max。优先用 row-group 统计，列缺统计时才读该列。

    实测（2026-11-24 体检）stk_limits.parquet 读整列 3.135s、daily_kbar.parquet 2.329s；
    row-group 统计分别是 0.0027s / 0.0021s。
    """
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    names = list(pf.schema.names)
    for c in cols:
        if c not in names:
            continue
        index = names.index(c)
        mins: list = []
        maxs: list = []
        has_stats = True
        for rg in range(pf.metadata.num_row_groups):
            stats = pf.metadata.row_group(rg).column(index).statistics
            if stats is None or not stats.has_min_max:
                has_stats = False
                break
            mins.append(stats.min)
            maxs.append(stats.max)
        if has_stats:
            vals = _normalize_date_values([*mins, *maxs])
        else:
            vals = _normalize_date_values(pf.read(columns=[c]).column(0).to_pylist())
        if vals:
            return [max(vals), min(vals)]
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
     # 这是季频数据，不是日频：hk_hold 上游只在季末（3-31/6-30/9-30/12-31）发布。2026-11-18 实测
     # hk_holdings 表 2025 年以来的日期只有这四种，最新 2026-09-30。原阈值 (45,130) 会在每个季度
     # 中段（季末 +45 天起、到下一季末数据到达前）必然报黄，属口径错误、不是数据故障。
     # 改为：季末 +135 天仍未出现新季度 → 黄；+165 天 → 红（留出上游发布延迟）。
     "glob": "midterm/hk_holdings.parquet", "cols": ("trade_date",), "event_days": (135, 165), "max": YELLOW,
     "impact": "研究用（季度快照）"},
    {"key": "file:research_cache/kpl_list", "label": "开盘啦涨停榜缓存(kpl)", "mode": "daily",
     "glob": "research_cache/kpl_list/kpl_20[0-9][0-9].parquet", "cols": ("trade_date",), "max": YELLOW,
     "impact": "热点题材/龙头类研究；由 research_kpl_list_daily 每交易日 23:30 增量补齐，开机自愈也会补最近窗口"},
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
     # 业绩预告是强季节性事件：年报预告 1 月、一季报预告 4 月、中报预告 7 月、三季报预告 10 月，
     # 这四个活跃月合计占全年 >99%，2/3/5/6/8/9/11/12 月合计不足 1%（实测 202511 全月 17 条、
     # 202611 至今 0 条）。淡月里文件不增长是上游本就没有预告，不是停更，故放宽到 (45, 90)；
      # 但要求已跟上「最近一个活跃月」，否则说明真跳过了窗口，仍按严格阈值判。
     "seasonal": {"active_months": (1, 4, 7, 10), "thresholds": (45, 90)},
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

# ── 健康分分层：研究/回测产物不计入实盘健康分 ──────────────────────────────
# 这些 parquet 是 data/midterm|cache|research_cache 下的中间产物，由手动脚本或研究任务产出，
# **没有日更调度**，长期滞后是设计常态而不是故障。2026-11-23 体检：74 项里 19 项属此类，
# 黄项里过半来自它们，导致健康分理论上限被压掉 5~10 分，「每天都有黄项」成为必然。
# 处理方式：仍然巡检、仍然在面板展示（scope="research"），只是不参与扣分。
# 明确不在本表里的（属实盘读取链路）：basic_<year>.parquet（线上高股息/低市销率）、
# midterm/events/*_live（线上中线策略）、midterm/fina 目录（财报类长线基线）。
RESEARCH_ONLY_FILE_KEYS = frozenset({
    "file:midterm/daily_basic.parquet",
    "file:midterm/stk_limits.parquet",
    "file:midterm/index_daily.parquet",
    "file:midterm/hk_holdings.parquet",
    "file:cache/daily_kbar.parquet",
    "file:research_cache/kpl_list",
    "file:research_cache/limit_list_d",
    "file:midterm/events/holdertrade_in",
    "file:midterm/events/holdertrade_de",
    "file:midterm/forecast",
    "file:midterm/dividend",
    "file:midterm/holdernum",
})


def item_scope(key: str) -> str:
    """检查项的计分范围：research 只看不扣分，trading 参与健康分。"""
    return "research" if key in RESEARCH_ONLY_FILE_KEYS else "trading"


AGE_FILES: list[dict[str, Any]] = [
    {"key": "file:industry_cache.json", "label": "行业分类缓存 industry_cache.json", "path": "industry_cache.json",
     "yellow_days": 60, "red_days": 240, "max": YELLOW,
     "impact": "行业映射（板块相对强度、行业中性化）；新股/北交所行业会缺"},
    # 已移除 file:runtime/pool_bar_source_stats.json：它是 /api/data-sources/pool 的**按需缓存**
    # （data_sources._raw_bar_source_rows 在有人打开该页面且 bar 日期变化时才重算落盘），
    # 不是需要定期更新的数据集。文件 6 天没变只说明 6 天没人打开那个页面，不是数据故障；
    # 用 5 天阈值持续报黄属口径错误（2026-11-23 体检：9 天 22 次黄，无一对应真实问题）。
]


def _last_active_month_start(expected: str, active_months: set[int]) -> str | None:
    """返回不晚于 expected 的「最近一个活跃月」的首日（ISO），用作淡月豁免的下界。

    淡月放宽的隐含风险是：文件长期停更（比如卡在 6 月）而当前恰逢淡月，会被误判为正常。
    加这个下界即可堵住——文件最新日期必须已经进入最近一个活跃月，才承认它「跟上了」。
    """
    try:
        y, m = int(expected[:4]), int(expected[5:7])
    except (TypeError, ValueError):
        return None
    for _ in range(36):
        if m in active_months:
            return f"{y:04d}-{m:02d}-01"
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return None


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
        se = spec.get("seasonal")
        if se and sev is not GREEN:
            floor = _last_active_month_start(expected, set(se["active_months"]))
            if floor and mx >= floor:
                qy, qr = se["thresholds"]
                sev = RED if age > qr else YELLOW if age > qy else GREEN
                detail += (f"；季节性淡月口径（活跃月 {'/'.join(str(m) for m in se['active_months'])} 月之外"
                           f"历史占比<1%，已跟上最近活跃月 {floor}，放宽到黄>{qy} 红>{qr}）")
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


def check_capability_matrix(now: datetime | None = None, path: Path | None = None,
                            routing: dict | None = None) -> dict[str, Any]:
    p = path or (DATA_DIR / "runtime" / "datasource_capability_matrix.json")
    key, label = "file:runtime/datasource_capability_matrix.json", "数据源能力探测矩阵"
    impact = "只反映 Tushare 各接口(provider×api)是否可用，不覆盖 data/ 下的文件数据集——分钟K停更未被发现的原因之一"
    if not p.exists():
        return _item(key, label, "文件数据集", YELLOW, kind="file", detail="文件不存在", impact=impact)
    j = json.loads(p.read_text(encoding="utf-8"))
    probed = str(j.get("probed_at") or "")
    rows = j.get("rows") or []
    from app.services.datasource.capability import classify, load_routing_table

    # 2026-11-18：原来把 unsupported / unverified 全部算成「异常」，于是矩阵这个黄项永远不收敛。
    # 矩阵是 provider × api 的**全叉乘**，绝大多数组合本来就该是「这家不做这个接口」：
    # quantdata 没买 stk_mins、promax 没买 stk_factor、teajoin 不提供实时行情——这些是稳定事实，
    # 而且已经写进 routing_table 的 unsupported 列表，一致即正常。实测 114 项里 14 项「异常」
    # 全部属于此类（5 项 unsupported 已在路由表声明、9 项 unverified 是探测参数不被上游接受），
    # 真异常 0 项。见 L-207。
    # 现在只报真漂移：路由表把某 provider 排进了 order（认为它能服务该接口），实测却是硬拒绝。
    route = routing if routing is not None else load_routing_table()
    bad: list[str] = []
    transient = unverified = consistent = 0
    for r in rows:
        prov, api = str(r.get("provider") or ""), str(r.get("api") or "")
        cls = classify(r)
        if cls == "transient":
            transient += 1
        elif cls == "unverified":
            unverified += 1
        elif cls == "unsupported":
            if prov in tuple((route.get(api) or {}).get("order") or ()):
                bad.append(f"{prov}/{api}")
            else:
                consistent += 1
    age_days = None
    if probed:  # 缺 probed_at 记「未知」；有值但格式不对就地崩溃，不静默当成未知
        age_days = ((now or _now()) - datetime.strptime(probed, "%Y-%m-%dT%H:%M:%S%z")).total_seconds() / 86400
    sev = GREEN
    parts = [f"探测于 {probed}（{'%.1f 天前' % age_days if age_days is not None else '未知'}），{len(rows)} 项，异常 {len(bad)} 项"]
    if consistent:
        parts.append(f"{consistent} 项为已知不支持且与路由表一致")
    if unverified:
        # unverified 是「没取到结论」，不等于接口不可用（多为探测参数不被上游接受），只作计数。
        parts.append(f"{unverified} 项未取到能力结论（不代表接口不可用）")
    if transient:
        parts.append(f"另 {transient} 项因上游限流/额度耗尽未取到能力结论（见 数据网关承载）")
    if age_days is None or age_days > CAPABILITY_MATRIX_MAX_AGE_DAYS:
        sev = YELLOW
        parts.append(f"探测已超过 {CAPABILITY_MATRIX_MAX_AGE_DAYS} 天未更新")
    if bad:
        sev = _worse(sev, YELLOW)
        parts.append("路由表声明可用但实测不支持：" + "、".join(bad[:8]))
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
# 纯只读巡检作业：自身执行成功，却把「巡检结果有红/黄项」写成 partial_failed。
# 那些红黄项已经在 db:* / file:* / formal:* 等各自的检查项里计过分了，若这里再算一个 job:*
# 黄项，就是同一事实扣两次，且会让「红黄项越多 → 作业黄项越多」循环放大。
# 只对这些作业、且 summary 明确是巡检结果摘要时去重；真正的作业异常（别的 summary）照旧计黄。
_RESULT_DERIVED_JOBS = {"data_freshness_check_daily", "data_quality_check_morning", "data_quality_check_evening"}
_RESULT_SUMMARY_RE = re.compile(r"数据质量巡检\s*红\s*\d+\s*/\s*黄\s*\d+\s*/\s*绿\s*\d+")


def is_result_derived_partial_failure(task_id: str, status: str, summary: str) -> bool:
    """巡检作业的 partial_failed 是否只是「下游巡检结果不达标」而非作业本身失败。"""
    return (status == "partial_failed" and task_id in _RESULT_DERIVED_JOBS
            and _RESULT_SUMMARY_RE.search(summary or "") is not None)


def _cron_changed_at(payload_raw: Any) -> datetime | None:
    """从 scheduled_tasks.payload_json 读 cron 变更时刻（由 _converge_task_cron 写入，UTC ISO）。"""
    try:
        raw = (json.loads(payload_raw) or {}).get("_cron_changed_at")
        if not raw:
            return None
        return datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc).astimezone(_CST)
    except (TypeError, ValueError):
        return None


def check_scheduled_tasks(db, open_days: list[str], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or _now()
    out: list[dict[str, Any]] = []
    tasks = db.execute(text("SELECT task_id, name, cron_expression, created_at, payload_json FROM scheduled_tasks "
                            "WHERE enabled = 1")).all()
    for task_id, name, cron, created_at, payload_raw in tasks:
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
        # 改期宽限：cron 变更（_converge_task_cron 写入 _cron_changed_at）之前定义的计划时刻不算漏跑。
        # 否则「改期当天旧时点已跑过、新时点还没到」会立刻报一批漏跑，把修复显示成新故障。
        if exp and payload_raw:
            cron_changed = _cron_changed_at(payload_raw)
            if cron_changed is not None and exp < cron_changed:
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
            if is_result_derived_partial_failure(task_id, status, summary):
                # 保留可观测性：作业状态照实写出来，但不重复计一个黄项。
                notes.append(f"最近一次 {started:%m-%d %H:%M} 已执行；partial_failed 仅派生自巡检结果"
                             f"（{summary[:100]}），红黄项已在各自检查项计分，不重复计黄")
            else:
                fail_names = re.search(r"失败数据集[:：]([^）)]+)", summary)
                msg = f"最近一次 {started:%m-%d %H:%M} 状态 {status}，连续 {streak} 次非成功"
                msg += f"（失败数据集：{fail_names.group(1)}）" if fail_names else f"：{summary[:140]}"
                notes.append(msg)
                sev = _worse(sev, RED if (streak >= 3 and crit and status == "failed") else YELLOW)
        out.append(_item(key, f"调度任务 {task_id}", "调度任务", sev, kind="job", latest=f"{started:%Y-%m-%d %H:%M}",
                         expected=(f"{exp:%Y-%m-%d %H:%M}" if exp else None),
                         detail="；".join(notes) or f"最近一次 {started:%m-%d %H:%M} {status}", impact=str(name)))
    return out


def check_health_diagnosis(db) -> dict[str, Any]:
    """健康诊断链路自检：计算异常 / 结果不完整时显式红项，不再静默。

    2026-11-14 事故：诊断缓存 TTL 改造漏了 `import time`，`diagnose_all` 直接抛
    NameError，驾驶舱健康分 500、快照任务静默失败，一整天没人发现分数本身是坏的。
    巡检此前没有任何一项检查「健康诊断自己能不能跑」，这里补上，并把结果直接暴露
    成红项（不用 _safe 的兜底黄项——那会把致命故障降级成「需人工排查」的提示）。
    """
    from app.services.data_health_service import diagnose_all

    try:
        result = diagnose_all(db)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {str(exc)[:180]}"
        return _item("quality:data_health_diagnosis", "健康诊断可用性", "数据质量", RED,
                     kind="quality", detail=f"诊断计算抛异常：{err}",
                     impact="驾驶舱健康分与出票就绪闸门拿不到结果，故障会被静默掩盖",
                     diagnosis_error=err)
    score = result.get("score")
    ref = result.get("reference_trade_date")
    if not isinstance(score, (int, float)) or not ref:
        return _item("quality:data_health_diagnosis", "健康诊断可用性", "数据质量", RED,
                     kind="quality", detail=f"诊断结果不完整（score={score!r}，参考交易日={ref!r}）",
                     impact="健康分为空或参考交易日缺失，所有滞后判定都不可信")
    return _item("quality:data_health_diagnosis", "健康诊断可用性", "数据质量", GREEN,
                 kind="quality", latest=ref, detail=f"诊断正常：参考交易日 {ref}，库表健康分 {score}",
                 impact="诊断链路可用性自检")


def check_daily_output(db, open_days: list[str], now: datetime | None = None) -> dict[str, Any]:
    """盘后出票是否成功（短线实盘 run + 中长线运行记录），过了当天出票截止（OUTPUT_DEADLINE，22:50）
    仍没出来 = 红项；没到截止时判上一个交易日，21:30 出票还在跑的当天不会被判成失败。

    任务状态（job:post_sync_formal_recommendation）只说明 21:30 那一次跑没跑成；这里看结果：
    事后用正式流程补跑成功也算出票成功，带历史日期参数的补跑（记为回放）不算。"""
    from app.services.daily_output_check import daily_output_status, output_target_date

    now = now or _now()
    target = output_target_date(open_days, now)
    if not target:
        return _item("formal:daily_output", "盘后出票", "盘后出票", GREEN, kind="job", detail="没有可核对的交易日")
    st = daily_output_status(db, target, now=now.astimezone(_CST).replace(tzinfo=None))
    sev = GREEN if st["ok"] else RED  # 待数据、未到点（大宗交易 21:00 发布）不算失败
    detail = (f"{target} 短线 {st['short_done']}/{st['short_expected']} 个策略按时实盘出票（共 {st['short_recommended']} 只）"
              + ("" if st["status"] == "ok" else f"；{st['reason']}"))
    if not st["ok"]:
        # 历史漏跑（机器没开机）无法事后补成实盘出票，但仍要区分「已归档旧账」与
        # 「今天新出的故障」。只补上下文，不改 severity、不动出票成功率统计口径。
        try:
            from app.services.startup_self_heal import archived_output_miss

            note = archived_output_miss(target)
            if note:
                detail += f"；{note}"
        except Exception:  # noqa: BLE001
            logger.debug("daily_output: 自愈归档说明读取失败", exc_info=True)
    return _item("formal:daily_output", "盘后出票", "盘后出票", sev, kind="job", latest=target, detail=detail,
                 impact="当天没有实盘推荐，次日无票可买；实盘统计也会缺这一天", output=st)


def _tail_text(path: Path, max_bytes: int = 6_000_000) -> str:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        return f.read().decode("utf-8", errors="ignore")


_LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(ERROR|WARNING)\] (?:\[pid \d+\] )?([\w\.]+): (.*)$")
# 告警系统自己写的通知行（emit_alert 用 logger.error，resolve/TTL 用 logger.warning）。
# 它们是「巡检结论的通知」，不是「被吞掉的应用异常」：计入 error_count 会让告警越多分越低、
# 又把 log:error_volume 顶黄再触发新一轮告警（自激），同时把真正的异常淹没在几十条通知里。
# 告警本身另有 alerts:stale_active / honest_alerts 等专门巡检项盯着，剔除不会漏报。
_ALERT_NOTICE_RE = re.compile(r"^\[ALERT(?:_RESOLVED)?:")


def scan_log_signals(text_blob: str, now: datetime | None = None, hours: int = 24) -> dict[str, Any]:
    """近 N 小时日志：provider 回退次数、真实应用 ERROR 条数与 Top 签名、告警通知条数。

    error_count 只数真正的应用异常；以 ``[ALERT:`` 开头的行单独记 alert_count，纯函数便于测试。
    """
    now = now or _now()
    since = (now - timedelta(hours=hours)).replace(tzinfo=None)
    fallbacks: dict[str, int] = {}
    errors: dict[str, int] = {}
    n_err = 0
    n_alert = 0
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
            if _ALERT_NOTICE_RE.match(msg):
                n_alert += 1
                continue
            n_err += 1
            sig = f"{logger_name}: {re.sub(r'[0-9]+', 'N', msg)[:70]}"
            errors[sig] = errors.get(sig, 0) + 1
    return {"fallbacks": fallbacks, "error_count": n_err, "alert_count": n_alert,
            "top_errors": sorted(errors.items(), key=lambda kv: -kv[1])[:5]}


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
    n_alert = sig.get("alert_count", 0)
    top = "；".join(f"{s}×{c}" for s, c in sig["top_errors"][:3])
    detail = f"{n} 条应用异常 ERROR" + (f"，Top：{top}" if top else "")
    if n_alert:
        detail += f"；另有 {n_alert} 条告警通知（[ALERT:*]，告警系统落盘，不计入应用异常）"
    out.append(_item("log:error_volume", "应用 ERROR 日志量（近24小时）", "静默失败", YELLOW if n > 30 else GREEN, kind="log",
                     detail=detail, impact="被吞掉的异常只留在日志里"))
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


def check_provider_empty_rate(db, now: datetime | None = None, hours: int = 24) -> dict[str, Any]:
    """provider 级空返回率（请求层成功率）：近 N 小时样本里"应答了但没数据"的比例。

    为什么单列一项：``check_db_dataset`` 只看数据集最后一根 K 线的日期新鲜度，一个 provider
    哪怕 80% 请求返回空、每天都在兜底，只要主源还新鲜就永远绿。eastmoney 实测 606 次里
    484 次空返回（79.9%），在这套规则下却一直是绿的——真实额度全被它白吃了。
    阈值：样本 <50 不判定（样本不足），≥60% 红，≥30% 黄。
    """
    from app.services.datasource import quota_view

    now = now or _now()
    key, label = "gateway:provider_empty_rate", "数据源空返回率"
    try:
        rows = quota_view.quality_by_provider(db, hours=hours)
    except Exception:  # noqa: BLE001
        logger.debug("provider empty-rate check failed", exc_info=True)
        rows = []
    judged = [r for r in rows if r.get("attempts", 0) >= quota_view.MIN_SAMPLE_FOR_VERDICT]
    red = [r for r in judged if (r.get("empty_rate") or 0) >= quota_view.EMPTY_RATE_RED]
    yellow = [
        r for r in judged
        if quota_view.EMPTY_RATE_YELLOW <= (r.get("empty_rate") or 0) < quota_view.EMPTY_RATE_RED
    ]
    if red:
        sev = RED
    elif yellow:
        sev = YELLOW
    else:
        sev = GREEN

    def _fmt(r: dict[str, Any]) -> str:
        return f"{r['provider']} {r['empty_count'] if 'empty_count' in r else r['empty']}/{r['attempts']}" \
               f"（{round((r.get('empty_rate') or 0) * 100, 1)}%，均延迟 {r.get('avg_latency_seconds')}s）"

    if not judged:
        detail = f"近 {hours} 小时没有 provider 达到 {quota_view.MIN_SAMPLE_FOR_VERDICT} 次样本，暂不判定"
    elif red or yellow:
        detail = "；".join(_fmt(r) for r in (red + yellow)[:5])
    else:
        worst = max(judged, key=lambda r: r.get("empty_rate") or 0)
        detail = f"近 {hours} 小时各源空返回率正常，最高 {worst['provider']} {round((worst.get('empty_rate') or 0) * 100, 1)}%"
    return _item(
        key, label, "数据网关", sev, kind="gateway",
        latest=now.strftime("%Y-%m-%d %H:%M"), detail=detail,
        impact="空返回率高的源在吃掉限频额度却拿不到数据，应降级/摘除；否则表现为「明明还没打满额度却一直没数据」",
        empty_rate_rows=[{k: r.get(k) for k in ("provider", "attempts", "empty", "empty_rate", "success_rate")} for r in judged[:10]],
    )


def check_provider_limits(db, now: datetime | None = None) -> dict[str, Any]:
    """配额与限频健康：跨进程限频开关、当前熔断、日额度水位。"""
    from app.services.datasource import quota_view

    now = now or _now()
    key, label = "gateway:quota_limits", "数据源配额与限频"
    try:
        view = quota_view.collect(db, hours=6)
    except Exception:  # noqa: BLE001
        logger.debug("quota view failed in monitor", exc_info=True)
        return _item(key, label, "数据网关", GREEN, kind="gateway",
                     latest=now.strftime("%Y-%m-%d %H:%M"), detail="配额视图不可用，跳过（不影响其它检查）",
                     impact="无")

    problems: list[str] = []
    sev = GREEN
    if not (view.get("shared_limiter") or {}).get("enabled"):
        sev = YELLOW
        problems.append("跨进程限频未启用：backend 与 Prefect worker 各持一份配额，合计可打满双倍上限")
    for b in view.get("breakers") or []:
        sev = YELLOW if sev == GREEN else sev
        problems.append(f"熔断开启 {b.get('key')}（剩 {int(b.get('seconds_remaining') or 0)}s，{b.get('reason')}）")
    for d in view.get("daily") or []:
        if d.get("exhausted_today"):
            sev = RED
            problems.append(f"{d['provider']} 今日额度已耗尽（{d['used_today']}/{d['daily_quota']}）")
        elif (d.get("used_pct") or 0) >= 80:
            sev = YELLOW if sev == GREEN else sev
            problems.append(f"{d['provider']} 日额度已用 {d['used_pct']}%")

    promax = view.get("promax") or {}
    detail = "；".join(problems) if problems else (
        f"跨进程限频已启用；无熔断；promax 实际生效 {promax.get('tightest_effective_per_minute')}/min"
        f"（声明 {promax.get('declared_per_minute')}/min）"
    )
    return _item(
        key, label, "数据网关", sev, kind="gateway",
        latest=now.strftime("%Y-%m-%d %H:%M"), detail=detail,
        impact="配额用尽会被 quota_skip 拦下（补数静默少数据）；跨进程限频关闭时会与 Prefect 抢配额",
        shared_limiter=view.get("shared_limiter"),
        promax_effective_per_minute=promax.get("tightest_effective_per_minute"),
        promax_declared_per_minute=promax.get("declared_per_minute"),
    )


def check_confirmation_coverage(db, open_days: list[str], now: datetime | None = None) -> dict[str, Any]:
    """盘中买入确认覆盖率：今天入场窗口内 09:45 后仍没被检查 → 红；近 5 个交易日有入场日没检查就过期 → 黄。"""
    from app.services.execution_confirmation_coverage import (
        COVERAGE_GRACE, _eligible_run_ids, entry_day_unchecked, recent_expired_without_check,
        sweep_coverage_alerts,
    )

    now = now or _now()
    now_cst = now.astimezone(_CST)
    today = now_cst.date().isoformat()
    try:  # 告警自动解除/每日重发：轮询停了（非交易日、无待确认推荐）也要靠数据健康巡检清理
        sweep_coverage_alerts(db, today)
    except Exception:  # noqa: BLE001
        logger.exception("买入确认告警清理失败")
    key, label = "job:execution_confirmation_coverage", "盘中买入确认覆盖"
    unchecked: list[dict[str, Any]] = []
    if today in set(open_days) and now_cst.time() >= COVERAGE_GRACE:
        unchecked = entry_day_unchecked(db, today, run_ids=_eligible_run_ids(db))
    gaps = recent_expired_without_check(db, today)
    if unchecked:
        sev = RED
        detail = f"今天 {len(unchecked)} 只待确认推荐入场日盘中尚未被检查：" + "、".join(
            f"{u['code']}({u['signal_date'][5:]})" for u in unchecked[:8])
    elif gaps:
        sev = YELLOW
        detail = f"近 5 个交易日 {len(gaps)} 只推荐入场日盘中没被检查就过期：" + "、".join(
            f"{g['code']}({g['entry_day'][5:]})" for g in gaps[:8])
    else:
        sev, detail = GREEN, "入场窗口内的待确认推荐都已在盘中被检查"
    return _item(key, label, "调度任务", sev, kind="job", detail=detail,
                 impact="漏查的推荐收盘后会被判过期，实盘少了本该确认的买入")


# --------------------------------------------------------------------------- 汇总
def _rollback(db) -> None:
    """逐项检查之间重置会话，让上一项的失败不污染后面所有项。

    回滚本身失败只记日志：这里抛出会盖掉真正的原始错误，而调用方需要继续跑完剩余检查。
    """
    try:
        db.rollback()
    except Exception:  # noqa: BLE001 - 恢复动作，失败不能盖掉原始错误
        logger.warning("数据质量巡检：会话回滚失败，后续检查可能读到失效事务", exc_info=True)


ROLLING_R12_KEY = "formal:rolling_r12"
_ROLLING_RED_NIGHTS = 6   # 本月已过这么多个交易日夜晚仍没有结果文件 = 红
_ROLLING_RED_FAILS = 2    # 或滚动重算任务连续失败这么多次仍没有本月结果 = 红


def rolling_r12_item(latest: dict[str, Any] | None, month: str, nights_passed: int, fail_streak: int,
                     last_error: str = "") -> dict[str, Any]:
    """近12个月滚动重算的结果是否出来了、是否完整。失败不能只靠任务黄灯：连续失败或久不出结果记红。"""
    impact = "现役策略近12个月每笔是否转负看不到（结果文件 rolling_r12_latest.json），转负告警也发不出"
    done = bool(latest) and latest.get("month") == month and latest.get("status") == "completed"
    if done:
        crashed = latest.get("crashed") or {}
        if crashed:
            return _item(ROLLING_R12_KEY, "近12个月滚动重算", "盘后出票", RED, kind="job", latest=str(latest.get("cutoff")),
                         detail=f"{len(crashed)} 个策略选股抛异常被跳过（其余已算）：" + "；".join(
                             f"{sid}（{str(c.get('error'))[:80]}）" for sid, c in crashed.items()),
                         impact=impact)
        replay = latest.get("replay") or {}
        inc = replay.get("incomplete_days") or {}
        if inc:
            # 裸 dict 看不出量级：按失败天数降序，标出「整个窗口无样本」的策略，
            # 并说明跨夜续跑时累计失败天数会大于本次运行天数（否则 271 > 227 很费解）。
            window_days = int(replay.get("window_days") or 0)
            run_days = int(replay.get("days") or 0)
            unavailable = set(replay.get("unavailable_strategies") or [])
            bits = [f"{sid} {n}{'/' + str(window_days) if window_days else ''} 天"
                    + ("（指标不可得）" if sid in unavailable else "")
                    for sid, n in sorted(inc.items(), key=lambda kv: -kv[1])]
            detail = "回放取数失败：" + "；".join(bits)
            if run_days and any(n > run_days for n in inc.values()):
                detail += f"（本次运行 {run_days} 天，失败天数为跨夜续跑累计）"
            if unavailable:
                detail += "；整窗失败多为回放数据通道未接本地存档，须修通道后重算（重试上游无效）"
            return _item(ROLLING_R12_KEY, "近12个月滚动重算", "盘后出票", YELLOW, kind="job", latest=str(latest.get("cutoff")),
                         detail=detail[:460], impact=impact)
        return _item(ROLLING_R12_KEY, "近12个月滚动重算", "盘后出票", GREEN, kind="job", latest=str(latest.get("cutoff")),
                     detail=f"{month} 结果已出（截止 {latest.get('cutoff')}）")
    err = f"；最近一次报错：{last_error[:120]}" if last_error else ""
    if fail_streak >= _ROLLING_RED_FAILS or nights_passed >= _ROLLING_RED_NIGHTS:
        return _item(ROLLING_R12_KEY, "近12个月滚动重算", "盘后出票", RED, kind="job",
                     detail=f"{month} 还没有结果文件（本月已过 {nights_passed} 个交易日夜晚，任务连续失败 {fail_streak} 次）{err}",
                     impact=impact)
    if fail_streak >= 1:
        return _item(ROLLING_R12_KEY, "近12个月滚动重算", "盘后出票", YELLOW, kind="job",
                     detail=f"{month} 还没有结果文件，最近一次任务失败{err}", impact=impact)
    return _item(ROLLING_R12_KEY, "近12个月滚动重算", "盘后出票", GREEN, kind="job",
                 detail=f"{month} 结果还在跑或还没到点")


def check_rolling_r12(db, open_days: list[str], now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    from app.services.rolling_r12 import LATEST

    now = now or _now()
    month = now.strftime("%Y-%m")
    today = now.strftime("%Y-%m-%d")
    nights = sum(1 for d in open_days if d.startswith(month) and d < today)
    try:
        latest = json.loads((path or LATEST).read_text())
    except Exception:  # noqa: BLE001 - 没有文件 / 坏文件都当没有结果
        latest = None
    runs = db.execute(text(
        "SELECT status, summary FROM scheduled_task_runs WHERE task_id = 'rolling_r12_monthly' "
        "ORDER BY started_at DESC LIMIT 5")).all()
    streak = 0
    for r in runs:
        if str(r[0]) in _BAD_STATUS:
            streak += 1
        else:
            break
    return rolling_r12_item(latest, month, nights, streak, str(runs[0][1] or "") if runs and streak else "")


# 短线回放版本 vs 实盘版本。回放（historical_reconstruction）是「现行规则在历史上会怎么做」，策略 yaml 一改版，
# 旧批次就代表不了现行规则；而实盘每天只有几笔，评估现行规则靠的是回放。2026-11-26 复核：三条现役策略的回放
# 停在 10-19 的版本，之后 yaml 改了 1~2 次（出场 EL_C、超跌 pct≥5 硬门槛），策略页近 12 个月数字算的是旧规则。
# 判定键用 run 的 strategy_policy_fingerprint（单策略 run 下只随该策略实现 + 政策版本变）。
SHORT_REPLAY_VERSION_KEY = "formal:short_replay_version"


def short_replay_version_rows(db) -> list[dict[str, Any]]:
    """每个现役短线策略：最新实盘 run 的指纹/日期 vs 最新已验证回放批次的指纹/完成日/覆盖终点。"""
    from app.services.daily_output_check import expected_short_strategies
    from app.services.evidence.reconstruction_batch import latest_validated_reconstruction_cohorts

    expected = list(expected_short_strategies(db))
    cohorts: dict[str, Any] = {}
    for c in latest_validated_reconstruction_cohorts(db):
        key = str(c.strategy_key or "")
        if key.startswith("skills:") and "+" not in key:
            sid = key[len("skills:"):]
            if sid not in cohorts or c.completed_at > cohorts[sid].completed_at:
                cohorts[sid] = c
    live = db.execute(text(
        "SELECT selected_skills_json, strategy_policy_fingerprint, trade_date FROM recommendation_runs "
        "WHERE run_mode='formal' AND evaluation_scope='live_forward' AND status='completed' "
        "AND trade_date >= DATE_SUB(CURDATE(), INTERVAL 20 DAY) ORDER BY trade_date DESC"
    )).all()
    latest_live: dict[str, tuple[str, str]] = {}
    for sel, fp, td in live:
        try:
            ids = [str(x.get("skill_id")) for x in json.loads(sel or "[]")]
        except (TypeError, ValueError):
            continue
        if len(ids) == 1 and ids[0] not in latest_live and fp:
            latest_live[ids[0]] = (str(fp), str(td)[:10])
    rows = []
    for sid in expected:
        c = cohorts.get(sid)
        lv = latest_live.get(sid)
        rows.append({
            "strategy_id": sid,
            "live_fingerprint": lv[0] if lv else None, "live_date": lv[1] if lv else None,
            "replay_fingerprint": c.strategy_policy_fingerprint if c else None,
            "replay_completed_at": c.completed_at.strftime("%Y-%m-%d") if c else None,
            "replay_end": max(c.expected_trade_dates) if c else None,
            "replay_days": len(c.expected_trade_dates) if c else 0,
        })
    return rows


def short_replay_version_item(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stale = [r for r in rows if r["live_fingerprint"] and r["replay_fingerprint"] and r["live_fingerprint"] != r["replay_fingerprint"]]
    missing = [r for r in rows if not r["replay_fingerprint"]]
    parts = []
    if stale:
        parts.append("回放版本落后于实盘：" + "；".join(
            f"{r['strategy_id']}（回放 {r['replay_completed_at']} 建、覆盖到 {r['replay_end']}，实盘 {r['live_date']} 已是新版本）" for r in stale))
    if missing:
        parts.append("没有已验证回放批次：" + "、".join(r["strategy_id"] for r in missing))
    sev = YELLOW if (stale or missing) else GREEN
    detail = "；".join(parts) if parts else f"{len(rows)} 个现役短线策略的回放批次与实盘同版本"
    if sev != GREEN:
        detail += "。用现行代码重建：scripts/replay_all_days.py --single-skill <id> --start 2025-10-20 --end <昨日> --skip-existing"
    return _item(SHORT_REPLAY_VERSION_KEY, "短线回放版本", "盘后出票", sev, kind="job",
                 latest=min((r["replay_end"] for r in rows if r["replay_end"]), default=None), detail=detail,
                 impact="策略页/近12个月滚动体检用回放评估现行规则；回放落后一个版本时，评估的是旧规则不是现在跑的规则")


def check_short_replay_version(db) -> dict[str, Any]:
    return short_replay_version_item(short_replay_version_rows(db))


# 长远目标三指标（goal_metrics）：去重近 12 月滚动超额 > 0、逐月为正 ≥ 8/12、每策略全期独有信号扣费净合计 > 0（L-238）。
GOAL_METRICS_KEY = "formal:goal_metrics_mid"


def goal_metrics_item(g: dict[str, Any]) -> dict[str, Any]:
    from app.services.goal_metrics import summary_text

    fails = []
    if not g["rolling_excess_pass"]:
        fails.append("滚动超额未>0")
    if not g["positive_months_pass"]:
        fails.append(f"逐月为正 {g['positive_months']}/{g['month_count']}")
    if g["strategies_failing"]:
        fails.append(f"{len(g['strategies_failing'])} 个策略全期独有信号净合计为负")
    sev = GREEN if not fails else YELLOW
    detail = summary_text(g) + ("" if sev == GREEN else "。未达标：" + "、".join(fails))
    return _item(GOAL_METRICS_KEY, "中线长远目标三指标", "盘后出票", sev, kind="job", latest=g["window_end"], detail=detail,
                 impact="去重口径下系统是否稳定跑赢买全市场；全期独有信号扣费净合计为负的策略去掉会多赚（L-238）；净正超额负的只列观察")


def check_goal_metrics(db) -> dict[str, Any]:
    from app.services import goal_metrics as gm
    from app.services import horizon_analytics as ha
    from app.services import midterm_service as ms

    g = gm.compute(ha.trades(db, "mid"), [k for k, v in ms.STRATEGIES.items() if v.get("horizon") == "mid"],
                   research_rows=gm.load_research_trades())
    return goal_metrics_item(g)


def run_checks(db, *, now: datetime | None = None, include_files: bool = True) -> dict[str, Any]:
    now = now or _now()
    _CHECK_TIMINGS.clear()
    started_at = time.monotonic()
    open_days = load_open_days(db)
    expected = expected_trade_date(open_days, now)
    items: list[dict[str, Any]] = []
    # 诊断链路自检放在最前：它一旦崩溃，后面所有基于诊断的判定（含出票闸门）都不可信。
    items += _safe("health_diagnosis", "quality:data_health_diagnosis", "数据质量", "quality",
                   lambda: [check_health_diagnosis(db)])
    _rollback(db)
    for spec in DB_DATASETS:
        items += _safe(spec["label"], spec["key"], "数据库表", "db", lambda s=spec: [check_db_dataset(db, s, open_days, expected)])
        _rollback(db)
    items += _safe("指数日线", "db:index_daily", "数据库表", "db", lambda: check_index_daily(db, open_days, expected))
    items += _safe("日K值域", "db:daily_kbars:values", "数据质量", "quality",
                   lambda: [check_daily_kbar_values(db, open_days, expected)])
    items += _safe("筹码空洞", "db:daily_kbars:chip_history", "数据质量", "quality", lambda: [check_chip_history(db)])
    items += _safe("解禁比例口径", "db:share_floats:ratio", "数据质量", "quality", lambda: [check_share_float_ratio(db)])
    items += _safe("两融金额列完整性", "db:margin_detail:columns", "数据质量", "quality",
                   lambda: [check_margin_columns(db, open_days, expected)])
    items += _safe("概念板块完整性", "db:concept_sector:completeness", "数据质量", "quality",
                   lambda: [check_concept_completeness(db, open_days, expected)])
    _rollback(db)
    items += _safe("按报告期财报覆盖率", "db:fina_period_coverage", "数据质量", "quality",
                   lambda: [check_fina_period_coverage(db, now)])
    _rollback(db)
    items += _safe("交易日历", "db:trade_calendar", "数据库表", "db", lambda: [check_calendar_horizon(open_days, now)])
    _rollback(db)
    items += _safe("盘中买入确认覆盖", "job:execution_confirmation_coverage", "调度任务", "job",
                   lambda: [check_confirmation_coverage(db, open_days, now)])
    _rollback(db)
    if include_files:
        for spec in FILE_DATASETS:
            items += _safe(spec["label"], spec["key"], "文件数据集", "file",
                           lambda s=spec: [check_file_dataset(s, open_days, expected, now)])
        for spec in DIR_DATASETS:
            items += _safe(spec["label"], spec["key"], "文件数据集", "file", lambda s=spec: [check_dir_dataset(s, now)])
        items += _safe("本地逐股财务指标库覆盖", "file:midterm/fina:coverage", "数据质量", "quality",
                       lambda: [check_local_fina(db, open_days, expected, now)])
        _rollback(db)
        for spec in AGE_FILES:
            items += _safe(spec["label"], spec["key"], "文件数据集", "file", lambda s=spec: [check_age_file(s, now)])
        items += _safe("capability_matrix", "file:runtime/datasource_capability_matrix.json", "文件数据集", "file",
                       lambda: [check_capability_matrix(now)])
        items += _safe("st_hist", "research:st_hist", "研究静态文件", "research", lambda: [st_hist_vs_db(db, expected)])
        _rollback(db)
        items += _safe("index parquet", "research:index_daily_consistency", "研究静态文件", "research",
                       lambda: [parquet_vs_db_index(db)])
        _rollback(db)
    items += _safe("gateway", "gateway:providers", "数据网关", "gateway", lambda: [check_provider_gateway(now)])
    # 请求层健康（2026-11-15）：配额/限频 + 空返回率。dataset 新鲜度管不到这两件事——
    # 主源还新鲜时，一个 79.9% 空返回的废源永远绿。
    items += _safe("provider_empty_rate", "gateway:provider_empty_rate", "数据网关", "gateway",
                   lambda: [check_provider_empty_rate(db, now)])
    _rollback(db)
    items += _safe("provider_limits", "gateway:quota_limits", "数据网关", "gateway",
                   lambda: [check_provider_limits(db, now)])
    _rollback(db)
    items += _safe("scheduled", "job:*", "调度任务", "job", lambda: check_scheduled_tasks(db, open_days, now))
    _rollback(db)
    items += _safe("daily_output", "formal:daily_output", "盘后出票", "job", lambda: [check_daily_output(db, open_days, now)])
    _rollback(db)
    items += _safe("rolling_r12", ROLLING_R12_KEY, "盘后出票", "job", lambda: [check_rolling_r12(db, open_days, now)])
    items += _safe("短线回放版本", SHORT_REPLAY_VERSION_KEY, "盘后出票", "job", lambda: [check_short_replay_version(db)])
    _rollback(db)
    items += _safe("中线长远目标三指标", GOAL_METRICS_KEY, "盘后出票", "job", lambda: [check_goal_metrics(db)])
    _rollback(db)
    _rollback(db)
    items += _safe("log", "log:*", "静默失败", "log", lambda: check_log_signals(now))
    items += _safe("stale_alerts", "alerts:stale_active", "静默失败", "log", lambda: [check_stale_alerts(now)])
    items += _safe("ds_all_failed", "alerts:datasource_all_failed", "静默失败", "log", lambda: [check_datasource_all_failed(now)])
    items.sort(key=lambda i: (-_RANK[i["severity"]], i["category"], i["key"]))
    counts = {s: sum(1 for i in items if i["severity"] == s) for s in (RED, YELLOW, GREEN)}
    elapsed = time.monotonic() - started_at
    slowest = ", ".join(f"{k}={v:.1f}s" for k, v in sorted(_CHECK_TIMINGS, key=lambda x: -x[1])[:5] if v >= 1.0)
    logger.info("数据质量巡检完成: %.1fs，%d 项检查%s", elapsed, len(_CHECK_TIMINGS), f"，最慢 {slowest}" if slowest else "")
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
    # 2026-11-15：限频改为「暂停+降级」，暂停行不是熔断（只是这一分钟跳过本源），
    # 不能算进「熔断打开」去扣健康分，否则一次正常限频就报红。
    open_live = [
        b for b in breakers_fn()
        if str(b.get("key", "")).startswith("live:")
        and not str(b.get("key", "")).endswith("|pause")
        and float(b.get("open_until") or 0) > now_ts
    ]
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
    if "promax" in names:
        from app.core.config import get_settings as _gs

        _st = _gs()
        if bool(getattr(_st, "tushare_promax_rtk_reserved", True)):
            notes.append("promax 额度专留实时行情 rt_k/rt_min（其余接口走 teajoin 等；补数进程"
                         + ("不动用" if not bool(getattr(_st, "tushare_promax_research_enabled", False)) else "仍可动用") + " promax）")
            if "promax" in exhausted:
                notes.append("⚠ promax 今日已耗尽：rt_k 现由 新浪→腾讯→AKShare 全市场快照→分钟→小石 依次兜底")
    if "teajoin" in names:
        from app.core.config import get_settings
        from app.services.datasource import teajoin_capability as _tc

        if bool(getattr(get_settings(), "tushare_teajoin_lead", True)):
            if bool(getattr(get_settings(), "tushare_teajoin_intraday_yield", False)):
                notes.append(f"teajoin 为 {len(_tc.TEAJOIN_APIS)} 个接口的主源（盘中 {len(_tc.INTRADAY_SENSITIVE)} 个延迟敏感接口让原源先走，失败/超时自动降级）")
            else:
                notes.append(f"teajoin 为 {len(_tc.TEAJOIN_APIS)} 个接口的主源（含历史分钟 stk_mins；盘中不再让位，读超时收紧到 "
                             f"{get_settings().tushare_teajoin_intraday_read_timeout_seconds:g}s，超时/失败自动降级）")
        else:
            notes.append("teajoin 主源开关已关闭（仅兜底）")
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
    """把统一数据质量巡检的红/黄项折算成健康分扣分。

    规则：红项每个 −8（最多 −40）；黄项每个 −1（最多 −10）。巡检报告超过 36 小时未更新再 −5（巡检停了本身就是问题）。
    只统计 scope="trading"（实盘链路）；研究/回测产物（scope="research"）仍展示但不扣分——它们没有日更调度，
    长期滞后是设计常态，算进去会永久压住健康分上限（见 RESEARCH_ONLY_FILE_KEYS 注释）。已退役项不计。
    没有报告时不扣分但在 note 里说明。返回 {penalty, red, yellow, rule, note, generated_at}。"""
    rule = (f"红项每个扣 {PENALTY_PER_RED} 分（最多 {PENALTY_RED_CAP}）；黄项每个扣 {PENALTY_PER_YELLOW} 分（最多 {PENALTY_YELLOW_CAP}）；"
            f"仅计实盘链路项（研究/回测产物不扣分）；巡检报告超过 {REPORT_STALE_HOURS} 小时没更新另扣 5 分；已退役项不计")
    if not report or not report.get("items"):
        return {"penalty": 0, "red": 0, "yellow": 0, "rule": rule, "note": "尚无数据质量巡检报告，未扣分", "generated_at": None}
    items = [i for i in report["items"] if not i.get("retired") and i.get("scope", "trading") == "trading"]
    red = sum(1 for i in items if i.get("severity") == RED)
    yellow = sum(1 for i in items if i.get("severity") == YELLOW)
    pen = min(PENALTY_RED_CAP, red * PENALTY_PER_RED) + min(PENALTY_YELLOW_CAP, yellow * PENALTY_PER_YELLOW)
    note = f"红 {red} 项、黄 {yellow} 项"
    research_bad = sum(1 for i in report["items"]
                       if not i.get("retired") and i.get("scope") == "research" and i.get("severity") != GREEN)
    if research_bad:
        note += f"；另有 {research_bad} 项研究/回测产物滞后（只展示不扣分）"
    gen = report.get("generated_at")
    if gen:  # 缺 generated_at 不扣「巡检停了」的分；有值但格式不对就地崩溃
        age_h = ((now or _now()) - datetime.strptime(str(gen), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_CST)).total_seconds() / 3600
        if age_h > REPORT_STALE_HOURS:
            pen += 5
            note += f"；巡检报告已 {age_h:.0f} 小时未更新（再扣 5）"
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
    # 已退役项（retired=True）只展示事实：即使实现里给了红黄也不发告警，否则退役等于换一种方式持续打扰。
    bad = {i["key"]: i for i in report["items"] if i["severity"] != GREEN and not i.get("retired")}
    previous = {str(r["key"]): r for r in active_alert_records(ALERT_KIND)}
    for key, it in bad.items():
        tag = "红" if it["severity"] == RED else "黄"
        prev_msg = str((previous.get(key) or {}).get("message") or "")
        prev_tag = "红" if prev_msg.startswith("[红]") else ("黄" if prev_msg.startswith("[黄]") else None)
        # 首次出现 / 严重度变化（含恢复前再次恶化）立即告警；否则按日级冷却抑制重复 ERROR。
        cooldown = 0.0 if prev_tag != tag else QUALITY_ALERT_COOLDOWN_SECONDS
        emit_alert(ALERT_KIND, key, f"[{tag}] {it['label']}: {it['detail']}（影响：{it['impact']}）"[:480],
                   cooldown=cooldown)
        emitted.append(key)
    for rec in active_alert_records(ALERT_KIND):
        k = str(rec["key"])
        if k not in bad and resolve_alert(ALERT_KIND, k, f"{k} 已恢复"):
            resolved.append(k)
    return {"emitted": emitted, "resolved": resolved}


def reconcile_fact_alerts(db) -> list[str]:
    """复核解除「只在失败时 emit、成功路径不一定跑得到」的事实型告警。

    daily_output_failed（盘后出票失败）和 late_repick_failed（晚间补跑失败）都由作业在失败时
    emit，解除只挂在「下一次复核作业也跑到」这条链上。机器离线、自愈补跑或人工修复把票补齐后，
    那个复核作业往往早已跑过，告警就一直挂到 24h TTL 自动过期；期间面板显示着已经不成立的红项，
    24h 后还可能被 alerts:stale_active 当成「放着不管的告警」再扣一个黄项。

    每次正式巡检（alert=True）按出票判定的唯一口径 daily_output_status 复核一次：ok 才解除，
    pending/failed/读不到一律不动，绝不把真未恢复的告警提前抹掉。key 必须是交易日，异常 key 跳过。
    daily_output_status 的 ok 同时覆盖短线 completed live_forward 与中长线无 data_error/data_not_ready，
    因此也能正确代表补跑是否达成目标。
    """
    from app.core.alerts import active_alert_records, resolve_alert
    from app.services.daily_output_check import daily_output_status

    resolved: list[str] = []
    for kind in ("daily_output_failed", "late_repick_failed"):
        for rec in active_alert_records(kind):
            key = str(rec["key"])
            td = key[:10]
            if len(td) != 10 or td[4] != "-" or td[7] != "-":
                continue
            try:
                st = daily_output_status(db, td)
            except Exception:  # noqa: BLE001 - 复核失败不影响巡检
                logger.debug("fact alert reconcile unreadable %s|%s", kind, key, exc_info=True)
                continue
            if st.get("ok") and resolve_alert(
                    kind, key, f"{td} 复核：出票已恢复 {st.get('short_done')}/{st.get('short_expected')}"):
                resolved.append(f"{kind}|{key}")
    return resolved


def run_data_quality_check(db, *, alert: bool = True, now: datetime | None = None, include_files: bool = True,
                           persist: bool = True) -> dict[str, Any]:
    """入口：只读巡检 → 写报告 → 同步告警。永不抛出（失败返回 overall=error）。"""
    try:
        report = run_checks(db, now=now, include_files=include_files)
    except Exception as exc:  # noqa: BLE001
        logger.exception("data quality monitor failed")
        err = f"{type(exc).__name__}: {str(exc)[:200]}"
        # 巡检整体崩溃时报告里 items 为空，页面只会看到「没有数据」而不是故障；
        # 显式告警，避免像 11-14 那样静默一整天。
        try:
            from app.core.alerts import emit_alert

            emit_alert("data_quality_check_failed", (now or _now()).strftime("%Y-%m-%d"),
                       f"数据质量巡检整体失败：{err}")
        except Exception:  # noqa: BLE001
            logger.debug("data quality failure alert failed", exc_info=True)
        return {"overall": "error", "error": err, "items": [], "summary": {}}
    if persist:
        write_report(report)
    if alert:
        try:
            report["alerts"] = sync_alerts(report)
        except Exception as exc:  # noqa: BLE001
            logger.warning("data quality alert sync failed: %r", exc)
        try:
            report["reconciled_alerts"] = reconcile_fact_alerts(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fact alert reconcile failed: %r", exc)
    return report
