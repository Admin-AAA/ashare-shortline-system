"""Regression guard: self_check E 项 gate_blocked 门线拦截根因明细（2026-07-21 T3.6 / T26）。

背景: 3 个 sub-20 策略每日补跑 completed 但 0 产出。filter_audit 实测：
  reason_counts.score_line = {"最终分 73.16 低于有效线 89.55": 1, ...}  # key=文案
旧实现扫 .values()（计数）→ 恒「无候选分」，且用 score_line 列 72 当有效线误导。
T26 改为扫 key（+兼容 value 文案夹具），有效线取文案/meta 最高值。
"""
import json
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _fa_with_scores(*pairs, as_keys: bool = True):
    """构造 filter_audit_json。as_keys=True 模拟生产（key=文案）；False 模拟旧夹具（value=文案）。"""
    if as_keys:
        score_line = {f"最终分 {x} 低于有效线 {y}": 1 for x, y in pairs}
    else:
        score_line = {
            f"rule_{i}": f"最终分 {x} 低于有效线 {y}" for i, (x, y) in enumerate(pairs)
        }
    return json.dumps({"reason_counts": {"score_line": score_line}})


def test_parse_max_candidate_score_picks_highest():
    fa = _fa_with_scores((73.16, 89.55), (69.79, 101.35), (76.0, 89.55))
    assert self_check._parse_max_candidate_score(fa) == 76.0


def test_parse_max_candidate_score_production_keys():
    """生产格式：文案在 key、计数在 value。"""
    fa = _fa_with_scores((73.16, 89.55), (69.79, 101.35), as_keys=True)
    stats = self_check._parse_score_line_gate_stats(fa)
    assert stats["max_candidate_score"] == 73.16
    assert stats["max_effective_line"] == 101.35


def test_parse_max_candidate_score_legacy_values():
    """旧夹具：文案在 value。"""
    fa = _fa_with_scores((73.16, 89.55), (76.0, 90.0), as_keys=False)
    assert self_check._parse_max_candidate_score(fa) == 76.0


def test_parse_max_candidate_score_single():
    fa = _fa_with_scores((73.16, 89.55))
    assert self_check._parse_max_candidate_score(fa) == 73.16


def test_parse_max_candidate_score_none_input():
    assert self_check._parse_max_candidate_score(None) is None


def test_parse_max_candidate_score_invalid_json():
    assert self_check._parse_max_candidate_score("{not json") is None


def test_parse_max_candidate_score_no_score_line_key():
    fa = json.dumps({"reason_counts": {"prefilter_skill": {"x": "未命中"}}})
    assert self_check._parse_max_candidate_score(fa) is None


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return _FakeResult(self._row)


def test_fetch_gate_blocked_detail_prefers_audit_effective_line():
    """有效线取 audit 文案最高值，非 score_line 列 base。"""
    fa = _fa_with_scores((73.16, 89.55), (69.79, 101.35))
    # 列 base=72，audit 有效线最高 101.35，最高候选 73.16
    db = _FakeDB((72.0, fa, None))
    det = self_check._fetch_gate_blocked_detail(db, "chip-single-peak-breakout")
    assert det is not None
    assert det["max_candidate_score"] == 73.16
    assert det["effective_line"] == 101.35
    assert det["base_score_line"] == 72.0


def test_fetch_gate_blocked_detail_no_row_returns_none():
    db = _FakeDB(None)
    assert self_check._fetch_gate_blocked_detail(db, "ghost-skill") is None


def test_fetch_gate_blocked_detail_db_error_returns_none():
    class _BoomDB:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert self_check._fetch_gate_blocked_detail(_BoomDB(), "x") is None
