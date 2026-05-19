import sys
import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_DB_URL = (os.environ.get("ASHARE_TEST_DATABASE_URL") or os.environ.get("ASHARE_DATABASE_URL") or "").strip()
if not TEST_DB_URL:
    raise RuntimeError("运行后端测试前必须设置 ASHARE_TEST_DATABASE_URL，并指向独立的 MySQL 测试库。")
if not TEST_DB_URL.startswith("mysql"):
    raise RuntimeError("后端测试仅支持 MySQL；ASHARE_TEST_DATABASE_URL 必须是 MySQL 连接串。")

os.environ["ASHARE_DATABASE_URL"] = TEST_DB_URL
