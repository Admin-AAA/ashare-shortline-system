"""Regression guard: self_check E 项 gate_blocked 门线拦截根因明细（2026-07-21 T3.6）。

背景: 3 个 sub-20 策略(chip-single-peak-breakout / small-cap-thematic-momentum /
trend-pullback-continuation) 每日补跑 completed 但 0 产出。filter_audit_json 证实根因是
「regime 有效线偏高」(候选最终分 69~76 < 有效线 89~101)，与 A 项 3 天保护性 0 推荐同源，
非配置 bug。本测试锁住解析逻辑，使未来轮次一眼区分「regime 有效线偏高(等国转强会喂)」
vs「真无候选」，避免误把门线放宽去喂样本而污染 D 项胜率。
"""
import json
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def _fa_with_scores(*pairs):
    """构造 filter_audit_json，score_line reason_counts 含 '最终分 X 低于有效线 Y'。"""
    score_line = {f"rule_{i}": f"最终分 {x} 低于有效线 {y}" for i, (x, y) in enumerate(pairs)}
    return json.dumps({"reason_counts": {"score_line": score_line}})


def test_parse_max_candidate_score_picks_highest():
    fa = _fa_with_scores((73.16, 89.55), (69.79, 101.35), (76.0, 89.55))
    assert self_check._parse_max_candidate_score(fa) == 76.0


def test_parse_max_candidate_score_single():
    fa = _fa_with_scores((73.16, 89.55))
    assert self_check._parse_max_candidate_score(fa) == 73.16


def test_parse_max_candidate_score_none_input():
    assert self_check._parse_max_candidate_score(None) is None


def test_parse_max_candidate_score_invalid_json():
    assert self_check._parse_max_candidate_score("{not json") is None


def test_parse_max_candidate_score_no_score_line_key():
    # reason_counts 无 score_line 段 → 无匹配 → None
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


def test_fetch_gate_blocked_detail_returns_line_and_max():
    fa = _fa_with_scores((73.16, 89.55), (69.79, 101.35))
    db = _FakeDB((89.55, fa))  # (score_line 列, filter_audit_json)
    det = self_check._fetch_gate_blocked_detail(db, "chip-single-peak-breakout")
    assert det == {"effective_line": 89.55, "max_candidate_score": 73.16}


def test_fetch_gate_blocked_detail_no_row_returns_none():
    db = _FakeDB(None)
    assert self_check._fetch_gate_blocked_detail(db, "ghost-skill") is None


def test_fetch_gate_blocked_detail_db_error_returns_none():
    class _BoomDB:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert self_check._fetch_gate_blocked_detail(_BoomDB(), "x") is None
