from pathlib import Path

from app.engine.skill_loader import load_skills
from app.engine.strategy_runner import run_strategy
from app.services.feature_builder import build_features
from app.services.workbench_service import _default_preset_skill_rows, _strategy_backtest_adapter_manifest


SHORTLINE_SKILL_IDS = {
    "chen-xiaoqun-skill",
    "chao-gu-yang-jia-skill",
    "overnight-holding-method",
}


def _skills_by_id():
    skills_dir = Path(__file__).resolve().parents[1] / "skills"
    return {skill.skill_id: skill for skill in load_skills(skills_dir)}


def _stock(code: str, **overrides):
    stock = {
        "code": code,
        "name": f"测试{code}",
        "price": 10.0,
        "close": 10.0,
        "pct": 4.0,
        "industry": "机器人",
        "concept_sector_name": "机器人",
        "concept_sector_heat_rank": 3,
        "amount_yi": 8.0,
        "turnover_rate": 6.0,
        "free_mv_yi": 80.0,
        "chg20": 12.0,
        "dist20": 6.0,
        "dist60": 12.0,
        "pos20": 60.0,
        "pos60": 72.0,
        "eff20": 0.55,
        "breakout20": True,
        "primary_excess20": 12.0,
        "off_high_pct": -4.0,
        "vol5_20": 1.4,
        "max_dd20": -8.0,
        "upper_shadow_count20": 2,
        "industry_capacity_like_count": 3,
        "limit_up_days20": 1,
        "main_net_inflow_yi": 2.2,
        "main_net_inflow_ratio": 9.5,
        "super_large_net_inflow_yi": 0.8,
        "large_net_inflow_yi": 0.4,
        "small_net_inflow_yi": -0.5,
        "sector_fund_rank": 3,
        "announcement_negative_count": 0,
        "is_st": False,
        "data_quality": {"capital_flow_checked": True, "missing_fields": []},
        "dsa_profile": {
            "ma5": 9.9,
            "ma10": 9.7,
            "ma20": 9.4,
            "ma60": 9.0,
            "macd_status": "多头",
            "rsi_12": 62.0,
        },
    }
    stock.update(overrides)
    return stock


def _snapshot(**overrides):
    snapshot = {
        "sample_count": 300,
        "up_count": 180,
        "down_count": 90,
        "limit_up_count": 12,
        "limit_down_count": 2,
        "main_industries": ["机器人"],
        "trade_date": "2026-05-12",
        "breadth": {"regime": "强势", "risk": "中"},
    }
    snapshot.update(overrides)
    return snapshot


def _shared_factor_stock():
    stocks = [
        _stock("002001"),
        _stock("002002", is_limit_up=True, limit_up_consecutive_days=1, pct=10.0),
        _stock("002003", is_limit_up=True, limit_up_consecutive_days=2, pct=10.0),
        _stock("002004", is_limit_up=True, limit_up_consecutive_days=4, pct=10.0),
    ]
    return build_features(stocks, _snapshot())[0]


def test_shared_shortline_factors_enter_real_strategy_inputs():
    stock = _shared_factor_stock()
    skills = _skills_by_id()
    missing = SHORTLINE_SKILL_IDS - set(skills)
    assert not missing

    signals = {
        skill_id: run_strategy(skills[skill_id], stock, ["机器人"])
        for skill_id in SHORTLINE_SKILL_IDS
    }

    assert "buy_signal" not in stock
    assert stock["is_main_theme"] is True
    assert stock["is_shortline_leader_candidate"] is True
    assert stock["sector_ladder_completeness_score"] >= 75
    assert stock["capital_resonance_signal"] == "主力共振"

    chen_evidence = "；".join(signals["chen-xiaoqun-skill"]["evidence"])
    yangjia_evidence = "；".join(signals["chao-gu-yang-jia-skill"]["evidence"])
    overnight_evidence = "；".join(signals["overnight-holding-method"]["evidence"])

    assert "龙头候选" in chen_evidence
    assert "主线龙头共振" in chen_evidence
    assert "板块梯队完整" in yangjia_evidence
    assert "板块容量合力" in yangjia_evidence
    assert "尾盘资金承接较强" in overnight_evidence
    assert "隔夜尾盘确认" in overnight_evidence

    setup_matches = {signal["setup_match"] for signal in signals.values()}
    assert setup_matches == {"分歧转一致", "平台突破", "隔夜尾盘确认"}
    assert len({signal["score"] for signal in signals.values()}) > 1
    assert signals["overnight-holding-method"]["strategy_independence"] == 0.9
    assert signals["chen-xiaoqun-skill"]["strategy_independence"] == 0.75


