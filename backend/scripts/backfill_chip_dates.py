"""Re-pull cyq_perf for given trade dates and overwrite chip_* (writes NULL-free real values only).

  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py 2026-09-22 2026-09-23 ...
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --zero-days-since 2026-09-01
  # 冻结日（筹码与前一交易日整列相同）：先清空当日 chip_* 再重拉，上游没有当日数据就保持 NULL
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --overwrite 2026-07-31 2026-08-03 ...
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --overwrite --frozen-days-since 2026-04-30
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --frozen-days-since 2025-01-01 --frozen-days-until 2025-12-31 --list-only
"""
from __future__ import annotations

import argparse
import time

from sqlalchemy import text

from app.core.provider_quota import BACKFILL_BUDGET_FRACTION, backfill_budget_check, flush
from app.db.session import SessionLocal
from app.services.data_gap_remediation_service import _backfill_chip_date
from app.services.tushare_transport import TushareProviderError

# cyq_perf 路由会落到的上游。quantdata 是按天限额（42901 token daily limit），promax 是代理连接池瞬时
# 耗尽（实测见 app/core/promax_window.py）。2026-10-19 起：只要还有一家可用就继续；quantdata 报日限后
# 路由自动只走 promax，节奏由传输层的 promax 窗口令牌桶控制；promax 限流/熔断时等冷却到点再重试当天。
CHIP_PROVIDERS = ("promax", "quantdata")
TRANSIENT = {"http_rate_limit", "circuit_open", "timeout", "network", "http_server"}


def usable_providers(fraction: float) -> tuple[list[str], list[str]]:
    ok, blocked = [], []
    for p in CHIP_PROVIDERS:
        good, reason = backfill_budget_check((p,), fraction)
        (ok if good else blocked).append(p if good else reason)
    return ok, blocked


def transient_wait(exc: TushareProviderError) -> float | None:
    """全部失败都是瞬时类（promax 限流/熔断等）或日限类时，返回应等待的秒数；有别的硬失败返回 None。"""
    from app.core import promax_window
    from app.core.shared_breaker import breaker_namespace, shared_open_until

    cats = {f.category.value for f in exc.failures}
    hard = {c for c in cats if c not in TRANSIENT and c not in ("business_rate_limit", "quota_exhausted")}
    if hard or not any(c in TRANSIENT for c in cats):
        return None
    until = shared_open_until(f"{breaker_namespace()}:tushare:promax:cyq_perf") - time.time()
    hinted = max([f.retry_after_seconds or 0 for f in exc.failures if f.provider == "promax"] or [0])
    wait = max(until, hinted)
    return max(5.0, wait) if wait > 0 else promax_window.DEFAULT_COOLDOWN_SECONDS


def zero_days(db, since: str) -> list[str]:
    rows = db.execute(text(
        "SELECT trade_date, COUNT(*) n, SUM(chip_cost_50 IS NULL OR chip_cost_50 = 0) z FROM daily_kbars "
        "WHERE trade_date >= :s GROUP BY trade_date HAVING z > 0.05 * n ORDER BY trade_date"), {"s": since}).all()
    return [str(r[0]) for r in rows]


def frozen_days(db, since: str, ratio: float = 0.9, until: str | None = None) -> list[str]:
    """Trade dates in [since, until] whose chip_* equal the previous trade date's for > ratio of stocks."""
    from app.services.data_freshness import chip_history_dates

    days = chip_history_dates(db, since, until)
    out = []
    for prev, d in zip(days, days[1:]):
        n, same = db.execute(text(
            "SELECT COUNT(*), SUM(a.chip_cost_50 = b.chip_cost_50 AND a.chip_cost_15 <=> b.chip_cost_15 "
            "AND a.chip_cost_85 <=> b.chip_cost_85 AND a.chip_weight_avg <=> b.chip_weight_avg) "
            "FROM daily_kbars a JOIN daily_kbars b ON b.code = a.code AND b.trade_date = :p "
            "WHERE a.trade_date = :d AND a.chip_cost_50 IS NOT NULL"), {"p": prev, "d": d}).one()
        if n and int(same or 0) > ratio * int(n):
            out.append(d)
    return out


_NULL_CHIPS = ("UPDATE daily_kbars SET chip_cost_15=NULL, chip_cost_50=NULL, chip_cost_85=NULL, "
               "chip_weight_avg=NULL, chip_winner_rate=NULL, updated_at=UTC_TIMESTAMP() WHERE trade_date=:d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--zero-days-since")
    ap.add_argument("--frozen-days-since", help="自动选出筹码与前一交易日 >90%% 相同的日期")
    ap.add_argument("--frozen-days-until", help="与 --frozen-days-since 搭配：只选到该日（含）为止的冻结日")
    ap.add_argument("--list-only", action="store_true", help="只打印要补的日期，不拉取不写库")
    ap.add_argument("--overwrite", action="store_true",
                    help="重拉前先把当日 chip_* 全部置 NULL（与重拉同一事务；上游报错则回滚）。冻结日必须带")
    ap.add_argument("--max-transient-retries", type=int, default=30,
                    help="同一天连续遇到 promax 限流/熔断时最多等几轮冷却再重试（默认 30）")
    ap.add_argument("--budget-fraction", type=float, default=BACKFILL_BUDGET_FRACTION,
                    help="单个数据源当日额度用到该比例即停（默认 0.6），额度上限读 ASHARE_TUSHARE_<P>_DAILY_QUOTA")
    args = ap.parse_args()
    with SessionLocal() as db:
        dates = list(args.dates) or (zero_days(db, args.zero_days_since) if args.zero_days_since else [])
        if args.frozen_days_since:
            dates = sorted(set(dates) | set(frozen_days(db, args.frozen_days_since, until=args.frozen_days_until)))
        print(f"dates ({len(dates)}):", dates, flush=True)
        if args.list_only:
            return
        i, retries = 0, 0
        while i < len(dates):
            d = dates[i]
            usable, blocked = usable_providers(args.budget_fraction)
            if not usable:
                print(f"STOP before {d}: {'; '.join(blocked)}; 剩余 {len(dates) - i} 天未补: {dates[i:]}", flush=True)
                break
            try:
                if args.overwrite:
                    db.execute(text(_NULL_CHIPS), {"d": d})  # committed together with the re-pull below
                out = _backfill_chip_date(db, d)
            except TushareProviderError as exc:
                db.rollback()
                wait = transient_wait(exc)
                if wait is None or retries >= args.max_transient_retries:
                    print(f"STOP at {d}: 上游失败 {exc}; 剩余 {len(dates) - i} 天未补: {dates[i:]}", flush=True)
                    break
                retries += 1
                print(f"WAIT {wait:.0f}s at {d} ({retries}/{args.max_transient_retries}): {exc}", flush=True)
                time.sleep(wait)
                continue
            retries = 0
            # whatever cyq_perf did not cover stays missing: 0 -> NULL (never 0-as-missing)
            res = db.execute(text(
                "UPDATE daily_kbars SET chip_cost_15=NULL, chip_cost_50=NULL, chip_cost_85=NULL, "
                "chip_weight_avg=NULL, chip_winner_rate=NULL, updated_at=UTC_TIMESTAMP() "
                "WHERE trade_date=:d AND chip_cost_50 = 0"), {"d": d})
            db.commit()
            print(d, out, "zero->NULL:", res.rowcount, flush=True)
            i += 1
    flush()


if __name__ == "__main__":
    main()
