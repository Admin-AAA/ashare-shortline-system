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
    # 无 snapshot → 非恐慌日
    assert det.get("panic_day") is False


def test_fetch_gate_blocked_detail_panic_day_from_snapshot():
    """market_snapshot limit_down>100 → panic_day，解释有效线 101 属设计内。"""
    fa = _fa_with_scores((73.2, 101.3), (69.7, 89.5))
    snap = json.dumps({
        "limit_down_count": 479,
        "limit_up_count": 68,
        "sample_count": 4955,
        "breadth": {"regime": "弱势"},
    })
    # 5 列：score_line, fa, result_summary, snapshot, trade_date
    db = _FakeDB((72.0, fa, None, snap, "2026-07-20"))
    det = self_check._fetch_gate_blocked_detail(db, "chip-single-peak-breakout")
    assert det is not None
    assert det["effective_line"] == 101.3
    assert det["panic_day"] is True
    assert det["limit_down_count"] == 479
    assert det["regime"] == "弱势"
    assert det["trade_date"] == "2026-07-20"


def test_fetch_gate_blocked_detail_non_panic_regime_only():
    """ld 正常时 panic_day=False，仍带 regime 标签。"""
    fa = _fa_with_scores((70.0, 80.0))
    snap = json.dumps({
        "limit_down_count": 12,
        "limit_up_count": 40,
        "breadth": {"regime": "震荡"},
    })
    db = _FakeDB((72.0, fa, None, snap, "2026-07-08"))
    det = self_check._fetch_gate_blocked_detail(db, "trend-pullback-continuation")
    assert det["panic_day"] is False
    assert det["limit_down_count"] == 12
    assert det["regime"] == "震荡"


def test_fetch_gate_blocked_detail_no_row_returns_none():
    db = _FakeDB(None)
    assert self_check._fetch_gate_blocked_detail(db, "ghost-skill") is None


def test_fetch_gate_blocked_detail_db_error_returns_none():
    class _BoomDB:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert self_check._fetch_gate_blocked_detail(_BoomDB(), "x") is None


# ---- T4：fetch_gate_blocked_details 批量查询（替代 N 次逐策略全扫） ----

