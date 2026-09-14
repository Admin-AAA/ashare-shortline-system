"""龙虎榜聪明钱因子/特征装配的端到端验证。

背景（第一性原理已验证）：
- 2026-09-13 修复（深审 A3-2）：此前把总净买额赋给 dragon_tiger_institution_net_buy_yi
  冒充机构净买，使 inst_buy_ratio 恒为 ±1（净买方向冒充机构身份）。现已撤除代理：
  top_inst 席位明细接入前该比例恒为 0，chen-xiaoqun / dragon-tiger 的"机构确认"
  类规则已从 YAML 删除；net_buy_ratio（净买占成交额）是唯一诚实可用的强度口径。
- 2026-09-15：策略 dragon-tiger-smart-money 因三窗生产同构消融全过 challenger 闸门退役
  （见 docs/strategy-retire-2026-09-15.md）。本文件保留仍在线运行的**因子/特征**部分：
  龙虎榜标记注入、净买比例口径、机构比例不被净买方向污染。
- dragon_tiger_net_buy_ratio = 龙虎榜净买占当日成交额比例（聪明钱强度）。
  回测 357 个上榜样本：净买≥5% -> WR 85.6% / avg+4.82%（远强于 69.8% 基线）。
"""
from pathlib import Path

from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy
from app.services.factors import FactorContext
from app.services.factors.shortline import DragonTigerFactor
from app.services.feature_builder import _enrich_with_auxiliary_data


def _apply_dragon_tiger_factor(stock: dict) -> None:
    """新接口适配：龙虎榜因子迁入因子框架后（任务 2.3），compute 返回新增字段 dict，
    此处按新接口调用并 in-place 合并回 stock，保持本用例断言语义不变。"""
    stock.update(DragonTigerFactor().compute(stock, FactorContext()))

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


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
    # 2026-09-13 修复（深审 A3-2）：不再用总净买额冒充机构净买（该代理使比例恒为 ±1，
    # 机构身份规则被净买方向误触发）；top_inst 席位明细接入前字段保持缺失/为 0。
    assert stock.get("dragon_tiger_institution_net_buy_yi") in (None, 0.0)
    # net_buy_ratio = 1.2 / 10.0 = 0.12（诚实口径，仍可用）
    assert abs(stock["dragon_tiger_net_buy_ratio"] - 0.12) < 1e-6
    # 无真实机构净买 -> inst_buy_ratio 输出 0，机构类规则不再被净买方向触发
    _apply_dragon_tiger_factor(stock)
    assert stock["dragon_tiger_inst_buy_ratio"] == 0.0


def test_feature_builder_net_sell_keeps_inst_ratio_zero():
    """净卖时机构比例同样不为 -1：无真实席位数据，不再输出方向代理。"""
    stock = _stock(amount_yi=10.0)
    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=_aux_with_dt(-0.5))

    assert stock["dragon_tiger_net_buy_ratio"] == -0.05
    _apply_dragon_tiger_factor(stock)
    assert stock["dragon_tiger_inst_buy_ratio"] == 0.0


def test_feature_builder_net_buy_ratio_clamped():
    # 极端净买（占成交额 >100%）应被 clamp 到 1.0
    stock = _stock(amount_yi=10.0)
    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=_aux_with_dt(50.0))
    assert stock["dragon_tiger_net_buy_ratio"] == 1.0


def test_chen_no_fake_institution_scoring():
    """深审 A3-2：chen 的 inst_buy_ratio>=0.5/+0.7 两规则（原净买时合计 +10、
    冒充"机构净买入"）已删除——inst_buy_ratio 取值不再影响 chen 评分，
    evidence 也不得再出现"机构净买入"类理由。"""
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
    # 机构身份规则已删除：inst_buy_ratio 不再产生任何评分差异
    assert sig_on["score"] == sig_off["score"]
    evidence_on = " ".join(sig_on.get("evidence") or [])
    assert "机构净买入" not in evidence_on
