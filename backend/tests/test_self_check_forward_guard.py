"""Regression guard: self_check D 项「前瞻验证护栏」(第171轮 T3.6 / D 项完整性)。

背景: D 项全量胜率可能高于基线（如当前 79.6% vs 基线 61.5%），但若前瞻窗口
（FORWARD_EPOCH 之后的修复后新 run 结算样本）为空，该「高于基线」可能含修复前历史污染，
未经样本外验证。运营者若据此 claim D 项改进并循环加规则，即违反「D 项必须前瞻样本验证、
不为刷数字而刷」护栏。

_forward_verification_flag 是纯函数：全量高于基线 + 前瞻空 → 返回警示；
前瞻有样本 / 全量未高于基线 → 返回 None（无需警示）。本测试锁定该逻辑。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

BASELINE = 61.5


def test_flag_when_full_beats_baseline_but_forward_empty():
    """全量 79.6% > 基线 61.5% 但前瞻窗口空 → 必须返回非空警示。"""
    flag = self_check._forward_verification_flag(79.6, None, BASELINE)
    assert flag is not None
    assert "前瞻窗口" in flag
    assert "未经样本外验证" in flag


def test_no_flag_when_forward_has_samples():
    """前瞻窗口已有样本（可样本外验证）→ 不警示，即使全量高于基线。"""
    fwd = (85.0, 12, -5.7)  # (wr, n, max_loss)
    flag = self_check._forward_verification_flag(79.6, fwd, BASELINE)
    assert flag is None


def test_no_flag_when_full_not_above_baseline():
    """全量未高于基线 → 无「虚高改进」可误读，不警示（含前瞻空）。"""
    flag = self_check._forward_verification_flag(55.0, None, BASELINE)
    assert flag is None


def test_no_flag_when_full_none():
    """全量无样本 → 不警示。"""
    flag = self_check._forward_verification_flag(None, None, BASELINE)
    assert flag is None
