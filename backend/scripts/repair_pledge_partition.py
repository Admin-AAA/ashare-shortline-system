import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import PledgeStat
from app.services.auxiliary.contracts import AuxiliaryDatasetRequest
from app.services.auxiliary.production import AUXILIARY_DATASET_BY_KEY
from app.services.auxiliary.state_adapters import (
    SQLAlchemyStateRepository,
    pledge_rows_from_frame,
)
from app.services.tushare_client import get_pro


def _local_size(db, iso_day: str) -> int:
    return int(db.scalar(select(func.count()).select_from(PledgeStat).where(PledgeStat.end_date == iso_day)) or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="按指定披露日补全 pledge_stats 的残缺期")
    ap.add_argument("--end-date", required=True, help="披露日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    compact = args.end_date.replace("-", "")
    iso_day = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    db = SessionLocal()
    try:
        before = _local_size(db, iso_day)
        frame = get_pro().pledge_stat(end_date=compact)
        upstream = 0 if frame is None else len(frame)
        print(f"{iso_day} 本地={before} 上游={upstream}")
        if upstream == 0:
            print("上游该披露日无数据，不写库")
            return 0
        got = {str(v).replace("-", "") for v in frame["end_date"].unique()}
        if got != {compact}:
            raise RuntimeError(f"上游返回的 end_date 与请求不一致：{sorted(got)} != {compact}")
        if not args.apply:
            print("未加 --apply，不写库")
            return 0
        rows = pledge_rows_from_frame(frame, set())
        repository = SQLAlchemyStateRepository(
            connection=db.connection(), entry=AUXILIARY_DATASET_BY_KEY["pledge_stat"]
        )
        request = AuxiliaryDatasetRequest(
            dataset_key="pledge_stat",
            instruments=(),
            trade_date=date.fromisoformat(iso_day),
            refresh_enabled=True,
            requested_at=datetime.now(timezone.utc),
        )
        result = repository.upsert(rows, request)
        db.commit()
        print(f"写入 {result.attempted_rows} 行，本地={_local_size(db, iso_day)}")
        return 0
    finally:
        db.close()


sys.exit(main())
