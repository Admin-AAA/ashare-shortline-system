"""每日在不同分数线下的合格卡（不设上限，保持 policy 排序）。"""
import json, sys
from dataclasses import replace
sys.path.insert(0, "scripts/walkforward")
from harness import DEFAULT_PRODUCTION_SELECTION_POLICY as POL, load, run_policy
days = load("output/research/stage5/panel6/entry_panel_WX.json")["days"]
wide = replace(POL, daily_output_limit=999)
out = {}
for i, day in enumerate(days):
    rec = {"sel": day["selected"], "elig": {}}
    for line in (60, 64, 66, 68, 70, 72, 74, 76, 80):
        rec["elig"][line] = run_policy(day, output_count=999, score_line=float(line), policy=wide)
    out[day["signal_date"]] = rec
    if i % 50 == 0: print(i, flush=True)
json.dump(out, open(".cache/wf/eligible.json", "w"))
print("done")
