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


_DATE_RE = None


def _date_int(value: str) -> int | None:
    """'YYYY-MM-DD' -> YYYYMMDD（整数序与字符串序一致）；格式不符返回 None。"""
    global _DATE_RE
    if _DATE_RE is None:
        import re
        _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    return int(value[0:4] + value[5:7] + value[8:10])


def _trim_spec() -> tuple[str, int] | None:
    """回放内存裁剪：ASHARE_KBAR_CACHE_MIN_DATE=YYYY-MM-DD + ASHARE_KBAR_CACHE_KEEP_BACK=N（默认 120）。
    每只股票只保留 trade_date>=MIN_DATE 的行，外加其前 N 行。只要 as_of>=MIN_DATE 且 limit<=N，
    as_of 前最近 limit 行全部在保留集合内 → 与全表逐值一致；否则 load_rows 返回 None（回退 SQL）。"""
    v = os.environ.get("ASHARE_KBAR_CACHE_MIN_DATE") or ""
    if _date_int(v) is None:
        return None
    return v, int(os.environ.get("ASHARE_KBAR_CACHE_KEEP_BACK") or 120)


def _segments(idx):
    import numpy as np

    n = len(idx)
    cuts = np.flatnonzero(np.diff(idx)) + 1
    return np.concatenate(([0], cuts)), np.concatenate((cuts, [n]))


def _load() -> bool:
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    if not PARQUET.exists():
        return False
    mtime = PARQUET.stat().st_mtime
    with _lock:
        trim = _trim_spec()
        if _mem["mtime"] == mtime and _mem.get("trim") == trim:
            return True
        _mem.update(mtime=None, trim=None, table=None, index=None, max_date=None)
        # 逐列读取：瞬时内存只有单列，避免整表解码后分配器不归还页（实测 RSS 4.1GB → ~2GB 全表）。
        pf = pq.ParquetFile(PARQUET, memory_map=True, pre_buffer=False)
        names = pf.schema_arrow.names
        td = pf.read(columns=["trade_date"]).column(0).combine_chunks()
        n = len(td)
        if n and not bool(pc.all(pc.match_substring_regex(td, r"^\d{4}-\d{2}-\d{2}$")).as_py()):
            logger.warning("kbar cache trade_date format unexpected; cache disabled (SQL fallback)")
            return False
        date_ints = (
            pc.cast(pc.replace_substring(td, "-", ""), "int32").to_numpy(zero_copy_only=False)
            if n else np.empty(0, dtype=np.int32)
        )
        enc = pf.read(columns=["code"]).column(0).combine_chunks().dictionary_encode()
        idx = enc.indices.to_numpy(zero_copy_only=False)
        code_names = enc.dictionary.to_pylist()
        del enc
        mask = None
        if n and trim is not None:
            min_i = _date_int(trim[0])
            starts, ends = _segments(idx)
            mask = np.zeros(n, dtype=bool)
            for a, b in zip(starts.tolist(), ends.tolist()):
                first = a + int(np.searchsorted(date_ints[a:b], min_i, side="left"))
                mask[max(a, first - trim[1]):b] = True
            date_ints = date_ints[mask]
            idx = idx[mask]
            n = int(mask.sum())
        amask = pa.array(mask) if mask is not None else None
        arrays = []
        for name in names:
            if name == "trade_date":
                col = td
            else:
                col = pf.read(columns=[name]).column(0).combine_chunks()
            if amask is not None:
                col = pc.filter(col, amask)
            arrays.append(col)
            del col
        del td
        table = pa.table(arrays, schema=pf.schema_arrow)
        del arrays
        index: dict[str, tuple[int, Any]] = {}
        if n:
            starts, ends = _segments(idx)
            for a, b in zip(starts.tolist(), ends.tolist()):
                index[code_names[idx[a]]] = (a, date_ints[a:b])
        max_date = pc.max(table.column("trade_date")).as_py() if n else None
        _mem.update(mtime=mtime, trim=trim, table=table, index=index, max_date=max_date)
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

    import numpy as np

    as_of_i = _date_int(as_of)
    if as_of_i is None or not covers(as_of):
        return None
    trim = _mem.get("trim")
    if trim is not None and (as_of < trim[0] or limit > trim[1]):
        return None
    table, index = _mem["table"], _mem["index"]
    takes: list[int] = []
    spans: list[tuple[str, int, int]] = []
    for code in codes:
        ent = index.get(code)
        if not ent:
            continue
        start, dates = ent
        hi = int(np.searchsorted(dates, as_of_i, side="right"))
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


# ---- 回放专用：跨交易日复用已转换的 bar 字典（默认关闭，长驻服务进程不启用） ----
_ROW_MEMO: dict = {"on": False, "mtime": None, "bars": {}}


def enable_row_memo() -> None:
    """开启后 load_bars 会记住上一次调用转换过的行（按 parquet 行号），相邻交易日只转换新增行。
    返回给调用方的永远是浅拷贝（含 _indicator 子字典），因此调用方修改不会污染缓存。"""
    _ROW_MEMO["on"] = True


def memo_enabled() -> bool:
    return bool(_ROW_MEMO["on"])


def load_bars(codes: list[str], limit: int, as_of: str, convert) -> dict[str, list[dict[str, Any]]] | None:
    """同 load_rows，但直接返回 convert(row) 后的 bar（convert 须是纯函数）。"""
    import bisect
    import pyarrow as pa

    import numpy as np

    as_of_i = _date_int(as_of)
    if as_of_i is None or not covers(as_of):
        return None
    trim = _mem.get("trim")
    if trim is not None and (as_of < trim[0] or limit > trim[1]):
        return None
    table, index = _mem["table"], _mem["index"]
    if _ROW_MEMO["mtime"] is not table:  # 行号只对同一张内存表有效（mtime 或 min_date 变化都会换表）
        _ROW_MEMO.update(mtime=table, bars={})
    old = _ROW_MEMO["bars"]
    spans: list[tuple[str, int, int]] = []
    for code in codes:
        ent = index.get(code)
        if not ent:
            continue
        start, dates = ent
        hi = int(np.searchsorted(dates, as_of_i, side="right"))
        lo = max(0, hi - limit)
        if hi > lo:
            spans.append((code, start + lo, start + hi))
    missing = [i for _c, a, b in spans for i in range(a, b) if i not in old]
    new: dict[int, dict[str, Any]] = {}
    if missing:
        import polars as pl

        with gc_paused():
            # 分块转换：中间态行字典不与整批结果同时驻留（首日全量转换的内存峰值减半）
            step = 50_000
            for off in range(0, len(missing), step):
                chunk = missing[off:off + step]
                rows = pl.from_arrow(table.take(pa.array(chunk, type=pa.int64()))).rows(named=True)
                for i, r in zip(chunk, rows):
                    new[i] = convert(r)
                del rows
    out: dict[str, list[dict[str, Any]]] = {}
    keep: dict[int, dict[str, Any]] = {}
    with gc_paused():
        for code, a, b in spans:
            lst = []
            for i in range(a, b):
                bar = old.get(i)
                if bar is None:
                    bar = new[i]
                keep[i] = bar
                c = dict(bar)
                c["_indicator"] = dict(bar["_indicator"])
                lst.append(c)
            out[code] = lst
    _ROW_MEMO["bars"] = keep
    return out
