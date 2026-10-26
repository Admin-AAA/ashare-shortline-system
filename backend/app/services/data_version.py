"""读接口缓存的「数据版本」与有上限的版本化缓存。

为什么要从库里取版本：后端（uvicorn）和 Prefect worker 是不同进程，Prefect 写入新运行、新结算、
新日K 时，后端进程内的变量看不到。所以缓存键里带一份从 MySQL 现算的「版本指纹」，指纹一变缓存立刻失效。

指纹怎么算（实测 1049 个 run / 536 条推荐时全部合计约 40ms）：
- recommendation_runs / recommendations 在迁移 0028 之后：只读 (主键, updated_at) 二级索引（见 _row_version_sql），
  不再读大字段；迁移前没有这一列时自动退回下面的全列算法。
- 小表（推荐、运行、结算、执行确认、重建批次、预设、关注）：COUNT(*) + 全部列拼接后 CRC32 的 BIT_XOR。
  任何一行任何一列的增删改都会改变指纹（CRC32 碰撞概率 ~2^-32，可忽略）。
  recommendation_runs 跳过 4 个大字段（params/market_snapshot/filter_audit/request，平均 5MB/行，
  使用本模块的读接口都不读它们）。
- 大表（daily_kbars / stk_limits，几百万行）：MAX(id) + MAX(trade_date)，走主键/索引，<1ms。
  能发现新增的日K和新交易日；**发现不了对旧日K的原地改写**（没有 updated_at 索引），
  所以每个缓存项另有 max_age 兜底（默认 10 分钟），过期必重算。

内存：每个缓存有条目上限（LRU 淘汰），并且同一键有计算锁，避免多个并发请求同时重算。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable, Iterable

from sqlalchemy import event, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

# 大表：只看新增（主键自增 + 最新交易日）
_APPEND_ONLY_TABLES = {"daily_kbars", "stk_limits"}
# 小表里不参与指纹的大字段（读接口不用）
_SKIP_COLUMNS = {
    "recommendation_runs": {"params_json", "market_snapshot_json", "filter_audit_json", "request_json"},
}

_columns_lock = threading.Lock()
_columns: dict[str, list[str]] = {}


def _table_columns(db: Session, table: str) -> list[str]:
    with _columns_lock:
        cols = _columns.get(table)
    if cols is not None:
        return cols
    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table},
    ).all()
    skip = _SKIP_COLUMNS.get(table, set())
    cols = [str(r[0]) for r in rows if str(r[0]) not in skip]
    with _columns_lock:
        _columns[table] = cols
    return cols


_ROW_VERSION_COLUMN = "updated_at"
_row_version_sql_cache: dict[str, str | None] = {}


def _row_version_sql(db: Session, table: str) -> str | None:
    """表上有迁移 0028 加的 updated_at（MySQL 自动维护）及其索引时，指纹只读这个二级索引：
    行数 + 每行 (主键, updated_at) 的 CRC 异或。二级索引自带主键，所以不回表、不碰大字段，
    缓冲池再挤也只是几十 KB。没有这一列（迁移前）返回 None，退回全列指纹。"""
    with _columns_lock:
        if table in _row_version_sql_cache:
            return _row_version_sql_cache[table]
    sql = None
    if _ROW_VERSION_COLUMN in _table_columns(db, table):
        index = f"ix_{table}_{_ROW_VERSION_COLUMN}"
        has_index = db.execute(text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
        ), {"t": table, "i": index}).scalar()
        pk = [str(r[0]) for r in db.execute(text(
            "SELECT column_name FROM information_schema.key_column_usage "
            "WHERE table_schema = DATABASE() AND table_name = :t AND constraint_name = 'PRIMARY' "
            "ORDER BY ordinal_position"
        ), {"t": table}).all()]
        if has_index and pk:
            keys = ",".join(f"`{c}`" for c in pk)
            sql = (f"SELECT COUNT(*), BIT_XOR(CRC32(CONCAT_WS('|',{keys},`{_ROW_VERSION_COLUMN}`))) "
                   f"FROM `{table}` FORCE INDEX (`{index}`)")
    with _columns_lock:
        _row_version_sql_cache[table] = sql
    return sql


def _fingerprint_sql(db: Session, table: str) -> str:
    if table in _APPEND_ONLY_TABLES:
        return f"SELECT MAX(id), MAX(trade_date) FROM `{table}`"
    row_version = _row_version_sql(db, table)
    if row_version is not None:
        return row_version
    cols = _table_columns(db, table)
    if not cols:
        return f"SELECT COUNT(*) FROM `{table}`"
    # CONCAT_WS 会跳过 NULL；用 IFNULL 区分 NULL 和空串，避免「NULL→''」这种改动漏检
    parts = ",".join(f"IFNULL(`{c}`,'\\0')" for c in cols)
    return f"SELECT COUNT(*), BIT_XOR(CRC32(CONCAT_WS('|',{parts}))) FROM `{table}`"


_SESSION_KEY = "_data_version_memo"


def data_version(db: Session, tables: Iterable[str]) -> tuple[Any, ...] | None:
    """返回这些表当前的版本指纹（可做字典键）。

    同一个会话、同一个事务里重复取同一张表（一次请求里多个函数各取一次）只查一次库：
    记在 session.info 里，会话一旦 flush / commit / rollback 就清掉（见下面的事件监听），
    所以本请求自己写了库也不会拿到旧指纹。"""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "mysql":
        return None  # 指纹 SQL 只支持 MySQL；其它库（单测用的 SQLite）不缓存，调用方直接现算
    memo: dict[str, Any] = db.info.setdefault(_SESSION_KEY, {})
    out: list[Any] = []
    for table in sorted(set(tables)):
        fp = memo.get(table)
        if fp is None:
            row = db.execute(text(_fingerprint_sql(db, table))).first()
            fp = tuple(str(v) for v in row) if row is not None else ("",)
            memo[table] = fp
        out.append((table, fp))
    return tuple(out)


def _drop_memo(session, *_args) -> None:
    session.info.pop(_SESSION_KEY, None)


def _drop_memo_on_write(orm_execute_state) -> None:
    # session.execute(text("UPDATE ...")) / core update() 不经过 flush，也要清掉本事务里记下的指纹
    if orm_execute_state.is_select:
        return
    stmt = orm_execute_state.statement
    if isinstance(stmt, TextClause) and str(stmt).lstrip().upper().startswith(("SELECT", "SHOW", "EXPLAIN")):
        return  # 原生 SQL 的只读查询（包括指纹查询本身）
    orm_execute_state.session.info.pop(_SESSION_KEY, None)


event.listen(Session, "do_orm_execute", _drop_memo_on_write)
event.listen(Session, "after_flush", _drop_memo)
event.listen(Session, "after_commit", _drop_memo)
event.listen(Session, "after_rollback", _drop_memo)
event.listen(Session, "after_soft_rollback", _drop_memo)


class VersionedCache:
    """键 -> (版本, 写入时间, 值)。版本不同或超过 max_age 即视为未命中；条目数超 maxsize 按 LRU 淘汰。"""

    def __init__(self, name: str, *, maxsize: int, max_age: float):
        self.name = name
        self.maxsize = maxsize
        self.max_age = max_age
        self._data: OrderedDict[Hashable, tuple[Any, float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = {}
        self.hits = 0
        self.misses = 0

    def _lookup(self, key: Hashable, version: Any) -> tuple[bool, Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False, None
            ver, ts, value = entry
            if ver != version or (time.time() - ts) >= self.max_age:
                self._data.pop(key, None)
                return False, None
            self._data.move_to_end(key)
            return True, value

    def get_or_compute(self, key: Hashable, version: Any, factory: Callable[[], Any]) -> Any:
        if version is None:  # 拿不到数据版本：不缓存
            return factory()
        found, value = self._lookup(key, version)
        if found:
            self.hits += 1
            return value
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            # 等锁期间别的请求可能已算好同版本
            found, value = self._lookup(key, version)
            if found:
                self.hits += 1
                return value
            self.misses += 1
            value = factory()
            with self._lock:
                self._data[key] = (version, time.time(), value)
                self._data.move_to_end(key)
                while len(self._data) > self.maxsize:
                    old_key, _ = self._data.popitem(last=False)
                    self._key_locks.pop(old_key, None)
            return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


_registry: list[VersionedCache] = []


def make_cache(name: str, *, maxsize: int, max_age: float = 600.0) -> VersionedCache:
    cache = VersionedCache(name, maxsize=maxsize, max_age=max_age)
    _registry.append(cache)
    return cache


def clear_all() -> None:
    for cache in _registry:
        cache.clear()


def clear_named(prefix: str) -> None:
    for cache in _registry:
        if cache.name.startswith(prefix):
            cache.clear()
