"""Xiaoshi A-share minute-history delivery and session-aware aggregation."""
from __future__ import annotations

import hashlib
import math
import os
import tempfile
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import tenacity
import pandas as pd

from app.services.xiaoshi_client import XiaoshiClient

_CANONICAL_COLUMNS = (
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "adj_factor",
    "trade_date",
    "trade_time",
)
_SUPPORTED_FREQS = frozenset({"1min", "5min", "15min", "30min", "60min"})
_FREQ_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}


class XiaoshiMinuteSourceError(RuntimeError):
    """A sanitized minute-history delivery or normalization error."""


def _normalize_code(value: Any) -> str:
    return str(value or "").strip().upper().split(".", 1)[0]


def _normalize_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw[:10]


def _date_range(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    start = _normalize_date(start_date)
    end = _normalize_date(end_date)
    if not start and not end:
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=10)).isoformat()
    elif not start:
        start = end
    elif not end:
        end = start
    if start > end:
        raise XiaoshiMinuteSourceError("分钟数据起始日期不能晚于结束日期")
    return start, end


def _year_range(start_date: str, end_date: str) -> range:
    return range(int(start_date[:4]), int(end_date[:4]) + 1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_part(file_info: Mapping[str, Any]) -> str:
    part = str(file_info.get("part") or "").strip().lower()
    if part not in {"base", "update"}:
        raise XiaoshiMinuteSourceError("小石分钟文件缺少有效分区类型")
    return part


def _cache_path(cache_dir: Path, code: str, file_info: Mapping[str, Any]) -> Path:
    part = _file_part(file_info)
    year = str(file_info.get("year") or "base")
    return cache_dir / code / year / f"{part}.parquet"


def _valid_cache(path: Path, file_info: Mapping[str, Any]) -> bool:
    if not path.is_file():
        return False
    try:
        expected_size = int(file_info.get("size") or 0)
    except (TypeError, ValueError):
        return False
    if expected_size <= 0 or path.stat().st_size != expected_size:
        return False
    expected_sha = str(file_info.get("sha256") or "").strip().lower()
    return bool(expected_sha) and _sha256(path) == expected_sha


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _frame_for_file(path: Path, code: str) -> pd.DataFrame:
    try:
        frame = pd.read_parquet(path, engine="pyarrow")
    except ImportError as exc:
        raise XiaoshiMinuteSourceError("读取小石 Parquet 需要安装 pyarrow") from exc
    except Exception as exc:
        raise XiaoshiMinuteSourceError("小石分钟 Parquet 读取失败") from exc
    if frame.empty:
        return frame
    if any(name in {"trade_date", "trade_time"} for name in frame.index.names):
        frame = frame.reset_index()
    aliases = {
        "code": "ts_code",
        "datetime": "trade_time",
        "date": "trade_date",
        "volume": "vol",
    }
    frame = frame.rename(
        columns={
            source: target
            for source, target in aliases.items()
            if target not in frame.columns and source in frame.columns
        }
    )
    if "adj_factor" not in frame.columns:
        frame["adj_factor"] = 1.0
    missing = [column for column in _CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise XiaoshiMinuteSourceError(f"小石分钟 Parquet 缺少字段: {', '.join(missing)}")
    frame = frame.loc[:, list(_CANONICAL_COLUMNS)].copy()
    frame = frame.loc[frame["ts_code"].map(_normalize_code) == code].copy()
    if frame.empty:
        return frame
    frame["_timestamp"] = pd.to_datetime(frame["trade_time"], errors="coerce")
    frame = frame.loc[frame["_timestamp"].notna()].copy()
    return frame


def _normalize_rows(
    frames: list[pd.DataFrame],
    code: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not frames:
        return []
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.sort_values("_timestamp", kind="stable")
    frame = frame.drop_duplicates(subset=["_timestamp"], keep="last")
    frame = frame.loc[
        (frame["_timestamp"].dt.strftime("%Y-%m-%d") >= start_date)
        & (frame["_timestamp"].dt.strftime("%Y-%m-%d") <= end_date)
    ]
    rows: list[dict[str, Any]] = []
    for _, raw in frame.iterrows():
        timestamp = raw["_timestamp"]
        open_price = _safe_float(raw.get("open"))
        high_price = _safe_float(raw.get("high"))
        low_price = _safe_float(raw.get("low"))
        close_price = _safe_float(raw.get("close"))
        if close_price <= 0:
            continue
        if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
            raise XiaoshiMinuteSourceError("小石分钟数据存在无效 OHLC 关系")
        trade_time = timestamp.to_pydatetime().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        rows.append(
            {
                "code": code,
                "trade_date": trade_time[:10],
                "trade_time": trade_time,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": _safe_float(raw.get("vol")),
                "amount": _safe_float(raw.get("amount")),
                "source": "xiaoshi_min1",
            }
        )
    return rows


def resample_minute_rows(rows: list[dict[str, Any]], freq: str) -> list[dict[str, Any]]:
    """Aggregate 1-minute rows without crossing the A-share lunch break."""
    normalized_freq = str(freq or "1min").strip().lower()
    if normalized_freq == "1min":
        return rows
    if normalized_freq not in _SUPPORTED_FREQS:
        raise ValueError(f"小石分钟聚合仅支持: {', '.join(sorted(_SUPPORTED_FREQS))}")
    if not rows:
        return []

    frame = pd.DataFrame(rows)
    frame["_timestamp"] = pd.to_datetime(frame["trade_time"], errors="coerce")
    frame = frame.loc[frame["_timestamp"].notna()].copy()
    if frame.empty:
        return []
    minutes = _FREQ_MINUTES[normalized_freq]
    result: list[dict[str, Any]] = []
    for trade_date, day_frame in frame.groupby(frame["_timestamp"].dt.strftime("%Y-%m-%d"), sort=True):
        for session_start, session_end in ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))):
            session = day_frame.loc[
                (day_frame["_timestamp"].dt.time >= session_start)
                & (day_frame["_timestamp"].dt.time <= session_end)
            ].copy()
            if session.empty:
                continue
            session = session.set_index("_timestamp").sort_index()
            origin = pd.Timestamp(datetime.combine(date.fromisoformat(trade_date), session_start))
            aggregated = session.resample(
                f"{minutes}min",
                origin=origin,
                label="right",
                closed="right",
            ).agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                    "amount": "sum",
                    "code": "first",
                }
            )
            aggregated = aggregated.loc[aggregated["close"].notna()]
            for timestamp, raw in aggregated.iterrows():
                timestamp_value = timestamp.to_pydatetime().replace(tzinfo=None)
                result.append(
                    {
                        "code": str(raw.get("code") or rows[0].get("code") or ""),
                        "trade_date": trade_date,
                        "trade_time": timestamp_value.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": _safe_float(raw.get("open")),
                        "high": _safe_float(raw.get("high")),
                        "low": _safe_float(raw.get("low")),
                        "close": _safe_float(raw.get("close")),
                        "volume": _safe_float(raw.get("volume")),
                        "amount": _safe_float(raw.get("amount")),
                        "source": "xiaoshi_min1_resampled",
                    }
                )
    return sorted(result, key=lambda item: str(item["trade_time"]))


