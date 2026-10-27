"""只读核对：全部真实入选股，批量结算 == 逐只单查（含所有策略、全部历史批次）。
用法：cd backend && PYTHONPATH=. .venv/bin/python scripts/perf/check_realize.py"""
import json

from app.db.session import SessionLocal
from app.services import midterm_service as ms

db = SessionLocal()
metas = []
for f in ms._pick_files(None, None):
    d = json.loads(f.read_text())
    metas.append((d["strategy_id"], d["signal_date"], [p["code"] for p in d["picks"]]))
ms._REALIZED_FINAL.clear()
ms._REALIZED_LIVE.clear()
ms._LIVE_STATE.update(version=None)
ms.prefetch_realized(db, metas)
bad = n = 0
for sid, d, codes in metas:
    h = ms._hold(sid)
    for c in codes:
        n += 1
        if ms._realize(db, c, d, h) != ms._realize_query(db, c, d, h):
            bad += 1
print("picks", n, "mismatch", bad)