def test_strategies_use_shared_factors_with_different_contract_boundaries():
    skills = _skills_by_id()
    chen = str(skills["chen-xiaoqun-skill"].contract)
    yangjia = str(skills["chao-gu-yang-jia-skill"].contract)
    overnight = str(skills["overnight-holding-method"].contract)

    assert "is_shortline_leader_candidate" in chen
    assert "dragon_tiger_buy_dispersion" in chen
    assert "sector_ladder_completeness_score" in yangjia
    assert "capital_resonance_score" in yangjia
    assert "twenty_cm_risk_gate != 'block'" in overnight
    assert "capital_resonance_score >= 45" in overnight


def test_strategy_backtest_adapter_metadata_comes_from_strategy_contract():
    skills = _skills_by_id()
    adapter = _strategy_backtest_adapter_manifest(skills["overnight-holding-method"])

    assert adapter == {
        "default_action": "run",
        "actions": ["preview_minute_gate", "remediate_minute_gaps", "run"],
    }
    assert _strategy_backtest_adapter_manifest(skills["chen-xiaoqun-skill"]) is None


def test_new_strategy_plugin_can_use_shared_factors_without_core_engine_changes(tmp_path):
    skills_dir = tmp_path / "skills"
    plugin_dir = skills_dir / "generic-momentum"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(
        '{"id":"generic-momentum","name":"通用共享因子动量","version":"0.1.0","enabled":true}',
        encoding="utf-8",
    )
    (plugin_dir / "SKILL.md").write_text(
        "# 通用共享因子动量\n\n只读取共享因子层输出，不要求核心引擎认识本策略。",
        encoding="utf-8",
    )
    (plugin_dir / "strategy.yaml").write_text(
        """
id: generic-momentum
name: 通用共享因子动量
enabled: true

meta:
  version: 0.1.0
  strategy_independence: 0.62
  default_preset_groups:
    formal_trend_core:
      weight: 0.77
      sort_order: 5

contract:
  type: strategy
  executable: true
  schema_version: 1

universe:
  include:
    - "amount_yi >= 3"
    - "capital_resonance_score >= 45"
    - "twenty_cm_risk_gate != 'block'"

phases:
  - name: 共享因子确认
    when: "sector_ladder_completeness_score >= 50 and capital_resonance_score >= 60"
    score_cap: 92
    hard_veto: false

setups:
  - name: 共振突破
    when: "breakout20 == true and sector_ladder_completeness_score >= 50"
    score_delta: 8
    entry_rule: "共享因子确认后跟随突破"
    invalid_rule: "板块梯队转弱或资金背离"

scoring:
  base: 48
  rules:
    - when: "sector_ladder_completeness_score >= 75"
      delta: 12
      reason: 共享板块梯队完整
    - when: "capital_resonance_signal == '主力共振'"
      delta: 10
      reason: 共享资金共振
    - when: "limit_up_overheat_score < 70"
      delta: 5
      reason: 共享涨停过热未阻断

risk:
  rules:
    - when: "limit_up_overheat_score >= 70"
      level: medium
      flag: 共享涨停过热降权
""".lstrip(),
        encoding="utf-8",
    )

    skills = {skill.skill_id: skill for skill in load_skills(skills_dir)}
    stock = _shared_factor_stock()
    signal = run_strategy(skills["generic-momentum"], stock, ["机器人"])

    assert signal["phase"] == "共享因子确认"
    assert signal["setup_match"] == "共振突破"
    assert signal["score"] > 70
    assert signal["strategy_independence"] == 0.62
    assert "共享资金共振" in signal["evidence"]

    rows = _default_preset_skill_rows(
        {"skill_groups": ["formal_trend_core"]},
        list(skills.values()),
    )
    assert rows == [{"skill_id": "generic-momentum", "weight": 0.77, "sort_order": 5}]
