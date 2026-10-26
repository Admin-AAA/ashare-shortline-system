"""0 推荐原因文字：分数未过线 / 过线但被风控否决 / 无候选。"""
from app.services.workbench_pure import _no_opportunity_reason, _policy_block_detail

LINE = {"effective_score_line": 72.0, "market_base": "震荡"}


def test_below_score_line():
    text = _no_opportunity_reason([{"final_score": 68.4}], LINE)
    assert text == "最高分 68.4；未过有效分数线 72.0；市场阶段 震荡"


def test_above_line_but_policy_vetoed():
    text = _no_opportunity_reason(
        [{"final_score": 73.6}], LINE, "production_policy_blocked: 1/1；high_amount_veto=1"
    )
    assert text == "最高分 73.6 已过分数线 72.0，但被风控否决：大成交额×1；市场阶段 震荡"


def test_unknown_rule_code_shown_as_is():
    detail = _policy_block_detail("production_policy_blocked: 3/3；max_dd60_veto=2, brand_new_veto=1")
    assert detail == "60日回撤过深×2、brand_new_veto×1"


def test_no_candidates():
    assert _no_opportunity_reason([], LINE) == "无候选通过规则门（全部低于 Skill 门槛）"


def test_empty_reason_not_policy_is_ignored():
    text = _no_opportunity_reason([{"final_score": 73.6}], LINE, "全部低于分数线")
    assert text == "最高分 73.6；有效分数线 72.0；市场阶段 震荡"
