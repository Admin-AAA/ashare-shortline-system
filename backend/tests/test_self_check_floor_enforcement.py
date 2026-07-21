"""Regression guard: self_check D 项契约层地板生产态不变量（T5/Contract 一致性护栏）。

背景: FORMAL_SCORE_LINE_FLOOR=72.0 在生产 DB 自 2026-07-17 起已落地生效——此前（≤07-16）
的低线 run 是修复前遗留污染（matrix 把 score_line=55 写入 formal），非当前系统行为。
本测试锁定两件事：

1. `_floor_violation_count` 纯函数：只把「first_clean_td 起仍出现 sub-72 formal」计为 violation；
   历史遗留（< first_clean_td 的低线票）不算 violation（那是修复前污染，已不被新 run 产出）。
2. `_print_floor_enforcement` 接 DB 的行为：0 violation → ✅ 安静通过；
   出现 violation → 🚨 loud-fail（保护 20+ 轮 D 工作成果不被静默回归吞掉）。

与 _low_score_line_pollution（污染诊断，只暴露幅度不 fail-loud）正交。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

FIRST_CLEAN_TD = self_check.FORMAL_FLOOR_FIRST_CLEAN_TD  # "2026-07-17"
FLOOR = self_check.FORMAL_SCORE_LINE_FLOOR               # 72.0

# rows_with_sl schema: (code, trade_date, label, ret, score_line)


def _row(code, td, sl, label="success", ret=1.0):
    return (code, td, label, ret, sl)


# --- _floor_violation_count 纯函数 ---

def test_empty_rows_zero_violation():
    assert self_check._floor_violation_count([], FIRST_CLEAN_TD) == 0


def test_legacy_low_score_line_before_first_clean_not_counted():
    # 修复前遗留污染（≤07-16 的低线票）不计入 violation
    rows = [
        _row("600000", "2026-07-16", 55.0),
        _row("600001", "2026-07-15", 60.0),
        _row("600002", "2026-07-10", 66.0),
    ]
    assert self_check._floor_violation_count(rows, FIRST_CLEAN_TD) == 0


def test_low_score_line_on_or_after_first_clean_counted():
    rows = [
        _row("600000", "2026-07-16", 55.0),   # 之前 → 不计
        _row("600001", "2026-07-17", 60.0),   # 边界当日 → 计
        _row("600002", "2026-07-20", 66.0),   # 之后 → 计
    ]
    assert self_check._floor_violation_count(rows, FIRST_CLEAN_TD) == 2


def test_score_line_exactly_floor_not_violation():
    # score_line == 72.0 不算 violation（严格 < floor）
    rows = [
        _row("600000", "2026-07-17", 72.0),
        _row("600001", "2026-07-18", 72.0001),
        _row("600002", "2026-07-19", 71.99),  # 唯一 violation
    ]
    assert self_check._floor_violation_count(rows, FIRST_CLEAN_TD) == 1


def test_custom_floor_param():
    rows = [_row("600000", "2026-07-20", 80.0)]
    # 默认 floor 72：80 >= 72 → 0 violation
    assert self_check._floor_violation_count(rows, FIRST_CLEAN_TD) == 0
    # 自定义 floor 85：80 < 85 → 1 violation
    assert self_check._floor_violation_count(rows, FIRST_CLEAN_TD, floor=85.0) == 1


def test_none_score_line_treated_as_zero():
    # score_line 为 None/0 → 视作低线，first_clean_td 之后计 violation
    rows = [
        _row("600000", "2026-07-20", None),
        _row("600001", "2026-07-20", 0),
    ]
    assert self_check._floor_violation_count(rows, FIRST_CLEAN_TD) == 2


# --- _print_floor_enforcement 接 DB（fake db，不碰真实库） ---

class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return self._rows


def test_enforcement_print_ok_when_zero_violation(capsys):
    # 全部为修复后生产态（≥72 或历史遗留 ≤07-16）→ ✅
    rows = [
        _row("600000", "2026-07-16", 55.0),   # 历史遗留
        _row("600001", "2026-07-17", 72.0),   # 边界达标
        _row("600002", "2026-07-20", 85.0),   # 达标
    ]
    self_check._print_floor_enforcement(_FakeDb(rows))
    out = capsys.readouterr().out
    assert "✅" in out
    assert "契约层地板生产态有效" in out
    assert "🚨" not in out


def test_enforcement_print_alert_when_violation(capsys):
    # first_clean_td 之后出现 sub-72 → 🚨 loud-fail
    rows = [
        _row("600000", "2026-07-16", 55.0),   # 历史遗留，不计
        _row("600001", "2026-07-20", 60.0),   # 生产态破坏 → 计
    ]
    self_check._print_floor_enforcement(_FakeDb(rows))
    out = capsys.readouterr().out
    assert "🚨" in out
    assert "契约层地板生产态被破坏" in out
