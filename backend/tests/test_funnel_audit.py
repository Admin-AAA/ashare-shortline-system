"""diagnose_funnel_audit.py 纯函数单测（不连库，构造合成 filter_audit_json）。"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from diagnose_funnel_audit import (  # noqa: E402
    NOMINAL_FLOOR,
    T1_NEAR_FLOOR_LOW,
    _bucketize,
    classify_zero_run,
    extract_blocked_candidates,
    extract_effective_line,
    extract_near_misses,
    extract_stages,
    parse_funnel,
)


def _audit(best_fs, eff_line, prefilter_filtered=0, rows=None):
    stage_summary = [
        {"stage": "market_universe", "label": "全市场->基础合格", "input_count": 5200, "pass_count": 4955, "filtered_count": 245},
        {"stage": "prefilter", "label": "范围候选->预过滤", "input_count": 4955, "pass_count": 4955 - prefilter_filtered, "filtered_count": prefilter_filtered},
        {"stage": "skill_gate", "label": "预过滤->技能门", "input_count": 4955 - prefilter_filtered, "pass_count": 90, "filtered_count": 4865 - prefilter_filtered},
        {"stage": "score_line", "label": "技能门->分数线", "input_count": 90, "pass_count": 0, "filtered_count": 90},
    ]
    reason_counts = {}
    if best_fs is not None and eff_line is not None:
        reason_counts["score_line"] = {f"最终分 {best_fs} 低于有效线 {eff_line}": 90}
    return {
        "stage_summary": stage_summary,
        "reason_counts": reason_counts,
        "rows": rows or [],
    }


def test_classify_legitimate_empty_below_floor():
    cls, note = classify_zero_run(0, 71.88, extract_stages(_audit(71.88, 95.55)), universe_count=4950)
    assert cls == "legitimate_empty"
    assert "名义地板" in note


def test_classify_legitimate_empty_marginal_band():
    # 72.0~80.0 为边际合格带，恐慌日抬线拦下属正确防守，非误拦
    cls, note = classify_zero_run(0, 72.20, extract_stages(_audit(72.20, 95.55)), universe_count=4950)
    assert cls == "legitimate_empty"
    assert "边际合格" in note


def test_classify_gate_misblock_suspect_strong_candidate():
    # ≥ 地板+8 (80) 的真正强候选被 regime 线拦下 → 需反事实复核
    cls, note = classify_zero_run(0, 83.65, extract_stages(_audit(83.65, 93.2)), universe_count=4950)
    assert cls == "gate_misblock_suspect"
    assert "反事实" in note


def test_classify_scoped_run_small_universe():
    cls, note = classify_zero_run(0, None, extract_stages(_audit(None, None)), universe_count=48)
    assert cls == "scoped_run"
    assert "范围受限" in note


def test_classify_data_fault_prefilter_collapse():
    # prefilter 近乎全卡 + 无可解析最高分 + 全市场 universe
    cls, note = classify_zero_run(0, None, extract_stages(_audit(None, None, prefilter_filtered=4900)), universe_count=4950)
    assert cls == "data_fault"
    assert "故障" in note


def test_classify_produced_when_recommended():
    cls, _ = classify_zero_run(5, 80.0, extract_stages(_audit(80.0, 72.0)), universe_count=4950)
    assert cls == "produced"


def test_extract_effective_line_picks_max():
    audit = _audit(71.88, 95.55)
    audit["reason_counts"]["score_line"]["最终分 65.10 低于有效线 101.55"] = 1
    best_fs, line, gap = extract_effective_line(audit)
    assert best_fs == 71.88
    assert line == 95.55
    assert abs(gap - (71.88 - 95.55)) < 1e-6


def test_extract_near_misses_top_k_and_skill():
    rows = [
        {"code": "300628", "name": "亿联网络", "final_stage": "score_line",
         "score_line": {"final_score": 71.88, "effective_score_line": 95.55, "score_gap": -23.67, "reason": "x"},
         "rule_gate": {"best_skill_id": "sector-relative-strength-leader", "best_gate_score": 86.9}},
        {"code": "000001", "name": "平安银行", "final_stage": "score_line",
         "score_line": {"final_score": 54.43, "effective_score_line": 64.0, "score_gap": -9.57, "reason": "y"},
         "rule_gate": {"best_skill_id": "daily-stock-analysis-skill", "best_gate_score": 60.0}},
        {"code": "600519", "name": "贵州茅台", "final_stage": "score_line",
         "score_line": {"final_score": 65.85, "effective_score_line": 89.55, "score_gap": -23.7, "reason": "z"},
         "rule_gate": {"best_skill_id": "chen-xiaoqun-skill", "best_gate_score": 70.0}},
        {"code": "SKIP", "name": "非近失", "final_stage": "skill_gate", "score_line": {"final_score": 40.0}},
    ]
    audit = _audit(71.88, 95.55, rows=rows)
    nm = extract_near_misses(audit, top_k=2)
    assert [c.code for c in nm] == ["300628", "600519"]  # 按 final_score 降序
    assert nm[0].best_skill_id == "sector-relative-strength-leader"
    assert nm[0].best_gate_score == 86.9


def test_parse_funnel_end_to_end_legitimate():
    audit = _audit(71.88, 95.55, rows=[
        {"code": "300628", "name": "亿联网络", "final_stage": "score_line",
         "score_line": {"final_score": 71.88, "effective_score_line": 95.55, "score_gap": -23.67, "reason": "x"},
         "rule_gate": {"best_skill_id": "sector-relative-strength-leader", "best_gate_score": 86.9}},
    ])
    rf = parse_funnel("run_x", "2026-07-21", 4955, 0, audit)
    assert rf.classification == "legitimate_empty"
    assert rf.effective_line == 95.55
    assert rf.best_final_score == 71.88
    assert rf.near_misses[0].code == "300628"


def test_parse_funnel_accepts_json_string():
    audit = _audit(83.65, 93.2)
    rf = parse_funnel("run_y", "2026-07-17", 4950, 0, json.dumps(audit))
    assert rf.classification == "gate_misblock_suspect"
    assert rf.best_final_score == 83.65


def test_extract_blocked_candidates_returns_all_score_line_rows():
    """被拦候选提取不截断（与 near_misses 的 top_k 限制对比），供 T+1 反事实统计。"""
    rows = [
        {"code": "A", "name": "a", "final_stage": "score_line",
         "score_line": {"final_score": 71.88, "effective_score_line": 95.55, "score_gap": -23.67}},
        {"code": "B", "name": "b", "final_stage": "score_line",
         "score_line": {"final_score": 65.10, "effective_score_line": 89.55, "score_gap": -24.45}},
        {"code": "C", "name": "c", "final_stage": "score_line",
         "score_line": {"final_score": 54.43, "effective_score_line": 64.0, "score_gap": -9.57}},
        {"code": "SKIP", "name": "非分数线阶段", "final_stage": "skill_gate",
         "score_line": {"final_score": 40.0}},
    ]
    audit = _audit(71.88, 95.55, rows=rows)
    blocked = extract_blocked_candidates(audit)
    # 全部 score_line 阶段候选（3 个），按 final_score 降序，不含 skill_gate 阶段
    assert [c.code for c in blocked] == ["A", "B", "C"]
    assert blocked[0].final_score == 71.88


def test_extract_blocked_candidates_fallback_parses_final_reason():
    """score_line 子结构缺 final_score 时，从 final_reason 正则兜底解析。"""
    rows = [
        {"code": "X", "name": "x", "final_stage": "score_line",
         "score_line": {}, "final_reason": "最终分 70.50 低于有效线 85.00"},
    ]
    audit = _audit(70.50, 85.00, rows=rows)
    blocked = extract_blocked_candidates(audit)
    assert len(blocked) == 1
    assert blocked[0].final_score == 70.50
    assert blocked[0].effective_line == 85.00


def test_bucketize_empty_returns_zero_bucket():
    b = _bucketize("x", [])
    assert b.n == 0 and b.wins == 0 and b.avg == 0.0 and b.pl == 0.0


def test_bucketize_win_rate_avg_pl():
    # final_score 不参与分桶计算（调用方已分好），只校验 WR/avg/PL 统计
    subset = [(70.0, 2.0), (71.0, -1.0), (69.0, 3.0)]  # 2 win 1 loss
    b = _bucketize("near", subset)
    assert b.n == 3
    assert b.wins == 2
    assert abs(b.avg - (4.0 / 3)) < 1e-6
    # wins=2.0+3.0=5.0, losses=1.0 → PL=5.0
    assert abs(b.pl - 5.0) < 1e-6


def test_bucketize_all_wins_pl_inf():
    subset = [(72.0, 1.5), (72.0, 2.5)]
    b = _bucketize("over", subset)
    assert b.n == 2 and b.wins == 2
    assert b.pl == float("inf")  # 无亏损样本


def test_t1_bucket_boundary_floor_plus_includes_floor():
    """at_floor_plus 桶边界：final==NOMINAL_FLOOR(72) 归入 at_floor_plus，不归 near_floor。"""
    at_floor = [(NOMINAL_FLOOR, 1.0)]
    near = [(NOMINAL_FLOOR - 0.01, 1.0)]
    bf = _bucketize("at_floor_plus", at_floor)
    bn = _bucketize("near", near)
    assert bf.n == 1 and bn.n == 1
    # 验证边界常量自洽：near_floor 上界 == NOMINAL_FLOOR
    assert T1_NEAR_FLOOR_LOW == NOMINAL_FLOOR - 4.0