class _SpyResult:
    def __init__(self, rows):
        self._rows = rows if isinstance(rows, list) else [rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _SpyDB:
    """捕获执行的 SQL，并回放预置行（支持两步查询：fetchall → fetchone）。

    两步查询模式（T4 优化后）：
    1. fetchall → (run_id, selected_skills_json) 小列扫描
    2. fetchone → (score_line, filter_audit, result_summary, snapshot, trade_date) 按主键
    第 1 次 execute 返回 fetchall（列表行），第 2+ 次返回 fetchone（单行 detail）。
    """

    def __init__(self, rows, spy):
        # rows[0] 是 step1 的 fetchall 列表；每个匹配 sid 的 step2 行
        # 用 _batch_row 构造的行同时含 step1 和 step2 的列
        self._rows = rows
        self._spy = spy
        self._call_count = 0

    def execute(self, stmt, params=None):
        self._spy.setdefault("sqls", []).append(str(stmt))
        self._spy.setdefault("params_list", []).append(params)
        self._call_count += 1
        if self._call_count == 1:
            # Step 1: fetchall 返回所有行（只读 run_id + selected_skills_json）
            return _SpyResult(self._rows)
        else:
            # Step 2: fetchone 返回单行 detail（score_line, fa, rs, snap, trade_date）
            # 从 params 中取 run_id，找到对应行，返回 detail 列
            rid = (params or {}).get("rid", "")
            for row in self._rows:
                if row[0] == rid:
                    # 返回 (score_line, filter_audit, result_summary, snapshot, trade_date)
                    return _SpyResult([(row[2], row[3], row[4], row[5], row[6])])
            return _SpyResult([])


def _batch_row(sid, fa, snap=None, trade_date="2026-07-20",
               started_at="2026-07-20 10:00:00"):
    """构造批量查询的一行：两步模式 step1=(run_id, selected_skills_json)，
    step2=(score_line, filter_audit_json, result_summary_json, market_snapshot_json, trade_date)。"""
    skills = json.dumps([{"skill_id": sid}])
    return ("run_x", skills, 72.0, fa, None, snap, trade_date, started_at)


def test_fetch_gate_blocked_details_single_range_scan_no_like():
    """批量版走单条 started_at 索引范围扫描，无逐策略 LIKE 全扫。"""
    fa = _fa_with_scores((73.16, 89.55), (69.79, 101.35))
    spy: dict = {}
    db = _SpyDB([_batch_row("chip-single-peak-breakout", fa)], spy)
    out = self_check.fetch_gate_blocked_details(db, ["chip-single-peak-breakout"])
    # step1 SQL 不应含 LIKE
    assert "LIKE" not in spy["sqls"][0], "批量版不应再发逐策略 LIKE 全扫"
    assert "ORDER BY r.started_at DESC" in spy["sqls"][0]
    assert "started_at >= DATE_SUB" in spy["sqls"][0]
    det = out["chip-single-peak-breakout"]
    assert det is not None
    assert det["max_candidate_score"] == 73.16
    assert det["effective_line"] == 101.35


def test_fetch_gate_blocked_details_matches_singular():
    """批量版与单条版输出逐字段一致（共享 _build_gate_detail）。"""
    fa = _fa_with_scores((73.2, 101.3), (69.7, 89.5))
    snap = json.dumps({
        "limit_down_count": 479, "limit_up_count": 68,
        "breadth": {"regime": "弱势"},
    })
    # 批量路径：8 列批行
    db_b = _SpyDB([_batch_row("chip-single-peak-breakout", fa, snap)], {})
    # 单条路径：自身 5 列查询（score_line, fa, rs, snap, trade_date）
    row5 = (72.0, fa, None, snap, "2026-07-20")
    db_s = _SpyDB([row5], {})
    batched = self_check.fetch_gate_blocked_details(db_b, ["chip-single-peak-breakout"])
    singular = self_check._fetch_gate_blocked_detail(db_s, "chip-single-peak-breakout")
    assert batched["chip-single-peak-breakout"] == singular


def test_fetch_gate_blocked_details_empty_input():
    assert self_check.fetch_gate_blocked_details(None, []) == {}


def test_fetch_gate_blocked_details_picks_most_recent():
    """同一 sid 多条 run（DESC 首条=最新），取最新命中（= LIMIT 1 语义）。"""
    fa_new = _fa_with_scores((80.0, 90.0))
    fa_old = _fa_with_scores((60.0, 70.0))
    # 模拟 SQL ORDER BY started_at DESC：最新在前
    rows = [
        _batch_row("s", fa_new, trade_date="2026-07-20", started_at="2026-07-20 10:00:00"),
        _batch_row("s", fa_old, trade_date="2026-07-10", started_at="2026-07-10 10:00:00"),
    ]
    db = _SpyDB(rows, {})
    det = self_check.fetch_gate_blocked_details(db, ["s"])["s"]
    assert det["max_candidate_score"] == 80.0  # 取最新，非旧



def test_parse_ignores_zero_zero_noise():
    """历史噪声「最终分 0.0 低于有效线 0.0」不得污染 max 候选/有效线。"""
    import json
    fa = json.dumps({
        "reason_counts": {
            "score_line": {
                "最终分 0.0 低于有效线 0.0": 34,
                "最终分 72.0 低于有效线 95.55": 1,
                "最终分 36.03 低于有效线 89.55": 5,
            }
        }
    })
    stats = self_check._parse_score_line_gate_stats(fa)
    assert stats["max_candidate_score"] == 72.0
    assert stats["max_effective_line"] == 95.55
