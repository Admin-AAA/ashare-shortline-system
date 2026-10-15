import random, statistics
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import indicator_computer as ic
db=SessionLocal()
codes=[r[0] for r in db.execute(text("select distinct code from daily_kbars where trade_date='2026-10-14'"))]
random.seed(5); codes=random.sample(codes,60)
d={f:[] for f in ["rsi_6","rsi_12","rsi_24","macd_dif","macd_dea","macd_bar"]}
for c in codes:
    rows=[dict(r._mapping) for r in db.execute(text("select trade_date,close,high,rsi_6,rsi_12,rsi_24,macd_dif,macd_dea,macd_bar from daily_kbars where code=:c order by trade_date"),{"c":c})]
    bars=[{"trade_date":str(r["trade_date"]),"close":float(r["close"] or 0),"high":float(r["high"] or 0)} for r in rows]
    out=ic.compute_daily_indicators(c,bars)
    for r in rows:
        td=str(r["trade_date"])
        if td<"2025-06-01": continue
        for f in d:
            v=r[f]
            if v is not None and float(v)!=0 and out[td][f] is not None: d[f].append(abs(float(v)-out[td][f]))
for f,x in d.items():
    x.sort(); print(f,len(x),"p50",round(x[len(x)//2],4),"p90",round(x[int(.9*len(x))],4))
