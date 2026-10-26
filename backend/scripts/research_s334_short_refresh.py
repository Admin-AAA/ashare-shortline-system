"""现役 4 短线体检刷新（2026-10-26）：12m 面板 = 原 output/research/solo/<sid>（2025-10-01~2026-10-08）+ 尾部重生成
output/research/solo_tail_20261026/<sid>（2026-10-09~10-15，口径同 solo/run.sh）；合并后写 output/research/solo_20261026/<sid>，
再用 §282 BASE 框架（research_s282_short_quality，21 次抽样、EL_C、按当前库内行情重算出场）重评。只写研究产物，不改线上配置。
用法：cd backend && S282_OUT=output/research/s334/short_quality_refresh.json .venv/bin/python scripts/research_s334_short_refresh.py"""
import json, os, sys
from pathlib import Path
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
R = Path(os.environ.get("ASHARE_RESEARCH_ROOT", str(B / "output/research")))
SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")
CUT = "2026-10-09"


def merge():
    for sid in SIDS:
        o = json.loads((R / "solo" / sid / "panel/entry_panel_WX.json").read_text())
        t = json.loads((R / "solo_tail_20261026" / sid / "panel/entry_panel_WX.json").read_text())
        assert (R / "solo_tail_20261026" / sid / "replay.exit").read_text().strip() == "0", sid
        days = [d for d in o["days"] if d["signal_date"] < CUT] + [d for d in t["days"] if d["signal_date"] >= CUT]
        m = dict(o, days=days, end=t["end"], fidelity_mismatch_days=list(o.get("fidelity_mismatch_days") or []) + list(t.get("fidelity_mismatch_days") or []))
        d = R / "solo_20261026" / sid
        (d / "panel").mkdir(parents=True, exist_ok=True)
        (d / "panel/entry_panel_WX.json").write_text(json.dumps(m, ensure_ascii=False))
        (d / "replay.exit").write_text("0")
        print(sid, "days", len(o["days"]), "->", len(days), "last", days[-1]["signal_date"], flush=True)


if __name__ == "__main__":
    merge()
    import scripts.research_stability_gates as G
    import scripts.research_s282_short_quality as Q
    orig = G.panels

    def panels(sid):
        out = orig(sid)
        f = R / "solo_20261026" / sid / "panel/entry_panel_WX.json"
        if f.exists():
            out["12m"] = f
        return out
    Q.panels = panels
    Q.main()
