"""盘中逐股 K 线回退（get_market_universe → _fetch_first_kline）的当日跨进程缓存 + 日额度闸。

背景（2026-10-26 额度耗尽根因）：盘中买入确认轮询（Prefect execution-confirmation-poll，每 5 分钟一个新进程，
09:30–14:00 共 ~33 次）每次都调 get_market_universe()；本地 daily_kbars 不足 30 根的股票（≈25 只次新股）
会回退到 Tushare 逐股 `pro.daily` + `pro.daily_basic`，拿回仍不足 30 根 → 丢弃，下一轮（新进程、无内存缓存）再来一遍。
33 轮 × 25 只 × 2 次 ≈ 1650 次，promax（13:00 前 quantdata 920 先耗尽，14:16 promax 耗尽）的当日额度被它吃光，
17:35 盘后全量同步无额度可用。

本模块只在盘中窗口（工作日 09:15–15:05，Asia/Shanghai）生效：
- 当日同一只股票的回退结果（含「仍不足 30 根」的负结果）跨进程缓存，同一天只请求一次；
- 每天最多做 KLINE_FALLBACK_MAX_STOCKS_PER_DAY（默认 150，可用环境变量
  ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY 调整，0 = 不限）只股票的真实回退请求，超出的当日直接放弃（等同取数失败）；
- 窗口之外（15:05 后的盘后全量/17:35 正式推荐）完全不缓存、不限额，行为与改动前一致，避免拿上午的缓存当收盘数据。
读写失败一律回退为「不缓存」，绝不影响选股主流程。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Shanghai")
WINDOW_START = (9, 15)
WINDOW_END = (15, 5)
DEFAULT_MAX_STOCKS_PER_DAY = 150
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime" / "kline_fallback_cache.sqlite"


def _now() -> datetime:
    return datetime.now(_TZ)


def in_window(now: datetime | None = None) -> bool:
    now = now or _now()
    if now.weekday() >= 5:
        return False
    return WINDOW_START <= (now.hour, now.minute) < WINDOW_END


def max_stocks_per_day() -> int:
    raw = os.environ.get("ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY", "").strip()
    if not raw:
        try:
            from app.core.provider_quota import _dotenv_value

            raw = _dotenv_value("ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY")
        except Exception:  # noqa: BLE001
            raw = ""
    try:
        return int(raw) if raw else DEFAULT_MAX_STOCKS_PER_DAY
    except ValueError:
        return DEFAULT_MAX_STOCKS_PER_DAY


def _conn() -> sqlite3.Connection | None:
    try:
        from app.core import metrics

        if metrics._process_role() == "pytest" and not os.environ.get("ASHARE_KLINE_FALLBACK_CACHE_FORCE"):
            return None
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH), timeout=2.0)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kline_fallback (day TEXT NOT NULL, code TEXT NOT NULL, source TEXT NOT NULL, "
            "n_bars INTEGER NOT NULL, bars_json TEXT NOT NULL, errors_json TEXT NOT NULL, created_ts REAL NOT NULL, "
            "PRIMARY KEY (day, code))"
        )
        return conn
    except Exception:  # noqa: BLE001
        logger.debug("kline_fallback_cache store unavailable", exc_info=True)
        return None


def lookup(code: str, *, now: datetime | None = None) -> tuple[list[dict[str, Any]], str, list[str]] | None:
    """窗口内命中当日缓存 → (bars, source, errors)（负结果 bars 为空）；未命中/窗口外/出错 → None。"""
    now = now or _now()
    if not in_window(now):
        return None
    conn = _conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT source, bars_json, errors_json FROM kline_fallback WHERE day=? AND code=?",
            (now.strftime("%Y-%m-%d"), str(code)),
        ).fetchone()
        if not row:
            return None
        return json.loads(row[1]), str(row[0]), list(json.loads(row[2]))
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()


def budget_allows_fetch(*, now: datetime | None = None) -> bool:
    """窗口内：当天已真实回退请求的股票数 < 日上限才允许；窗口外恒 True。"""
    now = now or _now()
    if not in_window(now):
        return True
    limit = max_stocks_per_day()
    if limit <= 0:
        return True
    conn = _conn()
    if conn is None:
        return True
    try:
        n = conn.execute("SELECT COUNT(*) FROM kline_fallback WHERE day=?", (now.strftime("%Y-%m-%d"),)).fetchone()[0]
        return int(n) < limit
    except Exception:  # noqa: BLE001
        return True
    finally:
        conn.close()


def store(code: str, bars: list[dict[str, Any]], source: str, errors: list[str], *, now: datetime | None = None) -> None:
    now = now or _now()
    if not in_window(now):
        return
    conn = _conn()
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kline_fallback(day, code, source, n_bars, bars_json, errors_json, created_ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), str(code), source, len(bars),
                 json.dumps(bars, ensure_ascii=False), json.dumps(errors, ensure_ascii=False), time.time()),
            )
            # 只保留最近 3 天，防止表无限增长
            conn.execute("DELETE FROM kline_fallback WHERE day < ?", ((now - timedelta(days=3)).strftime("%Y-%m-%d"),))
    except Exception:  # noqa: BLE001
        logger.debug("kline_fallback_cache store failed", exc_info=True)
    finally:
        conn.close()
