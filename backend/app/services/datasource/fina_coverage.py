"""按报告期全市场财报（fina_indicator_vip period=…）每次实际取到多少只股票，落一份小快照给数据质量巡检看。

2026-11-05：teajoin 单次最多 10000 行，period=20260630 恰好 10000 行（5258 只）被截断、没翻页，
科创板尾部 121 只（688631~689009）的半年报没进来，业绩加速/SUE/连续双增家族漏信号。
网关层已自动翻页；这里记录每期最近一次的行数/股票数/是否命中上限，巡检据此出覆盖率项。
写失败只记日志，绝不影响取数。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PATH = Path(__file__).resolve().parents[3] / "data" / "runtime" / "fina_period_coverage.json"
ROW_CAP = 10000
_lock = threading.Lock()


def tradable(ts_code: str) -> bool:
    return bool(ts_code) and str(ts_code)[:1] in "036"


KEEP_PERIODS = 8


def summarize(frame: Any) -> dict[str, Any]:
    codes = [str(c) for c in frame["ts_code"].dropna().unique()] if frame is not None and "ts_code" in frame else []
    rows = 0 if frame is None else int(len(frame))
    trad = sorted({c[:6] for c in codes if tradable(c)})
    # 存可交易代码清单（约 5 千个 × 6 字符），巡检拿它和库里的同期股票逐只比，能看出「尾部被截」这种覆盖率看不出的缺口
    return {"rows": rows, "codes": len(codes), "tradable_codes": len(trad), "tradable": trad,
            "row_cap_multiple": bool(rows) and rows % ROW_CAP == 0}


def record(period: str, frame: Any, *, source: str, path: Path | None = None) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") and path is None:
        return
    target = path or PATH
    try:
        entry = {**summarize(frame), "source": source, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        with _lock:
            data = load(target)
            data[str(period)] = entry
            data = {k: data[k] for k in sorted(data, reverse=True)[:KEEP_PERIODS]}
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".fina_cov_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, target)
    except Exception:  # noqa: BLE001
        logger.debug("fina coverage record failed", exc_info=True)


def load(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
