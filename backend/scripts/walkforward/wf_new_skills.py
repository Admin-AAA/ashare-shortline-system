"""新策略逐折评估：round1 回放面板（含研究原型技能）上，按季度统计
(1) 全组合入选卡指标；(2) 含某新技能非否决信号的入选卡指标，用来判断新技能带来的卡在每一折是否赚钱。
用法：python scripts/walkforward/wf_new_skills.py output/research/round1/panel/entry_panel_WX.json
"""
import json, sys
sys.path.insert(0, "scripts/walkforward")
from harness import metrics, fmt
NEW = ("oversold-capital-reversal", "low-position-first-board", "second-board-leader", "sector-laggard-catchup")
FOLDS = [("2025Q1","2025-01-01","2025-04-01"),("2025Q2","2025-04-01","2025-07-01"),("2025Q3","2025-07-01","2025-10-01"),
         ("2025Q4","2025-10-01","2026-01-01"),("2026Q1","2026-01-01","2026-04-01"),("2026Q2","2026-04-01","2026-07-01"),
         ("2026Q3*","2026-07-01","2026-10-10")]
days = json.load(open(sys.argv[1]))["days"]
def has(card, sid):
    return any((s.get("strategy_id") == sid) and not s.get("hard_veto") for s in card.get("strategy_signals") or [])
for name, lo, hi in FOLDS:
    ds = [d for d in days if lo <= d["signal_date"] < hi]
    allr = [(d["signal_date"], d["net_by_code"].get(c)) for d in ds for c in d["selected"]]
    line = [name, "全部 " + fmt(metrics(allr))]
    for sid in NEW:
        r = []
        for d in ds:
            cards = {str(c["code"]): c for c in d["cards"]}
            r += [(d["signal_date"], d["net_by_code"].get(c)) for c in d["selected"] if has(cards.get(c, {}), sid)]
        line.append(f"{sid}: " + fmt(metrics(r)))
    print(" | ".join(line))
