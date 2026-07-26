"""T1 端到端验证：龙虎榜聪明钱因子修复 + 新策略 dragon-tiger-smart-money。

背景（第一性原理已验证）：
- feature_builder 此前从不给 dragon_tiger_institution_net_buy_yi 赋值，导致
  shortline_factors._apply_dragon_tiger_factor 算出的 dragon_tiger_inst_buy_ratio 恒为 0，
  令 chen-xiaoqun / limit-up-reseal 的 inst_buy_ratio>=0.5 规则永不起作用（沉睡因子）。
- 新增 dragon_tiger_net_buy_ratio = 龙虎榜净买占当日成交额比例（聪明钱强度）。
  回测 357 个上榜样本：净买≥5% -> WR 85.6% / avg+4.82%（远强于 69.8% 基线）。
- 新增策略 dragon-tiger-smart-money，并在 prefilter 前注入龙虎榜标记（同 limit_up 机制），
  使其 universe 能被正确命中（此前因 prefilter 阶段 dragon_tiger 字段缺失永远 0 命中）。
"""
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.engine.aggregator import aggregate
from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy
from app.models.tables import DragonTiger
from app.services.factors import FactorContext
from app.services.factors.shortline import DragonTigerFactor
from app.services.feature_builder import _enrich_with_auxiliary_data, build_features
from app.services.auxiliary.application import (
    inject_auxiliary_prefilter_flags,
    load_auxiliary_feature_views,
)


def _apply_dragon_tiger_factor(stock: dict) -> None:
    """新接口适配：龙虎榜因子迁入因子框架后（任务 2.3），compute 返回新增字段 dict，
    此处按新接口调用并 in-place 合并回 stock，保持本用例断言语义不变。"""
    stock.update(DragonTigerFactor().compute(stock, FactorContext()))

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
NEW_SKILL_DIR = SKILLS_DIR / "dragon-tiger-smart-money"


def _stock(code="600519", name="测试龙虎", **kw):
    d = {
        "code": code,
        "name": name,
        "price": 50.0,
        "close": 50.0,
        "pct": 3.0,
        "industry": "半导体",
        "amount_yi": 10.0,
        "turnover_rate": 5.0,
        "free_mv_yi": 80.0,
        "chg20": 15.0,
        "dist20": 8.0,
        "dist60": 10.0,
        "pos20": 65,
        "pos60": 70,
        "eff20": 0.5,
        "rsi_12": 50,
        "breakout20": True,
        "vol5_20": 1.0,
        "max_dd20": -6.0,
        "upper_shadow_count20": 1,
        "off_high_pct": -3.0,
        "primary_excess20": 10.0,
        "is_st": False,
        "is_main_theme": True,
        "is_limit_up": False,
        "is_shortline_leader_candidate": True,
        "limit_up_overheat_score": 40,
        "twenty_cm_risk_gate": "pass",
        "market_sentiment_phase": "活跃",
        "main_net_inflow_yi": 0.8,
        "main_net_inflow_ratio": 6.0,
        "capital_resonance_signal": "主力共振",
        "capital_resonance_score": 70,
        "announcement_negative_count": 0,
        "news_risk_count": 0,
        "industry_capacity_like_count": 3,
        "dragon_tiger_is_on_list": True,
        "dragon_tiger_buy_dispersion": "集中",
        "dragon_tiger_net_buy_ratio": 0.12,
        "dragon_tiger_inst_buy_ratio": 1.0,
        "dragon_tiger_net_amount_yi": 1.2,
        "market_sample_count": 300,
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
        "data_quality": {"missing_fields": ["dragon_tiger"]},
        "dsa_profile": {},
    }
    d.update(kw)
    return d


def _snapshot(**kw):
    d = {
        "sample_count": 300,
        "up_count": 180,
        "down_count": 100,
        "limit_up_count": 12,
        "limit_down_count": 3,
        "main_industries": ["半导体", "电子"],
        "trade_date": "2026-07-06",
        "breadth": {"regime": "多头"},
    }
    d.update(kw)
    return d


def _aux_with_dt(net_amount_yi, amount_yi=10.0):
    return {
        "limit_up": {},
        "concept_sectors": {},
        "capital_flows": {},
        "dragon_tigers": {
            "600519": {
                "trade_date": "2026-07-06",
                "buy_amount_yi": 2.0,
                "sell_amount_yi": 0.8,
                "net_amount_yi": net_amount_yi,
            }
        },
        "news": {},
        "auctions": {},
        "pledges": {},
        "broker_recs": {},
        "share_floats_upcoming": {},
        "coverage": {"dragon_tiger_trade_date_covered": True},
    }


def test_feature_builder_populates_smart_money_fields_net_buy():
    stock = _stock(amount_yi=10.0)
    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=_aux_with_dt(1.2))

    assert stock["dragon_tiger_is_on_list"] is True
    assert stock["dragon_tiger_net_amount_yi"] == 1.2
    # 修复沉睡因子：institution_net_buy_yi 此前从不赋值 -> inst_buy_ratio 恒为 0
    assert stock["dragon_tiger_institution_net_buy_yi"] == 1.2
    # net_buy_ratio = 1.2 / 10.0 = 0.12
    assert abs(stock["dragon_tiger_net_buy_ratio"] - 0.12) < 1e-6
    # shortline_factors: inst_net / abs(net) = 1.2 / 1.2 = 1.0
    _apply_dragon_tiger_factor(stock)
    assert stock["dragon_tiger_inst_buy_ratio"] == 1.0


