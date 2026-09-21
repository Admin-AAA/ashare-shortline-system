"""Xiaoshi A-share minute-history delivery and session-aware aggregation."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import tempfile
import threading
import time as time_module
from collections import OrderedDict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from app.services.xiaoshi_client import XiaoshiClient, XiaoshiError

logger = logging.getLogger(__name__)

_CANONICAL_COLUMNS = (
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "trade_time",
)
_SUPPORTED_FREQS = frozenset({"1min", "5min", "15min", "30min", "60min"})
_FREQ_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}


class XiaoshiMinuteSourceError(RuntimeError):
    """A sanitized minute-history delivery or normalization error."""


_download_locks: dict[str, threading.Lock] = {}
_download_locks_guard = threading.Lock()

_session_cooldown_until: datetime | None = None
_session_cooldown_lock = threading.Lock()
_SESSION_COOLDOWN_CAP_SECONDS = 1800.0
_SESSION_TRANSPORT_FAILURE_COOLDOWN_SECONDS = 90.0
# 签名接口按请求计配额，且触发后会给出很长的等待窗口；这里固定间隔发送，
# 让一个交易日 16 个分桶的签名请求保持在配额之内。
_SESSION_MIN_INTERVAL_SECONDS = 0.6
_session_pace_lock = threading.Lock()
_session_last_request_at: datetime | None = None


def _wait_for_session_slot() -> None:
    global _session_last_request_at
    with _session_pace_lock:
        now = datetime.now()
        if _session_last_request_at is not None:
            elapsed = (now - _session_last_request_at).total_seconds()
            wait = _SESSION_MIN_INTERVAL_SECONDS - elapsed
            if wait > 0:
                time_module.sleep(wait)
                now = datetime.now()
        _session_last_request_at = now


def _session_cooldown_remaining_seconds() -> float:
    with _session_cooldown_lock:
        if _session_cooldown_until is None:
            return 0.0
        remaining = (_session_cooldown_until - datetime.now()).total_seconds()
        return remaining if remaining > 0 else 0.0


def _start_session_cooldown(seconds: float) -> None:
    global _session_cooldown_until
    capped = max(1.0, min(_SESSION_COOLDOWN_CAP_SECONDS, float(seconds)))
    with _session_cooldown_lock:
        _session_cooldown_until = datetime.now() + timedelta(seconds=capped)


def session_cooldown_seconds() -> float:
    """签名接口剩余冷却时间；大于 0 表示供应商正在限流。"""
    return _session_cooldown_remaining_seconds()


def clear_session_cooldown() -> None:
    """清除签名接口冷却状态；数据源配置变更或测试隔离时使用。"""
    global _session_cooldown_until
    with _session_cooldown_lock:
        _session_cooldown_until = None


_published_state: tuple[datetime, str | None] | None = None
_published_lock = threading.Lock()
_PUBLISHED_TTL_SECONDS = 600.0


def published_minute_last_date() -> str | None:
    """小石已发布的分钟历史最新日期；用于区分“供应商未发布”与“本系统漏拉”。"""
    global _published_state
    now = datetime.now()
    with _published_lock:
        cached = _published_state
        if cached is not None and now < cached[0] + timedelta(seconds=_PUBLISHED_TTL_SECONDS):
            return cached[1]
    from app.services.xiaoshi_client import get_xiaoshi_client

    try:
        manifest = get_xiaoshi_client().get_history_manifest()
        published = _normalize_date(
            ((manifest.get("coverage") or {}).get("cn-minute") or {}).get("last")
        ) or None
    except Exception:
        published = None
    with _published_lock:
        _published_state = (now, published)
    return published


_BUCKET_ALPHABET = "0123456789abcdef"


def minute_bucket(symbol: Any) -> str:
    """小石按月分片的代码分桶：sha1(股票代码) 的首位十六进制字符。"""
    code = _normalize_code(symbol)
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[0]


def pick_bucket_probes(buckets: set[str], universe: list[str]) -> dict[str, str]:
    """为每个分桶挑一只代表股票，用于申请该分桶的整月分片签名。

    分桶由股票代码本身决定，因此不需要逐只申请签名就能算出目标股票落在哪些分桶；
    每个分桶只申请一次签名，取回的分片文件覆盖该分桶整月全部股票。
    """
    wanted = {bucket for bucket in buckets if bucket in _BUCKET_ALPHABET}
    probes: dict[str, str] = {}
    for symbol in universe:
        code = _normalize_code(symbol)
        if not code.isdigit() or len(code) != 6:
            continue
        bucket = minute_bucket(code)
        if bucket in wanted and bucket not in probes:
            probes[bucket] = code
            if len(probes) == len(wanted):
                break
    return probes


def _download_lock(path: Path) -> threading.Lock:
    """同一分区文件可能被多个股票同时请求，只允许一次真实下载。"""
    key = str(path)
    with _download_locks_guard:
        lock = _download_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _download_locks[key] = lock
        return lock


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_key(file_info: Mapping[str, Any]) -> str:
    key = str(file_info.get("key") or "").strip().lstrip("/")
    if not key or ".." in key.split("/"):
        raise XiaoshiMinuteSourceError("小石分钟文件缺少有效对象键")
    return key


def _file_sha(file_info: Mapping[str, Any]) -> str:
    return str(file_info.get("sha256") or "").strip().lower()


def _cache_path(cache_dir: Path, file_info: Mapping[str, Any]) -> Path:
    """缓存路径直接复用对象键；同一月份分片被多只股票共享，只需下载一次。"""
    return cache_dir / _file_key(file_info)


def _covered_range(file_info: Mapping[str, Any]) -> tuple[str, str]:
    first = _normalize_date(file_info.get("first_date"))
    last = _normalize_date(file_info.get("last_date"))
    return first, last


def _covers_requested_range(file_info: Mapping[str, Any], start_date: str, end_date: str) -> bool:
    """分区文件的覆盖区间与请求区间无交集时跳过，避免下载用不到的历史基表。"""
    first, last = _covered_range(file_info)
    if not first and not last:
        return True
    if last and last < start_date:
        return False
    if first and first > end_date:
        return False
    return True


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


def _read_canonical_frame(path: Path) -> pd.DataFrame:
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
        "code": "symbol",
        "ts_code": "symbol",
        "datetime": "trade_time",
        "vol": "volume",
        "date": "trade_date",
    }
    frame = frame.rename(
        columns={
            source: target
            for source, target in aliases.items()
            if target not in frame.columns and source in frame.columns
        }
    )
    missing = [column for column in _CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise XiaoshiMinuteSourceError(f"小石分钟 Parquet 缺少字段: {', '.join(missing)}")
    frame = frame.loc[:, list(_CANONICAL_COLUMNS)].copy()
    frame["symbol"] = frame["symbol"].map(_normalize_code)
    frame["_timestamp"] = pd.to_datetime(frame["trade_time"], errors="coerce")
    frame = frame.loc[frame["_timestamp"].notna()].copy()
    return frame


def _frame_for_file(path: Path, code: str) -> pd.DataFrame:
    frame = _read_canonical_frame(path)
    if frame.empty:
        return frame
    return frame.loc[frame["symbol"] == code].copy()


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
                "volume": _safe_float(raw.get("volume")),
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
        start, end = _date_range(start_date, end_date)
        return self._fetch_from_files(
            normalized_code,
            self._request_session(normalized_code, start, end),
            start,
            end,
            freq,
        )

    def fetch_many(
        self,
        codes: list[str],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        freq: str = "1min",
        universe: list[str] | None = None,
        max_workers: int = 4,
    ) -> dict[str, list[dict[str, Any]]]:
        rows_by_code, _unresolved = self.fetch_many_detailed(
            codes,
            start_date=start_date,
            end_date=end_date,
            freq=freq,
            universe=universe,
            max_workers=max_workers,
        )
        return rows_by_code

    def fetch_many_detailed(
        self,
        codes: list[str],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        freq: str = "1min",
        universe: list[str] | None = None,
        max_workers: int = 4,
    ) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
        """批量取分钟历史：按月分片批量申请，每个分片只下载解析一次。

        小石的签名接口只接受单只股票并且对逐只枚举有严格限流，因此这里先按目标股票
        算出需要的代码分桶，每个分桶用一只代表股票申请一次签名，再读回该分桶整月的
        共享分片。分桶数量上限为 16，请求数量与目标股票数量无关。

        返回 (按股票分组的行, 没有拿到分片而无法判定有无数据的股票集合)。第二项的股票
        才需要调用方走其它供应商；分片已经覆盖但当天没有记录的股票属于供应商当天没有
        这笔数据，不必也不应逐只重试。
        """
        normalized_freq = str(freq or "1min").strip().lower()
        if normalized_freq not in _SUPPORTED_FREQS:
            raise ValueError(f"小石分钟频率不支持: {normalized_freq}")
        normalized_codes: list[str] = []
        for code in codes:
            normalized = _normalize_code(code)
            if normalized.isdigit() and len(normalized) == 6 and normalized not in normalized_codes:
                normalized_codes.append(normalized)
        if not normalized_codes:
            return {}, set()
        start, end = _date_range(start_date, end_date)
        buckets = {minute_bucket(code) for code in normalized_codes}
        probe_universe = list(OrderedDict.fromkeys([*normalized_codes, *(universe or [])]))
        probes = pick_bucket_probes(buckets, probe_universe)
        result: dict[str, list[dict[str, Any]]] = {code: [] for code in normalized_codes}
        covered_buckets: set[str] = set()
        if probes:
            sessions = self._request_sessions(probes, start, end, max_workers=max_workers)
            index_by_code = {code: minute_bucket(code) for code in normalized_codes}
            frames_by_code: dict[str, list[tuple[int, pd.DataFrame]]] = {
                code: [] for code in normalized_codes
            }
            for probe_bucket, files in sessions.items():
                selected = [info for info in files if _covers_requested_range(info, start, end)]
                if not selected:
                    continue
                for position, info in enumerate(selected):
                    # 分片自带 bucket 字段；分桶以文件为准，避免代表股票与文件不一致时错配。
                    bucket = str(info.get("bucket") or probe_bucket)
                    covered_buckets.add(bucket)
                    codes_in_bucket = [
                        code for code in normalized_codes if index_by_code[code] == bucket
                    ]
                    if not codes_in_bucket:
                        continue
                    path = self._ensure_file(info)
                    frame = _read_canonical_frame(path)
                    if frame.empty:
                        continue
                    subset = frame.loc[frame["symbol"].isin(codes_in_bucket)]
                    for code, code_frame in subset.groupby("symbol", sort=False):
                        frames_by_code.setdefault(str(code), []).append((position, code_frame))
            for code, ordered_frames in frames_by_code.items():
                if not ordered_frames:
                    continue
                frames = [
                    frame for _, frame in sorted(ordered_frames, key=lambda item: item[0])
                ]
                rows = _normalize_rows(frames, code, start, end)
                result[code] = resample_minute_rows(rows, normalized_freq)

        uncovered = [code for code in normalized_codes if minute_bucket(code) not in covered_buckets]
        for code in uncovered:
            result[code] = []
        return result, set(uncovered)

    def _request_sessions(
        self,
        probes: Mapping[str, str],
        start_date: str,
        end_date: str,
        *,
        max_workers: int,
    ) -> dict[str, list[Mapping[str, Any]]]:
        sessions: dict[str, list[Mapping[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(probes)))) as pool:
            futures = {
                pool.submit(self._request_session, symbol, start_date, end_date): bucket
                for bucket, symbol in probes.items()
            }
            for future in as_completed(futures):
                bucket = futures[future]
                try:
                    sessions[bucket] = future.result()
                except Exception:
                    logger.warning("小石分钟分桶签名失败 bucket=%s", bucket)
        return sessions

    def _fetch_from_files(
        self,
        code: str,
        files: list[Mapping[str, Any]],
        start_date: str,
        end_date: str,
        freq: str,
    ) -> list[dict[str, Any]]:
        frames: list[pd.DataFrame] = []
        for info in files:
            if not _covers_requested_range(info, start_date, end_date):
                continue
            frame = _frame_for_file(self._ensure_file(info), code)
            if not frame.empty:
                frames.append(frame)
        rows = _normalize_rows(frames, code, start_date, end_date)
        normalized_freq = str(freq or "1min").strip().lower()
        if normalized_freq not in _SUPPORTED_FREQS:
            raise ValueError(f"小石分钟频率不支持: {normalized_freq}")
        return resample_minute_rows(rows, normalized_freq)

    def _request_session(
        self, code: str, start_date: str, end_date: str
    ) -> list[Mapping[str, Any]]:
        """取回单只股票的逻辑数据集分片；返回顺序就是平台规定的合并顺序。"""
        remaining = _session_cooldown_remaining_seconds()
        if remaining > 0:
            raise XiaoshiMinuteSourceError(
                f"小石签名接口限流中，约 {int(remaining)} 秒后恢复"
            )
        _wait_for_session_slot()
        try:
            payload = self.client.get_history_download_session(
                dataset="min1",
                code=code,
                since=start_date,
                to=end_date,
            )
        except XiaoshiError as exc:
            if exc.status_code == 429:
                # 服务端给出的等待时间就是配额窗口；继续逐只试探只会被持续拒绝。
                _start_session_cooldown(exc.retry_after_seconds or 60.0)
            elif exc.status_code is None or exc.status_code >= 500:
                # 连接被拒、TLS 失败或服务端异常时整站都不可用，短时间内不再逐个试探。
                _start_session_cooldown(_SESSION_TRANSPORT_FAILURE_COOLDOWN_SECONDS)
            raise
        files = [item for item in payload.get("files") or [] if isinstance(item, Mapping)]
        if not files:
            raise XiaoshiMinuteSourceError("小石分钟历史没有返回文件分区")
        return files

    def _ensure_file(self, file_info: Mapping[str, Any]) -> Path:
        path = _cache_path(self.cache_dir, file_info)
        if _valid_cache(path, file_info):
            return path
        url = str(file_info.get("url") or "").strip()
        if not url:
            raise XiaoshiMinuteSourceError("小石分钟文件缺少下载地址")
        with _download_lock(path):
            if _valid_cache(path, file_info):
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path: Path | None = None
            try:
                with self._r2_client.stream(
                    "GET", url, headers={"Accept": "application/octet-stream"}
                ) as response:
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
