"""回归测试：champion 同口径基线重建脚本的口径与统计契约。

锁定三件事，防止未来改动悄悄破坏 §2 / L-001 / L-002 要求的口径隔离：
1. canonical 前瞻口径（official_sample_conditions）必须要求 provenance 完整，
   且带 data_as_of <= trade_date 的因果约束。
2. 旧宽口径（FORMAL_ALL_LEGACY）确实不限制 evidence_class，因此会把
   historical_reconstruction / unverified 样本算进来——这正是需要被量化的差值来源。
3. 指标与等权组合资金曲线（含最大回撤）的纯函数语义正确。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.tables import Recommendation, RecommendationRun  # noqa: E402
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402

SCRIPT_PATH = BACKEND_ROOT / "scripts" / "si_champion_baseline_rebuild.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("si_champion_baseline_rebuild", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_script_module()


def _compiled_where(conditions) -> str:
    stmt = select(Recommendation.recommendation_id).where(*conditions)
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_strict_scope_requires_live_forward_evidence_class():
    sql = _compiled_where(official_sample_conditions())
    assert "evidence_class" in sql
    assert "live_forward" in sql


def test_strict_scope_requires_complete_provenance():
    sql = _compiled_where(official_sample_conditions())
    for column in (
        "actual_generated_at",
        "data_as_of",
        "source_snapshot_id",
        "evidence_identity_hash",
        "strategy_policy_fingerprint",
        "execution_policy_fingerprint",
        "code_revision",
        "provenance_schema_version",
    ):
        assert column in sql, f"canonical 前瞻口径缺少 provenance 列 {column}"


def test_strict_scope_enforces_causal_data_as_of():
    sql = _compiled_where(official_sample_conditions())
    assert "data_as_of <= recommendation_runs.trade_date" in sql


def test_legacy_scope_does_not_restrict_evidence_class(mod):
    sql = _compiled_where(mod.LEGACY_CONDITIONS)
    assert "evidence_class" not in sql
    assert "run_mode" in sql and "completed" in sql


def test_strict_and_legacy_are_distinct_populations(mod):
    strict_sql = _compiled_where(mod.STRICT_CONDITIONS)
    legacy_sql = _compiled_where(mod.LEGACY_CONDITIONS)
    assert strict_sql != legacy_sql
    # 严格口径必须是宽口径的真子集：两者都限定 formal + completed + rec_ 前缀
    assert "run_mode" in legacy_sql and "run_mode" in strict_sql


def test_metrics_semantics(mod):
    records = [
        {"net": 4.0, "trade_date": "2026-01-05"},
        {"net": -6.0, "trade_date": "2026-01-05"},
        {"net": 2.0, "trade_date": "2026-01-06"},
    ]
    m = mod._metrics(records)
    assert m["n"] == 3
    assert m["trade_days"] == 2
    assert m["win_rate_pct"] == pytest.approx(66.7, abs=0.05)
    assert m["avg_net_pct"] == pytest.approx(0.0, abs=1e-6)
    assert m["profit_factor"] == pytest.approx(1.0, abs=1e-6)
    assert m["max_loss_pct"] == pytest.approx(-6.0, abs=1e-6)


def test_metrics_all_losses_pf_is_zero(mod):
    m = mod._metrics([{"net": -1.0, "trade_date": "d1"}, {"net": -2.0, "trade_date": "d1"}])
    assert m["profit_factor"] == 0.0
    assert m["win_rate_pct"] == 0.0


def test_equity_curve_mdd_and_cum(mod):
    records = [
        {"net": 10.0, "trade_date": "2026-01-05"},
        {"net": -10.0, "trade_date": "2026-01-06"},
        {"net": -10.0, "trade_date": "2026-01-07"},
    ]
    curve = mod._equity_curve(records)
    # 100 -> 110 -> 99 -> 89.1：累计 -10.9%，峰后回撤 (89.1-110)/110 = -19.0%
    assert curve["cum_net_pct"] == pytest.approx(-10.9, abs=0.05)
    assert curve["max_drawdown_pct"] == pytest.approx(-19.0, abs=0.05)
    assert len(curve["daily"]) == 3
    assert "equal_weight" in curve["method"]


def test_board_classification(mod):
    assert mod._board("300790") == "20cm"
    assert mod._board("688536") == "20cm"
    assert mod._board("301001") == "20cm"
    assert mod._board("600000") == "10cm"
    assert mod._board("000001") == "10cm"


def test_score_floor_clause_filters_high_scores(mod):
    class _Result:
        def all(self):
            return []

    class _FakeDB:
        def __init__(self):
            self.statements = []

        def scalars(self, stmt):
            self.statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
            return _Result()

    def _where_clause(stmt_sql: str) -> str:
        return stmt_sql.split("WHERE", 1)[-1]

    db = _FakeDB()
    mod._selected(db, mod.STRICT_CONDITIONS, 72.0)
    assert "final_score >= 72.0" in _where_clause(db.statements[-1])
    mod._selected(db, mod.STRICT_CONDITIONS, None)
    assert "final_score >=" not in _where_clause(db.statements[-1])
