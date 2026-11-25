"""margin_detail 截断自动补缺（数据中心 provider 优先级链 + 东财链尾补充）。

背景（2026-10-19）：promax 的 margin_detail 常态只回 2001 行（分页上限，只有沪市），
10-16 手工用东财只插缺失代码从 4111 行补到 4459 行。本模块把这一步固化为盘后例行。

2026-11-15 修正：此前 `_default_fetch` 硬编码只调东财一家，绕开了数据中心的
`data_source_config` 启停/优先级、`routing_table.json` 链序与熔断限流。现在改走
`datasource.complete_rows`：按 router 排好的 provider 顺序逐个 `request_pinned`，
把「只回半个市场」当作换源信号，按 code 合并去重，东财作为链尾补充源。
即使所有源都只有沪市（实测 2026-11-13 promax/quantdata/teajoin/东财均 2019 行），
provenance 也会记下「已穷尽 N 个可用源」，而不是让人以为只试了一家就放弃。

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

from sqlalchemy import case, func, insert, or_, select, text

from app.models.tables import MarginDetail

logger = logging.getLogger(__name__)

SOURCE = "eastmoney.margin_detail_fill"
DEFAULT_PAGE_CAPS: tuple[int, ...] = (2000, 3000, 4000, 5000, 6000, 8000, 10000)
DEFAULT_MIN_ROWS = 2000
DEFAULT_COVERAGE_DROP = 0.05
BASELINE_DAYS = 5
# 补完仍低于中位数的这个比例，说明上游当日还没全量发布，必须留在待补清单里次日重试。
_INCOMPLETE_COVERAGE_FLOOR = 0.8
# 上游当日未全量发布时（实测 2026-11-13 promax 与东财都只给 2019/4485 行），每 2 小时的
# 自愈轮次会重复打同一个 WARNING、重复请求同一个上游。连续 N 次 incomplete 且库内行数
# 毫无变化，说明上游还没发布，先退避 12 小时；行数一旦变化立即恢复正常重试。
_INCOMPLETE_BACKOFF_ATTEMPTS = 4
_INCOMPLETE_BACKOFF_SECONDS = 12 * 3600
_VALUE_FIELDS = ("rzye", "rqye", "rzmre", "rzche", "rzrqye", "rqyl")
# 行齐但列缺：2026-11-24 体检发现 11 个交易日行数完整（4490 行）而全部深市行的 rzche 为 NULL，
# 沪市完整。行数口径的截断判定看不到这种缺口，_insert_missing 又因 code 已存在全部跳过，
# 于是缺口永久留着。rzche 缺失会让「融资净买入 = 买入 − 偿还」把买入额整个当成净流入。
# rqyl（融券余量数量）长期整列为空属上游常态，不纳入。
_GAP_FIELDS = ("rzye", "rqye", "rzmre", "rzche", "rzrqye")
_GAP_NULL_SHARE = 0.05
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


def detect_column_gaps(db, iso_day: str, *, null_share: float = _GAP_NULL_SHARE) -> dict[str, int]:
    """当日 NULL 占比超过阈值的金额列 -> NULL 行数。行数完整也可能整批列缺。

    计数必须用 case(...,1,0)：直接 sum(col.is_(None)) 的表达式类型是 Boolean，
    SQLAlchemy 会把 2119 这样的计数结果强制转成 True，缺口永远检测不到。
    """
    cols = [func.sum(case((getattr(MarginDetail, f).is_(None), 1), else_=0)) for f in _GAP_FIELDS]
    row = db.execute(
        select(func.count(), *cols).where(MarginDetail.trade_date == iso_day)
    ).one()
    total = int(row[0] or 0)
    if not total:
        return {}
    return {f: int(n or 0) for f, n in zip(_GAP_FIELDS, row[1:]) if int(n or 0) > total * null_share}


def _null_field_rows(db, iso_day: str, fields: Iterable[str]) -> dict[str, dict[str, Any]]:
    """待修列存在 NULL 的已有行：code -> {列: 当前值}，用于只补 NULL、不覆盖已有值。"""
    fields = tuple(fields)
    if not fields:
        return {}
    cols = [getattr(MarginDetail, f) for f in fields]
    cond = or_(*[c.is_(None) for c in cols])
    rows = db.execute(
        select(MarginDetail.code, *cols).where(MarginDetail.trade_date == iso_day, cond)
    ).all()
    return {str(r[0]): dict(zip(fields, r[1:])) for r in rows}


def _fill_null_fields(db, iso_day: str, fetched: list[dict[str, Any]], gaps: dict[str, int]) -> int:
    """用备份源补已有行的 NULL 金额列。只写 NULL 位置，非 NULL 一律不动。"""
    if not gaps:
        return 0
    current = _null_field_rows(db, iso_day, gaps)
    if not current:
        return 0
    updates: list[dict[str, Any]] = []
    for r in fetched:
        code = str(r.get("code") or "")
        holes = current.get(code)
        if not holes or _iso(r.get("trade_date") or iso_day) != iso_day:
            continue
        patch = {f: r[f] for f in gaps if holes.get(f) is None and r.get(f) is not None}
        if patch:
            updates.append({"b_code": code, "b_trade_date": iso_day, **{f"v_{k}": v for k, v in patch.items()}})
            for f in patch:
                holes[f] = patch[f]
    if not updates:
        return 0
    filled = 0
    for u in updates:
        sets = ", ".join(f"{k[2:]} = :{k}" for k in u if k.startswith("v_"))
        before = db.execute(
            text(f"UPDATE margin_detail SET {sets} WHERE code = :b_code AND trade_date = :b_trade_date"), u
        ).rowcount
        filled += int(before or 0)
    db.commit()
    return filled


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


def _backoff_active(trade_date: str, now: datetime | None = None) -> bool:
    """该日是否处于「上游连续未发布全量」的退避窗口。纯读 provenance，失败一律不误退避。"""
    try:
        lines = (runtime_dir() / "margin_fill_provenance.jsonl").read_text(encoding="utf-8").splitlines()[-300:]
    except OSError:
        return False
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if _iso(rec.get("trade_date") or "") == trade_date:
            rows.append(rec)
    tail = [r for r in rows[-_INCOMPLETE_BACKOFF_ATTEMPTS:] if r.get("status") == "incomplete"]
    if len(tail) < _INCOMPLETE_BACKOFF_ATTEMPTS:
        return False
    if len({r.get("rows_after") for r in tail}) != 1:
        return False  # 行数在变 = 上游正在陆续发布，不能退避
    try:
        last = datetime.fromisoformat(str(tail[-1].get("ts") or ""))
        ref = (now or datetime.now()).replace(tzinfo=None)
        return (ref - last.replace(tzinfo=None)).total_seconds() < _INCOMPLETE_BACKOFF_SECONDS
    except (ValueError, TypeError):
        return False


def _last_rows_after(trade_date: str) -> int:
    """该日最近一次 provenance 记录的库内行数（退避探测的比较基线）。"""
    try:
        lines = (runtime_dir() / "margin_fill_provenance.jsonl").read_text(encoding="utf-8").splitlines()[-300:]
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if _iso(rec.get("trade_date") or "") == trade_date:
            return int(rec.get("rows_after") or 0)
    return 0


def _upstream_recovered(trade_date: str, rows_after: int) -> bool:
    """退避期间轻量探测数据中心链：任一启用源给出比上次更多的行 = 上游开始补发。

    纯读 provenance 的退避看不到上游变化：即使上游在退避第 5 小时补发全量，
    系统也会白等到 12 小时窗口结束。这里只问数据中心已启用的 provider
    （不含 promax，也不写库），一次探测约 3 个请求，换来「上游一恢复就补」。
    """
    from app.services.datasource.complete_rows import fetch_complete_trade_date

    try:
        probe = fetch_complete_trade_date("margin_detail", trade_date,
                                          expected_rows=0, exclude=("promax",))
    except Exception as exc:  # noqa: BLE001 - 探测失败继续退避，绝不误解除
        logger.info("margin_detail_fill td=%s 退避探测失败，继续退避: %r", trade_date, exc)
        return False
    n = len(probe.get("rows") or [])
    logger.info("margin_detail_fill td=%s 退避探测 rows=%d vs 库内 %d chain=%s errors=%s",
                trade_date, n, rows_after, ",".join(probe.get("attempted") or []),
                probe.get("errors") or [])
    return n > max(0, rows_after)


def _from_provider_row(row: dict[str, Any], trade_date: str) -> dict[str, Any] | None:
    """Tushare-compatible margin_detail 行 -> 本模块写入契约（6 位 code、金额亿元）。

    与 scripts/backfill_stale_domains.py 的口径一致：金额字段 /1e8，rqyl 是数量不换算。
    provider 不返回 rzrqye 时留 None，由东财链尾补充的行提供。
    """
    code = str(row.get("ts_code") or row.get("code") or "").split(".")[0]
    if len(code) != 6:
        return None

    def yi(value: Any) -> float | None:
        try:
            return None if value is None else round(float(value) / 1e8, 6)
        except (TypeError, ValueError):
            return None

    def num(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    return {"code": code, "trade_date": _iso(trade_date),
            "rzye": yi(row.get("rzye")), "rqye": yi(row.get("rqye")),
            "rzmre": yi(row.get("rzmre")), "rzche": yi(row.get("rzche")),
            "rzrqye": yi(row.get("rzrqye")), "rqyl": num(row.get("rqyl"))}


def _default_fetch(trade_date: str, expected_rows: int = 0) -> dict[str, Any]:
    """按数据中心优先级链取当日全市场两融明细；返回 {rows, probe}。

    排除 promax：主表数据本就来自它，再问一次只会重复拿到同一批沪市行，还占实盘额度。
    expected_rows 由调用方（fill_margin_day）用近 5 日中位数给出，达标即停止问后续源。
    """
    from app.services.datasource.complete_rows import fetch_complete_trade_date
    from app.services.datasource.eastmoney import _call, fetch_margin_detail

    def eastmoney(d: str):
        return _call("margin.detail", fetch_margin_detail, d)

    # 只让数据中心链取原始行；东财是网站接口、字段单位不同，放在链尾单独补，
    # 不能塞进 extra_fetchers 与 provider 行混在一起去重（会把亿元再 /1e8）。
    probe = fetch_complete_trade_date(
        "margin_detail", trade_date,
        expected_rows=int(expected_rows or 0),
        exclude=("promax",),
    )
    merged: dict[str, dict[str, Any]] = {}
    for raw in probe["rows"]:
        row = _from_provider_row(raw, trade_date)
        if row and row["code"] not in merged:
            merged[row["code"]] = row
    if not probe["complete"]:
        added = 0
        try:
            for row in eastmoney(trade_date) or []:
                code = str(row.get("code") or "")
                if len(code) == 6 and code not in merged:
                    merged[code] = row
                    added += 1
            probe["attempted"].append("eastmoney")
            probe["by_source"]["eastmoney"] = added
        except Exception as exc:  # noqa: BLE001 - 链尾补充源失败不影响数据中心已取到的行
            probe["errors"].append(f"eastmoney: {type(exc).__name__}: {str(exc)[:120]}")
            probe["by_source"]["eastmoney"] = 0
    return {
        "rows": list(merged.values()),
        "probe": {"attempted": probe["attempted"], "by_source": probe["by_source"],
                  "errors": probe["errors"], "complete": probe["complete"],
                  "expected_rows": probe["expected_rows"]},
    }


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
    gaps = detect_column_gaps(db, iso_day)
    if gaps:
        det["reasons"].append("column_gap")
    out: dict[str, Any] = {
        "trade_date": iso_day, "source": SOURCE, "promax_rows": promax_rows,
        "baseline_median": det["baseline_median"], "coverage_before": det["coverage"],
        "reasons": det["reasons"], "eastmoney_rows": 0, "eastmoney_inserted": 0,
        "rows_after": promax_rows, "coverage_after": det["coverage"], "status": "ok",
        "column_gaps": gaps, "null_fields_filled": 0,
    }
    if promax_rows == 0 and not force and os.environ.get("ASHARE_MARGIN_FILL_ON_EMPTY", "0") != "1":
        out["status"] = "skipped_no_primary"  # 主源整日缺失属另一类故障，交给辅助数据补数重试
    elif not det["truncated"] and not gaps and not force:
        out["status"] = "not_truncated"
        pend = set(load_pending())
        if iso_day in pend:
            _save_pending(pend - {iso_day})
    elif in_trading_session(now):
        _save_pending(set(load_pending()) | {iso_day})
        out["status"] = "deferred_trading_session"
    else:
        try:
            fetcher = fetch or (lambda d: _default_fetch(d, det["baseline_median"]))
            raw = fetcher(iso_day.replace("-", ""))
            # 新 _default_fetch 返回 {rows, probe}；测试/外部注入的 fetch 仍可只返回行列表。
            probe = raw.get("probe") if isinstance(raw, dict) else None
            fetched = list(raw.get("rows") if isinstance(raw, dict) else (raw or []))
            if probe:
                out["provider_probe"] = probe
                logger.info("margin_detail_fill td=%s datacenter_chain=%s by_source=%s errors=%s",
                            iso_day, ",".join(probe["attempted"]), probe["by_source"], probe["errors"])
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
            null_filled = _fill_null_fields(db, iso_day, fetched, gaps)
            after = _count(db, iso_day)
            median = det["baseline_median"]
            coverage_after = round(after / median, 4) if median else None
            gaps_left = detect_column_gaps(db, iso_day)
            out.update(
                eastmoney_rows=len(fetched), eastmoney_inserted=inserted, rows_after=after,
                coverage_after=coverage_after, status="filled",
                null_fields_filled=null_filled, column_gaps_left=gaps_left,
            )
            out["inserted_codes"] = [r["code"] for r in new]
            pend = set(load_pending())
            # 备份源也没给全（上游当日未全量发布）时不能标 filled：否则该日会从
            # 待补清单里被删掉，次日断点直接跳过，缺口永久留着。实测 2026-11-13
            # promax 2019 行、东财也只给 2019 行，coverage_after=0.4498 却被判 filled。
            # 列缺口同理：行数达标但 rzche 仍整批为 NULL 时也要留在清单里。
            if (median and after / median < _INCOMPLETE_COVERAGE_FLOOR) or gaps_left:
                out["status"] = "incomplete"
                _save_pending(pend | {iso_day})
            elif iso_day in pend:
                _save_pending(pend - {iso_day})
        except Exception as exc:  # noqa: BLE001 - 备份路径不阻断日跑
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 - 恢复动作，抛出会盖掉原始错误
                logger.warning("margin_detail_fill 回滚失败 td=%s", iso_day, exc_info=True)
            out.update(status="error", error=str(exc)[:200])
            _save_pending(set(load_pending()) | {iso_day})
    # 无缺口 / 主源未给数据只是例行巡检：自愈每 2 小时扫 5 个交易日，全打 WARNING
    # 会每天刷几十条告警级噪音，把真正的 incomplete / error 淹掉。
    level = logging.INFO if out["status"] in ("not_truncated", "skipped_no_primary") else logging.WARNING
    logger.log(
        level,
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
        pending = set(load_pending())
        result["backoffed"] = []
        for d in sorted(set(recent) | pending):
            # 退避只认 provenance（连续 4 次 incomplete 且库内行数不变），不依赖待补清单：
            # 清单文件被清掉/写失败时若只看 pending，退避失效会退回「每 2 小时白打上游」。
            if _backoff_active(d, now):
                baseline = _last_rows_after(d)
                if _upstream_recovered(d, baseline):
                    logger.info("margin_detail_fill td=%s 上游行数变化，立即解除退避并补齐", d)
                    # 写一条非 incomplete 记录打断「连续 4 次 incomplete」的退避判定。
                    _append_provenance({"ts": datetime.now().isoformat(timespec="seconds"),
                                        "trade_date": d, "source": SOURCE, "status": "recovered_probe",
                                        "rows_after": baseline})
                else:
                    result["backoffed"].append(d)
                    logger.info("margin_detail_fill td=%s 上游连续未发布全量，退避一次重试", d)
                    continue
            result["days"].append(fill_margin_day(db, d, fetch=fetch, now=now))
    except Exception as exc:  # noqa: BLE001
        logger.warning("margin_detail_fill run failed: %s", exc, exc_info=True)
        result["errors"].append(str(exc)[:200])
    result["inserted"] = sum(int(d.get("eastmoney_inserted") or 0) for d in result["days"])
    return result
