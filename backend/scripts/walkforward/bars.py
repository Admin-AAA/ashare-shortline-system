import json, sys
from datetime import date, timedelta
sys.path.insert(0, ".")
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tables import DailyKbar
E = json.load(open(".cache/wf/eligible.json"))
db = SessionLocal(); out = {}
for i, (dt, v) in enumerate(sorted(E.items())):
    codes = sorted(set(v["elig"]["60"]) | set(v["sel"]))
    if not codes: continue
    end = (date.fromisoformat(dt) + timedelta(days=30)).isoformat()
    rows = db.scalars(select(DailyKbar).where(DailyKbar.code.in_(codes), DailyKbar.trade_date > dt, DailyKbar.trade_date <= end).order_by(DailyKbar.code, DailyKbar.trade_date)).all()
    g = {}
    for r in rows:
        lst = g.setdefault(str(r.code), [])
        if len(lst) < 12:
            lst.append([str(r.trade_date), float(r.open or 0), float(r.high or 0), float(r.low or 0), float(r.close or 0), float(r.volume) if r.volume is not None else None])
    out[dt] = g
    if i % 100 == 0: print(i, flush=True)
json.dump(out, open(".cache/wf/bars.json", "w")); print("done")
