"""
Backfill real daily K-line data for the local unified market pool.

The default scope is the base-filter eligible universe from the local
UnifiedQuote pool. Data is fetched from real external sources only; short
listing history is persisted and reported, never padded with synthetic bars.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import DailyBar
from app.services.data_sources import enabled_sources
from app.services.market_data import _fetch_kline, _kline_sources, _select_candidates
from app.services.market_pool import (
    _latest_allowed_bar_date,
    _load_cached_bars_summary,
    _load_cached_quotes,
    _persist_normalized_bars,
    _persist_raw_bars,
    _record_source_health,
)


PROGRESS_FILE = Path(__file__).resolve().parent / ".backfill_daily_bars_progress.jsonl"
DEFAULT_SOURCE_ORDER = ["tencent", "tickflow", "thsdk", "eastmoney", "akshare", "baostock", "tushare"]


@dataclass
class FetchResult:
    status: str
    code: str
    name: str
    source: str | None = None
    bar_count: int = 0
    latest_trade_date: str | None = None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill real A-share daily K-line data into the local pool.")
    parser.add_argument(
        "--scope",
        choices=["eligible", "all-quotes"],
        default="eligible",
        help="eligible = base-filter eligible stocks; all-quotes = every active unified quote.",
    )
    parser.add_argument("--days", type=int, default=None, help="K-line count requested from each source.")
    parser.add_argument("--min-bars", type=int, default=30, help="Minimum bars required for recommendation readiness.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N stocks after filtering.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N stocks in the selected universe.")
    parser.add_argument("--commit-interval", type=int, default=25, help="Commit every N processed stocks.")
    parser.add_argument("--rate-limit", type=float, default=0.08, help="Sleep seconds after each fetched stock.")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCE_ORDER),
        help="Comma-separated K-line source order. Disabled sources are ignored.",
    )
    parser.add_argument("--force", action="store_true", help="Refetch even when local bars are already ready.")
    parser.add_argument("--reset-progress", action="store_true", help="Ignore and remove the previous progress file.")
    return parser.parse_args()


def load_progress(reset: bool) -> set[str]:
    if reset:
        PROGRESS_FILE.unlink(missing_ok=True)
        return set()
    if not PROGRESS_FILE.exists():
        return set()
    done: set[str] = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("status") in {"ready", "persisted", "short_history"}:
            code = str(item.get("code") or "")
            if code:
                done.add(code)
    return done


def append_progress(result: FetchResult) -> None:
    payload = {
        "status": result.status,
        "code": result.code,
        "name": result.name,
        "source": result.source,
        "bar_count": result.bar_count,
        "latest_trade_date": result.latest_trade_date,
        "error": result.error,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with PROGRESS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def sorted_sources(db, requested: list[str]) -> list[str]:
    settings = get_settings()
    active = set(enabled_sources(db, "kline", _kline_sources(settings)))
    ordered = [source for source in requested if source in active]
    return ordered or [source for source in _kline_sources(settings) if source in active]


def load_universe(db, scope: str) -> list[dict[str, Any]]:
    quotes = _load_cached_quotes(db, skip_metrics=True)
    if scope == "eligible":
        return _select_candidates(quotes, len(quotes))
    return quotes


def latest_bar_date(bars: list[dict[str, Any]]) -> str | None:
    if not bars:
        return None
    return max(str(bar.get("trade_date") or "") for bar in bars)


def bars_ready(bar_count: int, latest: str | None, min_bars: int, latest_allowed: str) -> bool:
    return bar_count >= min_bars and bool(latest) and str(latest) >= latest_allowed


def source_error_summary(errors: list[str]) -> str:
    if not errors:
        return "no source returned usable bars"
    return " | ".join(errors[:4])


def fetch_and_persist(
    db,
    quote: dict[str, Any],
    sources: list[str],
    settings: Any,
    min_bars: int,
    latest_allowed: str,
) -> FetchResult:
    code = str(quote.get("code") or "")
    name = str(quote.get("name") or code)
    errors: list[str] = []
    best_short: tuple[str, list[dict[str, Any]]] | None = None

    for source in sources:
        start = time.perf_counter()
        try:
            bars = _fetch_kline(source, quote, settings)
            latency_ms = (time.perf_counter() - start) * 1000
            cleaned = [bar for bar in bars if str(bar.get("trade_date") or "") <= latest_allowed]
            latest = latest_bar_date(cleaned)
            if cleaned and (best_short is None or len(cleaned) > len(best_short[1])):
                best_short = (source, cleaned)
            if bars_ready(len(cleaned), latest, min_bars, latest_allowed):
                _persist_raw_bars(db, source, cleaned)
                _persist_normalized_bars(db, cleaned)
                _record_source_health(db, source, "kline", "success", latency_ms=latency_ms)
                return FetchResult("persisted", code, name, source, len(cleaned), latest)
            reason = (
                f"{source}: {len(cleaned)} bars latest={latest or 'none'}"
                if cleaned
                else f"{source}: empty"
            )
            _record_source_health(db, source, "kline", "empty", error=reason, latency_ms=latency_ms)
            errors.append(reason)
        except Exception as exc:  # noqa: BLE001 - report and fallback to the next real source
            latency_ms = (time.perf_counter() - start) * 1000
            message = f"{source}: {exc}"
            _record_source_health(db, source, "kline", "failure", error=str(exc), latency_ms=latency_ms)
            errors.append(message)

    if best_short is not None:
        source, bars = best_short
        _persist_raw_bars(db, source, bars)
        _persist_normalized_bars(db, bars)
        return FetchResult(
            "short_history",
            code,
            name,
            source,
            len(bars),
            latest_bar_date(bars),
            source_error_summary(errors),
        )
    return FetchResult("failed", code, name, error=source_error_summary(errors))


def main() -> int:
    args = parse_args()
    settings = get_settings()
    fetch_days = int(args.days or settings.market_kline_days)
    saved_days = settings.market_kline_days
    settings.market_kline_days = max(fetch_days, args.min_bars)
    requested_sources = [item.strip().lower() for item in args.sources.split(",") if item.strip()]
    latest_allowed = _latest_allowed_bar_date()
    done_codes = load_progress(args.reset_progress)

    db = SessionLocal()
    try:
        sources = sorted_sources(db, requested_sources)
        universe = load_universe(db, args.scope)
        if args.offset:
            universe = universe[args.offset :]
        if args.limit > 0:
            universe = universe[: args.limit]
        codes = [str(item.get("code") or "") for item in universe if item.get("code")]
        summaries = _load_cached_bars_summary(db, codes, args.min_bars)

        to_process: list[dict[str, Any]] = []
        ready_existing = 0
        for quote in universe:
            code = str(quote.get("code") or "")
            summary = summaries.get(code, {})
            if not args.force and code in done_codes:
                ready_existing += 1
                continue
            if not args.force and bars_ready(
                int(summary.get("bar_count") or 0),
                summary.get("latest_trade_date"),
                args.min_bars,
                latest_allowed,
            ):
                ready_existing += 1
                continue
            to_process.append(quote)

        print("Daily K backfill")
        print(f"  scope={args.scope} universe={len(universe)} ready_or_progress={ready_existing} pending={len(to_process)}")
        print(f"  latest_allowed={latest_allowed} days={settings.market_kline_days} min_bars={args.min_bars}")
        print(f"  sources={','.join(sources) if sources else 'none'}")
        if not sources:
            print("No enabled K-line sources. Abort.")
            return 2

        counters = {"persisted": 0, "short_history": 0, "failed": 0}
        started = time.perf_counter()
        for index, quote in enumerate(to_process, start=1):
            result = fetch_and_persist(db, quote, sources, settings, args.min_bars, latest_allowed)
            counters[result.status] = counters.get(result.status, 0) + 1
            append_progress(result)

            if result.status == "failed":
                print(f"[{index}/{len(to_process)}] failed {result.code} {result.name}: {result.error}")
            elif result.status == "short_history":
                print(
                    f"[{index}/{len(to_process)}] short {result.code} {result.name} "
                    f"{result.source} bars={result.bar_count} latest={result.latest_trade_date}"
                )

            if index % max(1, args.commit_interval) == 0:
                db.commit()
                elapsed = max(0.001, time.perf_counter() - started)
                print(
                    f"progress {index}/{len(to_process)} | "
                    f"persisted={counters.get('persisted', 0)} short={counters.get('short_history', 0)} "
                    f"failed={counters.get('failed', 0)} | {index / elapsed:.2f} stocks/s"
                )
            if args.rate_limit > 0:
                time.sleep(args.rate_limit)

        db.commit()
        elapsed = time.perf_counter() - started
        print("Done")
        print(
            f"  processed={len(to_process)} persisted={counters.get('persisted', 0)} "
            f"short_history={counters.get('short_history', 0)} failed={counters.get('failed', 0)} "
            f"elapsed={elapsed:.1f}s"
        )
        if counters.get("failed", 0) == 0 and counters.get("short_history", 0) == 0:
            PROGRESS_FILE.unlink(missing_ok=True)
    finally:
        settings.market_kline_days = saved_days
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
