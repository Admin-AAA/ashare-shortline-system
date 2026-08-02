"""探测 limit_list_d 在 fastapic 代理上的可用性（限流窗口）。

只做少量探测调用（最多 6 次，间隔 10s），输出每次的 provider/类别/耗时，
用于确定历史回填的安全调用速率。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.tushare_client import get_pro  # noqa: E402


def main() -> None:
    pro = get_pro()
    for i, td in enumerate(["2025-07-15", "2025-07-16", "2025-07-17", "2025-09-10", "2025-09-11", "2025-09-12"]):
        compact = td.replace("-", "")
        t0 = time.time()
        try:
            df = pro.limit_list_d(trade_date=compact, limit_type="U")
            n = 0 if df is None else len(df)
            print(f"[{i + 1}] {td} OK rows={n} ({time.time() - t0:.1f}s)")
        except Exception as exc:
            print(f"[{i + 1}] {td} FAIL {type(exc).__name__}: {str(exc)[:120]} ({time.time() - t0:.1f}s)")
        time.sleep(10)


if __name__ == "__main__":
    main()
