"""Regression guard: self_check A 项 0 推荐日分类（保护性 vs 故障性，第183轮 T12 误报修复）。

背景: A 项原先把所有"系统跑了但 0 推荐"的交易日都当 T12 断档告警，但强势 regime
(score_line penalty +4/+8) / 恐慌日 (ld>100 panic veto +12) / 结构性强势 regime 会把
有效线拉到 95-103，候选最高分 82<95 不过线 → 0 推荐是"准确 > 可见"的设计意图，
非 pipeline 故障。误报会让 self-improve 浪费 D 攻击档期去查 T12。

_zero_rec_day_is_protective 用纯函数判断：至少一个 completed run 的 score_line 阶段
input>0 且 pass=0（候选进了分数线但分不够）= 保护性；否则（prefilter/skill_gate 卡住
或无 completed run）= 故障性。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _stage(stage: str, inp: int, pas: int) -> dict:
    return {"stage": stage, "input_count": inp, "pass_count": pas, "filtered_count": inp - pas}


def test_protective_when_score_line_zero_pass_with_candidates():
    """候选进入 score_line 但全被有效线拦截 = 保护性 0 推荐（D 项设计意图）。"""
    stages = [
        _stage("market_universe", 5200, 4950),
        _stage("prefilter", 4950, 60),
        _stage("skill_gate", 60, 60),
        _stage("skill_review", 60, 60),
        _stage("score_line", 60, 0),  # 60 候选进分数线，0 过线
    ]
    assert self_check._zero_rec_day_is_protective([stages]) is True


def test_protective_across_multiple_runs_one_reaches_score_line():
    """多个 completed run 中只要有一个走到 score_line 0 通过即算保护性。"""
    run_a = [_stage("prefilter", 4950, 0)]  # prefilter 卡住
    run_b = [
        _stage("prefilter", 4950, 52),
        _stage("skill_gate", 52, 52),
        _stage("score_line", 52, 0),  # 保护性
    ]
    assert self_check._zero_rec_day_is_protective([run_a, run_b]) is True


def test_faulty_when_prefilter_blocks_all():
    """prefilter 阶段就把候选全过滤（没进 score_line）= 故障性 0 推荐（真 T12）。"""
    stages = [
        _stage("market_universe", 5200, 4950),
        _stage("prefilter", 4950, 0),  # prefilter 全过滤
    ]
    assert self_check._zero_rec_day_is_protective([stages]) is False


def test_faulty_when_skill_gate_blocks_all():
    """skill_gate 阶段全 hard_veto/low_gate_score（没进 score_line）= 故障性。"""
    stages = [
        _stage("prefilter", 4950, 200),
        _stage("skill_gate", 200, 0),  # 全被 gate 卡
    ]
    assert self_check._zero_rec_day_is_protective([stages]) is False


def test_faulty_when_no_completed_runs():
    """无 completed run（全 interrupted/failed）= 故障性（空列表）。"""
    assert self_check._zero_rec_day_is_protective([]) is False
    assert self_check._zero_rec_day_is_protective([[]]) is False


def test_faulty_when_score_line_has_passes_but_rec_still_zero():
    """score_line 有通过但 recommended_count=0（理论上不该发生，边界）：不算保护性。
    保护性严格定义为 input>0 且 pass==0；pass>0 说明有候选过线，0 推荐另有原因。
    """
    stages = [
        _stage("prefilter", 4950, 60),
        _stage("score_line", 60, 5),  # 5 只过线
    ]
    assert self_check._zero_rec_day_is_protective([stages]) is False


# --------------------------------------------------------------------------- #
# 失败归因内联镜像漂移守卫（第184轮新增）
# --------------------------------------------------------------------------- #
def test_inline_classify_matches_authoritative_no_follow_through():
    """self_check 内联 _classify_failure_mode_inline 必须与权威 classify_failure_mode 一致。

    防止两份实现漂移（上轮 T12 的内联镜像已知风险）。覆盖 no_follow_through 新分支
    及边界：入场即套（max≤0.5 & ret<0）、max>0.5 不命中、可盈利单不命中。
    """
    from scripts.failure_attribution import classify_failure_mode

    cases = [
        # no_follow_through 正例
        {"exit_reason": "stop_loss_hit", "executed_return": -4.0, "max_return": 0.15,
         "rsi_12": 55, "chg20": 13, "pos60": 72, "setup_type": "多头顺势"},
        # horizon_close + 峰值负 → no_follow_through
        {"exit_reason": "horizon_close", "executed_return": -2.4, "max_return": -0.65,
         "rsi_12": 58, "chg20": 11, "pos60": 77, "setup_type": "单峰放量突破"},
        # 边界：max=1.0>0.5 不命中 no_follow_through（落到 other）
        {"exit_reason": "stop_loss_hit", "executed_return": -2.0, "max_return": 1.0,
         "rsi_12": 55, "chg20": 20, "pos60": 65, "setup_type": "多头顺势"},
        # gap_roundtrip 不被误判（max>3）
        {"exit_reason": "trailing_stop_hit", "executed_return": -1.5, "max_return": 5.0,
         "rsi_12": 70, "chg20": 30, "pos60": 80, "setup_type": "多头顺势"},
        # weak_stock 优先（rsi<45）不被 no_follow_through 抢
        {"exit_reason": "stop_loss_hit", "executed_return": -2.0, "max_return": 0.3,
         "rsi_12": 30, "chg20": -5, "pos60": 40, "setup_type": "多头顺势"},
    ]
    for kwargs in cases:
        expected = classify_failure_mode(**kwargs)
        got = self_check._classify_failure_mode_inline(**kwargs)
        assert got == expected, f"漂移: 输入{kwargs} 权威={expected} 内联={got}"
