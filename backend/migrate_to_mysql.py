"""
SQLite -> MySQL 数据迁移脚本（批量 INSERT 优化版）

用法:
    python migrate_to_mysql.py [--clear]

前提:
    - SSH 隧道已建立: ssh -f -N -L 13306:127.0.0.1:3306 root@139.196.242.53
    - MySQL 用户 aowang 已创建，数据库 ashare_workbench 已存在
"""

import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import Session
from app.models.tables import Base
from app.db.session import engine as sqlite_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")

MYSQL_URL = "mysql+pymysql://aowang:1279990918@127.0.0.1:13306/ashare_workbench?charset=utf8mb4"

INSERTS_PER_STMT = 500  # 每个 INSERT 语句插入的行数

TABLE_ORDER = [
    "stocks",
    "daily_bars",
    "daily_indicators",
    "raw_quotes",
    "raw_daily_bars",
    "unified_quotes",
    "data_source_health",
    "data_source_config",
    "market_snapshots",
    "skill_registry",
    "skill_versions",
    "strategy_versions",
    "presets",
    "preset_skills",
    "recommendation_runs",
    "recommendations",
    "recommendation_outcomes",
    "review_notes",
    "watch_items",
    "agent_decision_logs",
    "backtest_runs",
    "calibration_config",
    "raw_limit_up_board",
    "raw_concept_sector",
    "raw_capital_flow",
    "raw_dragon_tiger",
    "raw_news_announcement",
    "limit_up_board",
    "concept_sector",
    "concept_sector_stock",
    "capital_flow",
    "dragon_tiger",
    "news_announcement",
    "api_call_logs",
    "scheduled_tasks",
]


def multi_insert(conn, table_name: str, cols: list[str], rows_data: list[list]):
    """执行多行 INSERT: INSERT INTO t (a,b) VALUES (1,2), (3,4), ..."""
    if not rows_data:
        return
    col_names = ", ".join(f"`{c}`" for c in cols)
    # 构建 value 占位符: (:c0_0,:c1_0), (:c0_1,:c1_1), ...
    value_groups = []
    params = {}
    for ri, row in enumerate(rows_data):
        placeholders = []
        for ci, col in enumerate(cols):
            key = f"c{ci}_{ri}"
            placeholders.append(f":{key}")
            params[key] = row[ci]
        value_groups.append("(" + ", ".join(placeholders) + ")")

    sql = f"INSERT INTO `{table_name}` ({col_names}) VALUES {', '.join(value_groups)}"
    conn.execute(text(sql), params)


def main():
    clear = "--clear" in sys.argv

    logger.info("Connecting to MySQL...")
    mysql_engine = create_engine(
        MYSQL_URL,
        pool_size=2,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )

    with mysql_engine.connect() as conn:
        result = conn.execute(text("SELECT VERSION()"))
        logger.info("MySQL version: %s", result.scalar())

    if clear:
        logger.info("Clearing MySQL tables...")
        Base.metadata.drop_all(mysql_engine)
        logger.info("Tables dropped")

    logger.info("Creating tables in MySQL...")
    Base.metadata.create_all(mysql_engine)

    mysql_tables = {t for t in TABLE_ORDER}  # 信任 create_all 已创建

    # 外键检查在每个事务中关闭（session 级别，无需 SUPER 权限）

    total_rows = 0

    for table_name in TABLE_ORDER:
        with Session(sqlite_engine) as sqlite_session:
            sqlite_count = sqlite_session.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar()

        if sqlite_count == 0:
            logger.info("Table %s: empty, skip", table_name)
            continue

        with mysql_engine.connect() as conn:
            mysql_count = conn.execute(
                text(f"SELECT COUNT(*) FROM `{table_name}`")
            ).scalar()

        if mysql_count > 0:
            logger.info("Table %s: already has %d rows, skip", table_name, mysql_count)
            total_rows += mysql_count
            continue

        logger.info("Migrating %s: %d rows...", table_name, sqlite_count)

        insp = inspect(mysql_engine)
        cols = [c["name"] for c in insp.get_columns(table_name)]

        SCAN_BATCH = 20000
        offset = 0
        migrated = 0
        t0 = time.perf_counter()

        while offset < sqlite_count:
            with Session(sqlite_engine) as sqlite_session:
                rows = sqlite_session.execute(
                    text(f"SELECT * FROM {table_name} LIMIT {SCAN_BATCH} OFFSET {offset}")
                ).fetchall()

            if not rows:
                break

            # 在 MySQL 连接中批量插入（每次 INSERTS_PER_STMT 行）
            with mysql_engine.begin() as conn:
                conn.execute(text("SET SESSION foreign_key_checks = 0"))
                for i in range(0, len(rows), INSERTS_PER_STMT):
                    chunk = rows[i : i + INSERTS_PER_STMT]
                    multi_insert(conn, table_name, cols, chunk)

            migrated += len(rows)
            offset += SCAN_BATCH
            elapsed = time.perf_counter() - t0
            rate = migrated / elapsed if elapsed > 0 else 0
            logger.info(
                "  %s: %d/%d (%.1f%%) %.0f rows/s",
                table_name, migrated, sqlite_count,
                100 * migrated / sqlite_count, rate,
            )

        total_elapsed = time.perf_counter() - t0
        logger.info(
            "Table %s: done %d rows in %.1fs (%.0f rows/s)",
            table_name, migrated, total_elapsed,
            migrated / total_elapsed if total_elapsed > 0 else 0,
        )
        total_rows += migrated

    # 外键检查已经在每个事务提交后自动恢复（session 级别）

    logger.info("Migration complete: %d total rows", total_rows)

    # Quick verification
    with mysql_engine.connect() as conn:
        for table_name in TABLE_ORDER:
            cnt = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()
            if cnt > 0:
                logger.info("  %s: %d rows", table_name, cnt)


if __name__ == "__main__":
    main()
