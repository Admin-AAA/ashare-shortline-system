"""新数据维度全市场 strictly-prior 预筛（L-064 铁律：先全市场五分位单调性，再看选中池）。
覆盖两窗口：冻结 2025-01-15..2026-01-16 / 真前瞻 2026-02-09..2026-08-05。
因子（T 收盘已知，T+1 开盘买，毛口径 T+5 收盘卖，仅用于快速杀死因子，幸存者再上因果 harness）：
  daily_basic: log circ_mv(规模) / turnover_rate(换手) / pe_ttm
  limit_up_board 个股封板属性: limit_times 连板 / open_times 炸板 / fd_amount 封单
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import text
B=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(B))
from scripts.research_limit_sentiment_panel import load_raw, build_daily_flags
from app.db.session import SessionLocal
OUT=B/"output/research"; CACHE=OUT/"newfactor_fullmarket.pkl"
FZ=("2025-01-15","2026-01-16"); FW=("2026-02-09","2026-08-05")
def build():
    raw=load_raw("2025-01-01"); fl=build_daily_flags(raw); fl=fl[fl.tradable_univ].copy()
    fl=fl.sort_values(["code","trade_date"])
    fl["entry"]=fl.groupby("code").open.shift(-1)
    fl["exit5"]=fl.groupby("code").close.shift(-5)
    fl["fwd5"]=(fl.exit5/fl.entry-1)*100
    with SessionLocal() as s:
        db=pd.DataFrame(s.execute(text("SELECT code,trade_date,turnover_rate,pe_ttm,circ_mv FROM daily_basic")).mappings().all())
        lu=pd.DataFrame(s.execute(text("SELECT code,trade_date,limit_times,open_times,fd_amount_yi,first_time FROM limit_up_board")).mappings().all())
    m=fl.merge(db,on=["code","trade_date"],how="left").merge(lu,on=["code","trade_date"],how="left")
    m["logmv"]=np.log(m.circ_mv.astype(float))
    m.to_pickle(CACHE); return m
m=pd.read_pickle(CACHE) if CACHE.exists() else build()
m=m[m.fwd5.notna()].copy()
def win(d): return m[(m.trade_date>=FZ[0])&(m.trade_date<=FZ[1])],m[(m.trade_date>=FW[0])&(m.trade_date<=FW[1])]
fz,fw=win(m)
def quint_screen(col,label):
    print(f"\n===== {label} ({col}) 全市场日截面五分位, T+1open→T+5close 毛均收益% =====")
    for nm,d in [("冻结",fz),("前瞻",fw)]:
        x=d.dropna(subset=[col]).copy()
        x["q"]=x.groupby("trade_date")[col].transform(lambda s:pd.qcut(s.rank(method="first"),5,labels=False) if s.notna().sum()>=5 else np.nan)
        daily=x.groupby(["trade_date","q"]).fwd5.mean().unstack("q")
        qm=daily.mean()
        cs=sorted(daily.columns); hi,lo=cs[-1],cs[0]
        spr=(daily[hi]-daily[lo]).dropna()
        mon=spr.groupby(spr.index.str[:7]).mean().to_numpy(dtype=float)
        sgn=1.0 if float(spr.mean())>0 else -1.0
        samesign=float((np.sign(mon)==sgn).mean())
        print(f" {nm}: "+" ".join(f"Q{int(q)}={qm[q]:+.3f}" for q in qm.index)+
              f" | Q5-Q1={float(spr.mean()):+.4f} 逐月同号{samesign:.2f} rho={x[[col,'fwd5']].corr(method='spearman').iloc[0,1]:+.3f}")
for c,l in [("logmv","流通市值规模(log circ_mv)"),("turnover_rate","换手率 turnover_rate"),("pe_ttm","估值 pe_ttm")]:
    quint_screen(c,l)
# 涨停股封板属性
print("\n===== limit_up_board 个股封板属性（仅当日收盘涨停股）=====")
for nm,d in [("冻结",fz),("前瞻",fw)]:
    x=d[d.limit_times.notna()].copy()
    if not len(x): print(nm,"无涨停板数据"); continue
    print(f" {nm} 涨停票 n={len(x)}")
    for c in ["limit_times","open_times"]:
        g=x.groupby(c).fwd5.agg(["mean","size"]); print(f"   by {c}: "+" ".join(f"{int(k)}板:{r['mean']:+.2f}/n{int(r['size'])}" for k,r in g.iterrows() if r['size']>=20))
    x["fdq"]=pd.qcut(x.fd_amount_yi.rank(method="first"),3,labels=False)
    g=x.groupby("fdq").fwd5.agg(["mean","size"]); print("   by 封单额fd三分位: "+" ".join(f"Q{int(k)}={r['mean']:+.2f}/n{int(r['size'])}" for k,r in g.iterrows()))
