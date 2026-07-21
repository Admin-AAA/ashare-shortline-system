"""T5 市场状态信息量诊断 —— 纯函数单测（不依赖真实 DB）。

锁定：
1. extract_regime_sentiment 的 phase 提取口径（含「弱势·活跃」合体兼容）。
2. proportion_z_test 双比例 z 检验（pooled）数学与方向。
3. _chi2_sf 闭式 χ² 上尾 p 值（对齐 χ² 临界值表，df=1/2/4/6 @ p=0.05）。
4. chi_square_independence 列联表 χ² 统计量与 p（2x2 已知公式 + 退化情形）。
5. build_phase_report 分桶统计 + 信息量检验方向（合成记录，确定性）。

作者：self-improve T5 实例。
"""
import math
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import diagnose_phase_information as dpi  # type: ignore[import-not-found]


def test_extract_regime_sentiment():
    assert dpi.extract_regime_sentiment(None) == ("?", "?")
    assert dpi.extract_regime_sentiment({}) == ("?", "?")
    # 正常双字段
    fs = {"market_regime": "强势", "market_sentiment_phase": "高潮"}
    assert dpi.extract_regime_sentiment(fs) == ("强势", "高潮")
    # 历史「弱势·活跃」合体（sentiment 为空时拆开）
    fs2 = {"market_regime": "弱势·活跃"}
    assert dpi.extract_regime_sentiment(fs2) == ("弱势", "活跃")
    # score_line_meta 兜底
    fs3 = {"score_line_meta": {"market_base": "震荡", "market_sentiment": "平淡"}}
    assert dpi.extract_regime_sentiment(fs3) == ("震荡", "平淡")


def test_proportion_z_test():
    # 相等比例 → z≈0, p≈1
    z, p = dpi.proportion_z_test(20, 40, 20, 40)
    assert abs(z) < 1e-9
    assert abs(p - 1.0) < 1e-9
    # 已知差异：75% vs 25%（n=40 各）→ z≈4.472, p≈7.74e-6
    z2, p2 = dpi.proportion_z_test(30, 40, 10, 40)
    assert abs(z2 - 4.4721) < 0.01
    assert p2 < 1e-4
    # 退化：任一组 n=0 → 不判（z=0,p=1）
    assert dpi.proportion_z_test(0, 0, 1, 1) == (0.0, 1.0)


def test_chi2_sf_critical_values():
    # 对齐 χ² 临界值表 @ p=0.05
    assert abs(dpi._chi2_sf(1, 3.841) - 0.05) < 1e-3
    assert abs(dpi._chi2_sf(2, 5.991) - 0.05) < 1e-3
    assert abs(dpi._chi2_sf(4, 9.488) - 0.05) < 1e-3
    assert abs(dpi._chi2_sf(6, 12.592) - 0.05) < 1e-3
    # 极端：df=1, χ²=20 → p≈7.74e-6
    assert abs(dpi._chi2_sf(1, 20.0) - 7.7442e-6) < 1e-9
    # 退化
    assert dpi._chi2_sf(2, 0.0) == 1.0
    assert math.isnan(dpi._chi2_sf(0, 5.0))


def test_chi_square_independence():
    # 2x2 已知：[[30,10],[10,30]] → χ²=20.0, df=1, p≈7.74e-6
    chi2, df, p = dpi.chi_square_independence([[30, 10], [10, 30]])
    assert abs(chi2 - 20.0) < 1e-9
    assert df == 1
    assert abs(p - 7.7442e-6) < 1e-9
    # 独立表 → χ²=0, p=1
    chi2, df, p = dpi.chi_square_independence([[50, 50], [50, 50]])
    assert abs(chi2) < 1e-9
    assert abs(p - 1.0) < 1e-9
    # 退化：<2 桶 → (0,0,1)
    assert dpi.chi_square_independence([[10, 10]]) == (0.0, 0, 1.0)


def _synthetic_records():
    """合成记录：regime 有信息量、sentiment 无信息量（与真实 LIVE ledger 结论同构）。"""
    recs = []
    # 弱势: 70 胜 / 30 负；震荡: 30 胜 / 70 负（regime 显著）
    for _ in range(35):
        recs.append({"regime": "弱势", "sentiment": "活跃", "win": True, "ret": 2.0})
    for _ in range(15):
        recs.append({"regime": "弱势", "sentiment": "活跃", "win": False, "ret": -2.0})
    for _ in range(35):
        recs.append({"regime": "弱势", "sentiment": "高潮", "win": True, "ret": 2.0})
    for _ in range(15):
        recs.append({"regime": "弱势", "sentiment": "高潮", "win": False, "ret": -2.0})
    for _ in range(15):
        recs.append({"regime": "震荡", "sentiment": "活跃", "win": True, "ret": 1.0})
    for _ in range(35):
        recs.append({"regime": "震荡", "sentiment": "活跃", "win": False, "ret": -1.0})
    for _ in range(15):
        recs.append({"regime": "震荡", "sentiment": "高潮", "win": True, "ret": 1.0})
    for _ in range(35):
        recs.append({"regime": "震荡", "sentiment": "高潮", "win": False, "ret": -1.0})
    return recs


def test_build_phase_report_bucketing_and_info():
    recs = _synthetic_records()
    rep = dpi.build_phase_report(recs)
    # 全量：200 笔，100 胜 → 50%
    assert rep["overall"]["n"] == 200
    assert rep["overall"]["winrate"] == 50.0
    # regime 分桶统计正确
    assert rep["by_regime"]["弱势"]["winrate"] == 70.0
    assert rep["by_regime"]["震荡"]["winrate"] == 30.0
    assert rep["by_regime"]["弱势"]["net_plr"] == 1.0
    # sentiment 两桶胜率都=50%（无信息量）
    assert rep["by_sentiment"]["活跃"]["winrate"] == 50.0
    assert rep["by_sentiment"]["高潮"]["winrate"] == 50.0
    # 信息量检验方向：regime 显著、sentiment 不显著
    assert rep["regime_p"] < 0.05
    assert rep["sentiment_p"] >= 0.05
    # 显著偏离全量桶：弱势(+20pp) 与 震荡(-20pp) 均被标出，且 n>=20
    names = {b["name"] for b in rep["notable_buckets"]}
    assert names == {"弱势", "震荡"}
    # 无 冰点 regime → ice_vs_active 为 None
    assert rep["ice_vs_active"] is None


def test_render_report_runs():
    recs = _synthetic_records()
    rep = dpi.build_phase_report(recs)
    out = dpi.render_report(rep)
    assert "显著有信息量" in out
    assert "震荡" in out or "弱势" in out
