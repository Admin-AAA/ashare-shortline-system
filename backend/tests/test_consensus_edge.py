"""L-126 自适应共识闸门的产线化：统计量口径 + 逐日政策选择 + 保守回退。

统计量（严格因果）：`edge(t) = mean(net | 背书≥3) − mean(net | 背书<3)`，
只用 `trade_date < t` 且已结算（outcome_label ∈ success/failed）的成交，窗口 = 最近 15 个交易日。
选择：edge>0 → v20（正向分支）；edge<−1.5 → v21（反向分支）；其余/样本不足 → 默认（v19）。
"""
from __future__ import annotations

from app.services.consensus_edge import (
    NEGATIVE_VERSION,
    NEUTRAL_VERSION,
    POSITIVE_VERSION,
    compute_consensus_edge,
    count_non_veto_signals,
    resolve_adaptive_selection_policy,
)
from app.engine.production_policy import (
    load_production_selection_policy,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDB:
    """最小 DB 桩：记录 SQL，返回预设行（trade_date, skill_breakdown_json, net）。"""

    def __init__(self, rows, *, raise_on_query: bool = False):
        self._rows = rows
        self._raise = raise_on_query
        self.executed = 0

    def execute(self, _stmt, _params=None):
        self.executed += 1
        if self._raise:
            raise RuntimeError("db down")
        return _FakeResult(self._rows)


def _breakdown(nsigs: int) -> list[dict]:
    return [
        {"strategy_id": f"skill-{i}", "score": 80.0, "hard_veto": False} for i in range(nsigs)
    ]


def test_count_non_veto_signals_handles_json_and_veto() -> None:
    assert count_non_veto_signals(_breakdown(3)) == 3
    assert count_non_veto_signals('[{"strategy_id":"a","hard_veto":false},{"strategy_id":"b","hard_veto":true}]') == 1
    assert count_non_veto_signals(None) == 0
    assert count_non_veto_signals({"not": "a list"}) == 0


def test_edge_positive_and_negative_branches() -> None:
    # 近窗口里"≥3 背书"赚、"<3 背书"亏 → edge 明显为正
    rows = [
        ("2026-09-01", _breakdown(3), 2.0),
        ("2026-09-01", _breakdown(3), 1.0),
        ("2026-09-01", _breakdown(4), 1.5),
        ("2026-09-01", _breakdown(2), -3.0),
        ("2026-09-02", _breakdown(4), 3.0),
        ("2026-09-02", _breakdown(3), 0.5),
        ("2026-09-02", _breakdown(1), -2.0),
        ("2026-09-02", _breakdown(2), -1.0),
        ("2026-09-02", _breakdown(2), -1.5),
        ("2026-09-02", _breakdown(1), -0.5),
    ]
    edge = compute_consensus_edge(_FakeDB(rows), "2026-09-03")
    assert edge is not None and edge > 0
    default = load_production_selection_policy(version=19)
    chosen = resolve_adaptive_selection_policy(_FakeDB(rows), "2026-09-03", default)
    assert chosen.version == POSITIVE_VERSION

    neg_rows = [
        ("2026-09-01", _breakdown(3), -3.0),
        ("2026-09-01", _breakdown(3), -2.0),
        ("2026-09-01", _breakdown(4), -2.5),
        ("2026-09-01", _breakdown(2), 1.0),
        ("2026-09-02", _breakdown(4), -3.0),
        ("2026-09-02", _breakdown(3), -1.0),
        ("2026-09-02", _breakdown(2), 2.0),
        ("2026-09-02", _breakdown(1), 1.5),
        ("2026-09-02", _breakdown(2), 1.0),
        ("2026-09-02", _breakdown(1), 0.5),
    ]
    edge_neg = compute_consensus_edge(_FakeDB(neg_rows), "2026-09-03")
    assert edge_neg is not None and edge_neg < -1.5
    chosen_neg = resolve_adaptive_selection_policy(_FakeDB(neg_rows), "2026-09-03", default)
    assert chosen_neg.version == NEGATIVE_VERSION


def test_insufficient_sample_and_failures_fall_back_to_default() -> None:
    default = load_production_selection_policy(version=19)
    # 样本不足（任一侧 <5 笔）→ 保持默认
    few = [("2026-09-01", _breakdown(3), 1.0), ("2026-09-01", _breakdown(2), -1.0)]
    assert compute_consensus_edge(_FakeDB(few), "2026-09-03") is None
    # 样本不足 → 原样返回默认政策（默认即中性分支 v19）
    assert resolve_adaptive_selection_policy(_FakeDB(few), "2026-09-03", default) is default
    # DB 异常 → 不抛错、回落默认
    assert (
        resolve_adaptive_selection_policy(_FakeDB([], raise_on_query=True), "2026-09-03", default).version
        == default.version
    )


def test_disabled_flag_returns_policy_unchanged() -> None:
    """未启用自适应（v15 等）时，解析器必须原样返回传入政策（逐位不变）。"""
    legacy = load_production_selection_policy(version=15)
    assert legacy.adaptive_consensus_enabled is False
    chosen = resolve_adaptive_selection_policy(_FakeDB([]), "2026-09-03", legacy)
    assert chosen is legacy
