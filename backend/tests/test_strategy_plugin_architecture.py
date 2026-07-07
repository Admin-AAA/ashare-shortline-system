from pathlib import Path

from app.engine.skill_loader import load_skills
from app.engine.strategy_runner import run_strategy
from app.services.feature_builder import build_features
from app.services.workbench_service import _default_preset_skill_rows, _strategy_backtest_adapter_manifest


SHORTLINE_SKILL_IDS = {
    "chen-xiaoqun-skill",
    "chao-gu-yang-jia-skill",
    "high-range-breakout-momentum",
    "limit-up-reseal-relay",
    "overnight-holding-method",
    "sector-relative-strength-leader",
    "trend-pullback-continuation",
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
        "pos120": 82.0,
        "eff20": 0.55,
        "breakout20": True,
        "breakout60": True,
        "primary_excess20": 12.0,
        "off_high_pct": -4.0,
        "off_high60_pct": -1.0,
        "vol5_20": 1.4,
        "max_dd20": -8.0,
        "upper_shadow_count20": 2,
        "industry_capacity_like_count": 3,
        "limit_up_days20": 1,
        "limit_up_consecutive_days": 1,
        "limit_up_open_times": 3,
        "limit_up_fd_amount_yi": 0.6,
        "limit_up_turnover_ratio": 4.5,
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


def _shared_factor_stocks():
    stocks = [
        _stock("002001"),
        _stock("002002", is_limit_up=True, limit_up_consecutive_days=1, pct=10.0),
        _stock("002003", is_limit_up=True, limit_up_consecutive_days=2, pct=10.0),
        _stock("002004", is_limit_up=True, limit_up_consecutive_days=4, pct=10.0),
    ]
    return build_features(stocks, _snapshot())


def test_shared_shortline_factors_enter_real_strategy_inputs():
    stocks = _shared_factor_stocks()
    stock = stocks[0]
    limit_up_stock = stocks[1]
    pullback_stock = build_features(
        [
            _stock(
                "002005",
                pct=0.2,
                dist20=12.0,
                vol5_20=0.75,
                dsa_profile={
                    "ma5": 10.0,
                    "ma10": 9.8,
                    "ma20": 9.5,
                    "ma60": 9.0,
                    "macd_status": "多头",
                    "rsi_12": 58.0,
                },
            )
        ],
        _snapshot(),
    )[0]
    skills = _skills_by_id()
    missing = SHORTLINE_SKILL_IDS - set(skills)
    assert not missing

    signals = {
        skill_id: run_strategy(skills[skill_id], stock, ["机器人"])
        for skill_id in {
            "chen-xiaoqun-skill",
            "chao-gu-yang-jia-skill",
            "high-range-breakout-momentum",
            "overnight-holding-method",
            "sector-relative-strength-leader",
        }
    }
    signals["limit-up-reseal-relay"] = run_strategy(skills["limit-up-reseal-relay"], limit_up_stock, ["机器人"])
    signals["trend-pullback-continuation"] = run_strategy(skills["trend-pullback-continuation"], pullback_stock, ["机器人"])

    assert "buy_signal" not in stock
    assert stock["is_main_theme"] is True
    assert stock["is_shortline_leader_candidate"] is True
    assert stock["sector_ladder_completeness_score"] >= 75
    assert stock["capital_resonance_signal"] == "主力共振"

    chen_evidence = "；".join(signals["chen-xiaoqun-skill"]["evidence"])
    yangjia_evidence = "；".join(signals["chao-gu-yang-jia-skill"]["evidence"])
    overnight_evidence = "；".join(signals["overnight-holding-method"]["evidence"])
    breakout_evidence = "；".join(signals["high-range-breakout-momentum"]["evidence"])
    limit_up_evidence = "；".join(signals["limit-up-reseal-relay"]["evidence"])
    pullback_evidence = "；".join(signals["trend-pullback-continuation"]["evidence"])
    sector_evidence = "；".join(signals["sector-relative-strength-leader"]["evidence"])

    assert "龙头候选" in chen_evidence
    assert "主线龙头共振" in chen_evidence
    assert "板块梯队完整" in yangjia_evidence
    assert "板块容量合力" in yangjia_evidence
    assert "尾盘资金承接较强" in overnight_evidence
    assert "隔夜尾盘确认" in overnight_evidence
    assert "60日新高突破" in breakout_evidence
    assert "阶段新高确认" in breakout_evidence
    assert "分歧回封质量较好" in limit_up_evidence
    assert "分歧回封接力" in limit_up_evidence
    assert "资金承接未弱化" in pullback_evidence
    assert "强趋势轻分歧" in pullback_evidence
    assert "个股20日超额收益占优" in sector_evidence
    assert "主线龙头突破" in sector_evidence

    setup_matches = {signal["setup_match"] for signal in signals.values()}
    assert setup_matches == {
        "分歧转一致",
        "平台突破",
        "隔夜尾盘确认",
        "60日新高放量突破",
        "分歧回封接力",
        "强趋势轻分歧",
        "主线龙头突破",
    }
    assert len({signal["score"] for signal in signals.values()}) > 1
    assert signals["overnight-holding-method"]["strategy_independence"] == 0.9
    assert signals["limit-up-reseal-relay"]["strategy_independence"] == 0.88
    assert signals["trend-pullback-continuation"]["strategy_independence"] == 0.78
    assert signals["sector-relative-strength-leader"]["strategy_independence"] == 0.8
    assert signals["high-range-breakout-momentum"]["strategy_independence"] == 0.82
    assert signals["chen-xiaoqun-skill"]["strategy_independence"] == 0.75


def test_strategies_use_shared_factors_with_different_contract_boundaries():
    skills = _skills_by_id()
    chen = str(skills["chen-xiaoqun-skill"].contract)
    yangjia = str(skills["chao-gu-yang-jia-skill"].contract)
    breakout = str(skills["high-range-breakout-momentum"].contract)
    limit_up = str(skills["limit-up-reseal-relay"].contract)
    overnight = str(skills["overnight-holding-method"].contract)
    pullback = str(skills["trend-pullback-continuation"].contract)
    sector = str(skills["sector-relative-strength-leader"].contract)

    assert "is_shortline_leader_candidate" in chen
    assert "dragon_tiger_buy_dispersion" in chen
    assert "sector_ladder_completeness_score" in yangjia
    assert "capital_resonance_score" in yangjia
    assert "twenty_cm_risk_gate != 'block'" in overnight
    assert "capital_resonance_score >= 45" in overnight
    assert "experiment_high_range_breakout" in breakout
    assert "off_high60_pct" in breakout
    assert "experiment_limit_up_reseal_relay" in limit_up
    assert "limit_up_fd_amount_yi" in limit_up
    assert "experiment_trend_pullback" in pullback
    assert "volume_shrink_pullback" in pullback
    assert "experiment_sector_relative_strength" in sector
    assert "industry_positive_excess20_count" in sector


def test_sector_relative_strength_observation_setup_does_not_boost_score():
    skills = _skills_by_id()
    stock = _shared_factor_stock()
    stock.update(
        {
            "breakout20": False,
            "support_ma5": False,
            "primary_excess20": 6.0,
            "primary_excess60": 8.0,
            "off_high_pct": -10.0,
            "max_dd20": -16.0,
            "capital_resonance_score": 56.0,
            "sector_ladder_completeness_score": 55.0,
            "industry_positive_excess20_count": 2,
        }
    )

    signal = run_strategy(skills["sector-relative-strength-leader"], stock, ["机器人"])

    assert signal["setup_match"] == "主线相对强度观察"
    assert signal["entry_rule"] == "仅保留观察，等待重新突破平台、收回 MA5/MA10 或板块梯队继续加强"
    assert signal["setup_quality"] == 60.0
    assert "买点命中: 主线相对强度观察" in signal["evidence"]


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


def test_high_range_breakout_default_single_strategy_preset():
    skills = _skills_by_id()

    rows = _default_preset_skill_rows(
        {"skill_groups": ["experiment_high_range_breakout"]},
        list(skills.values()),
    )

    assert rows == [{"skill_id": "high-range-breakout-momentum", "weight": 1.0, "sort_order": 10}]


def test_new_default_single_strategy_presets():
    skills = _skills_by_id()
    cases = {
        "experiment_limit_up_reseal_relay": "limit-up-reseal-relay",
        "experiment_trend_pullback": "trend-pullback-continuation",
        "experiment_sector_relative_strength": "sector-relative-strength-leader",
    }

    for group, skill_id in cases.items():
        rows = _default_preset_skill_rows(
            {"skill_groups": [group]},
            list(skills.values()),
        )

        assert rows == [{"skill_id": skill_id, "weight": 1.0, "sort_order": 10}]


def test_dsa_oscillation_mid_price_risk_rule():
    """震荡期 + avg_cost 50-100 应触发风险扣分。
    数据依据：震荡期 avg_cost 50-100 WR=42.1% vs 震荡期其他 47.7%，
    Walk-forward 近期 30.8%（信号增强）"""
    skills = _skills_by_id()
    stock = _stock("600519")
    # 满足基础入选条件
    stock.update({
        "amount_yi": 8.0, "turnover_rate": 5.0,
        "breakout20": 1, "off_high_pct": -5.0, "pct": 1.0,
        "pos20": 75.0, "pos60": 70.0, "dist60": 8.0,
        "rsi_12": 55.0, "max_dd20": -8.0, "upper_shadow_count20": 2,
        "support_ma5": True,
        "avg_cost": 70.0,  # 50-100 区间
        "market_regime": "震荡",
        "dsa_trend_score": 20, "dsa_bias_score": 10, "dsa_macd_score": 6,
        "dsa_signal_score": 80, "volume_shrink_pullback": True,
        "bias_ma5": 1.5,
    })
    signal = run_strategy(skills["daily-stock-analysis-skill"], stock, ["测试"])

    # 应有震荡期中价股风险 flag
    risk_flags = signal.get("risk_flags") or []
    has_oscillation_risk = any("震荡期" in flag and "中价股" in flag for flag in risk_flags)
    assert has_oscillation_risk, f"震荡期中价股风险未触发: {risk_flags}"

    # 非震荡期不应触发
    stock["market_regime"] = "强势"
    signal2 = run_strategy(skills["daily-stock-analysis-skill"], stock, ["测试"])
    risk_flags2 = signal2.get("risk_flags") or []
    has_oscillation_risk2 = any("震荡期" in flag and "中价股" in flag for flag in risk_flags2)
    assert not has_oscillation_risk2, f"非震荡期不应触发: {risk_flags2}"


def test_dsa_resistance_too_far_risk_rule():
    """resistance_distance_pct > 10 应触发风险扣分，≤ 10 不应触发。
    数据依据：resist 10-15 WR=42.8% avg=-0.41%（PF=0.91 负收益）"""
    skills = _skills_by_id()
    stock = _stock("600519")
    stock.update({
        "amount_yi": 8.0, "turnover_rate": 6.0,
        "breakout20": 1, "off_high_pct": -5.0, "pct": 1.0,
        "pos20": 75.0, "pos60": 70.0, "dist60": 8.0,
        "rsi_12": 55.0, "max_dd20": -8.0, "upper_shadow_count20": 2,
        "support_ma5": True, "avg_cost": 15.0,
        "resistance_distance_pct": 15.0,  # > 10，触发风险
        "dsa_trend_score": 20, "dsa_bias_score": 10, "dsa_macd_score": 6,
    })
    signal = run_strategy(skills["daily-stock-analysis-skill"], stock, ["测试"])
    risk_flags = signal.get("risk_flags") or []
    has_resist_risk = any("远离阻力位" in flag for flag in risk_flags)
    assert has_resist_risk, f"远离阻力位风险未触发（resist=15%）: {risk_flags}"

    # ≤ 10 不应触发
    stock["resistance_distance_pct"] = 8.0
    signal2 = run_strategy(skills["daily-stock-analysis-skill"], stock, ["测试"])
    risk_flags2 = signal2.get("risk_flags") or []
    has_resist_risk2 = any("远离阻力位" in flag for flag in risk_flags2)
    assert not has_resist_risk2, f"≤10% 不应触发: {risk_flags2}"


def test_dsa_turnover_too_low_risk_rule():
    """turnover 1-3% 应触发换手率过低风险，≥5 不触发。
    数据依据：turnover 1-3 WR=38.7% avg=-0.46%（PF=0.86 负收益）"""
    skills = _skills_by_id()
    stock = _stock("600519")
    stock.update({
        "amount_yi": 8.0, "turnover_rate": 2.0,  # 1-3%，触发
        "breakout20": 1, "off_high_pct": -5.0, "pct": 1.0,
        "pos20": 75.0, "pos60": 70.0, "dist60": 8.0,
        "rsi_12": 55.0, "max_dd20": -8.0, "upper_shadow_count20": 2,
        "support_ma5": True, "avg_cost": 15.0,
        "resistance_distance_pct": 5.0,
        "dsa_trend_score": 20, "dsa_bias_score": 10, "dsa_macd_score": 6,
    })
    signal = run_strategy(skills["daily-stock-analysis-skill"], stock, ["测试"])
    risk_flags = signal.get("risk_flags") or []
    has_low_turnover = any("换手率过低" in flag for flag in risk_flags)
    assert has_low_turnover, f"换手率过低风险未触发（turnover=2%）: {risk_flags}"

    # ≥ 5 不应触发
    stock["turnover_rate"] = 6.0
    signal2 = run_strategy(skills["daily-stock-analysis-skill"], stock, ["测试"])
    risk_flags2 = signal2.get("risk_flags") or []
    has_low_turnover2 = any("换手率过低" in flag for flag in risk_flags2)
    assert not has_low_turnover2, f"≥5% 不应触发: {risk_flags2}"


def test_high_range_require_setup_and_observation_hard_veto():
    """回归测试：high-range-breakout-momentum 的 require_setup + 阶段高点蓄势观察 hard_veto。

    防止未来误删这两个保护：
    - require_setup: setup 未命中时 hard_veto（突破接力必须有明确入场 setup）
    - 阶段高点蓄势观察 hard_veto: "观察"语义=不确定，不该入场

    历史 23 笔 high-range 推荐全部是 null setup 或观察类（WR 30.4%），
    改动后应全部 hard_veto（回放验证 23/23 否决）。
    """
    skills = _skills_by_id()
    skill = skills["high-range-breakout-momentum"]

    # 1. null setup: breakout60=true 但 setup 量价条件不满足（pct>7, vol5_20<1.15）
    null_setup_stock = _stock(
        "000001",
        breakout60=True, breakout20=True,
        pos60=90, pos120=85, off_high60_pct=-3,
        vol5_20=1.05,  # < 1.15 不满足 60日 setup
        pct=8.0,  # > 7 不满足 60日 setup
        chg20=5.0,  # < 8 不满足 20日 setup
        support_ma5=True, turnover_rate=3.0,
        max_dd20=-5, dist60=10,
    )
    signal = run_strategy(skill, null_setup_stock, ["半导体"], skip_universe_check=True)
    assert signal["hard_veto"] is True, f"null setup 应 hard_veto: {signal}"
    assert "require_setup" in signal["veto_reason"] or "未命中" in signal["veto_reason"]

    # 2. 阶段高点蓄势观察: breakout60=false, pos60>=85, off_high60_pct>=-6
    observation_stock = _stock(
        "000002",
        breakout60=False, breakout20=False,
        pos60=90, pos120=85, off_high60_pct=-3,
        vol5_20=1.3, pct=2.0, support_ma5=True,
    )
    signal2 = run_strategy(skill, observation_stock, ["半导体"], skip_universe_check=True)
    assert signal2["hard_veto"] is True, f"阶段高点蓄势观察应 hard_veto: {signal2}"
    assert "观察类 setup" in signal2["veto_reason"]

    # 3. 真突破 setup 应放行（60日新高放量突破）
    true_breakout_stock = _stock(
        "000003",
        breakout60=True,
        pos60=85, pos120=75, off_high60_pct=-2,
        vol5_20=1.2,  # >= 1.15
        pct=3.0,  # 1.5 <= pct <= 7
        support_ma5=True, turnover_rate=3.0,
        max_dd20=-5, dist60=10,
    )
    signal3 = run_strategy(skill, true_breakout_stock, ["半导体"], skip_universe_check=True)
    assert signal3["hard_veto"] is False, f"真突破应放行: {signal3}"
    assert signal3["setup_match"] == "60日新高放量突破"


def test_high_range_60d_breakout_accepts_high_pct_breakout():
    """回归测试：60日新高放量突破 setup 移除 pct<=7 上限。

    历史回测（2026-04~07）：breakout60 + pct∈[1.5,7] + vol5_20>=1.15 + pos120>=70
    命中 6 笔 WR 50% / +2.47%；放开 pct>7（300502 pct=9.88 → success +5.94%）后
    命中 7 笔 WR 57.1% / +2.97%。pct>7 的过热由 risk rule（pct>7 → -8）+
    limit_up_overheat_score 控制，setup 不再硬卡上限。
    之前 pct>7 被 setup 的 pct<=7 条件直接排除（命中 null setup → require_setup hard_veto），
    改动后应放行进 60日新高放量突破 setup。
    """
    skills = _skills_by_id()
    skill = skills["high-range-breakout-momentum"]

    high_pct_breakout = _stock(
        "000099",
        breakout60=True,
        pos60=85, pos120=85, off_high60_pct=-1,
        vol5_20=1.2,  # >= 1.15
        pct=9.5,  # > 7，旧条件 pct<=7 会被排除
        support_ma5=True, turnover_rate=3.0,
        max_dd20=-5, dist60=10,
        limit_up_overheat_score=20,  # 未过热
    )
    signal = run_strategy(skill, high_pct_breakout, ["半导体"], skip_universe_check=True)
    assert signal["hard_veto"] is False, f"高 pct 真突破应放行（不再被 pct<=7 排除）: {signal}"
    assert signal["setup_match"] == "60日新高放量突破", f"应命中 60日 setup: {signal['setup_match']}"
    # pct>7 风险规则仍应生效（score_delta -8）
    assert any("涨幅过大" in f or "追高" in f for f in signal["risk_flags"]), (
        f"pct>7 风险标记应触发: {signal['risk_flags']}"
    )


def test_trend_pullback_prefilter_accepts_moderate_volume_pullback():
    """回归测试：trend-pullback-continuation prefilter.include_any 第三选项放宽。

    第 141 轮 T1：原 vol5_20<=0.9 过严，07-07 formal run 仅 1 只通过 prefilter。
    放宽到 vol5_20<=1.0（与 include 规则 1.05 对齐），让"均量未放大+MA5支撑+窄幅"
    的趋势内回踩候选能进 prefilter。vol5_20∈(0.9,1.0] 应通过，>1.05 仍被 include 拒。
    """
    from app.engine.strategy_runner import evaluate_skill_prefilter

    skills = _skills_by_id()
    skill = skills["trend-pullback-continuation"]

    # vol5_20=0.95 + support_ma5 + pct 窄幅：原<=0.9 拒，新<=1.0 过
    moderate_pullback = _stock(
        "000098",
        pct=0.3,  # 在 [-1.5, 1] 窄幅
        vol5_20=0.95,  # 在 (0.9, 1.0]
        support_ma5=True,
        volume_shrink_pullback=False,
        # 其他 prefilter include 必需
        max_dd20=-5, upper_shadow_count20=1,
    )
    passed, reason = evaluate_skill_prefilter(skill, moderate_pullback, stage="market", )
    assert passed, f"vol5_20=0.95 + MA5 支撑应通过放宽后的 prefilter: {reason}"

    # vol5_20=0.85 仍应通过（原阈值内）
    tight_pullback = dict(moderate_pullback, code="000099", vol5_20=0.85)
    passed2, _ = evaluate_skill_prefilter(skill, tight_pullback, stage="market")
    assert passed2, "vol5_20=0.85 应通过 prefilter"

    # vol5_20=1.08 仍应被 include 规则 <=1.05 拒（未放宽 include，只放宽 include_any）
    high_vol = dict(moderate_pullback, code="000100", vol5_20=1.08)
    passed3, reason3 = evaluate_skill_prefilter(skill, high_vol, stage="market")
    assert not passed3, "vol5_20=1.08 应被 include 规则拒"


def test_trend_pullback_phase_and_setup_match_moderate_volume():
    """回归测试：trend-pullback phase 趋势内轻回踩 + setup 强趋势轻分歧 放宽 vol5_20<=0.9→<=1.0。

    第 142 轮 T2：上轮放宽 prefilter 让候选进 skill_gate，但 phase/setup 仍 <=0.9
    导致 vol5_20∈(0.9,1.0] 候选不命中 setup、无 score_delta。放宽后应命中。
    回测：301086 vol5_20=0.987 命中 setup 强趋势轻分歧（+7），历史 outcome success +7.93%。
    """
    skills = _skills_by_id()
    skill = skills["trend-pullback-continuation"]

    # 301086 真实快照特征（vol5_20=0.987, 多头排列, dist20>10, eff20>0.5, 窄幅, support_ma5）
    stock = _stock(
        "301086",
        ma5=10.2, ma10=10.0, ma20=9.8,  # 多头排列
        dist20=14.0, eff20=0.828,  # 趋势强度
        pct=-0.568,  # 窄幅 [-1.5, 0.8]
        vol5_20=0.987,  # (0.9, 1.0] —— 旧 <=0.9 不命中，新 <=1.0 命中
        support_ma5=True,
        volume_shrink_pullback=False,
        dsa_trend_score=26, max_dd20=-5, off_high_pct=-3,
        upper_shadow_count20=1, rsi_12=55,
        capital_resonance_score=65,
        turnover_rate=3, amount_yi=5, chg20=8, pos60=70, pos120=75,
        twenty_cm_risk_gate="pass", news_risk_count=0, announcement_negative_count=0,
        is_st=False, limit_up_overheat_score=10,
    )
    signal = run_strategy(skill, stock, ["半导体"], skip_universe_check=True)
    # phase 应命中"趋势内轻回踩"（旧 <=0.9 不命中）
    assert signal["phase"] == "趋势内轻回踩", f"phase 应命中趋势内轻回踩: {signal['phase']}"
    # setup 应命中"强趋势轻分歧"（+7 score_delta）
    assert signal["setup_match"] == "强趋势轻分歧", f"setup 应命中强趋势轻分歧: {signal['setup_match']}"
    assert "强趋势轻分歧" in "；".join(signal["evidence"])


def test_all_skill_md_have_agent_output_constraints():
    """所有 SKILL.md 必须含 Agent 审查输出约束（stance + action 字段）。

    回归保护：第 101-102 轮 T9 给 5 个 B 类 SKILL.md 补齐输出约束，
    防止未来新增/修改 SKILL.md 时遗漏输出格式指导，导致 LLM agent 输出字段不一致。
    A 类 SKILL.md（DSA/chao-gu-yang-jia/chen-xiaoqun）自有 stance 表，B 类引用 SkillReviewOutput schema。
    """
    from pathlib import Path

    skills_dir = Path(__file__).resolve().parents[1] / "skills"
    # stance + action 是 SkillReviewOutput 的核心字段，所有 SKILL.md 必须提及
    required_markers = ["stance", "action"]
    missing = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_text(encoding="utf-8")
        for marker in required_markers:
            if marker not in content:
                missing.append(f"{skill_dir.name}: 缺 {marker}")

    assert not missing, f"SKILL.md 输出约束缺失: {missing}"
