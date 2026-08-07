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


class _LedgerResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _LedgerDb:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    def execute(self, statement, params):
        self.executions.append((statement, params))
        return _LedgerResult(self.rows)


def test_fetch_live_formal_ledger_fails_closed_and_binds_canonical_ids(monkeypatch):
    db = _LedgerDb([
        ("rec_phase", "success", 2.0, '{"market_regime":"弱势"}'),
    ])
    monkeypatch.setattr(
        dpi,
        "canonical_live_forward_run_ids",
        lambda _db: (),
    )

    assert dpi._fetch_live_formal_ledger(db) == []
    assert db.executions == []

    records = dpi._fetch_live_formal_ledger(db, ("run_live",))

    assert records == [
        {
            "regime": "弱势",
            "sentiment": "?",
            "win": True,
            "ret": 2.0,
        }
    ]
    assert db.executions[0][1] == {"canonical_run_ids": ("run_live",)}
    assert "IN (__[POSTCOMPILE_canonical_run_ids])" in str(db.executions[0][0])


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


def _phase_report_like(real: bool) -> dict:
    """构造一个贴近真实 LIVE ledger 的 phase 报告 dict（仅 regime 维度相关字段）。"""
    if real:
        # 真实态：regime χ² 不显著(0.0979)，但 震荡 桶 59.2% < 基线 61.5%
        return {
            "overall": {"winrate": 71.2, "n": 292},
            "by_regime": {
                "弱势": {"n": 207, "winrate": 73.4},
                "震荡": {"n": 49, "winrate": 59.2},
                "结构性强势": {"n": 22, "winrate": 63.6},
                "强势": {"n": 12, "winrate": 91.7},
            },
            "regime_p": 0.0979,
        }
    # 假设态：regime χ² 显著、两桶差异大
    return {
        "overall": {"winrate": 50.0, "n": 200},
        "by_regime": {
            "弱势": {"n": 100, "winrate": 70.0},
            "震荡": {"n": 100, "winrate": 30.0},
        },
        "regime_p": 0.01,
    }


def test_evaluate_regime_defense_below_baseline_keeps():
    # 当前真实态：χ² 不显著（检验力不足），但 震荡 桶低于基线 → 保留防守
    rep = _phase_report_like(real=True)
    d = dpi.evaluate_regime_defense(rep)
    assert d["keep_defense"] is True
    assert d["reason"] == "below_baseline_bucket_under_power_limited_chi2"
    assert d["below_baseline_buckets"] == ["震荡"]
    assert d["min_bucket_wr"] == 59.2


def test_evaluate_regime_defense_significant_keeps():
    # 假设态：χ² 显著 → 保留防守
    rep = _phase_report_like(real=False)
    d = dpi.evaluate_regime_defense(rep)
    assert d["keep_defense"] is True
    assert d["reason"] == "regime_chi2_significant"


def test_evaluate_regime_defense_no_info_drops():
    # χ² 不显著 且 所有可信桶 ≥ 基线 → 无信息量，标记可简化（不改规则）
    rep = {
        "overall": {"winrate": 65.0, "n": 200},
        "by_regime": {
            "弱势": {"n": 100, "winrate": 66.0},
            "强势": {"n": 100, "winrate": 64.0},
        },
        "regime_p": 0.5,
    }
    d = dpi.evaluate_regime_defense(rep)
    assert d["keep_defense"] is False
    assert d["reason"] == "no_information_content"
    assert d["below_baseline_buckets"] == []


def test_write_report_roundtrip(tmp_path):
    import json as _json
    recs = _synthetic_records()
    rep = dpi.build_phase_report(recs)
    defense = dpi.evaluate_regime_defense(rep)
    full = {
        "generated_at": "2026-07-22T00:00:00",
        "baseline_winrate": dpi.BASELINE_WR,
        "alpha": dpi.ALPHA,
        **rep,
        "regime_defense": defense,
    }
    p = str(tmp_path / "phase.json")
    dpi.write_report(full, p)
    back = _json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    assert back["regime_defense"]["keep_defense"] in (True, False)
    assert back["regime_chi2"] == rep["regime_chi2"]
    assert back["baseline_winrate"] == dpi.BASELINE_WR