class XiaoshiMinuteHistorySource:
    """Download and normalize Xiaoshi's authenticated min1 history contract."""

    def __init__(
        self,
        *,
        client: XiaoshiClient,
        cache_dir: str | Path,
        r2_client: httpx.Client | None = None,
    ) -> None:
        self.client = client
        self.cache_dir = Path(cache_dir)
        self._r2_client = r2_client or httpx.Client(follow_redirects=True)
        self._owns_r2_client = r2_client is None

    def close(self) -> None:
        if self._owns_r2_client:
            self._r2_client.close()

    def __enter__(self) -> "XiaoshiMinuteHistorySource":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def fetch(
        self,
        code: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        freq: str = "1min",
    ) -> list[dict[str, Any]]:
        normalized_code = _normalize_code(code)
        if not normalized_code.isdigit() or len(normalized_code) != 6:
            raise ValueError("股票代码必须是 6 位数字")
        normalized_freq = str(freq or "1min").strip().lower()
        if normalized_freq not in _SUPPORTED_FREQS:
            raise ValueError(f"小石分钟频率不支持: {normalized_freq}")
        start, end = _date_range(start_date, end_date)
        frames: list[pd.DataFrame] = []
        for year in _year_range(start, end):
            paths = self._ensure_year_files(normalized_code, year)
            for path in paths:
                frame = _frame_for_file(path, normalized_code)
                if not frame.empty:
                    frames.append(frame)
        rows = _normalize_rows(frames, normalized_code, start, end)
        return resample_minute_rows(rows, normalized_freq)

    def _ensure_year_files(self, code: str, year: int) -> list[Path]:
        def _once() -> list[Path]:
            payload = self.client.get_history_download_session(
                dataset="min1",
                code=code,
                year=str(year),
            )
            files = [item for item in payload.get("files") or [] if isinstance(item, Mapping)]
            if not files:
                raise XiaoshiMinuteSourceError("小石分钟历史没有返回文件分区")
            files.sort(key=lambda item: 0 if _file_part(item) == "base" else 1)
            return [self._ensure_file(code, file_info) for file_info in files]

        try:
            return tenacity.Retrying(
                stop=tenacity.stop_after_attempt(2),
                retry=tenacity.retry_if_exception_type(Exception),
                wait=tenacity.wait_none(),
                reraise=True,
            )(_once)
        except XiaoshiMinuteSourceError:
            raise
        except Exception as exc:
            raise XiaoshiMinuteSourceError("小石分钟历史下载失败") from exc

    def _ensure_file(self, code: str, file_info: Mapping[str, Any]) -> Path:
        path = _cache_path(self.cache_dir, code, file_info)
        if _valid_cache(path, file_info):
            return path
        url = str(file_info.get("url") or "").strip()
        if not url:
            raise XiaoshiMinuteSourceError("小石分钟文件缺少下载地址")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with self._r2_client.stream("GET", url, headers={"Accept": "application/octet-stream"}) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{path.name}.",
                    suffix=".part",
                    dir=path.parent,
                    delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                    digest = hashlib.sha256()
                    size = 0
                    for chunk in response.iter_bytes(1024 * 1024):
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            expected_size = int(file_info.get("size") or 0)
            expected_sha = str(file_info.get("sha256") or "").strip().lower()
            if size != expected_size or digest.hexdigest() != expected_sha:
                raise XiaoshiMinuteSourceError("小石分钟文件校验失败")
            os.replace(temp_path, path)
            temp_path = None
            return path
        except httpx.HTTPError as exc:
            raise XiaoshiMinuteSourceError("小石分钟文件下载失败") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def default_xiaoshi_minute_cache_dir(settings: Any) -> Path:
    configured = str(getattr(settings, "xiaoshi_minute_cache_dir", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(getattr(settings, "backend_root")) / "data" / "xiaoshi_min1"
