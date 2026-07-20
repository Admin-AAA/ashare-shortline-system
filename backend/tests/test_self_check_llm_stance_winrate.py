"""Regression guard: self_check LLM stance 强度分档胜率监测（_llm_stance_winrate，第193轮 T8）。

背景：真 LLM 样本 strong_support n=17 WR=82.4% vs support n=128 WR=64.1%（+18.3pp 溢价），
stance 强度与胜率单调正相关 = LLM 提供真实信心区分度。但溢价跨日不稳、样本小，不构成
可加 entry/加权规则——仅作 LLM 层健康监测。若 strong_support 溢价反转（<support），说明
LLM 信心校准退化，T9 需复核。本测试锁定分档胜率计算与 premium 判定逻辑。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _rec(stance, label):
    return {"delta": None, "stance": stance, "label": label}


def test_premium_positive_when_strong_outperforms():
    """strong_support 胜率 > support → premium 正，premium_ok=True。"""
    records = [_rec("strong_support", "success") for _ in range(8)]
    records += [_rec("strong_support", "failed") for _ in range(2)]   # 8/10=80%
    records += [_rec("support", "success") for _ in range(6)]
    records += [_rec("support", "failed") for _ in range(4)]          # 6/10=60%
    res = self_check._llm_stance_winrate(records)
    assert res["stance_wr"]["strong_support"] == (10, 80.0)
    assert res["stance_wr"]["support"] == (10, 60.0)
    assert res["premium"] == 20.0
    assert res["premium_ok"] is True
    assert res["note"] is None


def test_premium_reversal_warns():
    """strong_support 胜率 < support → premium 反转，premium_ok=False + 告警 note。"""
    records = [_rec("strong_support", "success") for _ in range(4)]
    records += [_rec("strong_support", "failed") for _ in range(6)]   # 4/10=40%
    records += [_rec("support", "success") for _ in range(7)]
    records += [_rec("support", "failed") for _ in range(3)]          # 7/10=70%
    res = self_check._llm_stance_winrate(records)
    assert res["premium"] == -30.0
    assert res["premium_ok"] is False
    assert res["note"] is not None
    assert "校准退化" in res["note"]


def test_insufficient_strong_support_sample_no_premium():
    """strong_support 样本 <10 → premium=None（不下溢价结论）。"""
    records = [_rec("strong_support", "success") for _ in range(5)]
    records += [_rec("support", "success") for _ in range(6)]
    records += [_rec("support", "failed") for _ in range(4)]
    res = self_check._llm_stance_winrate(records)
    assert res["premium"] is None
    assert res["premium_ok"] is None
    assert res["note"] is None


def test_none_stance_grouped():
    """stance 为 None/空串 → 归入 'None' 组，不报错。"""
    records = [_rec(None, "success"), _rec("", "failed")]
    records += [_rec("support", "success") for _ in range(10)]
    res = self_check._llm_stance_winrate(records)
    assert "None" in res["stance_wr"]
    assert res["stance_wr"]["None"] == (2, 50.0)


def test_missing_stance_groups_no_crash():
    """只有单一 stance（无 strong_support/support 对照）→ premium=None。"""
    records = [_rec("neutral_support", "success") for _ in range(20)]
    res = self_check._llm_stance_winrate(records)
    assert res["premium"] is None
    assert res["stance_wr"]["neutral_support"] == (20, 100.0)
