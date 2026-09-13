"""一次性运维脚本：等 daily_kbars 的 MDL 空闲后落列 ivol_dn20，再补 alembic 版本与回填。

设计要点（避免伤害并行实例）：
- 每次 ALTER 只申请 1 秒锁等待窗口（lock_wait_timeout=1）；拿不到就立刻撤回，
  绝不让一个 pending EXCLUSIVE 长期挂在 MDL 队列上阻塞其它读者。
- 拿到锁后立刻落列（ADD COLUMN 是很快的元数据操作），随即用 alembic 记录版本。
- 列已存在时 0019 的 upgrade 是幂等的，只补版本号。
"""
from __future__ import annotations
import subprocess
import sys
import time
from pathlib import Path

B = Path(__file__).resolve().parent
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

MAX_TRIES = 240          # 240 × 20s ≈ 80 分钟预算
SLEEP_S = 20


def column_exists() -> bool:
    with SessionLocal() as s:
        cols = {r[0] for r in s.execute(text("SHOW COLUMNS FROM daily_kbars")).fetchall()}
    return "ivol_dn20" in cols


def try_add() -> str:
    """返回 'added' / 'exists' / 'locked'。"""
    if column_exists():
        return "exists"
    with SessionLocal() as s:
        s.execute(text("SET SESSION lock_wait_timeout=1"))
        try:
            s.execute(text("ALTER TABLE daily_kbars ADD COLUMN ivol_dn20 FLOAT NULL"))
            s.commit()
            return "added"
        except Exception as exc:  # noqa: BLE001
            s.rollback()
            return "locked" if "1205" in str(exc) or "lock" in str(exc).lower() else f"error:{exc}"


def main() -> None:
    for i in range(1, MAX_TRIES + 1):
        r = try_add()
        print(f"[{i}/{MAX_TRIES}] {time.strftime('%H:%M:%S')} -> {r}", flush=True)
        if r in ("added", "exists"):
            break
        time.sleep(SLEEP_S)
    else:
        print("GIVE_UP: 列在预算内未能落库（仍被并行实例的长事务阻塞）", flush=True)
        return

    print("== alembic upgrade head ==", flush=True)
    rc = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(B)).returncode
    print(f"alembic rc={rc}", flush=True)

    print("== backfill ivol_dn20 ==", flush=True)
    rc = subprocess.run(
        [sys.executable, str(B / "scripts" / "backfill_ivol20.py"),
         "--start", "2024-06-01", "--metric", "ivol_dn20"],
        cwd=str(B),
    ).returncode
    print(f"backfill rc={rc}", flush=True)
    print("BOOTSTRAP_DONE", flush=True)


if __name__ == "__main__":
    main()
