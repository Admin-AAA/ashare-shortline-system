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
    DailyKbar,
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


def _days_before(iso_day: str, days: int) -> str:
    from datetime import timedelta

    return (date.fromisoformat(iso_day) - timedelta(days=days)).isoformat()


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


def _parse_observed(observed: Any) -> date | datetime | None:
    if isinstance(observed, (date, datetime)):
        return observed
    if observed:
        try:
            return date.fromisoformat(_iso_day(observed))
        except ValueError:
            return None
    return None


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
        if "code" in table.c and request.requested_codes:
            # 单条 GROUP BY code 同时得到 count / max(freshness) / 覆盖代码集（原 3 条查询）。
            # sum(count)=count(*)，max(各组 max)=max（均忽略 NULL），组键集合=distinct code。
            count = 0
            observed = None
            covered_set: set[str] = set()
            for code_v, cnt_v, max_v in self._connection.execute(
                select(table.c.code, func.count(), func.max(freshness)).where(*conditions).group_by(table.c.code)
            ):
                count += int(cnt_v or 0)
                if max_v is not None and (observed is None or max_v > observed):
                    observed = max_v
                if str(code_v or "").strip():
                    covered_set.add(str(code_v))
            return AuxiliaryRepositorySnapshot(count, frozenset(covered_set), _parse_observed(observed))
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
        return AuxiliaryRepositorySnapshot(count, covered, _parse_observed(observed))

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
        seen_keys: set[tuple[str, str, str]] = set()
        for sector in sectors:
            frame = self._client.ths_member(ts_code=sector.sector_code)
            if frame is None or frame.empty:
                continue
            for _, item in frame.iterrows():
                code = str(item.get("con_code") or "").split(".")[0]
                if len(code) == 6:
                    natural_key = (str(sector.sector_code), code, request.trade_date.isoformat())
                    if natural_key in seen_keys:
                        continue
                    seen_keys.add(natural_key)
                    rows.append({
                        "sector_code": natural_key[0],
                        "code": code,
                        "name": str(item.get("con_name") or ""),
                        "trade_date": natural_key[2],
                    })
        return rows

    def capital_flow(self, request: AuxiliaryDatasetRequest) -> Sequence[Mapping[str, Any]]:
        # 单股资金流向按 trade_date 批量一次拉全市场（~5500 行、<1s），再按
        # requested_codes 过滤；逐股循环会触发 ~4948 次 Tushare 请求 + 429 限流。
        # 本路径是资金流向唯一的定时写入口（scripts/daily_data_cron.py 从未排进调度），
        # 所以累计窗口必须在这里就地算好（L-166）。
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
            # 单位契约：Tushare moneyflow 各 amount 字段是万元，转亿用 /1e4（L-059）。
            super_large = round((_number(latest.get("buy_elg_amount")) - _number(latest.get("sell_elg_amount"))) / 1e4, 4)
            large = round((_number(latest.get("buy_lg_amount")) - _number(latest.get("sell_lg_amount"))) / 1e4, 4)
            records.append({
                "code": code,
                "trade_date": _iso_day(latest.get("trade_date") or target),
                # 主力 = 超大单 + 大单，与 backfill_moneyflow_concept 写入的全部历史同口径。
                # 旧实现用 net_mf_amount（另一套口径），使 08-17 起的行与历史不可比（L-166）。
                "main_net_inflow_yi": round(super_large + large, 4),
                "super_large_net_inflow_yi": super_large,
                "large_net_inflow_yi": large,
                "medium_net_inflow_yi": round((_number(latest.get("buy_md_amount")) - _number(latest.get("sell_md_amount"))) / 1e4, 4),
                "small_net_inflow_yi": round((_number(latest.get("buy_sm_amount")) - _number(latest.get("sell_sm_amount"))) / 1e4, 4),
                # 旧实现把 net_mf_vol（成交量，手）写成占比，规则 ratio>8 几乎恒真；
                # 历史行一律为 NULL，这里保持同口径，需要时另行接入成交额重算。
                "main_net_inflow_ratio": None,
            })
        self._attach_cumulative_inflows(records, target_day=_iso_day(target))
        return records

    def _attach_cumulative_inflows(self, records: list[dict[str, Any]], *, target_day: str) -> None:
        """按每只股票自己最近的历史行算 3/5/10 日累计主力净流入（含当日）。

        与 compute_cumulative_inflows 同口径：窗口取该股票最近 N 条记录，不按日历补零。
        """
        history: dict[str, list[float]] = defaultdict(list)
        if self._session is not None and records:
            codes = sorted({str(record["code"]) for record in records})
            for start in range(0, len(codes), 1000):
                chunk = codes[start:start + 1000]
                rows = self._session.execute(
                    select(CapitalFlow.code, CapitalFlow.trade_date, CapitalFlow.main_net_inflow_yi)
                    .where(
                        CapitalFlow.code.in_(chunk),
                        CapitalFlow.trade_date < target_day,
                        CapitalFlow.trade_date >= _days_before(target_day, 45),
                    )
                    .order_by(CapitalFlow.code, CapitalFlow.trade_date.desc())
                ).all()
                for code, _trade_date, value in rows:
                    if len(history[code]) < 9:
                        history[code].append(float(value or 0.0))
        for record in records:
            window = [float(record["main_net_inflow_yi"] or 0.0), *history.get(str(record["code"]), [])]
            record["cumulative_inflow_3d_yi"] = round(sum(window[:3]), 4)
            record["cumulative_inflow_5d_yi"] = round(sum(window[:5]), 4)
            record["cumulative_inflow_10d_yi"] = round(sum(window[:10]), 4)

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
            ann_date = _iso_day(item.get("ann_date"))
            # 只保留截至 trade_date 已公告的期末数据：end_date 早于 trade_date 不代表已披露（L-160）。
            if not (end_date and end_date <= iso_cutoff and ann_date and ann_date <= iso_cutoff):
                continue
            key = (code, end_date)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            holder_num = _safe_int(item.get("holder_num"))
            rows.append({
                "code": code,
                "end_date": end_date,
                "ann_date": ann_date,
                "holder_num": holder_num if holder_num is not None else 0,
            })
        return rows



