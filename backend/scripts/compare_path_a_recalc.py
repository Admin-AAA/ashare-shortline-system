"""对比 2025-07 窗口重算（reuse-recs，2026-08-02）与 7/31 基线的指标差异。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

NEW = Path(BACKEND_ROOT).parent / "output/output/path_a_recalc_202507_reuse.json"
OLD = Path(BACKEND_ROOT).parent / "output/si_path_a_202507_champion.json"


def _metrics(d: dict) -> dict:
    m = d.get("metrics") or d.get("details") or {}
    keys = ("win_rate", "avg_net", "sum_net", "profit_factor", "max_loss", "n", "exit_breakdown")
    return {k: m.get(k) for k in keys if k in m}


def main() -> None:
    new = json.loads(NEW.read_text(encoding="utf-8"))
    old = json.loads(OLD.read_text(encoding="utf-8"))
    print("== 重算（2026-08-02, reuse-recs, trailing=0.3）==")
    print(json.dumps(_metrics(new), ensure_ascii=False, indent=1))
    print("== 7/31 基线（trailing=0.3）==")
    print(json.dumps(_metrics(old), ensure_ascii=False, indent=1))
    print("== 样本对齐 ==")
    print(f"total_recs: {new.get('total_recs')} vs {old.get('total_recs')}")
    print(f"prod_recs: {new.get('prod_recs')} vs {old.get('prod_recs')}")


if __name__ == "__main__":
    main()
