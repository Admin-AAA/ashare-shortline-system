"""fina_indicator_vip（period 批量，仅 quantdata）的备用源（owner 2026-10-19）。

quantdata 日限耗尽 / 不可用时，按以下方式拼出与 period 批量同形的全市场 DataFrame：
1. 基线：本地逐股历史 data/midterm/fina/<code>.parquet 中 end_date == period 的行（midterm 研究补数维护）；
2. 增量：近 LOOKBACK_DAYS 天 news_announcement 里有定期报告（年报/半年报/季报）公告、且基线缺该期的股票，
   逐股走 promax fina_indicator(ts_code, period)，经数据网关 gw_call("fina_indicator", "promax")，
   单次最多 MAX_DELTA_CODES 只（只补当天新公告，量很小，不做批量）。
东财财务接口不在此接入：其开关当前关闭（IP 被封），尊重该开关。
结果按 (period, 日期) 进程内缓存，同日重复调用不再打上游。触发即发 [ALERT:fina_vip_fallback]。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.alerts import emit_alert

logger = logging.getLogger(__name__)

FINA_DIR = Path(__file__).resolve().parents[3] / "data" / "midterm" / "fina"
LOOKBACK_DAYS = 4
MAX_DELTA_CODES = 300
PERIODIC_TITLE_RE = r"年度报告|半年度报告|季度报告"
_cache: dict[tuple[str, str], pd.DataFrame] = {}


def _read_one(path: Path, period: str) -> pd.DataFrame | None:
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if "end_date" not in frame.columns:
        return None
    frame = frame[frame["end_date"].astype(str) == period]
    return frame if not frame.empty else None


def load_baseline(period: str, fina_dir: Path = FINA_DIR) -> pd.DataFrame:
    files = sorted(fina_dir.glob("*.parquet")) if fina_dir.exists() else []
    if not files:
        return pd.DataFrame()
    with ThreadPoolExecutor(8) as pool:
        from app.services.datasource.cache import submit_ctx

        futs = [submit_ctx(pool, _read_one, f, period) for f in files]
        parts = [p for p in (fu.result() for fu in futs) if p is not None]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def recent_periodic_announcers(today: date, lookback_days: int = LOOKBACK_DAYS) -> list[str]:
    """近 lookback_days 天发了定期报告的股票。

    主源是公告事件快照（盘后日更，全市场按日）；MySQL news_announcement 只由逐股补数写入、
    10-16 后不再日更，单用它会在财报季漏掉几乎全部新公告（10-20 只识别出 1 只），只作补充。
    """
    from sqlalchemy import text

    from app.db.session import SessionLocal

    since = (today - timedelta(days=lookback_days)).isoformat()
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT DISTINCT code FROM news_announcement WHERE announce_date >= :s AND announce_date <= :t "
            "AND title REGEXP :re AND title NOT LIKE '%摘要%'"), {"s": since, "t": today.isoformat(), "re": PERIODIC_TITLE_RE}
        ).all()
        codes = {str(r[0]) for r in rows}
        try:
            codes |= _snapshot_periodic_announcers(db.connection(), today, lookback_days)
        except Exception as exc:  # noqa: BLE001 - 快照读失败仍保留 MySQL 结果
            logger.warning("fina fallback: announcement snapshot lookup failed: %s", exc)
    return sorted(c for c in codes if c and len(c) == 6)


def _snapshot_periodic_announcers(connection, today: date, lookback_days: int) -> set[str]:
    import re

    from app.services.auxiliary.composition import EVENT_SNAPSHOT_CONTRACTS
    from app.services.auxiliary.event_snapshot import ActiveEventSnapshotReader, EventSnapshotRequest
    from app.services.auxiliary.snapshot_codec import decode_canonical_payload
    from app.services.auxiliary.snapshot_repository import SQLAlchemyImmutablePartitionSnapshotRepository

    reader = ActiveEventSnapshotReader(
        EVENT_SNAPSHOT_CONTRACTS["news_announcement"], SQLAlchemyImmutablePartitionSnapshotRepository(connection)
    )
    pattern = re.compile(PERIODIC_TITLE_RE)
    codes: set[str] = set()
    for offset in range(lookback_days + 1):
        day = (today - timedelta(days=offset)).strftime("%Y%m%d")
        snapshot = reader.load(EventSnapshotRequest.from_mapping({"ann_date": day}))
        for row in snapshot.rows if snapshot is not None else ():
            payload = decode_canonical_payload(row.lossless_payload)
            title = str(payload.get("title") or "")
            if pattern.search(title) and "摘要" not in title:
                codes.add(str(payload.get("ts_code") or "").split(".")[0])
    return codes


def _ts_code(code: str) -> str:
    return f"{code}.SH" if code[0] in "69" else (f"{code}.BJ" if code[0] in "48" else f"{code}.SZ")


def fetch_delta(pro: Any, codes: list[str], period: str) -> pd.DataFrame:
    from app.services.datasource.wrap import gw_call

    frames = []
    for code in codes[:MAX_DELTA_CODES]:
        try:
            frame = gw_call("fina_indicator", "promax", pro.fina_indicator, ts_code=_ts_code(code), period=period)
        except Exception as exc:  # noqa: BLE001 - 单只失败不影响其余
            logger.warning("fina fallback %s %s failed: %s", code, period, exc)
            continue
        if frame is not None and not frame.empty:
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fina_period_fallback(
    pro: Any,
    period: str,
    fields: str = "",
    *,
    today: date | None = None,
    baseline_loader=load_baseline,
    announcer_loader=recent_periodic_announcers,
) -> pd.DataFrame:
    today = today or date.today()
    key = (period, today.isoformat())
    if key not in _cache:
        base = baseline_loader(period)
        have = set(base["ts_code"].astype(str).str[:6]) if "ts_code" in base.columns else set()
        try:
            announcers = announcer_loader(today)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fina fallback: announcer lookup failed: %s", exc)
            announcers = []
        missing = [c for c in announcers if c not in have]
        delta = fetch_delta(pro, missing, period) if missing else pd.DataFrame()
        merged = pd.concat([f for f in (base, delta) if not f.empty], ignore_index=True) if (not base.empty or not delta.empty) else pd.DataFrame()
        if not merged.empty and "ts_code" in merged.columns:
            sort_cols = [c for c in ("ts_code", "ann_date") if c in merged.columns]
            merged = merged.sort_values(sort_cols).drop_duplicates(["ts_code"], keep="last")
        _cache[key] = merged
        emit_alert(
            "fina_vip_fallback", period,
            f"fina_indicator_vip 不可用，period={period} 走备用源：本地基线 {len(base)} 行 + promax 逐股 "
            f"{len(delta)} 行（新公告 {len(announcers)} 只，需补 {len(missing)} 只，上限 {MAX_DELTA_CODES}）",
        )
    out = _cache[key]
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    if wanted and not out.empty:
        for col in wanted:
            if col not in out.columns:
                out = out.assign(**{col: None})
        out = out[wanted]
    return out.copy()
