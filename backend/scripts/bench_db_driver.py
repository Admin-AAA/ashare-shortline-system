"""对比 pymysql 与 mysqlclient(C) 驱动：同一查询输出逐值一致 + 耗时。只读。"""
import sys, time, pickle, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.tables import DailyKbar
from app.services.market_pool.core import _load_cached_bars_batch

url = get_settings().database_url
res = {}
for name, u in (("pymysql", url), ("mysqlclient", url.replace("mysql+pymysql://", "mysql+mysqldb://"))):
    eng = create_engine(u, pool_pre_ping=True)
    with Session(eng) as db:
        codes = sorted({c for (c,) in db.execute(select(DailyKbar.code).where(DailyKbar.trade_date == "2026-09-29"))})
        for date in sys.argv[1:] or ["2026-09-29"]:
            t = time.perf_counter()
            out = _load_cached_bars_batch(db, codes, 80, as_of_date=date)
            dt = time.perf_counter() - t
            h = hashlib.sha256(pickle.dumps(sorted((k, v) for k, v in out.items()))).hexdigest()[:16]
            n = sum(len(v) for v in out.values())
            res.setdefault(date, {})[name] = (round(dt, 1), n, h)
            print(name, date, len(codes), "codes", n, "rows", round(dt, 1), "s", h, flush=True)
for d, r in res.items():
    print(d, "IDENTICAL" if r["pymysql"][2] == r["mysqlclient"][2] else "DIFFERENT", r)
