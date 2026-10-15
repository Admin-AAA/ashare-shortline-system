"""日K Parquet 本地缓存（无损加速 _load_cached_bars_batch）。

- 文件：backend/data/cache/daily_kbar.parquet + daily_kbar.fp.json（按交易日指纹）。
- refresh_kbar_cache(db)：盘后增量刷新。按 trade_date 计算 (count, sum(close), sum(chip_cost_50),
  sum(ma60), sum(max_dd60), sum(turnover_rate)) 指纹，只重拉新增/指纹变化的交易日，保证与 MySQL 一致。
- load_rows(codes, limit, as_of)：缓存覆盖 as_of 时，返回每只股票 trade_date<=as_of 的最近 limit 行
  （列值与 SQL 读出的 Python 值逐值一致：float/int/None/str）；不覆盖返回 None，调用方回退 SQL。
- 开关：ASHARE_KBAR_CACHE=0 关闭。
"""
from __future__ import annotations

import contextlib
import gc
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

COLUMNS: tuple[str, ...] = (
    "code", "trade_date", "open", "high", "low", "close", "volume", "amount",
    "pct_chg", "turnover_rate", "ma5", "ma10", "ma20", "ma60", "vol_ratio_5",
    "chip_winner_rate", "chip_weight_avg", "chip_cost_15", "chip_cost_50", "chip_cost_85",
    "holder_pct", "profit_efficiency", "rsi_6", "rsi_12", "rsi_24", "macd_dif",
    "macd_dea", "macd_bar", "bias_5", "bias_10", "bias_20", "breakout20",
    "breakout60", "upper_shadow_count20", "max_dd20", "max_dd60",
)
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
PARQUET = CACHE_DIR / "daily_kbar.parquet"
FP_FILE = CACHE_DIR / "daily_kbar.fp.json"

_lock = threading.Lock()
_mem: dict[str, Any] = {"mtime": None, "table": None, "index": None, "max_date": None}


def enabled() -> bool:
    flag = os.environ.get("ASHARE_KBAR_CACHE")
    if flag is not None:
        return flag not in {"0", "false", "False", ""}
    # 默认只对 MySQL 生产库启用；sqlite（单测/本地开发）不读共享缓存文件。
    try:
        from app.core.config import get_settings
        return not str(get_settings().database_url).startswith("sqlite")
    except Exception:  # noqa: BLE001
        return False


def _fingerprint_sql():
    from sqlalchemy import text
    return text(
        "SELECT trade_date, COUNT(*), ROUND(SUM(COALESCE(close,0)),4), ROUND(SUM(COALESCE(chip_cost_50,0)),4), "
        "ROUND(SUM(COALESCE(ma60,0)),4), ROUND(SUM(COALESCE(max_dd60,0)),4), ROUND(SUM(COALESCE(turnover_rate,0)),4), "
        "ROUND(SUM(COALESCE(rsi_6,0)),4) FROM daily_kbars GROUP BY trade_date"
    )


def _read_dates(db, dates: list[str]):
    import pyarrow as pa
    from sqlalchemy import select
    from app.models.tables import DailyKbar

    cols = [getattr(DailyKbar, c) for c in COLUMNS]
    out: dict[str, list] = {c: [] for c in COLUMNS}
    for i in range(0, len(dates), 20):
        chunk = dates[i:i + 20]
        for row in db.execute(select(*cols).where(DailyKbar.trade_date.in_(chunk))):
            for c, v in zip(COLUMNS, row):
                out[c].append(v)
    return pa.table(out)


def refresh_kbar_cache(db, *, full: bool = False) -> dict[str, Any]:
    """增量刷新缓存；返回统计。失败抛异常（调用方记录告警即可，读取端会回退 SQL）。"""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    t0 = time.perf_counter()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_fp = {str(r[0]): [int(r[1])] + [float(x or 0) for x in r[2:]] for r in db.execute(_fingerprint_sql())}
    old_fp: dict[str, list] = {}
    table = None
    if not full and PARQUET.exists() and FP_FILE.exists():
        old_fp = json.loads(FP_FILE.read_text())
        table = pq.read_table(PARQUET)
    changed = sorted(d for d, fp in db_fp.items() if old_fp.get(d) != fp)
    removed = sorted(d for d in old_fp if d not in db_fp)
    if table is not None and not changed and not removed:
        return {"changed_dates": 0, "rows": table.num_rows, "seconds": round(time.perf_counter() - t0, 1)}
    fresh = _read_dates(db, changed) if changed else None
    if table is not None:
        drop = pa.array(changed + removed, type=pa.string())
        keep = pc.invert(pc.is_in(table.column("trade_date"), value_set=drop))
        table = table.filter(keep)
        if fresh is not None and fresh.num_rows:
            fresh = fresh.cast(table.schema) if fresh.schema != table.schema else fresh
            table = pa.concat_tables([table, fresh], promote_options="permissive")
    else:
        table = fresh
    table = table.sort_by([("code", "ascending"), ("trade_date", "ascending")])
    tmp = PARQUET.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, PARQUET)
    FP_FILE.write_text(json.dumps(db_fp))
    with _lock:
        _mem["mtime"] = None
    return {"changed_dates": len(changed), "removed_dates": len(removed), "rows": table.num_rows,
            "seconds": round(time.perf_counter() - t0, 1)}


def _load() -> bool:
    import pyarrow.parquet as pq
    if not PARQUET.exists():
        return False
    mtime = PARQUET.stat().st_mtime
    with _lock:
        if _mem["mtime"] == mtime:
            return True
        table = pq.read_table(PARQUET)
        codes = table.column("code").to_pylist()
        dates = table.column("trade_date").to_pylist()
        index: dict[str, tuple[int, list[str]]] = {}
        start = 0
        n = len(codes)
        for i in range(1, n + 1):
            if i == n or codes[i] != codes[start]:
                index[codes[start]] = (start, dates[start:i])
                start = i
        _mem.update(mtime=mtime, table=table, index=index, max_date=max(dates) if dates else None)
    return True


@contextlib.contextmanager
def gc_paused():
    """临时关闭循环 GC（只影响回收时机，不影响结果）；退出时恢复原状态。"""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def covers(as_of: str) -> bool:
    if not enabled() or not _load():
        return False
    return bool(_mem["max_date"]) and as_of <= _mem["max_date"]


def load_rows(codes: list[str], limit: int, as_of: str) -> dict[str, list[dict[str, Any]]] | None:
    """缓存命中返回 {code: [row dict 按 trade_date 升序]}；否则 None。"""
    import bisect
    import pyarrow as pa

    if not covers(as_of):
        return None
    table, index = _mem["table"], _mem["index"]
    takes: list[int] = []
    spans: list[tuple[str, int, int]] = []
    for code in codes:
        ent = index.get(code)
        if not ent:
            continue
        start, dates = ent
        hi = bisect.bisect_right(dates, as_of)
        lo = max(0, hi - limit)
        if hi > lo:
            spans.append((code, len(takes), hi - lo))
            takes.extend(range(start + lo, start + hi))
    out: dict[str, list[dict[str, Any]]] = {}
    if not takes:
        return out
    taken = table.take(pa.array(takes, type=pa.int64()))
    # Arrow → Python 行字典交给 Polars（Rust 实现），取值/类型与 Table.to_pylist() 一致，
    # 约快 1.6 倍；构造期间暂停循环 GC，不改变任何取值。
    import polars as pl

    with gc_paused():
        rows = pl.from_arrow(taken).rows(named=True)
    for code, off, cnt in spans:
        out[code] = rows[off:off + cnt]
    return out
