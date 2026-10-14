import json,sys,itertools
from pathlib import Path
sys.path.insert(0,'.')
from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES,_metrics,run_rule
from scripts.research_round2_single_skill_eval import _register_only
p=json.loads(Path('output/research/round5/panel/entry_panel_WX.json').read_text())
_register_only(p); pol=load_production_selection_policy(); mod.OUTPUT_COUNT=3; mod.SCORE_LINE=72
base=RULES['only:sector-laggard-catchup']
days=[d['signal_date'] for d in p['days']]
def fs(c,k):
  v=(c.get('feature_snapshot') or {}).get(k); return v if v is not None else c.get(k)
# variants: tighten pct/vol/pos/excess/amount
cands=[]
for pct_hi,vol_lo,pos_hi,ex_hi,amt in itertools.product([5,6,7],[1.2,1.5,2.0],[60,70,75],[3,5],[2,3,5]):
  def rule(cards,d,_ph=pct_hi,_vl=vol_lo,_po=pos_hi,_ex=ex_hi,_am=amt):
    out=[]
    for c in base(cards,d):
      pct=fs(c,'pct'); vol=fs(c,'vol5_20'); pos=fs(c,'pos60'); ex=fs(c,'primary_excess20'); a=fs(c,'amount_yi')
      if pct is not None and not (2<=pct<=_ph): continue
      if vol is not None and vol<_vl: continue
      if pos is not None and pos>_po: continue
      if ex is not None and ex>_ex: continue
      if a is not None and a<_am: continue
      out.append(c)
    return out
  _,rows=run_rule(rule,p,pol,apply_global_weights=False)
  e=_metrics([r for r in rows if r['dt']<'2026-05-01'],[x for x in days if x<'2026-05-01'])
  l=_metrics([r for r in rows if r['dt']>='2026-05-01'],[x for x in days if x>='2026-05-01'])
  a=_metrics(rows,days)
  if e and e['n']>=20 and e['avg']>0.3:
    cands.append((e['avg'],l['avg'] if l else None,a['avg'] if a else None,e,l,a,(pct_hi,vol_lo,pos_hi,ex_hi,amt)))
cands.sort(reverse=True)
print('top by early avg:')
for t in cands[:8]:
  e,l,a,p=t[3],t[4],t[5],t[6]
  print('params',p,'E n',e['n'],f"wr{e['wr']*100:.1f} avg{e['avg']:+.2f}",'L',None if not l else f"n{l['n']} wr{l['wr']*100:.1f} avg{l['avg']:+.2f} pf{l['pf']:.2f}",'ALL',None if not a else f"n{a['n']} avg{a['avg']:+.2f}")
# baseline
_,rows=run_rule(base,p,pol,apply_global_weights=False)
for name,lo,hi in [('E','0','2026-05-01'),('L','2026-05-01','9'),('A','0','9')]:
  m=_metrics([r for r in rows if lo<=r['dt']<hi],[x for x in days if lo<=x<hi])
  print('BASE',name,None if not m else f"n{m['n']} wr{m['wr']*100:.1f} avg{m['avg']:+.2f} pf{m['pf']:.2f} dd{m['mdd']:.1f}")
