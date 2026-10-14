"""日K Parquet 缓存：构建/增量刷新 + 与 SQL 路径逐值比对（JSON 序列化哈希：区分 int/float/bool/None，不受对象复用影响）+ 计时。只读 MySQL。
用法：python scripts/bench_kbar_cache.py [--refresh] DATE [DATE...]"""
import hashlib, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
from app.services.market_pool import kbar_cache
from app.services.market_pool.core import _load_cached_bars_batch

args = sys.argv[1:]
with SessionLocal() as db:
    if "--refresh" in args:
        args.remove("--refresh")
        t = time.perf_counter(); st = kbar_cache.refresh_kbar_cache(db); print("refresh", st, round(time.perf_counter() - t, 1), "s", flush=True)
    for date in args:
        codes = sorted({c for (c,) in db.execute(select(DailyKbar.code).where(DailyKbar.trade_date == date))})
        res = {}
        for mode in ("1", "0"):
            os.environ["ASHARE_KBAR_CACHE"] = mode
            t = time.perf_counter()
            out = _load_cached_bars_batch(db, codes, 80, as_of_date=date)
            dt = time.perf_counter() - t
            res[mode] = (round(dt, 2), hashlib.sha256(json.dumps([(k, out[k]) for k in sorted(out)], ensure_ascii=False).encode()).hexdigest()[:16])
        print(date, len(codes), "codes", "cache", res["1"], "sql", res["0"], "IDENTICAL" if res["1"][1] == res["0"][1] else "DIFFERENT", flush=True)
