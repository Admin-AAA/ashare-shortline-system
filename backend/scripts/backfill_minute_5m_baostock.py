"""按股票范围回填 5min 分钟K（Baostock 主源、Eastmoney 北交所专用分流）。

Baostock ``query_history_k_data_plus`` 可单次返回普通沪深股票的 [start,end]
整段历史；北交所 ``920xxx`` 不受其支持，改用 Eastmoney push2his，并按安全
日期块串行拉取以规避单请求 1152 根截断。所有日期块成功后才按股票一次性
幂等 upsert；任一块失败会回滚该股票，避免半股数据提交。

``--resume`` 仅跳过目标闭区间内每个预期交易日均有 48 根 5min K 线的股票。
预期交易日来自该股票的日 K 集合，因此不会把停牌日误判为缺口。

用法：
    PYTHONPATH=backend backend/.venv/bin/python \
        backend/scripts/backfill_minute_5m_baostock.py \
        --start-date 2026-01-30 --end-date 2026-07-28 --freq 5min --resume
"""
from __future__ import annotations

import argparse
import logging
import math
import re
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import DailyKbar, MinuteKbar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_minute_5m")

_A_SHARE_CODE_RE = re.compile(r"^(?:[03468][0-9]{5}|920[0-9]{3})$")
_EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_TIMEOUT_SECONDS = 20.0
_EASTMONEY_MIN_INTERVAL_SECONDS = 1.0
_EASTMONEY_MAX_ATTEMPTS = 3
_EASTMONEY_CHUNK_DAYS = 20
_EASTMONEY_ROW_LIMIT = 1152
_BARS_PER_DAY_5MIN = 48
_EXPECTED_5MIN_TIMES = frozenset(
    f"{minute // 60:02d}:{minute % 60:02d}:00"
    for minute in (
        *range(9 * 60 + 35, 11 * 60 + 31, 5),
        *range(13 * 60 + 5, 15 * 60 + 1, 5),
    )
)
_EASTMONEY_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_eastmoney_lock = threading.Lock()
_eastmoney_last_request = 0.0


