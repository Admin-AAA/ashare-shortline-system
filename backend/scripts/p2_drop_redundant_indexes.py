"""删除前缀冗余/重复的 MySQL 索引（2026-10-25 P2）。

判定：被删索引的列序列是另一个（保留）索引列序列的前缀（或完全相同）；唯一键/主键永不删除。
执行前逐个校验覆盖关系，不满足则跳过；用 ALGORITHM=INPLACE, LOCK=NONE（仅元数据，瞬时）。
默认 dry-run；--apply 才执行。执行应在非交易时段、非 16:35–18:35 窗口、active_work_check 为 idle 时进行。

  PYTHONPATH=. .venv/bin/python scripts/p2_drop_redundant_indexes.py            # dry-run
  PYTHONPATH=. .venv/bin/python scripts/p2_drop_redundant_indexes.py --apply
"""
import argparse
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

CANDIDATES: dict[str, list[str]] = {
    "daily_kbars": ["ix_daily_kbars_code", "idx_kbars_code_td", "idx_kbars_td"],
    "minute_kbars": ["ix_minute_kbars_code", "idx_mk_td"],
    "stk_limits": ["ix_stk_limits_code", "idx_sl_code_td"],
    "capital_flow": ["ix_capital_flow_code"],
    "daily_basic": ["ix_daily_basic_code"],
    "margin_data": ["ix_margin_data_code", "idx_margin_td"],
    "auction_data": ["ix_auction_data_code", "idx_auction_td"],
    "limit_up_board": ["ix_limit_up_board_code"],
    "stock_quotes": ["ix_stock_quotes_code", "idx_quotes_td"],
}


def load_indexes(db, table: str) -> dict[str, dict]:
    rows = db.execute(text(
        "SELECT index_name, non_unique, seq_in_index, column_name FROM information_schema.statistics "
        "WHERE table_schema=DATABASE() AND table_name=:t ORDER BY index_name, seq_in_index"), {"t": table}).all()
    out: dict[str, dict] = {}
    for name, non_unique, _seq, col in rows:
        d = out.setdefault(name, {"unique": not non_unique, "cols": []})
        d["cols"].append(col)
    return out


def covering_index(indexes: dict[str, dict], name: str, dropping: set[str]) -> str | None:
    cols = indexes[name]["cols"]
    for other, info in indexes.items():
        if other == name or other in dropping:
            continue
        if info["cols"][: len(cols)] == cols:
            return other
    return None


def in_blocked_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone(timedelta(hours=8)))
    t = now.time()
    if now.weekday() < 5 and time(9, 15) <= t < time(15, 5):
        return True
    return time(16, 35) <= t < time(18, 35)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ignore-window", action="store_true")
    a = ap.parse_args()
    if a.apply and not a.ignore_window and in_blocked_window():
        print("当前在禁止窗口内，拒绝执行（--ignore-window 仅测试）")
        return 2
    dropped = skipped = 0
    with SessionLocal() as db:
        # ALTER 在等元数据锁时会堵住该表后续所有查询：最多等 10 秒，拿不到就放弃这一项（2026-10-25 曾遇到僵尸长查询持锁）。
        db.execute(text("SET SESSION lock_wait_timeout = 10"))
        for table, names in CANDIDATES.items():
            idx = load_indexes(db, table)
            drop_set = {n for n in names if n in idx}
            for n in names:
                if n not in idx:
                    print(f"{table}.{n}: 已不存在，跳过")
                    continue
                if idx[n]["unique"] or n == "PRIMARY":
                    print(f"{table}.{n}: 唯一键，禁止删除")
                    skipped += 1
                    continue
                cover = covering_index(idx, n, drop_set - {n})
                if not cover:
                    print(f"{table}.{n}{idx[n]['cols']}: 无覆盖索引，跳过")
                    skipped += 1
                    continue
                print(f"{table}.{n}{idx[n]['cols']} ← 被 {cover}{idx[n]['cols'] and idx[cover]['cols']} 覆盖 {'DROP' if a.apply else '(dry-run)'}", flush=True)
                if a.apply:
                    try:
                        db.execute(text(f"ALTER TABLE `{table}` DROP INDEX `{n}`, ALGORITHM=INPLACE, LOCK=NONE"))
                        db.commit()
                    except Exception as exc:  # noqa: BLE001 - 锁等待超时等：跳过，下次再来
                        db.rollback()
                        print(f"  !! {table}.{n} 删除失败，已跳过: {type(exc).__name__}: {str(exc)[:100]}", flush=True)
                        skipped += 1
                        continue
                    drop_set.discard(n)
                    idx = load_indexes(db, table)
                    dropped += 1
    print(f"完成：dropped={dropped} skipped={skipped} apply={a.apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
