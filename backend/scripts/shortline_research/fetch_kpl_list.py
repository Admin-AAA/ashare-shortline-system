"""[短线研究 §156] 拉开盘啦涨停榜 kpl_list(tag=涨停) 2019-01 ~ 今，按年存 parquet，可断点续拉。
每天一次调用；字段含 theme（题材，"、"分隔）、lu_time（首次封板时间）、last_time（最后封板时间）、status（N连板）。
用法：PYTHONPATH=backend backend/.venv/bin/python backend/scripts/shortline_research/fetch_kpl_list.py [start] [end]"""
import sys, time, signal
from pathlib import Path
import pandas as pd
from app.services.tushare_client import get_pro

OUT = Path(__file__).resolve().parents[2] / "data/research_cache/kpl_list"
OUT.mkdir(parents=True, exist_ok=True)
start = sys.argv[1] if len(sys.argv) > 1 else "20190101"
end = sys.argv[2] if len(sys.argv) > 2 else pd.Timestamp.now().strftime("%Y%m%d")


def _alarm(*_):
    raise TimeoutError("timeout 60s")


signal.signal(signal.SIGALRM, _alarm)
pro = get_pro()
cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
days = sorted(cal["cal_date"].astype(str))
done = set()
for f in OUT.glob("kpl_*.parquet"):
    done |= set(pd.read_parquet(f, columns=["trade_date"])["trade_date"].astype(str))
done_file = OUT / "_empty_days.txt"
if done_file.exists():
    done |= set(done_file.read_text().split())
todo = [d for d in days if d not in done]
print(f"{len(days)} trade days, {len(todo)} to fetch", flush=True)
buf, fails = [], 0


def flush():
    global buf
    if not buf:
        return
    new = pd.concat(buf, ignore_index=True)
    for y, part in new.groupby(new["trade_date"].astype(str).str[:4]):
        f = OUT / f"kpl_{y}.parquet"
        merged = pd.concat([pd.read_parquet(f), part], ignore_index=True) if f.exists() else part
        merged.drop_duplicates(["ts_code", "trade_date"]).to_parquet(f, index=False)
    buf = []


for i, d in enumerate(todo):
    df = None
    for attempt in range(3):
        signal.alarm(60)
        try:
            df = pro.kpl_list(trade_date=d, tag="涨停")
            signal.alarm(0)
            break
        except Exception as e:  # noqa: BLE001
            signal.alarm(0)
            print(d, "ERR", attempt, str(e)[:160], flush=True)
            time.sleep(10 * (attempt + 1))
    if df is None:
        fails += 1
        if fails >= 8:
            print("too many consecutive failures, stop", flush=True)
            break
        continue
    fails = 0
    if len(df):
        df["trade_date"] = df["trade_date"].astype(str)
        buf.append(df.astype({c: "string" for c in df.columns if df[c].dtype == object}))
    else:
        with open(done_file, "a") as fh:
            fh.write(d + "\n")
    if (i + 1) % 50 == 0:
        flush()
        print(f"{i + 1}/{len(todo)} last={d} rows={len(df)}", flush=True)
    time.sleep(0.3)
flush()
print("done", flush=True)
