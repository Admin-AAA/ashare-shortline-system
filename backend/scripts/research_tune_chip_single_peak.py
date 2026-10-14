"""筹码单峰（已删除，L-177）调参复现：standalone2 口径（only:<sid> + v27 + 每日前3 + 线72），早窗<2026-05-01 选参、晚窗检验。
在 backend/ 下运行：.venv/bin/python scripts/research_tune_chip_single_peak.py"""
import itertools, json, sys, time
from pathlib import Path
sys.path.insert(0, '.')
from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
OUT = Path("output/research/round3"); OUT.mkdir(parents=True, exist_ok=True)
t0 = time.time()
p = json.loads(Path('output/research/round3/panel/entry_panel_WX.json').read_text())
pol = load_production_selection_policy(); _register_only(p)
mod.OUTPUT_COUNT = 3; mod.SCORE_LINE = 72.0
SID = 'only:chip-single-peak-breakout'
base = RULES[SID]
def key(c):
    return c.get('ts_code') or c.get('code') or c.get('symbol')
# 预筛：只保留 base 规则放行的卡（原卡），加速网格
red_days = []
for d in p['days']:
    keep = {key(c) for c in base([dict(c) for c in d['cards']], d)}
    red_days.append({**d, 'cards': [c for c in d['cards'] if key(c) in keep]})
P = {**p, 'days': red_days}
days = [d['signal_date'] for d in p['days']]
print('loaded', round(time.time()-t0,1), 'cand', sum(len(d['cards']) for d in red_days), flush=True)
def fs(c, k):
    v = (c.get('feature_snapshot') or {}).get(k)
    if v is None: v = c.get(k)
    try: return float(v)
    except (TypeError, ValueError): return None
def le(k, x): return lambda c: (fs(c, k) is None) or fs(c, k) <= x
def ge(k, x): return lambda c: (fs(c, k) is None) or fs(c, k) >= x
F = {
 'turn<=5': le('turnover_rate',5), 'turn<=6': le('turnover_rate',6),
 'pct<=3': le('pct',3), 'pct<=5': le('pct',5), 'pct<=7': le('pct',7),
 'rsi6<=70': le('rsi_6',70), 'rsi6<=80': le('rsi_6',80),
 'chg20<=15': le('chg20',15), 'chg20<=25': le('chg20',25),
 'chip<=8': le('chip_concentration',8), 'vol5_20>=1.3': ge('vol5_20',1.3),
 'pos20<=85': le('pos20',85), 'inflow>0': lambda c: (fs(c,'main_net_inflow_yi') or 0) > 0,
 'rsi24>=56': ge('rsi_24',56), 'bias5<=5': le('bias_ma5',5), 'offhigh>=-5': ge('off_high_pct',-5),
}
combos = [()] + [(a,) for a in F] + list(itertools.combinations(F, 2))
def pick(m): return None if m is None else {k: round(m[k], 4) for k in ('n','wr','avg','pf','mdd')}
res = []
for i, combo in enumerate(combos):
    rule = (lambda cs, d, combo=combo: [c for c in base(cs, d) if all(F[k](c) for k in combo)])
    _, rows = run_rule(rule, P, pol, apply_global_weights=False)
    r = {'combo': list(combo)}
    for w, lo, hi in [('all','0','9'),('early','0','2026-05-01'),('late','2026-05-01','9')]:
        r[w] = pick(_metrics([x for x in rows if lo <= x['dt'] < hi], [x for x in days if lo <= x < hi]))
    res.append(r)
    if i < 3 or i % 20 == 0: print(i, len(combos), round(time.time()-t0,1), json.dumps(r, ensure_ascii=False), flush=True)
(OUT/'chip_tune_grid.json').write_text(json.dumps(res, ensure_ascii=False, indent=0))
print('done', round(time.time()-t0,1), flush=True)
