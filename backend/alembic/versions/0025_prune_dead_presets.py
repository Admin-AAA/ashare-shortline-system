"""presets: prune presets whose strategies were removed; merge duplicate 炒股养家 preset; fix descriptions.

Owner ruling 2026-10-19: keep chen (a4d5e2def09a) and high-range presets; delete presets that only
reference removed strategies; merge 炒股养家大局观 (2ce8e151fdb9) into preset_single_chao_gu with
hide_recent_repeats ON. recommendation_runs.preset_id has no FK; historical runs keep their preset_name.
Data-only migration; downgrade is a no-op.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0025_prune_dead_presets"
down_revision = "0024_share_floats_unique"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

DEAD_PRESETS = (
    "2ce8e151fdb9",  # 炒股养家大局观 — duplicate of preset_single_chao_gu
    "preset_experiment_limit_up_reseal_relay",  # limit-up-reseal-relay (removed)
    "0bc9fb74a9a7",  # high-position-volume-stall-warning (removed)
    "f11bb8fe375b",  # chip-single-peak-breakout (removed)
)


def upgrade() -> None:
    bind = op.get_bind()
    ids = sa.bindparam("ids", expanding=True)
    n_skills = bind.execute(sa.text("DELETE FROM preset_skills WHERE preset_id IN :ids").bindparams(ids), {"ids": list(DEAD_PRESETS)}).rowcount
    n_presets = bind.execute(sa.text("DELETE FROM presets WHERE preset_id IN :ids").bindparams(ids), {"ids": list(DEAD_PRESETS)}).rowcount
    orphans = bind.execute(sa.text(
        "DELETE ps FROM preset_skills ps LEFT JOIN skill_registry s ON s.skill_id = ps.skill_id WHERE s.skill_id IS NULL"
    )).rowcount
    bind.execute(sa.text("UPDATE presets SET hide_recent_repeats = 1 WHERE preset_id = 'preset_single_chao_gu'"))
    bind.execute(sa.text(
        "UPDATE presets SET description = '单策略预设：阶段新高突破动量。' "
        "WHERE preset_id = 'preset_experiment_high_range_breakout'"
    ))
    # preset_formal_opening 的描述在 API 层按成员数动态生成；这里只修正库内陈旧文本。
    bind.execute(sa.text(
        "UPDATE presets SET description = '全部在跑策略的组合预设，盘前 formal run 使用。' "
        "WHERE preset_id = 'preset_formal_opening'"
    ))
    log.info("0025: deleted %s presets, %s preset_skills rows, %s orphan preset_skills rows", n_presets, n_skills, orphans)


def downgrade() -> None:
    pass
