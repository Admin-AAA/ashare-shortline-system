"""研究用：各标签在可交易日之后的 T+1/T+3/T+5/T+20 超额收益。

不写推荐、不改回测。盘后/只有日期的公告已经在入库时对齐：
信号日收盘可见，下一交易日开盘买。收益 = 第 k 个交易日收盘 / 买入开盘 - 1，
减去同日同市值五分位（daily_basic.total_mv）其他股票的等权收益。

云端没有库时不要跑这个脚本。在本机：

    cd backend
    PYTHONPATH=. ../.venv/bin/python scripts/text_events/excess_return_study.py --start 2024-01-01 --end 2026-10-23 --out /tmp/text-event-excess.json

没有市值的日子不会冒充五分位结果，那些事件会计入 skipped。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_frame(db, start: str, end: str) -> tuple[list[dict], dict, list[str], dict]:
    from sqlalchemy import text

    events = [dict(row) for row in db.execute(text(
        "SELECT label, code, entry_trade_date FROM text_event_label "
        "WHERE status='ok' AND label IS NOT NULL AND code<>'' AND entry_trade_date BETWEEN :a AND :b"
    ), {"a": start, "b": end}).mappings()]
    sessions = [
        f"{str(row[0])[:4]}-{str(row[0])[4:6]}-{str(row[0])[6:8]}" if len(str(row[0])) == 8 else str(row[0])[:10]
        for row in db.execute(text(
            "SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 "
            "AND cal_date BETWEEN :a AND :b ORDER BY cal_date"
        ), {"a": start.replace("-", ""), "b": end.replace("-", "")})
    ]
    bars: dict[str, dict[str, dict[str, float]]] = {}
    caps: dict[str, dict[str, float]] = {}
    for row in db.execute(text(
        "SELECT code, trade_date, open, close FROM daily_kbars WHERE trade_date BETWEEN :a AND :b"
    ), {"a": start, "b": end}):
        bars.setdefault(row[0], {})[row[1]] = {"open": float(row[2] or 0), "close": float(row[3] or 0)}
    for row in db.execute(text(
        "SELECT code, trade_date, total_mv FROM daily_basic WHERE trade_date BETWEEN :a AND :b"
    ), {"a": start, "b": end}):
        if row[2]:
            caps.setdefault(row[1], {})[row[0]] = float(row[2])
    return events, bars, sessions, caps


def main() -> None:
    parser = argparse.ArgumentParser(description="文本标签超额收益研究骨架")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    from app.db.session import SessionLocal
    from app.services.text_events.study import excess_study

    try:
        with SessionLocal() as db:
            events, bars, sessions, caps = load_frame(db, args.start, args.end)
    except Exception as exc:  # noqa: BLE001 - 本脚本只在有库的机器上跑
        print(f"读库失败，未计算收益：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    report = excess_study(events, bars, sessions, caps)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
