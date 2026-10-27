"""[追龙数据 §325/§326] 补 promax limit_list_d（涨停/跌停/炸板榜，含 fd_amount 封单额、first_time/last_time、open_times 打开次数、limit_times 连板数）。
2020-01-02 起（实测 2019-12-31 开始有数据）；一天一次调用，默认返回 U/Z/D 三类；按年存 parquet，可断点续拉，不覆盖旧数据（旧涨停榜 kpl_list 另存）。
入库：backend/data/research_cache/limit_list_d/limit_list_d_YYYY.parquet（沿用 kpl_list 的“按年 parquet + _empty_days.txt”结构），
附两列：src='promax:limit_list_d'、fetched_at；已有 (ts_code, trade_date, limit) 不重复写。
限速：盘中(09:15–15:05) 默认 10 次/分钟，盘后 30 次/分钟（再叠加 promax_window 的研究令牌桶）；每次调用前看 live rt_k 熔断：
  开着就等它恢复再 +30s；最近 5 分钟内 rt_k 熔断开过则降到 5 次/分钟（直到连续 20 次平稳）。
用法：cd backend && PYTHONPATH=. .venv/bin/python scripts/shortline_research/fetch_limit_list_d.py [start=20200102] [end=最近交易日]"""
from __future__ import annotations

import sys
import time
import signal
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

OUT = Path(__file__).resolve().parents[2] / "data/research_cache/limit_list_d"
SRC = "promax:limit_list_d"
RT_K_KEY = "live:tushare:promax:rt_k"
TZ = ZoneInfo("Asia/Shanghai")


def intraday(now: datetime | None = None) -> bool:
    now = now or datetime.now(TZ)
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 5)


def pace_seconds(slow: bool, now: datetime | None = None) -> float:
    rpm = 5 if slow else (10 if intraday(now) else 30)
    return 60.0 / rpm


def rt_k_open_until() -> float:
    try:
        from app.core.shared_breaker import shared_open_until

        return float(shared_open_until(RT_K_KEY) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def merge_year(path: Path, part: pd.DataFrame) -> int:
    key = ["ts_code", "trade_date", "limit"]
    if path.exists():
        old = pd.read_parquet(path)
        part = part[~part.set_index(key).index.isin(old.set_index(key).index)]
        if part.empty:
            return 0
        merged = pd.concat([old, part], ignore_index=True)
    else:
        merged = part
    merged.to_parquet(path, index=False)
    return len(part)


def main() -> None:
    from app.services.tushare_client import get_pro

    OUT.mkdir(parents=True, exist_ok=True)
    start = sys.argv[1] if len(sys.argv) > 1 else "20200102"
    end = sys.argv[2] if len(sys.argv) > 2 else None

    def _alarm(*_):
        raise TimeoutError("timeout 90s")

    signal.signal(signal.SIGALRM, _alarm)
    pro = get_pro()
    if end is None:
        now = datetime.now(TZ)
        end = now.strftime("%Y%m%d") if now.hour >= 18 else (now - pd.Timedelta(days=1)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    days = sorted(cal["cal_date"].astype(str))
    done = set()
    for f in OUT.glob("limit_list_d_*.parquet"):
        done |= set(pd.read_parquet(f, columns=["trade_date"])["trade_date"].astype(str))
    empty_file = OUT / "_empty_days.txt"
    if empty_file.exists():
        done |= set(empty_file.read_text().split())
    todo = [d for d in days if d not in done]
    print(f"{len(days)} trade days, {len(todo)} to fetch; intraday={intraday()}", flush=True)
    buf: list[pd.DataFrame] = []
    fails = calm = 0
    slow = False
    rtk_events = 0
    last_rtk_open = 0.0

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        new = pd.concat(buf, ignore_index=True)
        for y, part in new.groupby(new["trade_date"].astype(str).str[:4]):
            merge_year(OUT / f"limit_list_d_{y}.parquet", part)
        buf = []

    for i, d in enumerate(todo):
        t0 = time.time()
        until = rt_k_open_until()
        if until > time.time():  # live rt_k 熔断中：让路
            wait = until - time.time() + 30
            print(f"rt_k breaker open, wait {wait:.0f}s", flush=True)
            time.sleep(wait)
            slow, calm = True, 0
            rtk_events += 1
            last_rtk_open = time.time()
        elif last_rtk_open and time.time() - last_rtk_open < 300:
            slow, calm = True, 0
        df = None
        for attempt in range(3):
            signal.alarm(90)
            try:
                df = pro.limit_list_d(trade_date=d)
                signal.alarm(0)
                break
            except Exception as e:  # noqa: BLE001
                signal.alarm(0)
                print(d, "ERR", attempt, str(e)[:160], flush=True)
                slow, calm = True, 0
                time.sleep(20 * (attempt + 1))
        if df is None:
            fails += 1
            if fails >= 8:
                print("too many consecutive failures, stop", flush=True)
                break
            continue
        fails = 0
        if len(df):
            df = df.copy()
            df["trade_date"] = df["trade_date"].astype(str)
            df["src"] = SRC
            df["fetched_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            buf.append(df)
        else:
            with open(empty_file, "a") as fh:
                fh.write(d + "\n")
        calm += 1
        if slow and calm >= 20:
            slow = False
        if (i + 1) % 25 == 0:
            flush()
            print(f"{i + 1}/{len(todo)} last={d} rows={len(df)} slow={slow} rt_k_events={rtk_events}", flush=True)
        time.sleep(max(0.0, pace_seconds(slow) - (time.time() - t0)))
    flush()
    print("done", flush=True)


if __name__ == "__main__":
    main()
