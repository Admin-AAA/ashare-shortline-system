"""把 teajoin 文本/事件接口经数据网关落入 text_event。不调用 LLM。

用法（在仓库根或 backend 下，密钥只从现有环境变量读取）：

    cd backend
    .venv/bin/python scripts/text_events/ingest_text_events.py --date 2026-10-23
    .venv/bin/python scripts/text_events/ingest_text_events.py --start 2026-10-01 --end 2026-10-23 --sources report_rc,stk_surv,forecast
    .venv/bin/python scripts/text_events/ingest_text_events.py --sources forecast_vip,namechange --ts-code 000001.SZ
    .venv/bin/python scripts/text_events/ingest_text_events.py --mirror-announcements --start 2026-10-01 --end 2026-10-23

超时、容量忙由网关降级到该接口的下一家上游。单日失败会记在 JSON 里，重跑同一天是幂等的。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="入库文本事件原文")
    parser.add_argument("--date")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--sources", help="逗号分隔的接口名；默认全部日频接口加 cn_schedule")
    parser.add_argument("--ts-code", help="forecast_vip / namechange 按股票回补")
    parser.add_argument("--mirror-announcements", action="store_true")
    args = parser.parse_args()
    start = args.date or args.start
    end = args.date or args.end
    if not args.mirror_announcements and not start:
        parser.error("需要 --date 或 --start/--end")
    from app.db.session import SessionLocal
    from app.services.text_events.ingest import ingest_range, mirror_announcements

    names = [item.strip() for item in args.sources.split(",")] if args.sources else None
    with SessionLocal() as db:
        if args.mirror_announcements:
            print(json.dumps(mirror_announcements(db, start=start, end=end), ensure_ascii=False, indent=2))
            return
        print(json.dumps(
            ingest_range(db, start=start, end=end or start, sources=names, ts_code=args.ts_code),
            ensure_ascii=False, indent=2,
        ))


if __name__ == "__main__":
    main()
