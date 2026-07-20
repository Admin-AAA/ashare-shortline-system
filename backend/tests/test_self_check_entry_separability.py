"""Regression guard: self_check 毒区特征可分性护栏（_entry_separability_falsified，第192轮 T3）。

背景：no_follow_through 模式级跨多日（单日集中度护栏报 ✅可验证），但失败笔与同窗口
成功笔在干净个股特征 vol5_20(1.18 vs 1.38)/pos60(77.3 vs 78.3) 中位数几乎完全重叠
（rel_gap 0.14/0.01）——无干净可分维度，失败由 regime 驱动而非个股特征，entry 侧不可解。
若不显式降级，运营者看到「✅可验证」会继续在该毒区空转挖 entry 规则（过拟合噪声）。
本测试锁定「干净特征全重叠 → 判不可分」逻辑。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _mk(vol5_20, pos60, chg20=None, turnover=None):
    fs = {"vol5_20": vol5_20, "pos60": pos60}
    if chg20 is not None:
        fs["chg20"] = chg20
    if turnover is not None:
        fs["turnover_rate"] = turnover
    return {"fs": fs}


def _mk_fail(vol5_20, pos60, **kw):
    it = _mk(vol5_20, pos60, **kw)
    it.update({"exit_reason": "stop_loss_hit", "executed_return": -5.0, "max_return": 0.3})
    return it


def test_overlapping_medians_falsified():
    """干净特征（vol5_20/pos60）失败与成功中位数全重叠 → 判不可分（返回 dict）。"""
    # 失败 5 笔 vol~1.2/pos60~77；成功 30 笔 vol~1.35/pos60~78 → rel_gap <0.20
    fails = [_mk_fail(1.1 + i * 0.05, 76.0 + i) for i in range(5)]
    succs = [_mk(1.3 + (i % 5) * 0.03, 77.5 + (i % 3)) for i in range(30)]
    res = self_check._entry_separability_falsified(fails, succs)
    assert res is not None
    assert "feature_gap" in res
    assert res["feature_gap"]["vol5_20"] < 0.20
    assert res["feature_gap"]["pos60"] < 0.20


def test_separable_feature_returns_none():
    """任一干净特征中位数差异 ≥tol → 可分，返回 None（不禁止 entry 规则）。"""
    # 失败 vol~3.0（远高于成功 ~1.3）→ vol5_20 rel_gap >>0.20
    fails = [_mk_fail(3.0 + i * 0.1, 77.0) for i in range(5)]
    succs = [_mk(1.3, 78.0) for _ in range(30)]
    assert self_check._entry_separability_falsified(fails, succs) is None


def test_insufficient_fail_sample_returns_none():
    """失败样本 <3 → None（样本不足，不下不可分结论）。"""
    fails = [_mk_fail(1.2, 77.0) for _ in range(2)]
    succs = [_mk(1.35, 78.0) for _ in range(30)]
    assert self_check._entry_separability_falsified(fails, succs) is None


def test_insufficient_success_sample_returns_none():
    """成功样本 <5 → None（对照不足，不下不可分结论）。"""
    fails = [_mk_fail(1.2, 77.0) for _ in range(5)]
    succs = [_mk(1.35, 78.0) for _ in range(3)]
    assert self_check._entry_separability_falsified(fails, succs) is None


def test_missing_feature_values_skipped():
    """特征值为 None 的样本被跳过，不污染中位数；全部缺失则该特征不参与判定。"""
    fails = [{"fs": {"vol5_20": None, "pos60": 77.0}, "exit_reason": "stop_loss_hit",
              "executed_return": -5.0, "max_return": 0.3} for _ in range(5)]
    succs = [{"fs": {"vol5_20": None, "pos60": 78.0}} for _ in range(30)]
    # vol5_20 全 None → 只判 pos60（重叠）→ 不可分
    res = self_check._entry_separability_falsified(fails, succs)
    assert res is not None
    assert "vol5_20" not in res["feature_gap"]
