"""Sprint2：standalone_dry_run 的包装——记录每次 build_features 输出里 news_risk_count/
announcement_negative_count > 0 的股票（候选层面新闻风险是否还在生效）。不改生产代码。
用法：python scripts/sprint2/news_risk_dryrun.py --trade-date 2026-10-14 --out x.json [--risk-log y.json]
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services import workbench_service as wb

LOG: list[dict] = []
_orig = wb.build_features

def _wrapped(*args, **kwargs):
    out = _orig(*args, **kwargs)
    risky = [{"code": s.get("code"), "news_risk_count": s.get("news_risk_count", 0),
              "announcement_negative_count": s.get("announcement_negative_count", 0),
              "risk_alerts": (s.get("risk_alerts") or [])[:2]}
             for s in out if (s.get("news_risk_count") or 0) > 0 or (s.get("announcement_negative_count") or 0) > 0]
    LOG.append({"n_features": len(out), "n_risky": len(risky), "risky": risky,
                "n_with_news": sum(1 for s in out if s.get("latest_news"))})
    print(f"[news-risk] features={len(out)} risky={len(risky)} with_news={LOG[-1]['n_with_news']}", flush=True)
    return out

wb.build_features = _wrapped

if __name__ == "__main__":
    risk_log = None
    if "--risk-log" in sys.argv:
        i = sys.argv.index("--risk-log"); risk_log = sys.argv[i + 1]; del sys.argv[i:i + 2]
    import runpy
    try:
        sys.argv[0] = "scripts/standalone_dry_run.py"
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "standalone_dry_run.py"), run_name="__main__")
    finally:
        if risk_log:
            Path(risk_log).write_text(json.dumps(LOG, ensure_ascii=False, indent=1), encoding="utf-8")
