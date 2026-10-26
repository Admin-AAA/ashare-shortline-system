"""从本地库 stocks 表（每日由 stock_basic 刷新）重新导出 data/industry_cache.json（代码 → 行业名）。

不调用任何上游接口、不占 Tushare 额度。口径与 market_data.fetch_tushare_industries 写出的缓存一致
（Tushare stock_basic.industry 的行业名）。行业为空/'None' 的股票（如已退市）保留旧缓存里的值，没有旧值则不写。
写之前备份旧文件为 industry_cache.json.bak.<日期>，原子替换。

  PYTHONPATH=. .venv/bin/python scripts/export_industry_cache.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal

PATH = Path(__file__).resolve().parents[1] / "data" / "industry_cache.json"
BAD = {"", "none", "null", "nan"}


def build(old: dict[str, str], rows: list[tuple[str, str | None]]) -> dict[str, str]:
    out = dict(old)
    for code, ind in rows:
        ind = (ind or "").strip()
        if len(str(code)) == 6 and ind.lower() not in BAD:
            out[str(code)] = ind
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    old = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    with SessionLocal() as db:
        rows = [(r[0], r[1]) for r in db.execute(text("SELECT code, industry FROM stocks")).all()]
    new = build(old, rows)
    changed = [c for c in old if c in new and old[c] != new[c]]
    print(f"旧 {len(old)} 只 → 新 {len(new)} 只；新增 {len(set(new) - set(old))}，行业变更 {len(changed)}")
    if len(new) < 0.9 * max(len(old), 1):
        raise SystemExit("新缓存比旧缓存少 10% 以上，疑似库表异常，放弃写入")
    if args.dry_run:
        return
    if PATH.exists():
        shutil.copy2(PATH, PATH.with_name(f"{PATH.name}.bak.{datetime.now():%Y%m%d}"))
    tmp = PATH.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PATH)
    print("已写入", PATH)


if __name__ == "__main__":
    main()
