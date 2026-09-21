import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _stage(stage: str, inp: int, pas: int) -> dict:
    return {"stage": stage, "input_count": inp, "pass_count": pas, "filtered_count": inp - pas}


def _audit(stages: list[dict], *, opportunity: bool = False) -> dict:
    rows = []
    if opportunity:
        rows.append({
            "price": 10.0,
            "hard_veto": False,
            "score_line": {
                "final_score": 80.0,
                "score_line_meta": {
                    "base_score_line": 72.0,
                    "execution_status": "tradable",
                    "execution_blocked": False,
                    "regime_struct_strong_hard_veto": False,
                    "regime_oscillation_hard_veto": False,
                },
            },
        })
    return {"stage_summary": stages, "rows": rows}


def test_reasonable_abstain_when_score_line_rejects_opportunity():
    stages = [
        _stage("market_universe", 5200, 4950),
        _stage("prefilter", 4950, 60),
        _stage("skill_gate", 60, 60),
        _stage("skill_review", 60, 60),
        _stage("score_line", 60, 0),
    ]
    assert self_check.classify_zero_output_health([_audit(stages, opportunity=True)]) == "reasonable_abstain"


def test_reasonable_abstain_across_multiple_runs():
    run_a = [_stage("prefilter", 4950, 0)]
    run_b = [
        _stage("prefilter", 4950, 52),
        _stage("skill_gate", 52, 52),
        _stage("score_line", 52, 0),
    ]
    audits = [_audit(run_a), _audit(run_b, opportunity=True)]
    assert self_check.classify_zero_output_health(audits) == "reasonable_abstain"


def test_faulty_when_prefilter_blocks_all():
    """prefilter 阶段就把候选全过滤（没进 score_line）= 故障性 0 推荐（真 T12）。"""
    stages = [
        _stage("market_universe", 5200, 4950),
        _stage("prefilter", 4950, 0),  # prefilter 全过滤
    ]
    assert self_check.classify_zero_output_health([_audit(stages)]) == "pipeline_fault"


def test_faulty_when_skill_gate_blocks_all():
    """skill_gate 阶段全 hard_veto/low_gate_score（没进 score_line）= 故障性。"""
    stages = [
        _stage("prefilter", 4950, 200),
        _stage("skill_gate", 200, 0),  # 全被 gate 卡
    ]
    assert self_check.classify_zero_output_health([_audit(stages)]) == "pipeline_fault"


def test_faulty_when_no_completed_runs():
    """无 completed run（全 interrupted/failed）= 故障性（空列表）。"""
    assert self_check.classify_zero_output_health([]) == "pipeline_fault"
    assert self_check.classify_zero_output_health([_audit([])]) == "pipeline_fault"


def test_faulty_when_score_line_has_passes_but_rec_still_zero():
    """score_line 有通过但 recommended_count=0（理论上不该发生，边界）：不算保护性。
    保护性严格定义为 input>0 且 pass==0；pass>0 说明有候选过线，0 推荐另有原因。
    """
    stages = [
        _stage("prefilter", 4950, 60),
        _stage("score_line", 60, 5),  # 5 只过线
    ]
    assert self_check.classify_zero_output_health([_audit(stages, opportunity=True)]) == "pipeline_fault"


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
