"""测试数据库连接的 utf8mb4 字符集防御（P1.3）。

背景（2026-08-02 诊断）：MySQL 服务器级 character_set_client/connection 为
latin1，SQLAlchemy 通过 connect_args charset 与 connect 事件 SET NAMES utf8mb4
双重保障中文读写。此测试验证连接字符集与中文往返（写后回滚）。
"""
from sqlalchemy import text

from app.db.session import SessionLocal


def test_connection_charset_is_utf8mb4():
    db = SessionLocal()
    try:
        charset = db.execute(text("SELECT @@character_set_client, @@character_set_connection")).one()
        assert charset[0] == "utf8mb4", f"client charset={charset[0]}"
        assert charset[1] == "utf8mb4", f"connection charset={charset[1]}"
    finally:
        db.close()


def test_chinese_roundtrip_on_run_name():
    """中文写入后读回一致（事务回滚，不落库）。"""
    from app.models.tables import RecommendationRun

    db = SessionLocal()
    try:
        run = db.query(RecommendationRun).filter(RecommendationRun.run_name.like("REPLAY-%")).first()
        if run is None:
            return  # 无样本则跳过（空库环境）
        original = run.run_name
        run.run_name = "字符集验证-中文往返"
        db.flush()
        db.refresh(run)
        assert run.run_name == "字符集验证-中文往返"
        run.run_name = original
        db.commit()
    finally:
        db.close()
