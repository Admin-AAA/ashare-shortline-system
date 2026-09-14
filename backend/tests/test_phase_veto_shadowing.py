"""深审 A3-3 软阶段遮蔽硬否决 回归测试。

修复前 strategy_runner 在首个命中阶段后 break：炒股养家在
弱势+RSI50+非主线+dist60=31+pos60=93 输入下先命中"弱市降权"（软 cap68），
排在其后的"非主线加速"（hard_veto cap20）不再评估，硬否决被架空。
修复后：首个命中阶段保留名称/上限优先级，后续 hard_veto 必须生效并取更严 cap。
"""
from pathlib import Path

from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _chao_gu():
    return load_skill(SKILLS_DIR / "chao-gu-yang-jia-skill")


def _stock(**kw):
    d = {
        "code": "600519",
        "name": "测试养家",
        "price": 50.0,
        "pct": 1.0,
        "industry": "多元金融",
        "amount_yi": 5.0,
        "turnover_rate": 5.0,
        "free_mv_yi": 80.0,
        "chg20": 15.0,
        "dist20": 8.0,
        "dist60": 10.0,
        "pos20": 65,
        "pos60": 70,
        "rsi_12": 50,
        "breakout20": False,
        "vol5_20": 1.0,
        "max_dd20": -6.0,
        "upper_shadow_count20": 1,
        "off_high_pct": -3.0,
        "is_st": False,
        "is_main_theme": False,
        "market_regime": "震荡",
        "market_sample_count": 300,
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
        "industry_capacity_like_count": 3,
        "announcement_negative_count": 0,
        "news_risk_count": 0,
    }
    d.update(kw)
    return d


def test_soft_phase_no_longer_masks_hard_veto():
    """深审 A3-3 构造输入：弱势+RSI50+非主线+dist60=31+pos60=93。
    先命中"弱市降权"（软），但"非主线加速"硬否决必须仍然生效。"""
    skill = _chao_gu()
    stock = _stock(market_regime="弱势", dist60=31.0, pos60=93.0, is_main_theme=False)
    signal = run_strategy(skill, stock, ["半导体", "电子"])
    assert signal.get("hard_veto") is True, "非主线加速 hard_veto 被 弱市降权 软阶段遮蔽"
    assert "非主线加速" in str(signal.get("veto_reason") or "")
    # 首个命中阶段仍是名称来源；cap 取更严（min(68, 20) = 20）
    assert signal.get("phase") == "弱市降权"
    assert signal.get("score_cap") == 20.0


def test_first_match_priority_preserved_for_soft_phases():
    """软阶段仍保持首匹配优先级：仅命中软阶段时不触发否决。"""
    skill = _chao_gu()
    stock = _stock(market_regime="弱势", dist60=10.0, pos60=70.0, is_main_theme=True)
    signal = run_strategy(skill, stock, ["半导体", "电子"])
    assert signal.get("hard_veto") is not True
    assert signal.get("phase") == "弱市降权"
    assert signal.get("score_cap") == 68.0


def test_first_match_hard_veto_still_works():
    """首匹配即硬否决（退潮）的行为保持不变。"""
    skill = _chao_gu()
    stock = _stock(dist20=-2.0, chg20=-5.0)
    signal = run_strategy(skill, stock, ["半导体", "电子"])
    assert signal.get("hard_veto") is True
    assert signal.get("phase") == "退潮"
    assert signal.get("score_cap") == 35.0
