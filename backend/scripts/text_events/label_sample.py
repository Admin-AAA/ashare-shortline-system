"""抽样跑文本标签，并打印分布、延迟、失败率、token 和与旧关键词的分歧。

默认不调用模型。在 backend 目录下打 LLM 标签：

    cd backend
    ASHARE_TEXT_EVENT_LLM_ENABLED=true .venv/bin/python scripts/text_events/label_sample.py --limit 500

未打开开关时，程序性公告会写成规则中性，其余记为 skipped，方便先看规则覆盖。
同一 input_hash 再次运行不会重复打模型。
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
    parser = argparse.ArgumentParser(description="抽样标注文本事件")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--out", help="把 JSON 报告写到这个路径")
    args = parser.parse_args()
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.services.text_events.labeler import run_label_batch
    from app.services.text_events.sample_report import build_sample_report

    settings = get_settings()
    if not settings.text_event_llm_enabled:
        print("ASHARE_TEXT_EVENT_LLM_ENABLED 未打开：只写规则预筛，其余跳过，不会请求模型。", file=sys.stderr)
    with SessionLocal() as db:
        results = run_label_batch(
            db, limit=max(1, args.limit), settings=settings,
            workers=max(1, int(settings.text_event_llm_concurrency or 1)),
            session_factory=SessionLocal,
        )
        report = build_sample_report(
            results,
            price_prompt=float(settings.text_event_llm_price_per_million_prompt or 0),
            price_completion=float(settings.text_event_llm_price_per_million_completion or 0),
        )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
