"""Built-in version registry; all production schema changes are forward-only."""
from __future__ import annotations

from ..auxiliary_unique import registered_auxiliary_unique_migrations
from ..contract import Migration
from .p0_research import migrations as p0_research_migrations
from .v000_baseline import migration as baseline_migration
from .v003_add_model_route_max_tokens import migrations as model_route_max_tokens_migrations
from .v004_add_model_route_temperature import migrations as model_route_temperature_migrations
from .v005_add_model_route_timeout_seconds import migrations as model_route_timeout_seconds_migrations
from .v024_auxiliary_partition_snapshots import migrations as auxiliary_partition_snapshot_migrations
from .v025_strategy_version_snapshots import migrations as strategy_version_snapshot_migrations
from .v026_recommendation_execution_states import migrations as recommendation_execution_state_migrations
from .workbench_run_lifecycle import migrations as workbench_run_lifecycle_migrations


def all_migrations() -> tuple[Migration, ...]:
    return (
        baseline_migration,
        *p0_research_migrations(),
        *workbench_run_lifecycle_migrations(),
        *model_route_max_tokens_migrations(),
        *model_route_temperature_migrations(),
        *model_route_timeout_seconds_migrations(),
        *registered_auxiliary_unique_migrations(),
        *auxiliary_partition_snapshot_migrations(),
        *strategy_version_snapshot_migrations(),
        *recommendation_execution_state_migrations(),
    )
