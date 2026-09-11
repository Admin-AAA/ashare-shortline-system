"""前瞻窗口(2026-02~08, canonical run btr_fa625, score>=70)出票结构预筛（只读）。
问题：v2 后 climax_output_divisor=2 只在强牛·高潮日把出票砍到 top5；被砍的 rank6-10
是否正期望？日内 final_score 排名是否有信息量（rank decay）？日上限10是否最优？
"""
from __future__ import annotations
import sys, pickle
from pathlib import Path
import numpy as np, pandas as pd
B=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(B))
from scripts.research_forward_validation import build_forward
from app.engine.aggregator import _market_base,_market_sentiment
OUT=B/"output/research"; SNAPC=OUT/"forward_snapshots.pkl"
tr=build_forward(); dates=sorted(tr.signal_date.unique())
if SNAPC.exists():
    snaps=pickle.loads(SNAPC.read_bytes())
else:
    from scripts.research_forward_validation import build_market_snapshots
    snaps=build_market_snapshots(dates); SNAPC.write_bytes(pickle.dumps(snaps))
tr=tr[tr.signal_date.isin(snaps)].copy()
tr["base"]=tr.signal_date.map(lambda d:_market_base(snaps.get(d)))
tr["sent"]=tr.signal_date.map(lambda d:_market_sentiment(snaps.get(d)))
tr["key"]=tr.base+"·"+tr.sent
# v2 有效线（前瞻只存 score>=70，64/60 放行档全部过线）
LINE={"强牛·高潮":64,"强牛·活跃":64,"偏多·活跃":71.4,"震荡·活跃":72,
      "偏多·高潮":120,"震荡·高潮":120,"弱势·高潮":120}
def eff(key): return LINE.get(key,72.0)
tr=tr[tr.score>=tr.key.map(eff)].copy()
# 日内排名（按 score 降序）
tr["rank"]=tr.groupby("signal_date").score.rank(ascending=False,method="first").astype(int)
def stat(x): return f"n{len(x):4d} avg{x.net.mean():+.3f} wr{(x.net>0).mean()*100:4.1f}%"
print("== 全样本日内 rank decay（过线候选）==")
for lo,hi in [(1,3),(4,5),(6,8),(9,10),(11,999)]:
    x=tr[(tr['rank']>=lo)&(tr['rank']<=hi)]
    print(f"rank {lo:2d}-{hi:<3d}: {stat(x)}")
print("\n== 强牛·高潮：top5 vs rank6-10 ==")
sc=tr[tr.key=="强牛·高潮"]
for lbl,x in [("top1-5",sc[sc['rank']<=5]),("rank6-10",sc[(sc['rank']>=6)&(sc['rank']<=10)]),("rank11+",sc[sc['rank']>=11])]:
    print(f"  {lbl:8s}: {stat(x)}")
print("\n== 各 cell：top5 vs rank6-10 逐笔 ==")
for k in ["强牛·高潮","强牛·活跃","偏多·活跃","震荡·活跃","弱势·活跃","弱势·平淡","弱势·冰点"]:
    c=tr[tr.key==k]
    if not len(c): print(f"  {k}: 无"); continue
    a,b=c[c['rank']<=5],c[(c['rank']>=6)&(c['rank']<=10)]
    print(f"  {k:6s} top5[{a.net.mean():+.3f}/n{len(a)}] rank6-10[{b.net.mean():+.3f}/n{len(b)}]")
# 日度组合：强牛·高潮日 top5(现状) vs top10(除数=1)
print("\n== 强牛·高潮日 日度等权组合 ==")
d=sc.groupby("signal_date")
rows=[]
for dt,g in d:
    g=g.sort_values("score",ascending=False)
    rows.append({"date":dt,"top5":g.head(5).net.mean(),"top10":g.head(10).net.mean(),
                 "add6_10":g.iloc[5:10].net.mean() if len(g)>5 else np.nan,"n":len(g)})
r=pd.DataFrame(rows)
print(f"  强牛高潮日数 {len(r)}; top5 日均{r.top5.mean():+.4f} | top10 日均{r.top10.mean():+.4f}")
print(f"  被新增的rank6-10篮: 日均{r.add6_10.mean():+.4f}, 有增量的天{(r.n>5).sum()}")
# 全局 top-N 日度组合曲线（所有日，空仓0）
print("\n== 全局日度等权 top-N 曲线（空仓日记0）==")
allrows={}
for dt,g in tr.groupby("signal_date"):
    g=g.sort_values("score",ascending=False)
    for N in (3,5,8,10,15):
        allrows.setdefault(N,[]).append(g.head(N).net.mean())
for N,v in allrows.items():
    v=np.array(v); print(f"  top{N:2d}: 日均{v.mean():+.4f} 累计{v.sum():+7.2f} 盈天{(v>0).mean()*100:4.1f}%")
