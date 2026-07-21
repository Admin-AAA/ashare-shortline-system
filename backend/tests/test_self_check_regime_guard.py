"""Regression guard: self_check._check_market_regime_calibration 运行时护栏（第185轮闭合）。

market_regime_score 必须保持逆势立场（强牛返分 < 弱市返分）。若被"修正校准"翻转，
会放行更差票、D 项恶化（过拟合）。本护栏纯函数比对函数输出梯度，不碰 DB。

锁定：
1. 当前校准正常 → 返回 None（self_check 运行中无警示）。
2. 若 market_regime_score 被翻转（强牛返高分、弱市返低分）→ 返回非空警示字符串。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_calibration_ok_returns_none():
    """当前真实 market_regime_score 保持逆势 → 护栏静默通过（返回 None）。"""
    result = self_check._check_market_regime_calibration()
    assert result is None


def test_calibration_flipped_returns_warning(monkeypatch):
    """若有人翻转校准（强牛返高分、弱市返低分）→ 护栏必须返回非空警示。

    这是核心安全网：翻转会在生产中长期放行更差票损害 D 项，护栏须在 self_check
    运行时即可发现，而非等到 D 项退化后才追查。
    """

    def _flipped(snapshot):
        up_ratio = float(snapshot.get("up_count") or 0) / max(1, int(snapshot.get("sample_count") or 1))
        # 翻转：强牛返高分(宽松)、弱市返低分(严格)
        if up_ratio >= 0.65:
            return 82.0
        if up_ratio < 0.35:
            return 32.0
        return 55.0

    monkeypatch.setattr(self_check, "market_regime_score", _flipped)
    result = self_check._check_market_regime_calibration()
    assert result is not None
    assert "翻转" in result
    assert "禁止翻转" in result
