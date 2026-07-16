"""Regression guard: self_check D 项规则覆盖前瞻校验 (第161轮 T3).

背景: D 项胜率被"规则加入前的历史失败样本"污染——规则晚于失败样本 commit，
单看总胜率无法区分"历史污染"vs"真退化"，导致每轮 self-improve 误判持续恶化、
循环加规则过拟合。_classify_failed_coverage 用当前 strategy.yaml 重跑失败样本，
统计被 hard_veto / universe 排除的笔数，给出反事实胜率。

本测试锁定纯统计逻辑（不依赖 DB 真实数据），用 overnight 构造样本验证：
1. 弱市失败笔若被当前 yaml hard_veto（弱市隔夜毒区）→ 计入 blocked，反事实胜率上升。
2. 非弱市失败笔未被 veto → 不计入 blocked。
3. 成功样本不计入 blocked（反事实只剔除失败笔）。
"""
import json
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.engine.skill_loader import load_skills

import self_check  # type: ignore[import-not-found]

SKILLS_DIR = BACKEND_ROOT / "skills"


def _skills():
    return {s.skill_id: s for s in load_skills(SKILLS_DIR)}


def _overnight_ctx(market_regime: str) -> dict:
    """overnight universe 放行 + regime 可控的 context。

    pct∈[3,5] turnover∈[5,10] dist20>=0 满足 universe.include；
    limit_up_days20>=1 满足 prefilter.include。
    regime=弱势 → 命中 弱市隔夜毒区 hard_veto；regime=强势 → 落 尾盘观察 不 veto。
    """
    return {
        "code": "603268", "name": "松发股份", "industry": "通用",
        "pct": 4.0, "turnover_rate": 6.0, "dist20": 5.0,
        "limit_up_days20": 1, "twenty_cm_risk_gate": "pass",
        # pos20>=50 满足 d80bd8f 加的「短期动量过滤」(prefilter 放行)，
        # 使 强势 用例不被该过滤误伤、仍靠 regime 判定覆盖——保持测试原意。
        "pos20": 60.0,
        "market_regime": market_regime, "market_sample_count": 4000,
        "capital_resonance_score": 50, "sector_ladder_completeness_score": 50,
        "limit_up_overheat_score": 30,
    }


def _row(rid: str, label: str, ret: float, fs: dict, skill_id: str):
    sb = [{"strategy_id": skill_id}]
    return (rid, label, ret, json.dumps(fs), json.dumps(sb))


def test_blocked_failure_raises_counterfactual_winrate():
    """弱市失败笔被 veto 覆盖 → blocked=1，反事实胜率 > 原始胜率。"""
    rows = [
        _row("rec_test_001", "failed", -5.69, _overnight_ctx("弱势"), "overnight-holding-method"),
        _row("rec_test_002", "failed", -5.72, _overnight_ctx("强势"), "overnight-holding-method"),
        _row("rec_test_003", "success", 6.39, _overnight_ctx("强势"), "overnight-holding-method"),
    ]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert total == 3
    assert wins == 1
    assert blocked == 1  # 仅 弱市那笔被 veto 覆盖
    assert new_total == 2  # 剔除 1 笔被覆盖失败后剩 2 笔
    # 反事实胜率 = 1 win / 2 = 50%；原始 = 1/3 = 33.3% → 反事实更高
    assert round(wins * 100 / new_total, 1) == 50.0


def test_uncovered_failure_not_blocked():
    """非弱市失败笔未被 veto → 不计入 blocked。"""
    rows = [_row("rec_test_002", "failed", -5.72, _overnight_ctx("强势"), "overnight-holding-method")]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert blocked == 0
    assert total == 1
    assert new_total == 1  # 没有被覆盖的，总数不变
    assert wins == 0


def test_success_never_blocked():
    """成功样本永远不计入 blocked（反事实只剔除失败笔）。"""
    rows = [
        _row("rec_a", "success", 6.39, _overnight_ctx("弱势"), "overnight-holding-method"),
        _row("rec_b", "success", 8.0, _overnight_ctx("强势"), "overnight-holding-method"),
    ]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert blocked == 0
    assert wins == 2
    assert new_total == 2