# ---- 回放专用整表预加载（默认关闭）：fina_indicator / holder_numbers 每日都要全量按 code 取历史，
# 回放期间这两张表不变，进程内读一次、按日在内存里做与 SQL 完全相同的 as-of 过滤。
# (code, end_date) 均有唯一键，"取最新"不依赖返回顺序 → 结果与逐日 SQL 一致。
_REPLAY_PRELOAD: dict[str, Any] = {"on": False}


def enable_replay_preload() -> None:
    _REPLAY_PRELOAD["on"] = True


def _preloaded(session: Session, key: str):
    hit = _REPLAY_PRELOAD.get(key)
    if hit is not None:
        return hit
    by_code: dict[str, list[tuple]] = defaultdict(list)
    if key == "fina":
        t = FinaIndicator
        stmt = select(t.code, t.end_date, t.ann_date, t.roe, t.grossprofit_margin, t.netprofit_yoy,
                      t.or_yoy, t.netprofit_margin, t.eps, t.bps)
    else:
        t = HolderNumber
        stmt = select(t.code, t.end_date, t.ann_date, t.holder_num)
    for row in session.execute(stmt):
        by_code[row[0]].append(tuple(row))
    _REPLAY_PRELOAD[key] = dict(by_code)
    return _REPLAY_PRELOAD[key]