def test_feature_builder_net_sell_gives_negative_ratio():
    stock = _stock(amount_yi=10.0)
    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=_aux_with_dt(-0.5))

    assert stock["dragon_tiger_net_buy_ratio"] == -0.05
    _apply_dragon_tiger_factor(stock)
    assert stock["dragon_tiger_inst_buy_ratio"] == -1.0


def test_feature_builder_net_buy_ratio_clamped():
    # 极端净买（占成交额 >100%）应被 clamp 到 1.0
    stock = _stock(amount_yi=10.0)
    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=_aux_with_dt(50.0))
    assert stock["dragon_tiger_net_buy_ratio"] == 1.0


def test_new_strategy_loads():
    skill = load_skill(NEW_SKILL_DIR)
    assert skill.skill_id == "dragon-tiger-smart-money"
    assert skill.executable is True
    assert skill.contract.get("weight") == 1.15
    # 应能被推荐管线识别（contract 存在且 executable）
    assert skill.contract.get("contract", {}).get("executable") is True


def test_new_strategy_produces_signal_for_net_buy():
    skill = load_skill(NEW_SKILL_DIR)
    stock = _stock(
        dragon_tiger_is_on_list=True,
        dragon_tiger_net_buy_ratio=0.12,
        dragon_tiger_inst_buy_ratio=1.0,
        dragon_tiger_net_amount_yi=1.2,
    )
    signal = run_strategy(skill, stock, ["半导体", "电子"])
    assert signal is not None
    assert signal.get("hard_veto") is not True
    # 强聪明钱（净买≥5%）应得高分；命中“强聪明钱接力”阶段
    assert signal.get("score", 0) >= 80
    assert signal.get("phase") == "强聪明钱接力"


def test_new_strategy_vetoes_net_sell():
    skill = load_skill(NEW_SKILL_DIR)
    stock = _stock(
        dragon_tiger_is_on_list=True,
        dragon_tiger_net_buy_ratio=-0.05,
        dragon_tiger_inst_buy_ratio=-1.0,
        dragon_tiger_net_amount_yi=-0.5,
    )
    signal = run_strategy(skill, stock, ["半导体", "电子"])
    assert signal.get("hard_veto") is True


def test_chen_inst_buy_ratio_revived():
    """修复前 inst_buy_ratio 恒为 0 -> chen 的 inst_buy_ratio>=0.5 规则永不起作用。
    修复后 net-buy 龙虎榜 -> inst_buy_ratio=1.0 -> 规则应生效（+6, +4 共 +10）。"""
    chen = load_skill(SKILLS_DIR / "chen-xiaoqun-skill")
    base = dict(
        dragon_tiger_is_on_list=True,
        dragon_tiger_net_buy_ratio=0.12,
        dragon_tiger_buy_dispersion="集中",
        is_main_theme=True,
        capital_resonance_signal="主力共振",
        main_net_inflow_yi=0.8,
        turnover_rate=4.0,
        upper_shadow_count20=1,
        breakout20=True,
        pos60=70,
        dist60=10,
        vol5_20=1.0,
        rsi_12=50,
        pct=2.0,
        off_high_pct=-3.0,
        primary_excess20=10,
        is_shortline_leader_candidate=True,
        is_limit_up=False,
        market_sentiment_phase="活跃",
        limit_up_overheat_score=40,
        twenty_cm_risk_gate="pass",
        industry_capacity_like_count=3,
        chg20=15.0,
        dist20=8.0,
        eff20=0.5,
        max_dd20=-6.0,
        pos20=65,
        announcement_negative_count=0,
        news_risk_count=0,
    )
    s_on = _stock(**base, dragon_tiger_inst_buy_ratio=1.0)
    s_off = _stock(**base, dragon_tiger_inst_buy_ratio=0.0)
    sig_on = run_strategy(chen, s_on, ["半导体", "电子"])
    sig_off = run_strategy(chen, s_off, ["半导体", "电子"])
    assert sig_on.get("hard_veto") is not True
    assert sig_off.get("hard_veto") is not True
    # 修复后 net-buy 龙虎榜 inst_buy_ratio=1.0 触发 chen 的 inst>=0.5(+6) 与 inst>=0.7(+4) 规则，
    # 其 reason("机构净买入占比过半" / "机构净买入高度集中") 应出现在 evidence 中；
    # inst_buy_ratio=0.0（修复前恒为0）时这些规则不触发 -> evidence 中无该理由。
    evidence_on = " ".join(sig_on.get("evidence") or [])
    evidence_off = " ".join(sig_off.get("evidence") or [])
    assert "机构净买入占比过半" in evidence_on
    assert "机构净买入占比过半" not in evidence_off
    # 分数也应更高（即便被阶段 score_cap 截断，证据差异已证明规则生效）
    assert sig_on["score"] >= sig_off["score"]


