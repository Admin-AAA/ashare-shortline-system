"""margin_detail 截断自动补缺（东财 datacenter，经 gw_call 网关）。

背景（2026-10-19）：promax 的 margin_detail 常态只回 2001 行（分页上限，只有沪市），
10-16 手工用东财只插缺失代码从 4111 行补到 4459 行。本模块把这一步固化为盘后例行：

* 截断判定（不写死 2001）：
  1) 主源行数 >= ``min_rows``（默认 2000）且等于某个分页上限或上限+1
     （默认 2000/3000/4000/5000/6000/8000/10000，``ASHARE_MARGIN_PAGE_CAPS`` 可配置）；或
  2) 覆盖率比前 5 个交易日（margin_detail 内已有日期）行数中位数低 5% 以上
     （``ASHARE_MARGIN_COVERAGE_DROP``，默认 0.05）。
* 只补缺失代码：事先排除已有 code + ``INSERT IGNORE``（sqlite: ``OR IGNORE``），绝不 UPDATE 已有行。
* 与 ``eastmoney.alt`` 总开关无关：总开关保持关闭，本路径单独由 ``ASHARE_MARGIN_FILL_ENABLED``
  控制（默认开）。上游请求仍经 ``gw_call("margin.detail", "eastmoney", ...)``，共享东财限速/熔断。
* 交易时段（工作日 09:15–15:05 Asia/Shanghai）不跑批量补缺：只记日志并把日期写进待补清单
  ``data/runtime/margin_fill_pending.json``，盘后下一次触发时一并处理。
* 来源标记：margin_detail 无来源列，不改表结构。每次触发
  - 记一行 WARNING 日志 ``margin_detail_fill td=… promax_rows=… eastmoney_inserted=… coverage_after=…``；
  - 发生补缺/延后/失败时向 ``data/runtime/margin_fill_provenance.jsonl`` 追加一行 JSON，含本次插入的
    全部 code（source=``eastmoney.margin_detail_fill``），可据此精确识别/回滚东财补入的行。
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import threading
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select

from app.models.tables import MarginDetail

logger = logging.getLogger(__name__)

SOURCE = "eastmoney.margin_detail_fill"
DEFAULT_PAGE_CAPS: tuple[int, ...] = (2000, 3000, 4000, 5000, 6000, 8000, 10000)
DEFAULT_MIN_ROWS = 2000
DEFAULT_COVERAGE_DROP = 0.05
BASELINE_DAYS = 5
_VALUE_FIELDS = ("rzye", "rqye", "rzmre", "rzche", "rzrqye", "rqyl")
_FILE_LOCK = threading.Lock()


# ── 配置 ─────────────────────────────────────────────────────────────────
def fill_enabled() -> bool:
    return os.environ.get("ASHARE_MARGIN_FILL_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def page_caps() -> tuple[int, ...]:
    raw = os.environ.get("ASHARE_MARGIN_PAGE_CAPS", "").strip()
    caps: list[int] = []
    for part in raw.split(","):
        try:
            caps.append(int(part.strip()))
        except ValueError:
            continue
    return tuple(sorted(set(caps))) or DEFAULT_PAGE_CAPS


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def runtime_dir() -> Path:
    override = os.environ.get("ASHARE_MARGIN_FILL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "runtime"


def in_trading_session(now: datetime | None = None) -> bool:
    """工作日 09:15–15:05（Asia/Shanghai），与 promax 盘中额度保留窗口同一口径。"""
    from app.services.tushare_transport import _in_research_window

    return _in_research_window(now)


def _iso(td: Any) -> str:
    td = str(td).replace("-", "")[:8]
    return f"{td[:4]}-{td[4:6]}-{td[6:8]}"


# ── 截断判定（纯函数） ────────────────────────────────────────────────────
def is_page_cap(rows: int, caps: Iterable[int] | None = None, min_rows: int | None = None) -> bool:
    """行数 >= min_rows 且恰为分页上限（或上限+1，如 promax 的 2001）。"""
    caps = tuple(caps) if caps is not None else page_caps()
    floor = DEFAULT_MIN_ROWS if min_rows is None else min_rows
    if rows < floor:
        return False
    return rows in caps or (rows - 1) in caps


def detect_truncation(
    rows: int,
    baseline_counts: Iterable[int],
    *,
    caps: Iterable[int] | None = None,
    min_rows: int | None = None,
    drop: float | None = None,
) -> dict[str, Any]:
    """返回 {truncated, reasons, baseline_median, coverage}；coverage = rows / 前 5 日行数中位数。"""
    base = [int(c) for c in baseline_counts if c]
    median = float(statistics.median(base)) if base else None
    drop = _float_env("ASHARE_MARGIN_COVERAGE_DROP", DEFAULT_COVERAGE_DROP) if drop is None else drop
    reasons: list[str] = []
    if is_page_cap(rows, caps, min_rows):
        reasons.append("page_cap")
    coverage = round(rows / median, 4) if median else None
    if median and rows < median * (1.0 - drop):
        reasons.append("coverage_drop")
    return {"truncated": bool(reasons), "reasons": reasons, "baseline_median": median, "coverage": coverage}


# ── DB 辅助 ──────────────────────────────────────────────────────────────
def _count(db, iso_day: str) -> int:
    return int(db.execute(
        select(func.count()).select_from(MarginDetail).where(MarginDetail.trade_date == iso_day)
    ).scalar() or 0)


def _baseline_counts(db, iso_day: str, days: int = BASELINE_DAYS) -> list[int]:
    rows = db.execute(
        select(MarginDetail.trade_date, func.count())
        .where(MarginDetail.trade_date < iso_day)
        .group_by(MarginDetail.trade_date)
        .order_by(MarginDetail.trade_date.desc())
        .limit(days)
    ).all()
    return [int(n) for _, n in rows]


def _existing_codes(db, iso_day: str) -> set[str]:
    return {c for (c,) in db.execute(select(MarginDetail.code).where(MarginDetail.trade_date == iso_day))}


def _insert_missing(db, rows: list[dict[str, Any]]) -> int:
    """INSERT IGNORE：唯一键 (code, trade_date) 冲突时保留已有行，绝不覆盖。"""
    if not rows:
        return 0
    days = sorted({r["trade_date"] for r in rows})
    before = sum(_count(db, d) for d in days)
    stmt = insert(MarginDetail).prefix_with("IGNORE", dialect="mysql").prefix_with("OR IGNORE", dialect="sqlite")
    db.execute(stmt, rows)
    db.commit()
    # executemany 的 rowcount 各驱动口径不一，按插入前后行数差计实际新增
    return sum(_count(db, d) for d in days) - before


# ── 待补清单 / 来源记录 ───────────────────────────────────────────────────
def _pending_path() -> Path:
    return runtime_dir() / "margin_fill_pending.json"


def load_pending() -> list[str]:
    try:
        data = json.loads(_pending_path().read_text(encoding="utf-8"))
        return sorted({_iso(d) for d in data.get("dates", [])})
    except (FileNotFoundError, ValueError, AttributeError, OSError):
        return []


def _save_pending(dates: Iterable[str]) -> None:
    try:
        path = _pending_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"dates": sorted(set(dates))}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("margin_detail_fill pending write failed", exc_info=True)


def _append_provenance(record: dict[str, Any]) -> None:
    try:
        path = runtime_dir() / "margin_fill_provenance.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _FILE_LOCK, path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("margin_detail_fill provenance write failed", exc_info=True)


def _default_fetch(trade_date: str) -> list[dict[str, Any]]:
    from app.services.datasource.eastmoney import _call, fetch_margin_detail

    return _call("margin.detail", fetch_margin_detail, trade_date)


# ── 单日补缺 ─────────────────────────────────────────────────────────────
def fill_margin_day(
    db,
    trade_date: str,
    *,
    fetch: Callable[[str], list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """检测并补一个交易日。force=True 跳过截断判定（仍只补缺失代码、仍受盘中限制）。"""
    iso_day = _iso(trade_date)
    promax_rows = _count(db, iso_day)
    det = detect_truncation(promax_rows, _baseline_counts(db, iso_day))
    out: dict[str, Any] = {
        "trade_date": iso_day, "source": SOURCE, "promax_rows": promax_rows,
        "baseline_median": det["baseline_median"], "coverage_before": det["coverage"],
        "reasons": det["reasons"], "eastmoney_rows": 0, "eastmoney_inserted": 0,
        "rows_after": promax_rows, "coverage_after": det["coverage"], "status": "ok",
    }
    if promax_rows == 0 and not force and os.environ.get("ASHARE_MARGIN_FILL_ON_EMPTY", "0") != "1":
        out["status"] = "skipped_no_primary"  # 主源整日缺失属另一类故障，交给辅助数据补数重试
    elif not det["truncated"] and not force:
        out["status"] = "not_truncated"
        pend = set(load_pending())
        if iso_day in pend:
            _save_pending(pend - {iso_day})
    elif in_trading_session(now):
        _save_pending(set(load_pending()) | {iso_day})
        out["status"] = "deferred_trading_session"
    else:
        try:
            fetched = list((fetch or _default_fetch)(iso_day.replace("-", "")) or [])
            have = _existing_codes(db, iso_day)
            seen: set[str] = set()
            new: list[dict[str, Any]] = []
            for r in fetched:
                code = str(r.get("code") or "")
                if len(code) != 6 or code in have or code in seen or _iso(r.get("trade_date") or iso_day) != iso_day:
                    continue
                seen.add(code)
                new.append({"code": code, "trade_date": iso_day, **{k: r.get(k) for k in _VALUE_FIELDS}})
            inserted = _insert_missing(db, new)
            after = _count(db, iso_day)
            median = det["baseline_median"]
            out.update(
                eastmoney_rows=len(fetched), eastmoney_inserted=inserted, rows_after=after,
                coverage_after=round(after / median, 4) if median else None, status="filled",
            )
            out["inserted_codes"] = [r["code"] for r in new]
            pend = set(load_pending())
            if iso_day in pend:
                _save_pending(pend - {iso_day})
        except Exception as exc:  # noqa: BLE001 - 备份路径不阻断日跑
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            out.update(status="error", error=str(exc)[:200])
            _save_pending(set(load_pending()) | {iso_day})
    logger.warning(
        "margin_detail_fill td=%s status=%s reasons=%s promax_rows=%d eastmoney_rows=%d eastmoney_inserted=%d "
        "rows_after=%d baseline_median=%s coverage_after=%s source=%s",
        iso_day, out["status"], ",".join(out["reasons"]) or "-", promax_rows, out["eastmoney_rows"],
        out["eastmoney_inserted"], out["rows_after"], out["baseline_median"], out["coverage_after"], SOURCE,
    )
    if out["status"] not in ("not_truncated", "skipped_no_primary"):
        _append_provenance({"ts": datetime.now().isoformat(timespec="seconds"), **out})
    return out


def run_margin_detail_fill(
    db,
    trade_date: str | None = None,
    *,
    lookback_days: int = 3,
    fetch: Callable[[str], list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """日跑入口：检查 margin_detail 中 <= trade_date 的最近 lookback_days 个日期 + 待补清单。

    两融数据 T+1 才出，日跑当天常常只到上一交易日，所以按“库里最近几个日期”检查而不是只看当天。
    异常不外抛。
    """
    result: dict[str, Any] = {"source": SOURCE, "days": [], "errors": [], "inserted": 0}
    if not fill_enabled():
        result["skipped"] = "ASHARE_MARGIN_FILL_ENABLED off"
        return result
    try:
        upper = _iso(trade_date) if trade_date else "9999-12-31"
        recent = [d for (d,) in db.execute(
            select(MarginDetail.trade_date).where(MarginDetail.trade_date <= upper)
            .group_by(MarginDetail.trade_date).order_by(MarginDetail.trade_date.desc()).limit(lookback_days)
        )]
        for d in sorted(set(recent) | set(load_pending())):
            result["days"].append(fill_margin_day(db, d, fetch=fetch, now=now))
    except Exception as exc:  # noqa: BLE001
        logger.warning("margin_detail_fill run failed: %s", exc, exc_info=True)
        result["errors"].append(str(exc)[:200])
    result["inserted"] = sum(int(d.get("eastmoney_inserted") or 0) for d in result["days"])
    return result
