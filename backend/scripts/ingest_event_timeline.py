"""按日下载 xiaoshi `event-timeline`（事件时间线）到本地 parquet，供研究脚本读取。

为什么这样落地（性能 + 约束）：
- **不写 MySQL**：任务约束里"改数据库 schema"属打断点，所以事件数据只落文件
  （`backend/output/research/event_timeline/date=YYYY-MM-DD/`，output/ 已被 gitignore），
  研究脚本直接列式读 parquet。既不动 schema，也不给 MySQL 加负载。
- **顺序 + 限速**：每天一次 download-session + N 个 parquet，串行请求、默认 sleep 0.4s，
  不并发；单日失败退避重试，不中断整窗。
- **可续跑**：每天写完落 `.done` 标记，重跑自动跳过。
- **体量已知**：manifest 报 event-timeline 共 923 文件 / 1,446,630 行 / 209MB，量级很小。
- **时点语义**：该数据集自带 `available_at` / `reported_available_at`，官方说明为
  "available_at <= as_of"，即**天然满足 T 决策时可得**（不用自己猜时点）。

用法：
  cd backend && .venv/bin/python scripts/ingest_event_timeline.py \
      --start 2025-05-14 --end 2026-09-12 --sleep 0.4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.xiaoshi_client import XiaoshiClient  # noqa: E402

OUT_DIR = B / "output" / "research" / "event_timeline"


def trade_days(start: str, end: str) -> list:
    with SessionLocal() as s:
        rows = s.execute(
            text(
                "select distinct trade_date from daily_kbars "
                "where trade_date between :a and :b order by trade_date"
            ),
            {"a": start, "b": end},
        ).all()
    return [str(r[0]) for r in rows]


def _file_name(url: str) -> str:
    """从预签名 URL 里取 partition 名，如 event_type=announcement/data.parquet。"""
    path = unquote(urlparse(url).path)
    parts = [p for p in path.split("/") if p and not p.startswith("data.parquet")]
    tag = next((p for p in parts if p.startswith("event_type=")), None)
    return f"{tag.split('=', 1)[1] if tag else 'unknown'}.parquet"


def _download(url: str, dest: Path, *, timeout: float = 60.0) -> int:
    tmp = dest.with_suffix(".parquet.tmp")
    written = 0
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
                written += len(chunk)
    tmp.replace(dest)
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sleep", type=float, default=0.4, help="每天之间的间隔秒数（限速）")
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    settings = get_settings()
    client = XiaoshiClient(base_url=settings.xiaoshi_api_base_url, api_key=settings.xiaoshi_api_key)
    days = trade_days(args.start, args.end)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[ingest] 交易日 {len(days)}：{days[0] if days else '-'}..{days[-1] if days else '-'} → {OUT_DIR}", flush=True)

    done = skipped = failed = 0
    total_bytes = 0
    t0 = time.time()
    for i, d in enumerate(days, 1):
        day_dir = OUT_DIR / f"date={d}"
        marker = day_dir / ".done"
        if marker.exists():
            skipped += 1
            continue
        day_dir.mkdir(parents=True, exist_ok=True)
        got = []
        for attempt in range(args.retries):
            try:
                sess = client.get_history_download_session(dataset="event-timeline", date=d)
                for f in sess.get("files") or []:
                    url = f.get("url")
                    if not url:
                        continue
                    dest = day_dir / _file_name(url)
                    n = _download(url, dest)
                    total_bytes += n
                    got.append({"file": dest.name, "bytes": n})
                marker.write_text(
                    json.dumps({"date": d, "files": got, "expires_at": sess.get("expires_at")},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
                done += 1
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == args.retries - 1:
                    failed += 1
                    print(f"[{i}/{len(days)}] {d} FAIL {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                else:
                    time.sleep(2.0 * (attempt + 1))
        if i % 25 == 0 or i == len(days):
            el = time.time() - t0
            print(f"[{i}/{len(days)}] done={done} skip={skipped} fail={failed} "
                  f"bytes={total_bytes/1e6:.1f}MB {el/max(1,i):.2f}s/d", flush=True)
        time.sleep(args.sleep)
    print(f"[ingest] 完成：新下 {done} 天，跳过 {skipped}，失败 {failed}，"
          f"共 {total_bytes/1e6:.1f}MB，用时 {(time.time()-t0)/60:.1f}min", flush=True)


if __name__ == "__main__":
    main()
