"""验证 session.py 的 SET NAMES utf8mb4 防御：新连接创建后会话字符集为 utf8mb4。"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import RecommendationRun  # noqa: E402
from sqlalchemy import text  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        charset = db.execute(text("SELECT @@character_set_client, @@character_set_connection")).one()
        print(f"character_set_client={charset[0]} character_set_connection={charset[1]}")
        # 中文往返写入验证（写一条临时 run_name 再读回，最后回滚）
        run = db.query(RecommendationRun).filter(RecommendationRun.run_name.like("REPLAY-%")).first()
        if run:
            original = run.run_name
            run.run_name = "字符集验证-中文往返"
            db.flush()
            db.refresh(run)
            ok = run.run_name == "字符集验证-中文往返"
            print(f"中文往返写入: {'OK' if ok else 'FAIL'} (读回={run.run_name!r})")
            run.run_name = original
            db.commit()
        else:
            print("无 REPLAY run 可验证，跳过往返测试")
    finally:
        db.close()


if __name__ == "__main__":
    main()
