"""汇总 2025-07/09 窗口重算指标（对比 7/31 基线）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(BACKEND_ROOT).parent


def _load(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("metrics") or (d.get("batches") or [{}])[0].get("metrics") or {}


def main() -> None:
    pairs = [
        ("2025-07", "output/output/path_a_recalc_202507_reuse.json", None),
        ("2025-09", "output/output/path_a_recalc_202509_reuse.json", None),
    ]
    for label, path, _old in pairs:
        m = _load(ROOT / path)
        print(f"{label}: n={m.get('n')} WR={m.get('win_rate_pct'):.1f}% "
              f"avg={m.get('average_net_return_pct'):.3f}% cum={m.get('cumulative_net_return_pct'):.2f}% "
              f"PF={m.get('profit_factor'):.2f} maxDD={m.get('max_drawdown_pct'):.1f}%")


if __name__ == "__main__":
    main()
