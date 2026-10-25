"""回填 stk_limits（涨跌停价）历史缺口，只补缺失行（INSERT IGNORE，绝不覆盖已有行）。

背景（2026-10-25 审计 P1）：库里 2022 年只有 34 个交易日、2023 年只有 21 个，2022-01~2023-12 约 430 个交易日缺失；
2018~2021 整段没有。上游 stk_limit 实测 2019-07-03 起就能取到全市场（3671 行），所以可以补。

约束（不要抢日更的限流窗口 / quantdata 日额度）：
- 只走 promax provider（不碰 quantdata 日额度）；两次请求间隔 --sleep 秒（默认 8）；
- 17:25-18:45、20:55-21:50 自动暂停（日更 17:35 / 晚间补数 21:00），工作日 09:10-15:10 也暂停；
- 可断点续跑：已有 >= --min-rows 行的日期跳过；失败的日期稍后在下一轮重试。

用法（backend 目录）：
  .venv/bin/python scripts/backfill_stk_limits_history.py --start 2022-01-01 --end 2023-12-31
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.mysql import insert as mysql_insert  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import StkLimit  # noqa: E402

BLOCKED = ((dt.time(17, 25), dt.time(18, 45)), (dt.time(20, 55), dt.time(21, 50)))
MARKET_HOURS = (dt.time(9, 10), dt.time(15, 10))


def _blocked_now(now: dt.datetime) -> bool:
    t = now.time()
    if any(a <= t <= b for a, b in BLOCKED):
        return True
    return now.weekday() < 5 and MARKET_HOURS[0] <= t <= MARKET_HOURS[1]


def _wait_if_blocked() -> None:
    while _blocked_now(dt.datetime.now()):
        print(f"{dt.datetime.now():%H:%M:%S} 处于日更/交易时段，暂停 5 分钟", flush=True)
        time.sleep(300)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=8.0)
    ap.add_argument("--min-rows", type=int, default=3000)
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()

    from app.services.tushare_client import _build_provider_router

    router, _ = _build_provider_router()
    promax = router._providers["promax"]
    db = SessionLocal()
    # trade_calendar 表只有 2025-09 起；这里用中证500(000905.SH) 日线的日期当 2018 年以来的交易日历
    days = [r[0] for r in db.execute(text(
        "select trade_date from index_daily where ts_code='000905.SH' and trade_date between :s and :e order by trade_date"),
        {"s": a.start, "e": a.end})]
    days = [str(d)[:10] for d in days]
    for rnd in range(1, a.rounds + 1):
        have = {str(r[0])[:10]: r[1] for r in db.execute(text(
            "select trade_date, count(*) from stk_limits where trade_date between :s and :e group by 1"),
            {"s": a.start, "e": a.end})}
        todo = [d for d in days if have.get(d, 0) < a.min_rows]
        print(f"round {rnd}: {len(todo)}/{len(days)} 个交易日待补", flush=True)
        if not todo:
            break
        ok = 0
        for d in todo:
            _wait_if_blocked()
            compact = d.replace("-", "")
            try:
                env = promax.request("stk_limit", params={"trade_date": compact}, fields="")
                data = env.get("data") or {}
                fields, items = list(data.get("fields") or []), list(data.get("items") or [])
            except Exception as exc:  # noqa: BLE001 - 本轮跳过，下一轮重试
                print(f"{d} 失败 {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                time.sleep(max(a.sleep, 30))
                continue
            if not items:
                print(f"{d} 空", flush=True)
                time.sleep(a.sleep)
                continue
            ix = {f: i for i, f in enumerate(fields)}
            rows = []
            for it in items:
                code = str(it[ix["ts_code"]]).split(".")[0]
                if len(code) != 6 or it[ix["up_limit"]] is None or it[ix["down_limit"]] is None:
                    continue
                rows.append({"code": code, "trade_date": d, "up_limit": float(it[ix["up_limit"]]),
                             "down_limit": float(it[ix["down_limit"]])})
            if rows:
                r = db.execute(mysql_insert(StkLimit).values(rows).prefix_with("IGNORE"))
                db.commit()
                ok += 1
                print(f"{d} 源 {len(items)} 行，新增 {r.rowcount}", flush=True)
            time.sleep(a.sleep)
        print(f"round {rnd} 完成：成功 {ok}/{len(todo)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