def test_unknown_skill_does_not_crash():
    """skill_breakdown 里的 strategy_id 在 skills 字典找不到时不应崩溃（静默跳过）。"""
    rows = [_row("rec_x", "failed", -5.0, _overnight_ctx("弱势"), "nonexistent-skill")]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert blocked == 0  # 未知策略无法重跑，不算被覆盖
    assert total == 1


def _chip_ctx(chip_concentration: float) -> dict:
    """chip-single-peak universe 全过、无 hard_veto/排除 phase 的 context（真实快照提炼）。

    chip_concentration>=20 才过 prefilter.market.include(chip_loose)；
    chip_concentration<20 → 生产 prefilter 拦截(chip_loose)，但 run_strategy 的
    _passes_universe 只查 universe + prefilter.market.exclude，漏 prefilter.market.include
    /include_any → 反事实若不复刻生产 prefilter 会把这笔判为「未覆盖」（漏判）。
    本 ctx 通过全部 universe.include（amount/turnover/chg20/pos60/off_high），
    专门锁定 prefilter.include 覆盖口径（非 universe、非 hard_veto、非排除 phase）。
    """
    return {
        "code": "003016", "name": "欣贺股份", "industry": "多元金融",
        "amount_yi": 9.02, "turnover_rate": 5.31, "chg20": 8.15, "pos60": 46.067,
        "off_high_pct": -1.172, "twenty_cm_risk_gate": "not_applicable",
        "is_st": False, "announcement_negative_count": 0, "news_risk_count": 0,
        "max_dd20": -12.424, "chip_concentration": chip_concentration,
        "breakout20": 1, "breakout60": 0, "vol5_20": 4.958,
        "upper_shadow_count20": 0, "pct": 3.69, "market_regime": "弱势",
        "market_sample_count": 4951, "capital_resonance_score": 57.0,
        "sector_ladder_completeness_score": 10.0, "limit_up_overheat_score": 32.0,
        "dsa_trend_score": 18, "dsa_support_score": 0, "dsa_macd_score": 12,
        "ma5": 4.61, "ma10": 4.5, "ma20": 4.52, "dist20": 8.532,
    }


def test_prefilter_include_coverage_counted():
    """被 prefilter.market.include（非 hard_veto、非 universe、非排除 phase）拦截的失败笔
    必须计入 blocked——否则反事实会低估规则覆盖、高估残留毒区，误导下轮 D 攻击。

    复刻生产管线（evaluate_skill_prefilter 查 prefilter.market.include/include_any），
    不能只靠 run_strategy._passes_universe（漏 prefilter.market.include/include_any）。
    chip_concentration=18 < 20 → chip_loose 拦截（生产 prefilter），反事实须判 blocked。
    """
    rows = [
        _row("rec_chip_001", "failed", -3.0, _chip_ctx(18.0), "chip-single-peak-breakout"),
        _row("rec_chip_002", "success", 5.0, _chip_ctx(25.0), "chip-single-peak-breakout"),
    ]
    blocked, new_total, wins, total = self_check._classify_failed_coverage(rows, _skills())
    assert total == 2
    assert wins == 1
    assert blocked == 1  # chip_concentration=18 的失败笔被 prefilter.include 覆盖
    assert new_total == 1  # 剔除被覆盖的 1 笔失败后剩 1 笔（成功笔不计入剔除）
    assert round(wins * 100 / new_total, 1) == 100.0  # 反事实胜率 = 1/1 = 100%


def test_collect_uncovered_returns_only_non_blocked():
    """_collect_uncovered_failures 只返回未被任何规则覆盖的失败笔（与 _classify_failed_coverage 互补）。"""
    rows = [
        _row("rec_w", "failed", -5.69, _overnight_ctx("弱势"), "overnight-holding-method"),
        _row("rec_s", "failed", -5.72, _overnight_ctx("强势"), "overnight-holding-method"),
        _row("rec_ok", "success", 6.39, _overnight_ctx("强势"), "overnight-holding-method"),
    ]
    uncovered = self_check._collect_uncovered_failures(rows, _skills())
    assert len(uncovered) == 1
    assert uncovered[0]["rid"] == "rec_s"  # 仅强势未 veto 的失败笔
    assert uncovered[0]["executed_return"] == -5.72


