"""值分布哨兵必须接到修复路径上，按钮才能自己修「有数据但值是错的」。

背景数据墙：健康度 87 分里 12 分来自值分布哨兵（pct_chg / turnover_rate 恒同值各扣 5、
upper_shadow_count20 扣 2），1 分来自概念板块成分滞后。而修复服务的动作计划此前完全由结构
信号生成（近 10 交易日缺失/不完整日期 + 各表 lag_days），`value_distribution` 在整个
2510 行的服务文件里出现 0 次——哨兵只接到评分路径。dry_run 实证：当时只规划出补 news 一个
动作，13 分扣分一分不动。concept_member 也不在可修复清单里。

本测试锁定四件事：
  1. 两个数据域登记进可修复清单
  2. 哨兵报出的失真列会生成 value_distribution 动作
  3. 失真列按修复手段正确分流（权威行情列 / 本地派生列）
  4. 哨兵全绿时不产生多余动作
"""
from __future__ import annotations

from app.services.data_gap_remediation_service import (
    DEFAULT_REMEDIABLE_TABLES,
    LOCAL_DERIVED_REPAIR_COLUMNS,
    QUOTE_METRIC_REPAIR_COLUMNS,
    _build_remediation_plan,
    _normalize_target_tables,
)

REFERENCE_DATE = "2026-09-24"


def _diagnosis(*, distorted: list[dict], stale_domains: list[dict] | None = None) -> dict:
    """最小诊断快照：结构维度全绿，只留值分布与数据域两处信号。"""
    return {
        "reference_trade_date": REFERENCE_DATE,
        "lag_days": {},
        "recent_trade_day_coverage": {"tables": {}},
        "data_domains": {"stale_domains": stale_domains or []},
        "value_distribution": {
            "reference_trade_date": REFERENCE_DATE,
            "distorted": distorted,
        },
    }


def _plan(diagnosis: dict, tables: list[str]) -> list[dict]:
    return _build_remediation_plan(
        None,
        diagnosis,
        requested_tables=_normalize_target_tables(tables),
        capital_flow_code_limit=200,
        daily_kbar_code_limit=200,
        minute_kbar_code_limit=200,
    )


def test_two_domains_are_registered_as_remediable():
    """不在清单里的数据域，按钮无从下手。"""
    assert "value_distribution" in DEFAULT_REMEDIABLE_TABLES
    assert "concept_member" in DEFAULT_REMEDIABLE_TABLES


def test_sentinel_distortion_becomes_a_plan_action():
    """哨兵报出的失真列必须变成动作，而不是只在评分里扣分。"""
    diagnosis = _diagnosis(
        distorted=[
            {"column": "pct_chg", "critical": True},
            {"column": "turnover_rate", "critical": True},
            {"column": "upper_shadow_count20", "critical": False},
        ]
    )
    items = [item for item in _plan(diagnosis, ["value_distribution"]) if item["table"] == "value_distribution"]
    assert len(items) == 1
    item = items[0]
    assert item["trade_date"] == REFERENCE_DATE
    assert item["quote_columns"] == ["pct_chg", "turnover_rate"]
    assert item["local_columns"] == ["upper_shadow_count20"]


def test_distorted_columns_route_to_the_right_repair():
    """权威行情列走重拉日线，本地派生列走本地重算；两类不能混。"""
    assert set(QUOTE_METRIC_REPAIR_COLUMNS).isdisjoint(LOCAL_DERIVED_REPAIR_COLUMNS)
    diagnosis = _diagnosis(distorted=[{"column": "ma5"}, {"column": "close"}])
    item = next(item for item in _plan(diagnosis, ["value_distribution"]) if item["table"] == "value_distribution")
    assert item["quote_columns"] == ["close"]
    assert item["local_columns"] == ["ma5"]


def test_unknown_distorted_column_is_reported_not_silently_dropped():
    """哨兵将来新增列时，修复侧不认识也要如实报出来，不能静默丢弃。"""
    diagnosis = _diagnosis(distorted=[{"column": "chip_cost_50"}, {"column": "ma5"}])
    item = next(item for item in _plan(diagnosis, ["value_distribution"]) if item["table"] == "value_distribution")
    assert item["unsupported_columns"] == ["chip_cost_50"]


def test_healthy_sentinel_plans_nothing():
    """哨兵全绿时不得产生多余动作。"""
    plan = _plan(_diagnosis(distorted=[]), ["value_distribution"])
    assert [item for item in plan if item["table"] == "value_distribution"] == []


def test_stale_concept_member_becomes_a_plan_action():
    """概念板块成分滞后必须可修，此前它根本不在清单里。"""
    diagnosis = _diagnosis(
        distorted=[],
        stale_domains=[{"key": "concept_member", "label": "概念板块成分"}],
    )
    items = [item for item in _plan(diagnosis, ["concept_member"]) if item["table"] == "concept_member"]
    assert len(items) == 1
    assert items[0]["trade_date"] == REFERENCE_DATE


def test_fresh_concept_member_plans_nothing():
    diagnosis = _diagnosis(distorted=[], stale_domains=[])
    assert [item for item in _plan(diagnosis, ["concept_member"]) if item["table"] == "concept_member"] == []
