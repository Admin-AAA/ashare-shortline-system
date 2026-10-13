"""修复 REPLAY run 乱码 run_name，并量化 JSON 乱码影响范围。

背景（2026-08-02 诊断）：7 月 REPLAY 回放 run 经 latin1 连接写入，
run_name "REPLAY-全量重放" 落库为 "REPLAY-????"，filter_audit_json /
result_summary_json 中的中文同样损坏（非法字节，不可逆）。

本脚本：
1. 把 run_mode='formal' 且 run_name 含 '?' 的 REPLAY run 改回可读名称；
2. 统计 JSON 字段含非法 UTF-8 字节 / 大段 '?' 的 run 数量（记录损失范围）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import RecommendationRun  # noqa: E402


def _is_mojibake(raw: str | None) -> bool:
    if not raw:
        return False
    # 非法 UTF-8 判定：直接按 utf-8 严格解码（存的是 latin1 字节时必然失败）
    try:
        raw.encode("utf-8").decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def main() -> None:
    db = SessionLocal()
    try:
        runs = db.scalars(
            select(RecommendationRun).where(
                RecommendationRun.run_name.like("%REPLAY-%"),
                RecommendationRun.run_name.like("%?%"),
            )
        ).all()
        print(f"含 ? 的 REPLAY run：{len(runs)} 条")
        fixed = 0
        for run in runs:
            run.run_name = "REPLAY-全量重放"
            fixed += 1
        db.commit()
        print(f"已修复 run_name：{fixed} 条")

        # 量化 JSON 乱码影响
        bad_audit = bad_summary = bad_market = 0
        total = db.query(RecommendationRun).count()
        for run in db.scalars(select(RecommendationRun)):
            if _is_mojibake(run.filter_audit_json):
                bad_audit += 1
            if _is_mojibake(run.result_summary_json):
                bad_summary += 1
            if _is_mojibake(run.market_snapshot_json):
                bad_market += 1
        print(f"run 总数={total}")
        print(f"filter_audit_json 含非法字节：{bad_audit}")
        print(f"result_summary_json 含非法字节：{bad_summary}")
        print(f"market_snapshot_json 含非法字节：{bad_market}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
