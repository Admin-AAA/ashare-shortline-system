"""backend 根 conftest：覆盖 tests/ 之外的测试目录（app/**/tests）。

tests/conftest.py 只对 tests/ 生效；`pytest app/...` 过去会直接用 .env 里的生产库。
这里在任何 app 导入前把生产库 URL 改指 ashare_test，并标记子进程；
app.db.session 另有第二道闸，pytest 下连 ashare_workbench 一律拒绝。
"""
import os

os.environ["ASHARE_UNDER_PYTEST"] = "1"
os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:9/api")
# 测试日志不写生产 backend/logs/app.log：否则全量回归的数千条测试日志（rec_conflict、假补数失败、
# 假失联回收…）混进线上日志，排障时被当成真故障，还会把 app.log 提前轮转掉。
os.environ.setdefault(
    "LOG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "pytest-logs")
)


def _redirect_to_test_db() -> None:
    from sqlalchemy.engine import make_url

    raw = (os.environ.get("ASHARE_TEST_DATABASE_URL") or "").strip()
    if not raw:
        from app.core.config import Settings

        raw = (os.environ.get("ASHARE_DATABASE_URL") or Settings().database_url).strip()
    if raw.startswith("mysql") and make_url(raw).database == "ashare_workbench":
        raw = make_url(raw).set(database="ashare_test").render_as_string(hide_password=False)
    if raw.startswith("mysql"):
        os.environ["ASHARE_DATABASE_URL"] = raw


_redirect_to_test_db()