def test_e2e_signal_reaches_skill_breakdown(db):
    """端到端：龙虎榜标记注入(prefilter 前) → build_features → 新策略 → aggregate
    → strategy_signals（即 skill_breakdown_json 内容）含 dragon-tiger-smart-money。

    验证真实管线顺序：feature_builder 在 prefilter 之后才运行，若无 inject_dragon_tiger_flags
    前置注入，dragon-tiger-smart-money 的 universe（dragon_tiger_is_on_list==true）在 prefilter
    阶段永远 0 命中，信号无法进入 skill_breakdown_json。
    """
    code = "609999"
    from app.engine.trading_calendar import latest_allowed_bar_date

    dt_date = latest_allowed_bar_date()
    # 在 dragon_tiger 表注入一只龙虎榜股（测试后随事务回滚，不影响生产数据）
    existing = db.scalar(
        select(DragonTiger).where(DragonTiger.code == code, DragonTiger.trade_date == dt_date)
    )
    if not existing:
        db.add(DragonTiger(
            code=code,
            trade_date=dt_date,
            name="E2E龙虎",
            buy_amount_yi=2.0,
            sell_amount_yi=0.8,
            net_amount_yi=1.2,
            created_at=datetime.utcnow(),
        ))
        db.commit()

    stock = _stock(code=code, amount_yi=10.0, dragon_tiger_is_on_list=False)
    # 1) prefilter 前注入龙虎榜标记（真实管线：inject_dragon_tiger_flags 在 apply_prefilters 之前）
    auxiliary_views = load_auxiliary_feature_views(db, [code], trade_date=dt_date)
    hit = inject_auxiliary_prefilter_flags([stock], auxiliary_views)
    assert hit == 1
    assert stock["dragon_tiger_is_on_list"] is True
    assert abs(stock["dragon_tiger_net_buy_ratio"] - 0.12) < 1e-6
    assert stock["dragon_tiger_institution_net_buy_yi"] == 1.2

    # 2) build_features（无 aux，不应覆盖已注入的龙虎榜字段）
    from app.services.factors.registry import bootstrap_default_factor_registry

    bootstrap_default_factor_registry()
    build_features([stock], _snapshot())
    assert stock["dragon_tiger_is_on_list"] is True
    assert abs(stock["dragon_tiger_net_buy_ratio"] - 0.12) < 1e-6

    # 3) 运行新策略
    skill = load_skill(NEW_SKILL_DIR)
    signal = run_strategy(skill, stock, ["半导体", "电子"])
    assert signal.get("strategy_id") == "dragon-tiger-smart-money"
    assert signal.get("hard_veto") is not True
    assert signal.get("score", 0) >= 80
    assert signal.get("phase") == "强聪明钱接力"

    # 4) aggregate → strategy_signals（即推荐落库的 skill_breakdown_json 内容）
    card = aggregate(
        stock=stock,
        signals=[signal],
        market_score=72.0,
        agent_review_score=72.5,
        risk_template="balanced",
    )
    strat_sigs = card.get("strategy_signals") or []
    assert any(s.get("strategy_id") == "dragon-tiger-smart-money" for s in strat_sigs)


def test_dragon_tiger_in_formal_opening_preset(db):
    """回归保护：dragon-tiger-smart-money 必须在 preset_formal_opening 里。

    第149轮 T1 发现：策略已注册（skill_registry mode=formal）+ strategy.yaml 声明
    default_preset_groups: formal_opening，但 preset_skills 表没记录→从未被 formal run 选中。
    _default_preset_skill_rows 同步逻辑只在 preset 无 skill_rows 时触发，已存在的 preset
    不会自动加新策略。修复：直接 INSERT preset_skills。

    第N+8轮 T5 发现：preset_skills 曾有孤儿记录 preset_id='formal_opening'（无前缀，
    presets 表无此 preset），导致 dragon-tiger 关联到不存在的 preset，test 查
    'preset_formal_opening' 找不到。修复：UPDATE preset_id 加 'preset_' 前缀。
    """
    from app.models.tables import PresetSkill

    row = db.scalar(
        select(PresetSkill).where(
            PresetSkill.preset_id == "preset_formal_opening",
            PresetSkill.skill_id == "dragon-tiger-smart-money",
        )
    )
    assert row is not None, (
        "dragon-tiger-smart-money 必须在 preset_formal_opening 里，否则 formal run 永远不会选中它"
    )
    assert float(row.weight) > 0

    # 孤儿 preset_id 检测：'formal_opening'（无 preset_ 前缀）不应存在，
    # 否则 dragon-tiger 会关联到不存在的 preset 而被 formal run 跳过。
    orphan = db.scalar(
        select(PresetSkill).where(
            PresetSkill.preset_id == "formal_opening",
            PresetSkill.skill_id == "dragon-tiger-smart-money",
        )
    )
    assert orphan is None, (
        "发现孤儿 preset_id='formal_opening'（无前缀），应为 'preset_formal_opening'。"
        "这是手动 INSERT 用错 preset_id 的残留，会导致 dragon-tiger 关联到不存在的 preset。"
    )