def test_collect_uncovered_tolerates_7tuple_and_captures_exit_reason():
    """生产路径返回 7 列（含 exit_reason/max_return），采集须容错并保留这两个字段用于归因。"""
    fs = _overnight_ctx("强势")
    sb = [{"strategy_id": "overnight-holding-method"}]
    row7 = (
        "rec_7", "failed", -5.67,
        json.dumps(fs), json.dumps(sb),
        "stop_loss_hit", 0.5,  # exit_reason, max_return
    )
    uncovered = self_check._collect_uncovered_failures([row7], _skills())
    assert len(uncovered) == 1
    assert uncovered[0]["exit_reason"] == "stop_loss_hit"
    assert uncovered[0]["max_return"] == 0.5


def test_classify_failure_mode_inline_modes():
    """内联镜像 classify_failure_mode 的模式判定须与 failure_attribution 口径一致。"""
    # gap_roundtrip：stop_loss_hit 且峰值 >3% 却净亏
    assert self_check._classify_failure_mode_inline(
        exit_reason="stop_loss_hit", executed_return=-1.0, max_return=4.0,
        rsi_12=55, chg20=10, pos60=50, setup_type=None,
    ) == "gap_roundtrip"
    # extension_chase：chg20 极高
    assert self_check._classify_failure_mode_inline(
        exit_reason="stop_loss_hit", executed_return=-5.0, max_return=0.1,
        rsi_12=70, chg20=55, pos60=60, setup_type=None,
    ) == "extension_chase"
    # other：无明显信号
    assert self_check._classify_failure_mode_inline(
        exit_reason="stop_loss_hit", executed_return=-2.0, max_return=0.5,
        rsi_12=55, chg20=10, pos60=50, setup_type=None,
    ) == "other"


def test_rid_trade_date_parses_iso_format():
    """_rid_trade_date 返回 ISO 格式，与 FORWARD_EPOCH 可直接字符串比较。"""
    assert self_check._rid_trade_date("rec_20260709_001_abc") == "2026-07-09"
    assert self_check._rid_trade_date("rec_20260708_002") == "2026-07-08"
    # 无法解析返回空串（字符串比较时 < 任何 FORWARD_EPOCH，不进前瞻窗口）
    assert self_check._rid_trade_date("badrid") == ""
    assert self_check._rid_trade_date("rec_x") == ""


def test_forward_veto_counterfactual_excludes_covered_failures(capsys):
    """前瞻 veto 反事实：剔除被覆盖失败笔后重算，反映 veto 后预期（非 veto 前历史）。

    构造前瞻窗口内 3 笔：1 弱市失败(被 veto 覆盖) + 1 强势失败(未覆盖) + 1 成功。
    剔除弱市失败后剩 2 笔(1 失败+1 成功)，胜率 50%。
    """
    rows = [
        _row("rec_20260710_001", "failed", -5.69, _overnight_ctx("弱势"), "overnight-holding-method"),
        _row("rec_20260710_002", "failed", -5.70, _overnight_ctx("强势"), "overnight-holding-method"),
        _row("rec_20260710_003", "success", 4.79, _overnight_ctx("强势"), "overnight-holding-method"),
        _row("rec_20260710_004", "success", 4.80, _overnight_ctx("强势"), "overnight-holding-method"),
        _row("rec_20260710_005", "success", 4.78, _overnight_ctx("强势"), "overnight-holding-method"),
    ]
    self_check.FORWARD_EPOCH = "2026-07-09"
    self_check._print_forward_veto_counterfactual(rows, _skills(), win_rate_baseline=61.5)
    out = capsys.readouterr().out
    assert "前瞻 veto 反事实" in out
    # 剔除 1 笔被覆盖失败后剩 2 笔，1 成功 → WR=50%
    assert "WR=75.0%" in out
    assert "剔除 1 笔" in out