class _StateViews:
    """Current ORM loaders, kept typed and independent from legacy auxiliary tables."""

    def __init__(self, session: Session, *, as_of_date: date | None = None) -> None:
        self._session = session
        self._as_of_date = as_of_date.isoformat() if as_of_date is not None else None

    @staticmethod
    def _target(request: AuxiliaryDatasetRequest) -> str:
        return request.trade_date.isoformat()

    def _decision_visible_date(self, model: Any, request: AuxiliaryDatasetRequest) -> str | None:
        """盘后 17:35 决策时刻可见的最近交易日（L-163/L-167）。

        龙虎榜约 21:00 入库、两融明细次日披露，17:35 正式推荐时拿不到当日数据。实盘与回放
        统一只读取严格早于信号日的最近一个有数据交易日，避免回放看到实盘看不到的信息。
        """
        latest = self._session.scalar(
            select(func.max(model.trade_date)).where(model.trade_date < self._target(request))
        )
        if latest is None:
            return None
        return latest.isoformat() if hasattr(latest, "isoformat") else str(latest)

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
        target = self._decision_visible_date(DragonTiger, request)
        rows = (
            self._session.scalars(select(DragonTiger).where(DragonTiger.trade_date == target)).all()
            if target
            else []
        )
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
                # 读的是前一交易日（17:35 决策时刻可见的最新一期），覆盖校验要按「为哪个信号日
                # 服务」判定；只有恰好是前一交易日才算覆盖，入库断档时仍判缺失（L-170）。
                "decision_visible_for": (
                    self._target(request) if rows and target == self._previous_trading_day(request) else None
                ),
            },
        }

    def _previous_trading_day(self, request: AuxiliaryDatasetRequest) -> str | None:
        try:
            latest = self._session.scalar(
                select(func.max(DailyKbar.trade_date)).where(DailyKbar.trade_date < self._target(request))
            )
        except Exception:  # noqa: BLE001 — 无日K表（如单测内存库）时不认定覆盖，保守判缺失
            return None
        if latest is None:
            return None
        return latest.isoformat() if hasattr(latest, "isoformat") else str(latest)

    def margin_detail(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        target = self._decision_visible_date(MarginDetail, request)
        rows = (
            self._session.scalars(select(MarginDetail).where(MarginDetail.code.in_(request.requested_codes), MarginDetail.trade_date == target)).all()
            if target
            else []
        )
        return {"margin_details": {item.code: _row(item) for item in rows}}

    def hsgt_flow(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        item = self._session.scalar(select(HsgtFlow).where(HsgtFlow.trade_date == self._target(request)))
        return {"hsgt_flow": None if item is None else {"north_money": item.north_money, "south_money": item.south_money, "hgt": item.hgt, "sgt": item.sgt}}

    def hsgt_top10(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        rows = self._session.scalars(select(HsgtTop10).where(HsgtTop10.code.in_(request.requested_codes), HsgtTop10.trade_date == self._target(request))).all()
        return {"hsgt_top10": {item.code: {"rank": item.rank, "amount": item.amount, "net_amount": item.net_amount, "buy": item.buy} for item in rows}}

    def pledge_stat(self, request: AuxiliaryDatasetRequest) -> Mapping[str, Any]:
        statement = select(PledgeStat).where(PledgeStat.code.in_(request.requested_codes))
        if self._as_of_date is not None:
            statement = statement.where(PledgeStat.end_date <= self._as_of_date)
        rows = self._session.scalars(statement).all()
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
        if _REPLAY_PRELOAD["on"]:
            data = _preloaded(self._session, "fina")
            out: dict[str, Any] = {}
            for code in dict.fromkeys(request.requested_codes):
                best = None
                for r in data.get(code, ()):
                    if r[2] is not None and r[2] <= target and (best is None or str(r[1]) > str(best[1])):
                        best = r
                if best is not None:
                    out[code] = {
                        "roe": best[3], "gross_margin": best[4], "net_profit_yoy": best[5],
                        "revenue_yoy": best[6], "netprofit_margin": best[7], "eps": best[8],
                        "bps": best[9], "end_date": best[1], "ann_date": best[2],
                    }
            return {"finas": out}
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
        if _REPLAY_PRELOAD["on"]:
            data = _preloaded(self._session, "holder")
            asof = self._as_of_date
            res: dict[str, dict[str, Any]] = {}
            for code in dict.fromkeys(request.requested_codes):
                rows_c = [
                    r for r in data.get(code, ())
                    if asof is None or (r[1] <= asof and r[2] is not None and r[2] <= asof)
                ]
                if not rows_c:
                    continue
                rows_c.sort(key=lambda r: r[1], reverse=True)
                latest = rows_c[0]
                previous = rows_c[1][3] if len(rows_c) > 1 else 0
                change = round((latest[3] - previous) / previous * 100, 2) if previous else 0.0
                res[code] = {
                    "holder_num": latest[3],
                    "holder_num_end_date": latest[1],
                    "holder_num_prev": previous,
                    "holder_num_change_pct": change,
                    "holder_num_decreasing": change < -3,
                }
            return {"holder_numbers": res}
        statement = select(HolderNumber).where(HolderNumber.code.in_(request.requested_codes))
        if self._as_of_date is not None:
            # 可见性按公告日判定；缺公告日的行无法证明当时已披露，一律不可见（L-160）。
            statement = statement.where(
                HolderNumber.end_date <= self._as_of_date,
                HolderNumber.ann_date.is_not(None),
                HolderNumber.ann_date <= self._as_of_date,
            )
        rows = self._session.scalars(statement.order_by(HolderNumber.end_date.desc())).all()
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
    as_of_date: date | None = None,
) -> Mapping[str, StandardAuxiliaryDatasetAdapters]:
    """Return the frozen state adapter bundle with no dataset switch at runtime.

    The mapping is built once at the composition root.  Each descriptor receives a
    concrete injected fetch callable, repository, and loader; descriptors and domain
    handlers neither acquire a provider nor inspect a global dispatcher.
    """
    views = _StateViews(session, as_of_date=as_of_date)

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
