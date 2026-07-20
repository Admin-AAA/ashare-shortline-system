"""self_check LLM delta 有效性护栏回归测试（第 8 轮 T8/T9）。

锁定 `_llm_delta_effectiveness` 纯函数的三类判定：
1. 去混杂单调性：delta 分档胜率应单调（大 delta=强信心→胜率更高），防止按「有无 delta」
   分组的单日集中混杂误判「LLM 负价值」。
2. oppose 率：LLM 几乎从不反对（<5%）→ 风险识别形同虚设，T9 prompt 校准目标。
3. 顶层聚合超 clamp：多 agent 求和后 score_delta 超单 agent [-18,16] → 数据完整性。

背景（第 8 轮 T8 实证）：delta>0 票 WR=63% 表面低于无 delta 74%，实为「开启 LLM 的 run
恰逢 07-07/08/09 regime 差日」的时间混杂（0 个 run 同时含两类票）；去混杂后按档单调
（30+ WR78% > 15-30 WR59% > 5-15 WR10%），LLM 审查强度与质量正相关。
"""
import pathlib
import sys

SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _rec(delta, stance="support", label="success"):
    return {"delta": delta, "stance": stance, "label": label}


def test_monotonic_positive_when_large_delta_wins_more():
    """大 delta 档胜率更高 → monotonic_ok=True（LLM 审查有效，第 8 轮实证形态）。"""
    recs = (
        [_rec(7, label="failed") for _ in range(9)] + [_rec(7, label="success")]        # 0-15: 10%
        + [_rec(20, label="success") for _ in range(6)] + [_rec(20, label="failed") for _ in range(4)]  # 15-30: 60%
        + [_rec(35, label="success") for _ in range(8)] + [_rec(35, label="failed") for _ in range(2)]  # 30+: 80%
    )
    r = self_check._llm_delta_effectiveness(recs)
    assert r["monotonic_ok"] is True
    assert r["bucket_wr"]["30+"] > r["bucket_wr"]["0-15"]


def test_non_monotonic_flagged_when_large_delta_loses():
    """大 delta 档胜率反而低 → monotonic_ok=False + 校准失效 note。"""
    recs = (
        [_rec(7, label="success") for _ in range(9)]          # 0-15: 100%
        + [_rec(35, label="failed") for _ in range(8)] + [_rec(35, label="success") for _ in range(2)]  # 30+: 20%
    )
    r = self_check._llm_delta_effectiveness(recs)
    assert r["monotonic_ok"] is False
    assert any("非单调" in n or "校准失效" in n for n in r["notes"])


def test_low_oppose_ratio_flagged():
    """oppose stance 占比 <5% 且样本足够 → 「几乎从不反对」note。"""
    recs = [_rec(20, stance="support") for _ in range(40)]  # 0 oppose
    r = self_check._llm_delta_effectiveness(recs)
    assert r["oppose_ratio"] == 0.0
    assert any("oppose" in n and "反对" in n for n in r["notes"])


def test_normal_oppose_ratio_not_flagged():
    """oppose 占比 >=5% → 不触发 oppose note。"""
    recs = [_rec(20, stance="support") for _ in range(18)] + [_rec(-5, stance="oppose") for _ in range(2)]
    r = self_check._llm_delta_effectiveness(recs)
    assert r["oppose_ratio"] >= 0.05
    assert not any("反对" in n for n in r["notes"])


def test_over_clamp_counted():
    """delta 超 [-18,16] → over_clamp_count + 数据完整性 note。"""
    recs = [_rec(40.0), _rec(17.0), _rec(-20.0), _rec(10.0)]
    r = self_check._llm_delta_effectiveness(recs)
    assert r["over_clamp_count"] == 3  # 40, 17, -20 超界；10 在界内
    assert any("clamp" in n and "数据完整性" in n for n in r["notes"])


def test_small_sample_note():
    """有 delta 样本 <20 → 单调性判断标「噪声」note（防小样本误读）。"""
    recs = [_rec(20), _rec(30), _rec(8)]
    r = self_check._llm_delta_effectiveness(recs)
    assert any("样本过少" in n or "噪声" in n for n in r["notes"])
