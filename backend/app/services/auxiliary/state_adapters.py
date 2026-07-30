"""Typed, transaction-bound adapters for S4 mutable auxiliary datasets.

This module contains no credential acquisition, Session creation, commits, or event-table
access.  The application boundary injects both a caller-owned SQLAlchemy Session/Connection
and a previously acquired Tushare-compatible client.  Event datasets stay in immutable
snapshot handlers defined by :mod:`app.services.auxiliary.composition`.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.adapters.market_source.dataframe_contracts import validate_market_frame
from app.models.tables import (
    AuctionData,
    BrokerRecommend,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DragonTiger,
    FinaIndicator,
    HolderNumber,
    HsgtFlow,
    HsgtTop10,
    LimitUpBoard,
    MarginDetail,
    PledgeStat,
)
from app.services.auxiliary.contracts import (
    AuxiliaryDatasetRequest,
    AuxiliaryLoadedView,
    AuxiliaryPersistResult,
    AuxiliaryRepositorySnapshot,
)
from app.services.auxiliary.production import (
    AUXILIARY_DATASET_BY_KEY,
    AuxiliaryDatasetInventoryEntry,
    AuxiliaryInventoryError,
    StandardAuxiliaryDatasetAdapters,
)


class TushareStateClientPort(Protocol):
    """Subset of the Tushare client used by injected state providers."""

    def __getattr__(self, api_name: str) -> Callable[..., Any]: ...


StateFetch = Callable[[AuxiliaryDatasetRequest], Sequence[Mapping[str, Any]]]
StateLoad = Callable[[AuxiliaryDatasetRequest], Mapping[str, Any]]


def _compact_day(value: date) -> str:
    return value.strftime("%Y%m%d")


def _quarter_end_day(value: date) -> str:
    """最近已披露的季末日（YYYYMMDD）。质押/股东人数/财务指标按季度披露，
    非季末日按 ts_code 调会返回空，且逐股循环会触发 429 限流。"""
    return _quarter_end_candidates(value)[0]


def _quarter_end_candidates(value: date, *, lookback_quarters: int = 6) -> list[str]:
    """季末候选日列表（YYYYMMDD），从近到远回退。质押等披露不规则的慢变数据，
    最近季末日可能尚未披露，需按候选回退到第一个有数据的日期。"""
    quarter_end_months = [(12, 31), (9, 30), (6, 30), (3, 31)]
    candidates: list[str] = []
    cursor_year = value.year
    placed = set()
    while len(candidates) < lookback_quarters:
        for m, day in quarter_end_months:
            candidate = date(cursor_year, m, day)
            if candidate > value:
                continue
            key = candidate.isoformat()
            if key in placed:
                continue
            placed.add(key)
            candidates.append(candidate.strftime("%Y%m%d"))
            if len(candidates) >= lookback_quarters:
                break
        cursor_year -= 1
    return candidates


def _iso_day(value: object) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value or "").strip()[:10]


def _number(value: object, default: float = 0.0) -> float:
    import math

    try:
        numeric = float(value) if value is not None else default
        return default if math.isnan(numeric) else numeric
    except (TypeError, ValueError):
        return default


def _safe_int(value: object) -> int | None:
    """安全转 int；None/NaN/空值返回 None（由 upsert 写 NULL，不抛异常中断整批）。"""
    import math
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    return int(v)


def _ts_code(code: str) -> str:
    if "." in code:
        return code
    return f"{code}.{'SH' if code.startswith(('5', '6', '9')) else 'SZ'}"


def _row(model: Any) -> dict[str, Any]:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


@dataclass(frozen=True, slots=True)
class _CallableFetcher:
    fetch_records: StateFetch

    def fetch(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        return self.fetch_records(request)


class _IdentityNormalizer:
    def normalize(
        self,
        raw: Mapping[str, Any],
        request: AuxiliaryDatasetRequest,
    ) -> Mapping[str, Any] | None:
        del request
        return dict(raw)


@dataclass(frozen=True, slots=True)
class _CallableLoader:
    view_key: str
    load_view: StateLoad

    def load(self, request: AuxiliaryDatasetRequest) -> AuxiliaryLoadedView[Mapping[str, Any]]:
        return AuxiliaryLoadedView(self.view_key, self.load_view(request))


class SQLAlchemyStateRepository:
    """State repository whose write transaction is owned entirely by the caller.

    The MySQL path uses one atomic upsert statement per chunk.  SQLite is a local
    development/test parity adapter with the same natural-key conflict semantics; it
    never claims MySQL deployment evidence and is guarded by the composition readiness
    object in production.
    """

    def __init__(
        self,
        *,
        connection: Connection,
        entry: AuxiliaryDatasetInventoryEntry,
    ) -> None:
        self._connection = connection
        self._entry = entry
        self._table = entry.model.__table__
        policy = entry.update_policy
        if policy is None:  # pragma: no cover - frozen inventory validates this
            raise AuxiliaryInventoryError(f"state entry lacks update policy: {entry.key}")
        self._overwrite = policy.overwrite_fields

    def inspect(self, request: AuxiliaryDatasetRequest) -> AuxiliaryRepositorySnapshot:
        table = self._table
        freshness = table.c[self._entry.freshness_field]
        conditions: list[Any] = []
        if "code" in table.c and request.requested_codes:
            conditions.append(table.c.code.in_(request.requested_codes))
        if self._entry.freshness_field == "trade_date":
            conditions.append(freshness == request.trade_date.isoformat())
        count_stmt = select(func.count()).select_from(table)
        freshness_stmt = select(func.max(freshness)).select_from(table)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
            freshness_stmt = freshness_stmt.where(*conditions)
        count = int(self._connection.scalar(count_stmt) or 0)
        observed = self._connection.scalar(freshness_stmt)
        covered: frozenset[str] = frozenset()
        if "code" in table.c and request.requested_codes:
            covered = frozenset(
                str(value)
                for value in self._connection.scalars(
                    select(table.c.code).where(*conditions).distinct()
                )
                if str(value or "").strip()
            )
        parsed: date | datetime | None = None
        if isinstance(observed, (date, datetime)):
            parsed = observed
        elif observed:
            try:
                parsed = date.fromisoformat(_iso_day(observed))
            except ValueError:
                parsed = None
        return AuxiliaryRepositorySnapshot(count, covered, parsed)

    def upsert(
        self,
        records: Sequence[Mapping[str, Any]],
        request: AuxiliaryDatasetRequest,
    ) -> AuxiliaryPersistResult:
        del request
        started = datetime.now(timezone.utc)
        rows = [self._encode(record) for record in records]
        self._assert_unique_batch(rows)
        if not rows:
            return AuxiliaryPersistResult(0, 0, 0, 0, 0.0, batch_size=500, batch_statement_bound=0)
        for offset in range(0, len(rows), 500):
            batch = rows[offset : offset + 500]
            if self._connection.dialect.name == "mysql":
                statement = mysql_insert(self._table).values(batch)
                statement = statement.on_duplicate_key_update(
                    **{field: getattr(statement.inserted, field) for field in self._overwrite}
                )
            elif self._connection.dialect.name == "sqlite":
                statement = sqlite_insert(self._table).values(batch)
                statement = statement.on_conflict_do_update(
                    index_elements=list(self._entry.natural_key),
                    set_={field: getattr(statement.excluded, field) for field in self._overwrite},
                )
            else:
                raise AuxiliaryInventoryError(
                    f"state atomic upsert requires MySQL (or SQLite local parity), got {self._connection.dialect.name}"
                )
            self._connection.execute(statement)
        duration = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        chunks = (len(rows) + 499) // 500
        return AuxiliaryPersistResult(
            attempted_rows=len(rows),
            affected_rows=None,
            failed_rows=0,
            execute_count=chunks,
            duration_ms=duration,
            batch_size=500,
            batch_statement_bound=chunks,
        )

    def _encode(self, record: Mapping[str, Any]) -> dict[str, Any]:
        columns = set(self._table.c.keys())
        row = {key: value for key, value in record.items() if key in columns and key not in {"id", "created_at", "updated_at"}}
        missing = [field for field in (*self._entry.natural_key, *self._overwrite) if field not in row]
        if missing:
            raise AuxiliaryInventoryError(f"{self._entry.key} normalized row missing fields: {', '.join(missing)}")
        return row

    def _assert_unique_batch(self, rows: Sequence[Mapping[str, Any]]) -> None:
        keys: set[tuple[object, ...]] = set()
        for row in rows:
            key = tuple(row[field] for field in self._entry.natural_key)
            if key in keys:
                raise AuxiliaryInventoryError(f"{self._entry.key} duplicate state natural key in one batch: {key!r}")
            keys.add(key)


class TushareStateProvider:
    """Current state feed algorithms bound to an already-acquired provider client."""

    def __init__(self, client: TushareStateClientPort, session: Session) -> None:
        self._client = client
        self._session = session

    def limit_up_board(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        rows: list[dict[str, Any]] = []
        target = _compact_day(request.trade_date)
        failures: list[str] = []
        for limit_type in ("U", "D", "Z"):
            try:
                frame = self._client.limit_list_d(trade_date=target, limit_type=limit_type)
            except Exception as exc:  # preserve all-source failure evidence
                failures.append(type(exc).__name__)
                continue
            if frame is None or frame.empty:
                continue
            for _, item in frame.iterrows():
                code = str(item.get("ts_code") or "").split(".")[0]
                if len(code) != 6:
                    continue
                rows.append({
                    "code": code,
                    "name": str(item.get("name") or ""),
                    "trade_date": _iso_day(item.get("trade_date") or target),
                    "pct_chg": _number(item.get("pct_chg")),
                    "limit_type": limit_type,
                    "limit_times": int(_number(item.get("limit_times"))),
                    "first_time": str(item.get("first_time") or ""),
                    "last_time": str(item.get("last_time") or ""),
                    "open_times": int(_number(item.get("open_times"))),
                    "up_stat": str(item.get("up_stat") or ""),
                    "limit_amount_yi": _number(item.get("limit_amount")) / 1e8,
                    "turnover_ratio": _number(item.get("turnover_ratio")),
                    "fd_amount_yi": _number(item.get("fd_amount")) / 1e8,
                    "industry": str(item.get("industry") or ""),
                })
        if failures and not rows:
            raise RuntimeError(f"limit_list_d failed for all partitions: {', '.join(failures)}")
        return rows

    def concept_sector(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        frame = self._client.ths_index()
        if frame is None or frame.empty:
            return ()
        return tuple(
            {
                "sector_code": str(item.get("ts_code") or ""),
                "sector_name": str(item.get("name") or ""),
                "member_count": int(_number(item.get("count"))),
                "exchange": str(item.get("exchange") or ""),
                "list_date": _iso_day(item.get("list_date")),
                "type": str(item.get("type") or ""),
                "heat_rank": rank,
                "trade_date": request.trade_date.isoformat(),
            }
            for rank, (_, item) in enumerate(frame.iterrows(), start=1)
            if str(item.get("ts_code") or "").strip()
        )

    def concept_sector_stock(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        sectors = self._session.scalars(
            select(ConceptSector)
            .where(ConceptSector.trade_date == request.trade_date.isoformat())
            .order_by(ConceptSector.heat_rank.asc())
            .limit(60)
        ).all()
        rows: list[dict[str, Any]] = []
        for sector in sectors:
            frame = self._client.ths_member(ts_code=sector.sector_code)
            if frame is None or frame.empty:
                continue
            for _, item in frame.iterrows():
                code = str(item.get("con_code") or "").split(".")[0]
                if len(code) == 6:
                    rows.append({
                        "sector_code": sector.sector_code,
                        "code": code,
                        "name": str(item.get("con_name") or ""),
                        "trade_date": request.trade_date.isoformat(),
                    })
        return rows

    def capital_flow(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        # 单股资金流向按 trade_date 批量一次拉全市场，再按 requested_codes 过滤；
        # 逐股循环会触发 ~4948 次 Tushare 请求 + 429 限流。当日累计字段由
        # backfill_moneyflow+compute_cumulative_inflows 在每日同步阶段补全，
        # 本路径在 run 时通常已被本地表覆盖（refresh=False）；refresh=True 兜底
        # 时仅入当日净额，累计窗口交给每日同步。
        target = _compact_day(request.trade_date)
        wanted = set(request.requested_codes)
        frame = self._client.moneyflow(trade_date=target)
        if frame is None or frame.empty:
            return ()
        validate_market_frame("moneyflow", frame, provider="tushare-auxiliary")
        records: list[dict[str, Any]] = []
        for _, latest in frame.iterrows():
            code = str(latest.get("ts_code") or "").split(".")[0]
            if len(code) != 6 or (wanted and code not in wanted):
                continue
            records.append({
                "code": code,
                "trade_date": _iso_day(latest.get("trade_date") or target),
                "main_net_inflow_yi": round(_number(latest.get("net_mf_amount")) / 1e8, 4),
                "super_large_net_inflow_yi": round((_number(latest.get("buy_elg_amount")) - _number(latest.get("sell_elg_amount"))) / 1e8, 4),
                "large_net_inflow_yi": round((_number(latest.get("buy_lg_amount")) - _number(latest.get("sell_lg_amount"))) / 1e8, 4),
                "medium_net_inflow_yi": round((_number(latest.get("buy_md_amount")) - _number(latest.get("sell_md_amount"))) / 1e8, 4),
                "small_net_inflow_yi": round((_number(latest.get("buy_sm_amount")) - _number(latest.get("sell_sm_amount"))) / 1e8, 4),
                "main_net_inflow_ratio": round(_number(latest.get("net_mf_vol")), 2),
                "cumulative_inflow_3d_yi": 0.0,
                "cumulative_inflow_5d_yi": 0.0,
                "cumulative_inflow_10d_yi": 0.0,
            })
        return records

    def dragon_tiger(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        frame = self._client.top_list(trade_date=_compact_day(request.trade_date))
        if frame is None or frame.empty:
            return ()
        target = request.trade_date.isoformat()
        # top_list 对同一 (code, trade_date) 可能返回多条（一只股票同日因多个
        # reason 上榜）。DragonTiger 表 unique 键是 (code, trade_date)，batch 内
        # 重复 natural key 会导致整批 upsert 失败。按 (code, trade_date) 去重，
        # 保留首条（reason 字段拼接多原因，便于追溯）。
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for _, item in frame.iterrows():
            code = str(item.get("ts_code") or "").split(".")[0]
            if len(code) != 6:
                continue
            trade_date = _iso_day(item.get("trade_date") or target)
            key = (code, trade_date)
            if key in deduped:
                reason = str(item.get("reason") or "").strip()
                if reason and reason not in (deduped[key]["reason"] or ""):
                    deduped[key]["reason"] = f"{deduped[key]['reason']};{reason}"
                continue
            deduped[key] = {
                "code": code,
                "name": str(item.get("name") or ""),
                "trade_date": trade_date,
                "reason": str(item.get("reason") or ""),
                "buy_amount_yi": round(_number(item.get("l_buy")) / 1e8, 4),
                "sell_amount_yi": round(_number(item.get("l_sell")) / 1e8, 4),
                "net_amount_yi": round(_number(item.get("net_amount")) / 1e8, 4),
            }
        return tuple(deduped.values())

    def margin_detail(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        target = _compact_day(request.trade_date)
        frame = self._client.margin_detail(trade_date=target)
        if frame is None or frame.empty:
            return ()
        return tuple(
            {
                "code": code,
                "trade_date": _iso_day(item.get("trade_date") or target),
                "rzye": _number(item.get("rzye")) / 1e8,
                "rqye": _number(item.get("rqye")) / 1e8,
                "rzmre": _number(item.get("rzmre")) / 1e8,
                "rzche": _number(item.get("rzche")) / 1e8,
                "rzrqye": _number(item.get("rzrqye")) / 1e8,
                "rqyl": _number(item.get("rqyl")),
            }
            for _, item in frame.iterrows()
            if len(code := str(item.get("ts_code") or "").split(".")[0]) == 6
        )

    def hsgt_flow(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        target = _compact_day(request.trade_date)
        frame = self._client.moneyflow_hsgt(trade_date=target)
        if frame is None or frame.empty:
            return ()
        item = frame.iloc[0]
        return ({
            "trade_date": _iso_day(item.get("trade_date") or target),
            "north_money": _number(item.get("north_money")) / 1e4,
            "south_money": _number(item.get("south_money")) / 1e4,
            "hgt": _number(item.get("hgt")),
            "sgt": _number(item.get("sgt")),
        },)

    def hsgt_top10(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        target = _compact_day(request.trade_date)
        frame = self._client.hsgt_top10(trade_date=target)
        if frame is None or frame.empty:
            return ()
        return tuple(
            {
                "code": code,
                "trade_date": _iso_day(item.get("trade_date") or target),
                "name": str(item.get("name") or ""),
                "close": _number(item.get("close")),
                "change_pct": _number(item.get("change")),
                "rank": int(_number(item.get("rank"))),
                "amount": _number(item.get("amount")),
                "net_amount": _number(item.get("net_amount")),
                "buy": _number(item.get("buy")),
            }
            for _, item in frame.iterrows()
            if len(code := str(item.get("ts_code") or "").split(".")[0]) == 6
        )

    def pledge_stat(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        # 质押统计按季度披露（慢变数据），用 end_date 批量一次拉全市场，再按
        # requested_codes 过滤。逐股循环会触发 ~4948 次 Tushare 请求 + 429 限流。
        # pledge_stat 披露日不规则（非严格季末有数据），按季末候选从近到远回退
        # 到第一个有数据的日期。
        frame: Any = None
        for candidate in _quarter_end_candidates(request.trade_date):
            try:
                candidate_frame = self._client.pledge_stat(end_date=candidate)
            except Exception:
                continue
            if candidate_frame is not None and not candidate_frame.empty:
                frame = candidate_frame
                break
        if frame is None:
            return ()
        validate_market_frame("pledge_stat", frame, provider="tushare-auxiliary")
        wanted = set(request.requested_codes)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        frame = frame.sort_values("end_date")
        for _, item in frame.iterrows():
            code = str(item.get("ts_code") or "").split(".")[0]
            if len(code) != 6 or code in seen or (wanted and code not in wanted):
                continue
            seen.add(code)
            pledge_count = _safe_int(item.get("pledge_count"))
            rows.append({
                "code": code,
                "end_date": _iso_day(item.get("end_date")),
                "pledge_count": pledge_count if pledge_count is not None else 0,
                "unrest_pledge": _number(item.get("unrest_pledge")),
                "rest_pledge": _number(item.get("rest_pledge")),
                "pledge_ratio": _number(item.get("pledge_ratio")),
            })
        return rows

    def broker_recommend(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        month = request.trade_date.strftime("%Y%m")
        frame = self._client.broker_recommend(month=month)
        if frame is None or frame.empty:
            return ()
        return tuple(
            {
                "code": code,
                "name": str(item.get("name") or ""),
                "month": str(item.get("month") or month),
                "broker": str(item.get("broker") or ""),
            }
            for _, item in frame.iterrows()
            if len(code := str(item.get("ts_code") or "").split(".")[0]) == 6 and str(item.get("broker") or "").strip()
        )

    def fina_indicator(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        # 财务指标按季度披露（慢变数据），只允许 period= 批量拉全市场。
        # 批量源不可用时如实返回空；禁止扩散成数百次逐股请求并触发 429。
        target = _compact_day(request.trade_date)
        period = _quarter_end_day(request.trade_date)
        wanted = set(request.requested_codes)
        rows: list[dict[str, Any]] = []

        try:
            frame = self._client.fina_indicator(period=period)
        except Exception:
            return ()
        if frame is None or frame.empty:
            return ()
        if "ann_date" in frame.columns:
            frame = frame[frame["ann_date"].astype(str) <= target]
        for _, item in frame.iterrows():
            code = str(item.get("ts_code") or "").split(".")[0]
            if len(code) != 6 or (wanted and code not in wanted):
                continue
            end_date = str(item.get("end_date") or "")
            if not end_date:
                continue
            rows.append({
                "code": code,
                "end_date": end_date,
                "ann_date": str(item.get("ann_date") or ""),
                "roe": _number(item.get("roe")),
                "grossprofit_margin": _number(item.get("grossprofit_margin")),
                "netprofit_margin": _number(item.get("netprofit_margin")),
                "netprofit_yoy": _number(item.get("netprofit_yoy")),
                "or_yoy": _number(item.get("or_yoy")),
                "eps": _number(item.get("eps")),
                "bps": _number(item.get("bps")),
            })
        return rows

    def auction(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        # 集合竞价按 trade_date 批量一次拉全市场（Tushare stk_auction）。
        # 逐股循环调 stk_auction 在无权限账号下会触发 N 次 API 错误；这里改成
        # 单次 trade_date 批量调用，失败即数据源不可用（无权限），返回 ()。
        target = _compact_day(request.trade_date)
        try:
            frame = self._client.stk_auction(trade_date=target)
        except Exception:
            return ()
        if frame is None or frame.empty:
            return ()
        wanted = set(request.requested_codes)
        rows: list[dict[str, Any]] = []
        for _, item in frame.iterrows():
            code = str(item.get("ts_code") or "").split(".")[0]
            if len(code) != 6 or (wanted and code not in wanted):
                continue
            rows.append({
                "code": code,
                "trade_date": _iso_day(item.get("trade_date") or target),
                "vol": _number(item.get("vol")),
                "price": _number(item.get("price")),
                "amount": _number(item.get("amount")),
                "pre_close": _number(item.get("pre_close")),
                "turnover_rate": _number(item.get("turnover_rate")),
                "volume_ratio": _number(item.get("volume_ratio")),
                "float_share": _number(item.get("float_share")),
            })
        return rows

    def holder_number(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        # 股东人数按季度披露（慢变数据），用 end_date 批量一次拉全市场，再按
        # requested_codes 过滤。逐股循环会触发 ~4948 次 Tushare 请求 + 429 限流。
        target = _quarter_end_day(request.trade_date)
        frame = self._client.stk_holdernumber(end_date=target)
        if frame is None or frame.empty:
            return ()
        validate_market_frame("holder_number", frame, provider="tushare-auxiliary")
        iso_cutoff = request.trade_date.isoformat()
        wanted = set(request.requested_codes)
        rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()  # 去重 (code, end_date)，Tushare 可能返回同股同期重复行
        for _, item in frame.iterrows():
            code = str(item.get("ts_code") or "").split(".")[0]
            if len(code) != 6 or (wanted and code not in wanted):
                continue
            end_date = _iso_day(item.get("end_date"))
            if not (end_date and end_date <= iso_cutoff):
                continue
            key = (code, end_date)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            holder_num = _safe_int(item.get("holder_num"))
            rows.append({"code": code, "end_date": end_date, "holder_num": holder_num if holder_num is not None else 0})
        return rows


class _StateViews:
    """Current ORM loaders, kept typed and independent from legacy auxiliary tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _target(request: AuxiliaryDatasetRequest) -> str:
        return request.trade_date.isoformat()

    def limit_up_board(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(LimitUpBoard).where(LimitUpBoard.trade_date == self._target(request))).all()
        return {
            "limit_up": {item.code: _row(item) for item in rows if item.limit_type == "U"},
            "broken_limit_up": {item.code: _row(item) for item in rows if item.limit_type == "Z"},
        }

    def concept_sector(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        member_rows = self._session.scalars(
            select(ConceptSectorStock).where(
                ConceptSectorStock.code.in_(request.requested_codes),
                ConceptSectorStock.trade_date == self._target(request),
            )
        ).all()
        sector_codes = {item.sector_code for item in member_rows}
        sectors = {
            item.sector_code: _row(item)
            for item in self._session.scalars(
                select(ConceptSector).where(
                    ConceptSector.sector_code.in_(sector_codes),
                    ConceptSector.trade_date == self._target(request),
                )
            ).all()
        }
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for member in member_rows:
            if sector := sectors.get(member.sector_code):
                grouped[member.code].append(sector)
        return {"concept_sectors": dict(grouped)}

    def concept_sector_stock(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        del request
        return {}

    def capital_flow(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(CapitalFlow).where(CapitalFlow.code.in_(request.requested_codes), CapitalFlow.trade_date == self._target(request))).all()
        return {"capital_flows": {item.code: _row(item) for item in rows}}

    def dragon_tiger(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        target = self._target(request)
        rows = self._session.scalars(
            select(DragonTiger).where(DragonTiger.trade_date == target)
        ).all()
        wanted = set(request.requested_codes)
        return {
            "dragon_tigers": {
                item.code: _row(item)
                for item in rows
                if not wanted or item.code in wanted
            },
            "_coverage": {
                "trade_date_covered": bool(rows),
                "covered_trade_date": target if rows else None,
            },
        }

    def margin_detail(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(MarginDetail).where(MarginDetail.code.in_(request.requested_codes), MarginDetail.trade_date == self._target(request))).all()
        return {"margin_details": {item.code: _row(item) for item in rows}}

    def hsgt_flow(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        item = self._session.scalar(select(HsgtFlow).where(HsgtFlow.trade_date == self._target(request)))
        return {"hsgt_flow": None if item is None else {"north_money": item.north_money, "south_money": item.south_money, "hgt": item.hgt, "sgt": item.sgt}}

    def hsgt_top10(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(HsgtTop10).where(HsgtTop10.code.in_(request.requested_codes), HsgtTop10.trade_date == self._target(request))).all()
        return {"hsgt_top10": {item.code: {"rank": item.rank, "amount": item.amount, "net_amount": item.net_amount, "buy": item.buy} for item in rows}}

    def pledge_stat(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(PledgeStat).where(PledgeStat.code.in_(request.requested_codes))).all()
        latest: dict[str, Any] = {}
        for item in rows:
            if item.code not in latest or str(item.end_date) > str(latest[item.code].end_date):
                latest[item.code] = item
        return {"pledges": {code: _row(item) for code, item in latest.items()}}

    def broker_recommend(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        month = request.trade_date.strftime("%Y%m")
        rows = self._session.execute(
            select(BrokerRecommend.code, func.count().label("count"))
            .where(BrokerRecommend.code.in_(request.requested_codes), BrokerRecommend.month == month)
            .group_by(BrokerRecommend.code)
        ).all()
        return {"broker_recs": {str(code): int(count) for code, count in rows}}

    def fina_indicator(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        target = _compact_day(request.trade_date)
        rows = self._session.scalars(
            select(FinaIndicator).where(FinaIndicator.code.in_(request.requested_codes), FinaIndicator.ann_date <= target)
        ).all()
        latest: dict[str, Any] = {}
        for item in rows:
            if item.code not in latest or str(item.end_date) > str(latest[item.code].end_date):
                latest[item.code] = item
        return {"finas": {
            code: {
                "roe": item.roe, "gross_margin": item.grossprofit_margin,
                "net_profit_yoy": item.netprofit_yoy, "revenue_yoy": item.or_yoy,
                "netprofit_margin": item.netprofit_margin, "eps": item.eps,
                "bps": item.bps, "end_date": item.end_date, "ann_date": item.ann_date,
            }
            for code, item in latest.items()
        }}

    def auction(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(AuctionData).where(AuctionData.code.in_(request.requested_codes), AuctionData.trade_date == self._target(request))).all()
        return {"auctions": {item.code: _row(item) for item in rows}}

    def holder_number(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(HolderNumber).where(HolderNumber.code.in_(request.requested_codes)).order_by(HolderNumber.end_date.desc())).all()
        grouped: dict[str, list[Any]] = defaultdict(list)
        for item in rows:
            if len(grouped[item.code]) < 2:
                grouped[item.code].append(item)
        result: dict[str, dict[str, Any]] = {}
        for code, items in grouped.items():
            latest = items[0]
            previous = items[1].holder_num if len(items) > 1 else 0
            change = round((latest.holder_num - previous) / previous * 100, 2) if previous else 0.0
            result[code] = {
                "holder_num": latest.holder_num,
                "holder_num_end_date": latest.end_date,
                "holder_num_prev": previous,
                "holder_num_change_pct": change,
                "holder_num_decreasing": change < -3,
            }
        return {"holder_numbers": result}


def build_standard_state_adapters(
    *,
    connection: Connection,
    session: Session,
    provider: TushareStateProvider | None,
) -> Mapping[str, StandardAuxiliaryDatasetAdapters]:
    """Return the frozen state adapter bundle with no dataset switch at runtime.

    The mapping is built once at the composition root.  Each descriptor receives a
    concrete injected fetch callable, repository, and loader; descriptors and domain
    handlers neither acquire a provider nor inspect a global dispatcher.
    """
    views = _StateViews(session)

    def unavailable(_: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        raise RuntimeError("state refresh requested without an application-injected provider")

    fetch = (lambda name: unavailable if provider is None else getattr(provider, name))
    bindings: tuple[tuple[str, StateFetch, StateLoad], ...] = (
        ("limit_up_board", fetch("limit_up_board"), views.limit_up_board),
        ("concept_sector", fetch("concept_sector"), views.concept_sector),
        ("concept_sector_stock", fetch("concept_sector_stock"), views.concept_sector_stock),
        ("capital_flow", fetch("capital_flow"), views.capital_flow),
        ("dragon_tiger", fetch("dragon_tiger"), views.dragon_tiger),
        ("margin_detail", fetch("margin_detail"), views.margin_detail),
        ("hsgt_flow", fetch("hsgt_flow"), views.hsgt_flow),
        ("hsgt_top10", fetch("hsgt_top10"), views.hsgt_top10),
        ("pledge_stat", fetch("pledge_stat"), views.pledge_stat),
        ("broker_recommend", fetch("broker_recommend"), views.broker_recommend),
        ("fina_indicator", fetch("fina_indicator"), views.fina_indicator),
        ("auction", fetch("auction"), views.auction),
        ("holder_number", fetch("holder_number"), views.holder_number),
    )
    adapters: dict[str, StandardAuxiliaryDatasetAdapters] = {}
    for key, fetch_records, load_view in bindings:
        entry = AUXILIARY_DATASET_BY_KEY[key]
        adapters[key] = StandardAuxiliaryDatasetAdapters(
            fetcher=_CallableFetcher(fetch_records),
            normalizer=_IdentityNormalizer(),
            repository=SQLAlchemyStateRepository(connection=connection, entry=entry),
            loader=_CallableLoader(entry.view_key, load_view),
        )
    return adapters


__all__ = [
    "SQLAlchemyStateRepository",
    "TushareStateClientPort",
    "TushareStateProvider",
    "build_standard_state_adapters",
]
