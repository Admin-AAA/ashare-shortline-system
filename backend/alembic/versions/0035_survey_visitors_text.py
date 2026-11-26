"""institutional_surveys 修正字段语义：fund_visitors 存接待人员文本，新增 org_count 存机构家数。

问题：上游 `stk_surv` / akshare `jgdy_em` 的 `fund_visitors` 是**接待人员姓名**
（如「董事、董事会秘书 王珏,证券事务代表 徐闻」），不是数字。原表把它定义成 INT，
采集时 `_int_or_none` 转失败写 NULL，视图层再 `sum()` 出来恒为 0——
`survey_fund_visitors` 从上线起就没有过有效值。

上游真正的数字字段是 `org_count`（接待机构数量，akshare 备用源提供）。
本修订：
1. fund_visitors 改 TEXT，存原始人员文本；
2. 新增 org_count INT，存机构家数；
3. 存量 fund_visitors 全为 0/NULL，无信息损失，直接改类型即可。

只对 MySQL 生效；SQLite（单测）由 Base.metadata.create_all 按模型建表。
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0035_survey_visitors_text"
down_revision = "0034_drop_redundant_minute_index"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

TABLE = "institutional_surveys"


def _column_type(bind, table: str, column: str) -> str | None:
    return bind.execute(sa.text(
        "SELECT DATA_TYPE FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.execute("SET SESSION lock_wait_timeout = 60")
    if _column_type(bind, TABLE, "fund_visitors") == "int":
        # INT -> TEXT 不支持 INPLACE（MySQL 限制），表只有 4.6 万行，COPY 很快。
        # 存量全是 0/NULL，转 TEXT 不丢信息。
        op.execute(f"ALTER TABLE {TABLE} MODIFY COLUMN fund_visitors TEXT NULL")
        log.info("institutional_surveys.fund_visitors INT -> TEXT")
    if _column_type(bind, TABLE, "org_count") is None:
        op.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN org_count INT NULL, ALGORITHM=INSTANT"
        )
        log.info("institutional_surveys.org_count 已新增")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if _column_type(bind, TABLE, "org_count") is not None:
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN org_count, ALGORITHM=INPLACE, LOCK=NONE")
    if _column_type(bind, TABLE, "fund_visitors") == "text":
        op.execute(
            f"ALTER TABLE {TABLE} MODIFY COLUMN fund_visitors INT NULL, "
            "ALGORITHM=INPLACE, LOCK=NONE"
        )
