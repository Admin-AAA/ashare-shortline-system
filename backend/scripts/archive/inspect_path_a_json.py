"""查看两份 PATH-A 结果 JSON 的实际键结构。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

NEW = Path(BACKEND_ROOT).parent / "output/output/path_a_recalc_202507_reuse.json"
OLD = Path(BACKEND_ROOT).parent / "output/si_path_a_202507_champion.json"


def main() -> None:
    for name, path in (("NEW", NEW), ("OLD", OLD)):
        d = json.loads(path.read_text(encoding="utf-8"))
        print(f"== {name} top keys: {list(d.keys())}")
        if name == "NEW":
            print("metrics keys:", list((d.get("metrics") or {}).keys()))
            print("metrics:", json.dumps(d.get("metrics"), ensure_ascii=False)[:600])
        else:
            print("batches:", len(d.get("batches") or []))
            b0 = (d.get("batches") or [{}])[0]
            print("batch[0] keys:", list(b0.keys()))
            print("batch[0] metrics:", json.dumps(b0.get("metrics") or b0.get("details"), ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
