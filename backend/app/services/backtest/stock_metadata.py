"""Point-in-time stock metadata contracts for research snapshots.

The current ``stocks`` table is a mutable reference table.  Historical research
must use an explicit interval source and fail closed when an instrument has no
matching row for the signal date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.engine.backtest import canonical_fingerprint


CURRENT_STATIC = "current_static"
POINT_IN_TIME = "point_in_time"
UNKNOWN = "unknown"


def _iso_date(value: str, field: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


@dataclass(frozen=True, slots=True)
class StockMetadataRecord:
    """One immutable metadata interval, inclusive at both boundaries."""

    code: str
    name: str
    industry: str | None
    market: str | None
    list_date: str | None
    is_st: bool
    effective_from: str
    effective_to: str | None
    source_version: str

    def __post_init__(self) -> None:
        if not str(self.code).strip():
            raise ValueError("code must be non-empty")
        if not str(self.source_version).strip():
            raise ValueError("source_version must be non-empty")
        start = _iso_date(self.effective_from, "effective_from")
        end = _iso_date(self.effective_to, "effective_to") if self.effective_to else None
        if end is not None and end < start:
            raise ValueError("effective_to must not precede effective_from")
        object.__setattr__(self, "code", str(self.code).strip())
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_to", end)
        if self.list_date:
            object.__setattr__(self, "list_date", _iso_date(self.list_date, "list_date"))

    def visible_on(self, signal_date: str) -> bool:
        target = _iso_date(signal_date, "signal_date")
        return self.effective_from <= target and (
            self.effective_to is None or target <= self.effective_to
        )

    def metadata_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name or self.code,
            "industry": self.industry or "未分类",
            "market": self.market,
            "list_date": self.list_date,
            "is_st": bool(self.is_st),
        }

    def canonical_payload(self) -> dict[str, Any]:
        return {
            **self.metadata_payload(),
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "source_version": self.source_version,
        }


@dataclass(frozen=True, slots=True)
class StockMetadataResolution:
    """Resolved metadata and its evidence identity for one signal date."""

    signal_date: str
    temporality: str
    source_version: str
    metadata_by_code: Mapping[str, Mapping[str, Any]]
    missing_codes: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        _iso_date(self.signal_date, "signal_date")
        if self.temporality not in {CURRENT_STATIC, POINT_IN_TIME, UNKNOWN}:
            raise ValueError("invalid stock metadata temporality")
        object.__setattr__(
            self,
            "metadata_by_code",
            MappingProxyType(
                {
                    str(code): MappingProxyType(dict(value))
                    for code, value in self.metadata_by_code.items()
                }
            ),
        )
        object.__setattr__(self, "missing_codes", tuple(sorted(set(self.missing_codes))))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "signal_date": self.signal_date,
            "temporality": self.temporality,
            "source_version": self.source_version,
            "metadata_by_code": {
                code: dict(value) for code, value in sorted(self.metadata_by_code.items())
            },
            "missing_codes": list(self.missing_codes),
        }


def _resolution(
    *,
    signal_date: str,
    temporality: str,
    source_version: str,
    metadata_by_code: Mapping[str, Mapping[str, Any]],
    missing_codes: Sequence[str],
) -> StockMetadataResolution:
    payload = {
        "signal_date": _iso_date(signal_date, "signal_date"),
        "temporality": temporality,
        "source_version": source_version,
        "metadata_by_code": {
            code: dict(value) for code, value in sorted(metadata_by_code.items())
        },
        "missing_codes": sorted(set(str(code) for code in missing_codes)),
    }
    return StockMetadataResolution(
        signal_date=payload["signal_date"],
        temporality=temporality,
        source_version=source_version,
        metadata_by_code=payload["metadata_by_code"],
        missing_codes=tuple(payload["missing_codes"]),
        fingerprint=canonical_fingerprint(payload),
    )


def resolve_stock_metadata_as_of(
    records: Sequence[StockMetadataRecord],
    codes: Sequence[str],
    signal_date: str,
) -> StockMetadataResolution:
    """Resolve one non-overlapping historical interval per requested code."""

    normalized_codes = tuple(sorted(set(str(code) for code in codes if str(code).strip())))
    matches: dict[str, StockMetadataRecord] = {}
    for code in normalized_codes:
        visible = [
            record for record in records if record.code == code and record.visible_on(signal_date)
        ]
        if len(visible) > 1:
            raise ValueError(f"overlapping stock metadata intervals for {code}")
        if visible:
            matches[code] = visible[0]
    missing = tuple(code for code in normalized_codes if code not in matches)
    metadata = {code: record.metadata_payload() for code, record in matches.items()}
    versions = sorted({record.source_version for record in matches.values()})
    source_version = "+".join(versions) if versions else "none"
    return _resolution(
        signal_date=signal_date,
        temporality=POINT_IN_TIME if not missing and normalized_codes else UNKNOWN,
        source_version=source_version,
        metadata_by_code=metadata,
        missing_codes=missing,
    )


def current_static_resolution(
    metadata_by_code: Mapping[str, Mapping[str, Any]],
    codes: Sequence[str],
    signal_date: str,
    *,
    source_version: str = "stocks/current-static/v1",
) -> StockMetadataResolution:
    normalized = tuple(sorted(set(str(code) for code in codes if str(code).strip())))
    selected = {code: dict(metadata_by_code[code]) for code in normalized if code in metadata_by_code}
    missing = tuple(code for code in normalized if code not in selected)
    return _resolution(
        signal_date=signal_date,
        temporality=CURRENT_STATIC,
        source_version=source_version,
        metadata_by_code=selected,
        missing_codes=missing,
    )


class StockMetadataProvider(Protocol):
    def resolve(self, codes: Sequence[str], signal_date: str) -> StockMetadataResolution:
        ...


class StockMetadataHistoryProvider:
    """Read-only adapter over ``stock_metadata_history`` interval rows."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._available: bool | None = None

    def resolve(self, codes: Sequence[str], signal_date: str) -> StockMetadataResolution:
        from app.models.tables import StockMetadataHistory

        normalized = tuple(sorted(set(str(code) for code in codes if str(code).strip())))
        if self._available is False:
            return _resolution(
                signal_date=signal_date,
                temporality=UNKNOWN,
                source_version="stock_metadata_history/unavailable",
                metadata_by_code={},
                missing_codes=normalized,
            )
        try:
            rows = self._session.scalars(
                select(StockMetadataHistory).where(
                    StockMetadataHistory.code.in_(normalized),
                    StockMetadataHistory.effective_from <= signal_date,
                    (StockMetadataHistory.effective_to.is_(None))
                    | (StockMetadataHistory.effective_to >= signal_date),
                )
            ).all()
        except SQLAlchemyError:
            # A missing table is an expected pre-migration state.  Cache the
            # negative capability so a long snapshot does not issue one failed
            # query per signal date.
            self._available = False
            return _resolution(
                signal_date=signal_date,
                temporality=UNKNOWN,
                source_version="stock_metadata_history/unavailable",
                metadata_by_code={},
                missing_codes=normalized,
            )
        self._available = True
        records = tuple(
            StockMetadataRecord(
                code=str(row.code),
                name=str(row.name or row.code),
                industry=row.industry,
                market=row.market,
                list_date=row.list_date,
                is_st=bool(row.is_st),
                effective_from=str(row.effective_from),
                effective_to=(str(row.effective_to) if row.effective_to else None),
                source_version=str(row.source_version),
            )
            for row in rows
        )
        return resolve_stock_metadata_as_of(records, normalized, signal_date)


__all__ = [
    "POINT_IN_TIME",
    "UNKNOWN",
    "StockMetadataHistoryProvider",
    "StockMetadataProvider",
    "StockMetadataRecord",
    "StockMetadataResolution",
    "current_static_resolution",
    "resolve_stock_metadata_as_of",
]
