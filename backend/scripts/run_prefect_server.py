"""Start the local Prefect server as an explicit development process."""
from __future__ import annotations

import os
import subprocess
import sys

# Prefect 默认只清理 events，flow run 历史永久保留：stale-run-recovery 每 5 分钟、
# 盘中锁利轮询每 5 分钟各产生一条 flow run，07-30 到 10-10 已积累约 1.9 万条，
# SQLite 文件涨到 825MB，写事务变慢、锁冲突变多。打开 flow_runs 清理并保留 30 天，
# 足够复盘一个月内的调度记录（业务侧执行记录在 MySQL scheduled_task_runs，不受影响）。
_PREFECT_SERVER_ENV_DEFAULTS = {
    "PREFECT_SERVER_SERVICES_DB_VACUUM_ENABLED": "events,flow_runs",
    "PREFECT_SERVER_SERVICES_DB_VACUUM_RETENTION_PERIOD": str(30 * 24 * 3600),
}


def main() -> None:
    env = {**_PREFECT_SERVER_ENV_DEFAULTS, **os.environ}
    raise SystemExit(subprocess.call([sys.executable, "-m", "prefect", "server", "start"], env=env))


if __name__ == "__main__":
    main()
