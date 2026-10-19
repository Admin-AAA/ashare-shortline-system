"""app.db.session 第二道闸：pytest 下拒连生产库 ashare_workbench。"""
import pytest

from app.db import session as db_session


def test_refuses_production_mysql_under_pytest():
    with pytest.raises(RuntimeError, match="production database"):
        db_session._refuse_production_db_under_pytest(
            "mysql+pymysql://u:p@127.0.0.1:3306/ashare_workbench?charset=utf8mb4"
        )


def test_allows_test_db_and_sqlite():
    db_session._refuse_production_db_under_pytest("mysql+pymysql://u:p@127.0.0.1:3306/ashare_test")
    db_session._refuse_production_db_under_pytest("sqlite:///:memory:")


def test_conftest_marks_subprocess_env():
    import os

    assert os.environ.get("ASHARE_UNDER_PYTEST") == "1"


def test_engine_is_not_production():
    assert db_session.engine.url.database != db_session.PRODUCTION_DB_NAME
