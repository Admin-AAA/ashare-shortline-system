import sys
import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_DB = BACKEND_ROOT / ".pytest_ashare_workbench.db"
os.environ["ASHARE_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