class EastmoneyFetchError(RuntimeError):
    """Eastmoney 请求失败，带可运营的失败分类。"""

    def __init__(
        self,
        category: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def _wait_for_eastmoney_rate_limit() -> None:
    global _eastmoney_last_request
    wait = _EASTMONEY_MIN_INTERVAL_SECONDS - (time.monotonic() - _eastmoney_last_request)
    if wait > 0:
        time.sleep(wait)


def _eastmoney_secid(code: str) -> str:
    """北交所股票的 Eastmoney 市场代码。"""
    if not re.fullmatch(r"920[0-9]{3}", code):
        raise ValueError(f"Eastmoney 北交所分流不支持 code={code}")
    return f"0.{code}"


def _date_chunks(
    start_date: str,
    end_date: str,
    *,
    days: int = _EASTMONEY_CHUNK_DAYS,
) -> list[tuple[str, str]]:
    """把输入闭区间切成连续、互不重叠的安全自然日块。"""
    if days < 1:
        raise ValueError("days must be >= 1")
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be <= end_date")
    chunks: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _parse_eastmoney_klines(code: str, freq: str, klines: list[str]) -> list[dict]:
    """解析 push2his K 线；volume 从手转换为股，amount 保持原单位。"""
    rows: list[dict] = []
    for raw in klines:
        fields = str(raw).split(",")
        if len(fields) < 8:
            raise ValueError("Eastmoney kline 字段不足")
        raw_time = fields[0].strip()
        if len(raw_time) == 16:
            raw_time = f"{raw_time}:00"
        trade_date = raw_time[:10]
        if len(raw_time) != 19 or len(trade_date) != 10:
            raise ValueError("Eastmoney kline 时间格式非法")
        try:
            open_price = float(fields[1])
            close_price = float(fields[2])
            high_price = float(fields[3])
            low_price = float(fields[4])
            volume_shares = float(fields[5]) * 100
            amount = float(fields[6])
        except (TypeError, ValueError) as exc:
            raise ValueError("Eastmoney kline 数值格式非法") from exc
        rows.append(
            {
                "code": code,
                "freq": freq,
                "trade_date": trade_date,
                "trade_time": raw_time,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume_shares,
                "amount": amount,
                "source": "eastmoney",
            }
        )
    return rows


def _classify_eastmoney_response(response: Any) -> EastmoneyFetchError | None:
    status = int(getattr(response, "status_code", 0))
    if status == 200:
        return None
    if status == 429:
        retry_after = 1.0
        try:
            retry_after = max(
                1.0,
                float((getattr(response, "headers", {}) or {}).get("Retry-After", 1)),
            )
        except (TypeError, ValueError):
            pass
        return EastmoneyFetchError(
            "http_rate_limit",
            "Eastmoney HTTP 429",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    if status in _EASTMONEY_RETRYABLE_STATUS or status >= 500:
        return EastmoneyFetchError("http_server", f"Eastmoney HTTP {status}", retryable=True)
    return EastmoneyFetchError("http_client", f"Eastmoney HTTP {status}", retryable=False)


def _request_eastmoney_chunk(
    client: Any,
    *,
    code: str,
    freq: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    global _eastmoney_last_request
    params = {
        "secid": _eastmoney_secid(code),
        "klt": "5",
        "fqt": "0",
        "lmt": str(_EASTMONEY_ROW_LIMIT),
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    last_error: EastmoneyFetchError | None = None
    for attempt in range(_EASTMONEY_MAX_ATTEMPTS):
        try:
            _wait_for_eastmoney_rate_limit()
            _eastmoney_last_request = time.monotonic()
            response = client.get(_EASTMONEY_URL, params=params, timeout=_EASTMONEY_TIMEOUT_SECONDS)
            failure = _classify_eastmoney_response(response)
            if failure is not None:
                raise failure
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise EastmoneyFetchError(
                    "invalid_response", "Eastmoney JSON 解析失败", retryable=False
                ) from exc
            if not isinstance(payload, dict):
                raise EastmoneyFetchError(
                    "invalid_response", "Eastmoney 响应不是对象", retryable=False
                )
            response_code = payload.get("rc")
            if response_code not in (None, 0, "0"):
                raise EastmoneyFetchError(
                    "provider_error",
                    f"Eastmoney rc={response_code}",
                    retryable=False,
                )
            data = payload.get("data")
            if data is None:
                return []
            if not isinstance(data, dict):
                raise EastmoneyFetchError(
                    "invalid_response", "Eastmoney data 类型非法", retryable=False
                )
            klines = data.get("klines")
            if klines is None:
                return []
            if not isinstance(klines, list):
                raise EastmoneyFetchError(
                    "invalid_response", "Eastmoney klines 类型非法", retryable=False
                )
            if len(klines) >= _EASTMONEY_ROW_LIMIT:
                raise EastmoneyFetchError(
                    "truncated_response",
                    f"Eastmoney 日期块达到 {_EASTMONEY_ROW_LIMIT} 根上限",
                    retryable=False,
                )
            try:
                return _parse_eastmoney_klines(code, freq, klines)
            except ValueError as exc:
                raise EastmoneyFetchError(
                    "invalid_response", str(exc), retryable=False
                ) from exc
        except EastmoneyFetchError as exc:
            last_error = exc
        except httpx.TimeoutException as exc:
            last_error = EastmoneyFetchError("timeout", str(exc), retryable=True)
        except httpx.RequestError as exc:
            last_error = EastmoneyFetchError("network", str(exc), retryable=True)
        logger.warning(
            "Eastmoney 请求失败 code=%s chunk=%s~%s attempt=%d category=%s retryable=%s",
            code,
            start_date,
            end_date,
            attempt + 1,
            last_error.category,
            last_error.retryable,
        )
        if not last_error.retryable or attempt == _EASTMONEY_MAX_ATTEMPTS - 1:
            raise last_error
        delay = last_error.retry_after_seconds or min(4.0, 0.5 * (2**attempt))
        time.sleep(delay)
    raise last_error or EastmoneyFetchError("unknown", "Eastmoney 未知错误", retryable=False)


def _fetch_eastmoney_minute_kbars(
    code: str,
    freq: str,
    start_date: str,
    end_date: str,
    *,
    client: Any | None = None,
) -> list[dict]:
    """串行拉取北交所整股窗口；所有日期块成功后才返回可写入数据。"""
    if freq != "5min":
        raise ValueError("Eastmoney 北交所分流仅支持 5min")
    owns_client = client is None
    http_client = client or httpx.Client(
        headers={"User-Agent": "Mozilla/5.0"},
        follow_redirects=True,
        trust_env=False,
    )
    rows_by_time: dict[str, dict] = {}
    try:
        with _eastmoney_lock:
            for chunk_start, chunk_end in _date_chunks(start_date, end_date):
                chunk_rows = _request_eastmoney_chunk(
                    http_client,
                    code=code,
                    freq=freq,
                    start_date=chunk_start,
                    end_date=chunk_end,
                )
                for row in chunk_rows:
                    rows_by_time[row["trade_time"]] = row
    finally:
        if owns_client:
            http_client.close()
    return [rows_by_time[key] for key in sorted(rows_by_time)]


def _fetch_rows(code: str, freq: str, start_date: str, end_date: str) -> list[dict]:
    if code.startswith("920"):
        return _fetch_eastmoney_minute_kbars(code, freq, start_date, end_date)
    from app.services.tushare_client import _fetch_baostock_minute_kbars

    rows = _fetch_baostock_minute_kbars(code, freq, start_date, end_date)
    for row in rows:
        row["freq"] = freq
    return rows


def _matches_market(code: str, market: str) -> bool:
    if market == "all":
        return True
    if market == "bse":
        return code.startswith("920")
    if market == "shsz":
        return not code.startswith("920")
    raise ValueError(f"unsupported market={market}")


def _active_codes(db, start_date: str, end_date: str) -> list[str]:
    """目标闭区间内有日K的合法 A 股代码。"""
    rows = db.execute(
        select(DailyKbar.code)
        .where(
            DailyKbar.trade_date >= start_date,
            DailyKbar.trade_date <= end_date,
        )
        .distinct()
    ).all()
    return sorted(
        code
        for row in rows
        if row and row[0] and _A_SHARE_CODE_RE.fullmatch(code := str(row[0]))
    )


def _completed_codes_for_date(db, freq: str, trade_date: str) -> set[str]:
    """Return codes whose one trading day passes the strict 5-minute contract."""
    bar_time = func.right(MinuteKbar.trade_time, 8)
    valid_bar = and_(
        func.left(MinuteKbar.trade_time, 10) == MinuteKbar.trade_date,
        bar_time.in_(tuple(sorted(_EXPECTED_5MIN_TIMES))),
        MinuteKbar.open > 0,
        MinuteKbar.high > 0,
        MinuteKbar.low > 0,
        MinuteKbar.close > 0,
        MinuteKbar.high >= MinuteKbar.open,
        MinuteKbar.high >= MinuteKbar.close,
        MinuteKbar.high >= MinuteKbar.low,
        MinuteKbar.low <= MinuteKbar.open,
        MinuteKbar.low <= MinuteKbar.close,
        MinuteKbar.volume >= 0,
        MinuteKbar.amount >= 0,
    )
    rows = db.execute(
        select(MinuteKbar.code)
        .where(
            MinuteKbar.freq == freq,
            MinuteKbar.trade_date == trade_date,
        )
        .group_by(MinuteKbar.code)
        .having(
            func.count(MinuteKbar.id) == _BARS_PER_DAY_5MIN,
            func.count(func.distinct(bar_time)) == _BARS_PER_DAY_5MIN,
            func.sum(case((valid_bar, 1), else_=0)) == _BARS_PER_DAY_5MIN,
        )
        .with_hint(
            MinuteKbar,
            "FORCE INDEX (idx_mk_td_code)",
            dialect_name="mysql",
        )
    ).all()
    return {str(row[0]) for row in rows if row and row[0]}


def _completion_summary(db, freq: str, start_date: str, end_date: str):
    if freq != "5min":
        raise ValueError("严格完整性判断仅支持 5min")

    expected_rows = db.execute(
        select(DailyKbar.code, DailyKbar.trade_date)
        .where(
            DailyKbar.trade_date >= start_date,
            DailyKbar.trade_date <= end_date,
        )
        .order_by(DailyKbar.trade_date, DailyKbar.code)
        .with_hint(
            DailyKbar,
            "FORCE INDEX (ix_daily_kbars_trade_date)",
            dialect_name="mysql",
        )
    ).all()
    expected_by_date: dict[str, set[str]] = {}
    for row in expected_rows:
        if not row or not row[0] or not row[1]:
            continue
        expected_by_date.setdefault(str(row[1]), set()).add(str(row[0]))

    summary: dict[str, list[object]] = {}
    total_dates = len(expected_by_date)
    for index, (trade_date, expected_codes) in enumerate(
        sorted(expected_by_date.items()),
        1,
    ):
        completed_codes = _completed_codes_for_date(db, freq, trade_date)
        for code in expected_codes:
            values = summary.setdefault(code, [0, 0, None, None])
            values[0] = int(values[0]) + 1
            if code in completed_codes:
                values[1] = int(values[1]) + 1
                continue
            if values[2] is None:
                values[2] = trade_date
            values[3] = trade_date
        if index % 20 == 0 or index == total_dates:
            logger.info("resume 严格扫描进度: %d/%d 交易日", index, total_dates)

    return [
        (code, values[0], values[1], values[2], values[3])
        for code, values in sorted(summary.items())
    ]


def _resume_plan(
    db,
    freq: str,
    start_date: str,
    end_date: str,
) -> tuple[set[str], dict[str, tuple[str, str]]]:
    """返回完整股票和每只不完整股票需要重抓的最小闭区间。"""
    done: set[str] = set()
    pending: dict[str, tuple[str, str]] = {}
    for row in _completion_summary(db, freq, start_date, end_date):
        code = str(row[0])
        if not _A_SHARE_CODE_RE.fullmatch(code):
            continue
        expected_days = int(row[1] or 0)
        complete_days = int(row[2] or 0)
        first_gap = str(row[3] or "")
        last_gap = str(row[4] or "")
        if expected_days > 0 and complete_days == expected_days:
            done.add(code)
        elif first_gap and last_gap:
            pending[code] = (first_gap, last_gap)
    return done, pending


def _done_codes(db, freq: str, start_date: str, end_date: str) -> set[str]:
    """仅返回每个预期交易日都有 48 根有效标准网格 K 线的股票。"""
    done, _pending = _resume_plan(db, freq, start_date, end_date)
    return done


def _expected_trade_dates(db, code: str, start_date: str, end_date: str) -> tuple[str, ...]:
    rows = db.execute(
        select(DailyKbar.trade_date)
        .where(
            DailyKbar.code == code,
            DailyKbar.trade_date >= start_date,
            DailyKbar.trade_date <= end_date,
        )
        .order_by(DailyKbar.trade_date)
    ).all()
    return tuple(str(row[0]) for row in rows if row and row[0])


def _validated_rows(
    rows: list[dict],
    *,
    code: str,
    freq: str,
    expected_dates: tuple[str, ...],
) -> list[dict]:
    """验证每个预期交易日的 48 根标准网格，并丢弃非预期交易日。"""
    if freq != "5min":
        raise ValueError("严格数据校验仅支持 5min")
    if not expected_dates:
        raise ValueError(f"code={code} 目标区间没有日K交易日")

    expected = set(expected_dates)
    rows_by_date: dict[str, dict[str, dict]] = {trade_date: {} for trade_date in expected_dates}
    for row in rows:
        row_code = str(row.get("code") or "")
        row_freq = str(row.get("freq") or freq)
        trade_date = str(row.get("trade_date") or "")
        trade_time = str(row.get("trade_time") or "")
        if trade_date not in expected:
            continue
        if row_code != code or row_freq != freq:
            raise ValueError(f"code={code} 返回行身份不一致")
        if len(trade_time) != 19 or trade_time[:10] != trade_date:
            raise ValueError(f"code={code} trade_time 与 trade_date 不一致")
        bar_time = trade_time[11:]
        if bar_time not in _EXPECTED_5MIN_TIMES:
            raise ValueError(f"code={code} 非标准 5min 时间={bar_time}")
        if trade_time in rows_by_date[trade_date]:
            raise ValueError(f"code={code} 重复 trade_time={trade_time}")
        try:
            open_price = float(row["open"])
            high_price = float(row["high"])
            low_price = float(row["low"])
            close_price = float(row["close"])
            volume = float(row["volume"])
            amount = float(row["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"code={code} K线数值字段非法") from exc
        values = (open_price, high_price, low_price, close_price, volume, amount)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"code={code} K线含非有限数值")
        if min(open_price, high_price, low_price, close_price) <= 0:
            raise ValueError(f"code={code} K线价格非正")
        if high_price < max(open_price, close_price, low_price):
            raise ValueError(f"code={code} K线 high 非法")
        if low_price > min(open_price, close_price, high_price):
            raise ValueError(f"code={code} K线 low 非法")
        if volume < 0 or amount < 0:
            raise ValueError(f"code={code} K线成交量额为负")
        normalized = dict(row)
        normalized["freq"] = freq
        rows_by_date[trade_date][trade_time] = normalized

    incomplete = [
        trade_date
        for trade_date, day_rows in rows_by_date.items()
        if len(day_rows) != _BARS_PER_DAY_5MIN
        or {trade_time[11:] for trade_time in day_rows} != _EXPECTED_5MIN_TIMES
    ]
    if incomplete:
        sample = ",".join(incomplete[:5])
        raise ValueError(f"code={code} 5min 数据不完整 days={sample} total={len(incomplete)}")
    return [
        rows_by_date[trade_date][trade_time]
        for trade_date in expected_dates
        for trade_time in sorted(rows_by_date[trade_date])
    ]


def _load_persisted_rows(
    db,
    code: str,
    freq: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    rows = db.execute(
        select(
            MinuteKbar.code,
            MinuteKbar.freq,
            MinuteKbar.trade_date,
            MinuteKbar.trade_time,
            MinuteKbar.open,
            MinuteKbar.high,
            MinuteKbar.low,
            MinuteKbar.close,
            MinuteKbar.volume,
            MinuteKbar.amount,
        ).where(
            MinuteKbar.code == code,
            MinuteKbar.freq == freq,
            MinuteKbar.trade_date >= start_date,
            MinuteKbar.trade_date <= end_date,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def _upsert_rows(db, rows: list[dict]) -> int:
    payload = [
        {
            "code": r["code"],
            "freq": r["freq"],
            "trade_date": r["trade_date"],
            "trade_time": r["trade_time"],
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "volume": r.get("volume"),
            "amount": r.get("amount"),
        }
        for r in rows
        if r.get("trade_date") and r.get("trade_time")
    ]
    if not payload:
        return 0
    stmt = mysql_insert(MinuteKbar).values(payload)
    db.execute(
        stmt.on_duplicate_key_update(
            open=stmt.inserted.open,
            high=stmt.inserted.high,
            low=stmt.inserted.low,
            close=stmt.inserted.close,
            volume=stmt.inserted.volume,
            amount=stmt.inserted.amount,
        )
    )
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="5min 分钟K按股范围回填")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--freq", choices=("5min",), default="5min")
    parser.add_argument(
        "--market",
        choices=("all", "shsz", "bse"),
        default="all",
        help="处理市场：all 全市场，shsz 沪深，bse 北交所 920",
    )
    parser.add_argument("--limit", type=int, default=None, help="调试用：只处理前 N 股")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="仅跳过窗口内每个预期交易日均有 48 根数据的股票",
    )
    args = parser.parse_args()
    start_day = date.fromisoformat(args.start_date)
    end_day = date.fromisoformat(args.end_date)
    if start_day > end_day:
        parser.error("--start-date must be <= --end-date")

    db = SessionLocal()
    try:
        codes = [
            code
            for code in _active_codes(db, args.start_date, args.end_date)
            if _matches_market(code, args.market)
        ]
        pending_ranges: dict[str, tuple[str, str]] = {}
        if args.resume:
            done, pending_ranges = _resume_plan(
                db,
                args.freq,
                args.start_date,
                args.end_date,
            )
            done = {code for code in done if _matches_market(code, args.market)}
            pending_ranges = {
                code: value
                for code, value in pending_ranges.items()
                if _matches_market(code, args.market)
            }
            codes = [code for code in codes if code not in done]
            logger.info(
                "resume: market=%s 跳过完整数据 %d 股，剩余 %d 股",
                args.market,
                len(done),
                len(codes),
            )
        if args.limit:
            codes = codes[: args.limit]
        total = len(codes)
        logger.info(
            "回填开始: freq=%s 窗口=%s~%s 股票=%d", args.freq, args.start_date, args.end_date, total
        )
        t0 = time.time()
        stored_total = 0
        failed: list[str] = []
        for idx, code in enumerate(codes, 1):
            fetch_start, fetch_end = pending_ranges.get(
                code,
                (args.start_date, args.end_date),
            )
            try:
                expected_dates = _expected_trade_dates(db, code, fetch_start, fetch_end)
                fetched_rows = _fetch_rows(code, args.freq, fetch_start, fetch_end)
                rows = _validated_rows(
                    fetched_rows,
                    code=code,
                    freq=args.freq,
                    expected_dates=expected_dates,
                )
                stored = _upsert_rows(db, rows)

                full_expected_dates = _expected_trade_dates(
                    db,
                    code,
                    args.start_date,
                    args.end_date,
                )
                persisted_rows = _load_persisted_rows(
                    db,
                    code,
                    args.freq,
                    args.start_date,
                    args.end_date,
                )
                _validated_rows(
                    persisted_rows,
                    code=code,
                    freq=args.freq,
                    expected_dates=full_expected_dates,
                )
                db.commit()
                stored_total += stored
            except Exception as exc:  # 单股失败不中断整体，但整次命令最终失败
                db.rollback()
                failed.append(code)
                logger.warning(
                    "回填失败 code=%s fetch=%s~%s: %s",
                    code,
                    fetch_start,
                    fetch_end,
                    str(exc)[:160],
                )
            if idx % 100 == 0 or idx == total:
                elapsed = time.time() - t0
                rate = idx / elapsed if elapsed else 0
                eta = (total - idx) / rate if rate else 0
                logger.info(
                    "进度 %d/%d 已存 %d 行 失败 %d 股 用时 %.0fs ETA %.0f 分",
                    idx, total, stored_total, len(failed), elapsed, eta / 60,
                )
        logger.info(
            "回填完成: 股票=%d 共存 %d 行 失败 %d 股 总用时 %.1f 分",
            total, stored_total, len(failed), (time.time() - t0) / 60,
        )
        if failed:
            logger.error("失败股票样例: %s", failed[:20])
            return 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
