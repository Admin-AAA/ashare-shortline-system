"""开机自愈（boot-time catch-up）：按「目标业务日」补齐漏掉的数据与文件数据集。

根因
----
这套系统跑在个人 Mac 上，机器不常在线。全部日更任务都挂在墙钟时刻
（21:00 晚间补数 / 21:30 盘后出票 / 22:55 健康快照 / 23:30 研究缓存），
机器没开机就整批漏掉。下次开机时没有任何机制按「本该跑的那个交易日」回补，
于是日K、指数、涨停板、daily_basic、两融、派生指标、研究 parquet、正式出票
成片滞后，健康分每隔一两天就掉一次（2026-11-13 实测 43 分、11-14 页面 67 分）。

设计
----
不新增调度时刻。「进程刚起来」是本机唯一可靠的时间点：

1. 扫描最近 N 个**已收盘的真实交易日**（Tushare 日历，失败回退本地日K），
   逐日检查核心表当日行数，得到 `gaps[trade_date] = [缺的表]`。
2. 只针对缺口日调用现有**幂等**补数入口（重新拉当日 / 只补 NULL / 只补缺失代码），
   已有完整数据的日期不动，避免每次开机都重刷上游。
3. 库表补齐后再刷新文件数据集（库 → parquet 增量导出），最后清掉诊断缓存，
   让 readiness 闸门与页面健康分立刻看到新数据。
4. 正式出票缺口先归档（复盘用），再在**入场日 09:15 之前**且库表已补齐时，
   复用 21:00 既有的幂等补跑流程（自带健康闸门 / 在途检查 / 缺策略检查）真正把票补出来；
   该窗口内生成的票按系统既有口径仍记 live_forward，过了窗口就绝不补造。
   2026-11-13 实测：机器离线让 17:35 主流水线没启动，19:30 watchdog 只告警，
   21:00 与次日 08:30 补跑又被健康闸门拦下，自愈补齐库表后没有任何环节再触发，漏票一直挂着。

所有动作彼此隔离（单个失败不影响其余），结果写
`data/runtime/startup_self_heal.json`（保留最近 20 次），并带最小间隔防抖。
启动由 `app.main.on_startup` 挂后台线程，另有低频守护循环覆盖「开机后不重启」。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 5
MIN_INTERVAL_SECONDS = 30 * 60
STARTUP_DELAY_SECONDS = 12.0
DAEMON_INTERVAL_SECONDS = 2 * 3600
STATE_KEEP_RUNS = 20

# 每个已收盘交易日都该有的核心表。(表名, 当日至少行数)
# 行数下限只用于区分「整日缺失」和「有数据」；精确完整性由既有巡检负责。
# margin_detail 是 T+1 盘后晚出，单独走截断补缺，不列入这里的整日缺失判定。
_CORE_DAILY_TABLES: tuple[tuple[str, int], ...] = (
    ("daily_kbars", 3000),
    ("stock_quotes", 3000),
    ("index_daily", 4),
    ("stk_limits", 3000),
    ("limit_up_board", 1),
    ("auction_data", 3000),
    ("capital_flow", 3000),
    ("concept_sector", 1),
    ("concept_sector_stock", 1000),
    ("dragon_tiger", 1),
    ("daily_basic", 3000),
    ("minute_kbars", 1000),
)

# 「能检测」不等于「能补回」。2026-11-14 审计：remediate_data_health_gaps 用的是
# 逻辑数据集名（daily_kbar/index_daily/news…），而 scan_gaps 用的是物理表名，
# 两条清单并不重合。把每个核心物理表的补数归属写死在这里，避免出现
# 「每轮都报缺口、却没有任何动作负责补」的静默盲区。
_REMEDIABLE_VIA_DIAGNOSIS: frozenset[str] = frozenset({
    "daily_kbars", "index_daily", "limit_up_board", "capital_flow",
    "concept_sector", "concept_sector_stock", "dragon_tiger", "minute_kbars",
})
# 这三张走各自专门的幂等动作（daily_basic_ingest / stk_limit 窗口 / auction 直连脚本）
_DEDICATED_ACTIONS: frozenset[str] = frozenset({"daily_basic", "stk_limits", "auction_data"})
# 盘中实时快照含无法从日K还原的时点字段，历史缺口一律不自动组装：
# 伪造一份「看起来像真的」快照会污染正式选股链路与 PIT 证据，只告警并给修复入口。
_NO_AUTO_REPAIR: frozenset[str] = frozenset({"stock_quotes"})
_NO_AUTO_REPAIR_HINT = (
    "stock_quotes 是盘中实时快照，历史日无法由日K无损还原；"
    "请按目标交易日人工重建（脚本 scripts/repair_snapshot_day.py，带 --dry-run），"
    "或在下一次正常盘后刷新中确认已恢复"
)

_daemon_started = False
_daemon_lock = threading.Lock()


def _safe_rollback(db: Any) -> None:
    """动作失败后回滚；rollback 自身的异常不能盖掉原始错误（也不要求 db 是真 Session）。"""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        logger.debug("self_heal: rollback 失败", exc_info=True)


def _state_path() -> Path:
    from app.services.data_quality_monitor import DATA_DIR

    return DATA_DIR / "runtime" / "startup_self_heal.json"


def _norm_date(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return None


def _recent_closed_trade_days(db: Session, now: datetime, lookback: int) -> list[str]:
    """最近 N 个已收盘交易日（升序）。优先 Tushare 真实日历，失败回退本地日K。"""
    from app.engine.trading_calendar import latest_allowed_bar_date, previous_weekday

    # 复刻 latest_allowed_bar_date() 的「工作日 17:30 前当日日K未就绪」判据，
    # 但用传入的 now 计算（可测试），而不是偷偷读系统时钟。
    day = now.date()
    if day.weekday() < 5 and (now.hour < 17 or (now.hour == 17 and now.minute < 30)):
        day = previous_weekday(day)
    cutoff = latest_allowed_bar_date(day)
    days: list[str] = []
    try:
        from app.services.tushare_client import fetch_recent_trade_dates

        for raw in fetch_recent_trade_dates():
            day = _norm_date(raw)
            if day:
                days.append(day)
    except Exception as exc:  # noqa: BLE001 - 上游不可用不该阻断自愈
        logger.debug("self_heal: 交易日历不可用，回退本地: %r", exc)
    if not days:
        try:
            rows = db.execute(text(
                "SELECT DISTINCT trade_date FROM daily_kbars "
                "ORDER BY trade_date DESC LIMIT :n"), {"n": lookback + 10}).all()
            days = [d for r in rows if (d := _norm_date(r[0]))]
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(db)
            logger.warning("self_heal: 本地交易日回退也失败: %r", exc)
            return []
    closed = sorted({d for d in days if d <= cutoff})
    return closed[-lookback:]


def _day_rows(db: Session, table: str, day: str) -> int:
    """当日行数。历史写入混用过 YYYY-MM-DD / YYYYMMDD，两种都算。"""
    try:
        return int(db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE trade_date IN (:d, :dc)"),
            {"d": day, "dc": day.replace("-", "")},
        ).scalar() or 0)
    except Exception as exc:  # noqa: BLE001 - 表不存在时按缺失处理
        _safe_rollback(db)
        logger.debug("self_heal: %s %s 计数失败: %r", table, day, exc)
        return 0


def scan_gaps(db: Session, trade_days: list[str]) -> dict[str, list[str]]:
    """逐交易日扫描核心表整日缺失。"""
    gaps: dict[str, list[str]] = {}
    for day in trade_days:
        missing = [t for t, floor in _CORE_DAILY_TABLES if _day_rows(db, t, day) < floor]
        if missing:
            gaps[day] = missing
    return gaps


# ---------------------------------------------------------------- 补数动作
# 每个动作签名 (db, trade_days, gaps) -> dict；内部自行决定要不要跑、跑哪几天。
# 抛异常由 _run_actions 统一兜住，不影响其它动作。

def _act_daily_basic(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    from app.services.daily_basic_ingest import ingest_daily_basic

    targets = [d for d in days if "daily_basic" in gaps.get(d, [])]
    if not targets:
        return {"status": "skipped", "reason": "daily_basic 无缺口"}
    out: dict[str, Any] = {"status": "completed", "days": {}}
    for day in targets:
        try:
            out["days"][day] = ingest_daily_basic(db, day)
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(db)
            out["days"][day] = {"error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return out


def _act_margin_detail(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """两融明细 T+1 晚出且 promax 分页常截断：按最近几日检测并只补缺失代码。"""
    from app.services.margin_detail_fill import run_margin_detail_fill

    if not days:
        return {"status": "skipped", "reason": "无交易日"}
    return {"status": "completed", **run_margin_detail_fill(db, trade_date=days[-1], lookback_days=5)}


def _derived_indicators_need_repair(db: Session, end: str) -> tuple[bool, dict[str, Any]]:
    """派生指标是否真的失真。fill_local_indicators 要加载全历史 OHLC 分批重算（分钟级），
    不能每次开机都白跑一遍；只有当日横截面出现整列 NULL / 大面积缺值才补。"""
    try:
        row = db.execute(text(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN ivol20 IS NOT NULL THEN 1 ELSE 0 END) AS ivol_nn, "
            "SUM(CASE WHEN ma5 IS NOT NULL THEN 1 ELSE 0 END) AS ma5_nn, "
            "SUM(CASE WHEN vol_ratio_5 IS NOT NULL THEN 1 ELSE 0 END) AS vr_nn "
            "FROM daily_kbars WHERE trade_date IN (:d, :dc)"
        ), {"d": end, "dc": end.replace("-", "")}).mappings().first()
    except Exception as exc:  # noqa: BLE001 - 探测失败时保守地执行修复
        _safe_rollback(db)
        return True, {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    n = int((row or {}).get("n") or 0)
    if not n:
        return True, {"reason": "当日无 daily_kbars 行"}
    ratios = {k: round(int((row or {}).get(k) or 0) / n, 3)
              for k in ("ivol_nn", "ma5_nn", "vr_nn")}
    return any(v < 0.8 for v in ratios.values()), {"rows": n, **ratios}


def _act_indicators(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """派生指标只补 NULL / 重算最近窗口，修「整列 NULL」这类结构检查看不见的失真。"""
    if not gaps or not days:
        return {"status": "skipped", "reason": "库表无缺口，跳过指标重算"}
    end = max(gaps)
    need, probe = _derived_indicators_need_repair(db, end)
    if not need:
        return {"status": "skipped", "reason": "派生指标覆盖正常", "probe": probe}
    from app.services.ivol_fill import fill_ivol_incremental
    from app.services.kdj_boll_cci_fill import fill_kdj_boll_cci
    from app.services.local_indicator_fill import fill_local_indicators

    end = max(gaps)
    start = (datetime.fromisoformat(end) - timedelta(days=18)).date().isoformat()
    out: dict[str, Any] = {"status": "completed", "window": [start, end], "probe": probe}
    steps: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("ivol", lambda: fill_ivol_incremental(db, end)),
        ("kdj_boll_cci", lambda: fill_kdj_boll_cci(db, since=start, end=end)),
        ("local_indicators", lambda: fill_local_indicators(db, since=start, end=end)),
    )
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(db)
            out[name] = {"error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return out


def _act_survey_margin(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    from app.services.survey_margin_ingest import daily_survey_margin_update

    targets = [d for d in days if gaps.get(d)]
    if not targets:
        return {"status": "skipped", "reason": "无缺口日"}
    out: dict[str, Any] = {"status": "completed", "days": {}}
    for day in targets[-2:]:  # 只补最近两天，避免开机一次打满上游
        try:
            out["days"][day] = daily_survey_margin_update(db, day)
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(db)
            out["days"][day] = {"error": f"{type(exc).__name__}: {str(exc)[:180]}"}
    return out


def _gap_tables(gaps: dict[str, list[str]]) -> set[str]:
    return {t for tables in gaps.values() for t in tables}


def _gap_days_for(gaps: dict[str, list[str]], table: str) -> list[str]:
    return sorted(d for d, tables in gaps.items() if table in tables)


def _act_remediate(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """诊断驱动的补数：覆盖日K / 指数 / 涨停板 / 概念成分 / 新闻等最近 10 个交易日缺口。

    只在缺口里确实有本入口能补的表时调用；否则冷诊断 + 计划构建纯属浪费（约 20 秒起），
    而且会掩盖「缺口补不回」这个事实。
    """
    targets = _gap_tables(gaps) & _REMEDIABLE_VIA_DIAGNOSIS
    if not targets:
        return {"status": "skipped", "reason": f"无本入口可补的表（缺口表={sorted(_gap_tables(gaps))}）"}
    from app.services.data_gap_remediation_service import remediate_data_health_gaps

    result = remediate_data_health_gaps(db, post_diagnosis=False)
    return {"status": "completed", "target_tables": sorted(targets),
            "plan_count": len(result.get("actions") or result.get("plan") or []),
            "remediation_status": result.get("status")}


def _act_kbar_cache(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """data/cache/daily_kbar.parquet：回测日K缓存，从库增量刷新（无变化时指纹快速跳过）。"""
    from app.services.market_pool import kbar_cache

    if not kbar_cache.enabled():
        return {"status": "skipped", "reason": "kbar cache 功能未启用"}
    return {"status": "completed", **kbar_cache.refresh_kbar_cache(db)}


def _act_research_parquets(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """研究 parquet（daily_basic / stk_limits / index_daily / hk_holdings）增量重导。"""
    from app.services.scheduler_service import _export_research_parquets

    return _export_research_parquets("startup_self_heal")


def _act_kpl_list(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    from app.services.scheduler_service import _refresh_kpl_list_cache

    return _refresh_kpl_list_cache("startup_self_heal")


def _act_limit_list_d(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """涨跌停榜 limit_list_d 研究缓存：脚本自带断点（已有日期跳过），只补最近窗口。

    此前这个 parquet 只靠手工跑，长期滞后 10+ 个交易日挂在巡检黄项里。
    """
    import subprocess
    import sys

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"status": "skipped", "reason": "pytest"}
    if not days:
        return {"status": "skipped", "reason": "无交易日"}
    backend = Path(__file__).resolve().parents[2]
    end = days[-1].replace("-", "")
    start = (datetime.fromisoformat(days[0]) - timedelta(days=5)).strftime("%Y%m%d")
    t0 = time.monotonic()
    try:
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(
            p for p in (str(backend), os.environ.get("PYTHONPATH", "")) if p)}
        proc = subprocess.run(
            [sys.executable, "scripts/shortline_research/fetch_limit_list_d.py", start, end],
            cwd=str(backend), env=env, capture_output=True, text=True, timeout=600, check=False,
        )
        return {"status": "completed" if proc.returncode == 0 else "failed",
                "window": f"{start}~{end}", "seconds": round(time.monotonic() - t0, 1),
                "stdout_tail": (proc.stdout or "")[-500:], "stderr_tail": (proc.stderr or "")[-300:]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "window": f"{start}~{end}",
                "seconds": round(time.monotonic() - t0, 1),
                "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _act_stk_limits(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """stk_limits 不在 remediate_data_health_gaps 的逻辑表清单里，但有现成的按日幂等回填。"""
    targets = _gap_days_for(gaps, "stk_limits")
    if not targets:
        return {"status": "skipped", "reason": "stk_limits 无缺口"}
    from app.services.scheduler_service import _backfill_stk_limit_window

    out = _backfill_stk_limit_window(db, reference_trade_date=targets[-1],
                                     window_days=max(1, len(days)))
    rows = out.get("backfilled_dates") or []
    done = [r for r in rows if r.get("status") == "success"]
    return {"status": "completed" if done else "failed", "target_days": targets,
            "backfilled": {r.get("trade_date"): r.get("count") for r in done},
            "unfilled": [r for r in rows if r.get("status") != "success"]}


def _act_auction_data(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """auction_data 同样不在诊断补数清单里；直连脚本按交易日删当日再插，天然幂等。"""
    import subprocess
    import sys

    targets = _gap_days_for(gaps, "auction_data")
    if not targets:
        return {"status": "skipped", "reason": "auction_data 无缺口"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"status": "skipped", "reason": "pytest"}
    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        p for p in (str(backend), os.environ.get("PYTHONPATH", "")) if p)}
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/backfill_auction_direct.py",
             "--start", targets[0], "--end", targets[-1]],
            cwd=str(backend), env=env, capture_output=True, text=True, timeout=900, check=False,
        )
        return {"status": "completed" if proc.returncode == 0 else "failed",
                "target_days": targets, "seconds": round(time.monotonic() - t0, 1),
                "stdout_tail": (proc.stdout or "")[-400:], "stderr_tail": (proc.stderr or "")[-300:]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "target_days": targets,
                "seconds": round(time.monotonic() - t0, 1),
                "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


_EVENT_PARQUET_STALE_DAYS = 3
_EVENT_BACKFILL_TIMEOUT_SECONDS = 900


def _event_parquet_latest(year: str) -> dict[str, str | None]:
    """读当年增减持事件 parquet 的最新公告日期（YYYYMMDD）。读不到视为缺失。"""
    import pandas as pd

    base = Path(__file__).resolve().parents[2] / "data" / "midterm" / "events"
    out: dict[str, str | None] = {}
    for name in ("holdertrade_in", "holdertrade_de"):
        f = base / f"{name}_{year}.parquet"
        try:
            s = pd.read_parquet(f, columns=["ann_date"])["ann_date"].astype(str)
            # ann_date 在不同批次里可能是 '20261105' / '2026-11-05' / 数值型，统一成 8 位数字
            s = s.str.replace(r"\D", "", regex=True).str[:8]
            s = s[s.str.len() == 8]
            out[name] = str(s.max()) if len(s) else None
        except Exception:  # noqa: BLE001 - 文件缺失/损坏都按「需补」处理
            out[name] = None
    return out


def _act_event_parquets(db: Session, days: list[str], gaps: dict[str, list[str]]) -> dict[str, Any]:
    """增减持/回购事件 parquet 增量日更。

    这两个事件集由 `scripts/midterm/backfill_longterm.py` 从 Tushare 按窗口拉取，
    但此前**没有任何日更调度**（只在 10 月人工全量回填过一次），所以每天都会挂一条
    「最新事件日期滞后 N 天」的巡检黄项。脚本自带按窗口的断点状态，重复执行只会
    拉最近未覆盖的窗口，适合放进自愈；这里再加滞后门槛，避免每次开机都打上游。
    """
    import subprocess
    import sys

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {"status": "skipped", "reason": "pytest"}
    if not days:
        return {"status": "skipped", "reason": "无交易日"}
    year = days[-1][:4]
    expected = datetime.fromisoformat(days[-1])
    def _is_stale(v: str | None) -> bool:
        if not v:
            return True
        try:
            return datetime.strptime(v, "%Y%m%d") < expected - timedelta(days=_EVENT_PARQUET_STALE_DAYS)
        except ValueError:
            return True

    before = _event_parquet_latest(year)
    stale = {k: v for k, v in before.items() if _is_stale(v)}
    if not stale:
        return {"status": "skipped", "reason": "事件 parquet 已新鲜", "latest": before}

    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(p for p in (str(backend), os.environ.get("PYTHONPATH", "")) if p),
           # 自愈是轻量增量，不用历史回填那套 6 轮 ×90s 的失败重试
           "ASHARE_DE_PASSES": "2", "ASHARE_DE_PASS_WAIT": "10", "ASHARE_DE_WINDOW_SLEEP": "0.6"}
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/midterm/backfill_longterm.py", "events", "events_de"],
            cwd=str(backend), env=env, capture_output=True, text=True,
            timeout=_EVENT_BACKFILL_TIMEOUT_SECONDS, check=False,
        )
        ok = proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "stale": stale,
                "error": f"{type(exc).__name__}: {str(exc)[:180]}",
                "seconds": round(time.monotonic() - t0, 1)}
    after = _event_parquet_latest(year)
    return {"status": "completed" if ok else "failed", "before": before, "after": after,
            "advanced": {k: (before.get(k), after.get(k)) for k in before if after.get(k) != before.get(k)},
            "seconds": round(time.monotonic() - t0, 1),
            "stdout_tail": (proc.stdout or "")[-500:], "stderr_tail": (proc.stderr or "")[-300:]}


_ACTIONS: tuple[tuple[str, Callable[[Session, list[str], dict[str, list[str]]], dict[str, Any]]], ...] = (
    ("remediate_health_gaps", _act_remediate),
    ("daily_basic", _act_daily_basic),
    ("margin_detail", _act_margin_detail),
    ("survey_margin", _act_survey_margin),
    ("derived_indicators", _act_indicators),
    ("kbar_parquet_cache", _act_kbar_cache),
    ("research_parquets", _act_research_parquets),
    ("kpl_list_cache", _act_kpl_list),
    ("limit_list_d_cache", _act_limit_list_d),
    ("event_parquets", _act_event_parquets),
    ("stk_limits", _act_stk_limits),
    ("auction_data", _act_auction_data),
)


def _archive_output_gaps(db: Session, days: list[str], now: datetime) -> dict[str, Any]:
    """把漏出票的交易日归档。**不补票、不改口径**：实盘出票错过无法事后补记为
    live_forward，但必须留下可复盘的记录，而不是只靠健康分红项事后猜。

    当天没过出票截止（daily_output_check.OUTPUT_DEADLINE）不归档：21:30 出票和 22:15 watchdog
    补跑还没轮完，此时缺票只是还没跑（2026-11-26 18:36 / 20:42 两轮都把当天记成了漏票）。"""
    try:
        from app.services.daily_output_check import daily_output_status, output_due
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, Any] = {}
    for day in days:
        if not output_due(day, now):
            continue
        try:
            st = daily_output_status(db, day, now=now.replace(tzinfo=None))
            if not st.get("ok"):
                out[day] = {"status": st.get("status"),
                            "reason": str(st.get("reason") or "")[:300],
                            "short_done": st.get("short_done"),
                            "short_expected": st.get("short_expected")}
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(db)
            out[day] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return out


def repick_enabled() -> bool:
    """漏出票自动补跑总开关（默认开，可用环境变量关掉做人工兜底）。"""
    return os.environ.get("ASHARE_SELF_HEAL_REPICK_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def _under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _try_repick_missed_output(db: Session, days: list[str], now: datetime,
                               remaining_gaps: dict[str, list[str]]) -> dict[str, Any]:
    """库表补齐后，重试一次正式出票补跑。只读判定 + 复用既有幂等流程，不新造出票口径。

    前置条件全部满足才调用 ``scheduler_service._late_repair_repick``：最近交易日已过出票截止仍缺票、
    还在入场日 09:15 之前、核心表缺口已清零、没有在途 formal run、健康闸门通过
    （后三项由被复用的流程自己再查一遍）。pytest 下直接跳过，避免单测误触发生产出票。

    出票截止之前不补跑当天：当天的票归 21:00 补数 / 21:30 出票 / 22:15 watchdog。2026-11-25 20:52
    守护轮把还没到点的当天判成漏票，抢在 21:00 补数和 21:30 出票之前自己出了 4 个策略的票。
    """
    if not days:
        return {"status": "skipped", "reason": "no_trade_day"}
    if _under_pytest():
        return {"status": "skipped", "reason": "pytest"}
    if not repick_enabled():
        return {"status": "skipped", "reason": "disabled"}
    if remaining_gaps:
        return {"status": "skipped", "reason": "data_gaps_remain", "gaps": sorted(remaining_gaps)}
    trade_date = days[-1]
    try:
        from app.services.daily_output_check import daily_output_status, output_due
        from app.services.midterm_live_seal import generated_before_entry_open

        if not output_due(trade_date, now):
            return {"status": "skipped", "reason": "before_output_deadline", "trade_date": trade_date}
        if not generated_before_entry_open(trade_date, datetime.now(timezone.utc)):
            return {"status": "skipped", "reason": "past_entry_open", "trade_date": trade_date}
        st = daily_output_status(db, trade_date, now=now.replace(tzinfo=None))
        if st.get("ok"):
            return {"status": "skipped", "reason": "already_completed", "trade_date": trade_date}
    except Exception as exc:  # noqa: BLE001 - 前置判定失败不阻塞自愈其余动作
        _safe_rollback(db)
        return {"status": "skipped", "reason": "precheck_error",
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # 自愈守护跑在后台线程（无 loop），这里只防「将来有人在 async 上下文直接调用」。
        return {"status": "skipped", "reason": "running_event_loop", "trade_date": trade_date}

    t = time.monotonic()
    try:
        from app.services.scheduler_service import _late_repair_repick

        out = asyncio.run(_late_repair_repick(db, "startup_self_heal_repick"))
        res = {"status": str(out.get("status") or "unknown"), "trade_date": out.get("trade_date") or trade_date,
               "reason": out.get("reason"), "blockers": out.get("blockers"),
               "seconds": round(time.monotonic() - t, 1)}
        logger.info("self_heal: 漏出票补跑 %s status=%s reason=%s (%.1fs)",
                    trade_date, res["status"], res["reason"], res["seconds"])
        return res
    except Exception as exc:  # noqa: BLE001 - 补跑失败只记录，不拖垮自愈
        _safe_rollback(db)
        logger.warning("self_heal: 漏出票补跑失败 %s: %r", trade_date, exc)
        return {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"runs": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        state.setdefault("runs", [])
        return state
    except (json.JSONDecodeError, OSError):
        return {"runs": []}


def _save_state(path: Path, run: dict[str, Any]) -> None:
    state = _load_state(path)
    runs = [r for r in state["runs"] if r.get("run_at") != run.get("run_at")]
    runs.append(run)
    state["runs"] = runs[-STATE_KEEP_RUNS:]
    state["last_run_at"] = run.get("run_at")
    state["last_reference_trade_date"] = run.get("reference_trade_date")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def archived_output_miss(trade_date: str) -> str | None:
    """只读查询：该交易日的历史漏出票是否已由自愈归档（供巡检红项补充上下文）。

    注意这里**不改判定、不降级别**：实盘出票错过就是错过。只是让页面上那条红项
    说清楚「数据已补齐、这是一笔已归档的旧账」，而不是让人以为故障仍在扩散。
    """
    state = _load_state(_state_path())
    for run in reversed(state.get("runs") or []):
        miss = (run.get("output_missed") or {}).get(trade_date)
        if miss and "error" not in miss:
            return (f"数据已由开机自愈补齐并归档（{run.get('run_at')}）；"
                    "实盘出票错过无法事后补记为 live_forward，见出票成功率趋势")
    return None


def _emit_unrepaired_alert(remaining: dict[str, list[str]], run_at: str) -> dict[str, Any]:
    """补完后仍有缺口时显式告警，别让「每轮都报、永远没人管」变成静默常态。"""
    blocked = sorted(_gap_tables(remaining) & _NO_AUTO_REPAIR)
    info: dict[str, Any] = {"days": sorted(remaining),
                            "tables": sorted(_gap_tables(remaining)),
                            "no_auto_repair_tables": blocked,
                            "hint": _NO_AUTO_REPAIR_HINT if blocked else None}
    try:
        from app.core import alerts

        msg = (f"开机自愈 {run_at} 后仍有 {len(remaining)} 个交易日库表缺口："
               f"{json.dumps(remaining, ensure_ascii=False)[:600]}；涉及表 {info['tables']}。"
               + (f"其中 {blocked} 不支持自动补回：{_NO_AUTO_REPAIR_HINT}" if blocked else ""))
        info["alerted"] = bool(alerts.emit_alert("data_health", "self_heal_unrepaired", msg))
    except Exception:  # noqa: BLE001 - 告警失败不影响自愈本身
        logger.debug("self_heal: 未修复缺口告警失败", exc_info=True)
        info["alerted"] = False
    return info


def run_self_heal(
    db: Session,
    *,
    now: datetime | None = None,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    trigger: str = "startup",
    force: bool = False,
) -> dict[str, Any]:
    """跑一次开机自愈。同步执行，失败不抛出；调用方负责放在后台线程。"""
    now = now or datetime.now().astimezone()
    path = _state_path()
    state = _load_state(path)
    t0 = time.monotonic()

    # 防抖：同一交易日、半小时内、且上次没有留下缺口，就不再打上游。
    last = (state.get("runs") or [{}])[-1]
    if not force and last.get("reference_trade_date") and not last.get("gaps"):
        try:
            age = (now.replace(tzinfo=None) - datetime.fromisoformat(last["run_at"]).replace(tzinfo=None)).total_seconds()
        except Exception:  # noqa: BLE001
            age = MIN_INTERVAL_SECONDS + 1
        if age < MIN_INTERVAL_SECONDS:
            return {"status": "skipped", "trigger": trigger,
                    "reason": f"距上次自愈 {age:.0f}s 且无遗留缺口",
                    "reference_trade_date": last.get("reference_trade_date")}

    try:
        days = _recent_closed_trade_days(db, now, lookback)
    except Exception as exc:  # noqa: BLE001
        logger.warning("self_heal: 交易日扫描失败: %r", exc)
        days = []
    gaps = scan_gaps(db, days) if days else {}
    result: dict[str, Any] = {
        "run_at": now.isoformat(timespec="seconds"),
        "trigger": trigger,
        "reference_trade_date": days[-1] if days else None,
        "scanned_days": days,
        "gaps": gaps,
        "actions": {},
    }

    for name, fn in _ACTIONS:
        t = time.monotonic()
        try:
            out = fn(db, days, gaps)
        except Exception as exc:  # noqa: BLE001 - 单个动作失败不拖垮整轮自愈
            _safe_rollback(db)
            logger.warning("self_heal: 动作 %s 失败: %r", name, exc)
            out = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        result["actions"][name] = {"seconds": round(time.monotonic() - t, 2), **out}

    result["output_missed"] = _archive_output_gaps(db, days, now)
    try:
        from app.services.data_health_service import clear_data_health_cache

        clear_data_health_cache()
    except Exception:  # noqa: BLE001
        logger.debug("self_heal: 清诊断缓存失败", exc_info=True)

    # 复扫一次，把补完后仍缺的表记下来，下一轮开机继续补（不靠人工发现）。
    result["remaining_gaps"] = scan_gaps(db, days) if days else {}
    result["status"] = "completed" if not result["remaining_gaps"] else "partial"
    if result["remaining_gaps"]:
        result["unrepaired"] = _emit_unrepaired_alert(result["remaining_gaps"], result["run_at"])
    # 数据补齐后再触发一次漏出票补跑：此前 21:00 / 08:30 的补跑可能被健康闸门拦下，
    # 而闸门恢复（正是本轮自愈做的事）之后没有任何环节会重试，漏票就一直挂到下一交易日。
    result["output_repick"] = _try_repick_missed_output(db, days, now, result["remaining_gaps"])
    result["seconds"] = round(time.monotonic() - t0, 2)
    try:
        _save_state(path, result)
    except Exception:  # noqa: BLE001
        logger.warning("self_heal: 状态写入失败", exc_info=True)
    logger.info("self_heal: %s 自愈完成 status=%s gaps=%s remaining=%s (%.1fs)",
                trigger, result["status"], list(gaps), list(result["remaining_gaps"]), result["seconds"])
    return result


def start_self_heal_daemon(
    *,
    startup_delay: float = STARTUP_DELAY_SECONDS,
    interval: float = DAEMON_INTERVAL_SECONDS,
) -> bool:
    """启动后台自愈循环（进程内单例）。首次延迟执行，避免和启动预热抢数据库/上游。"""
    global _daemon_started
    with _daemon_lock:
        if _daemon_started:
            return False
        _daemon_started = True

    def _loop() -> None:
        time.sleep(startup_delay)
        first = True
        while True:
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    from app.db.session import SessionLocal

                    with SessionLocal() as db:
                        out = run_self_heal(db, trigger="startup" if first else "daemon")
                        logger.info("self_heal: 轮次完成 trigger=%s status=%s gaps=%d",
                                    out.get("trigger"), out.get("status"), len(out.get("gaps") or {}))
                except Exception:  # noqa: BLE001
                    logger.exception("self_heal: 后台自愈轮次异常")
                finally:
                    first = False
            time.sleep(interval)

    threading.Thread(target=_loop, name="startup-self-heal", daemon=True).start()
    logger.info("self_heal: 后台守护已启动（首轮延迟 %.0fs，间隔 %.0fs）", startup_delay, interval)
    return True
