from types import SimpleNamespace

import pytest

from scripts.si_path_a_signal_backtest import PathABacktestRunner


def _rows(*, up: int, down: int, limit_up: int = 0):
    rows = []
    for idx in range(up):
        pct_chg = 10.0 if idx < limit_up else 1.0
        rows.append(SimpleNamespace(code=f"UP{idx}", pct_chg=pct_chg))
    for idx in range(down):
        rows.append(SimpleNamespace(code=f"DOWN{idx}", pct_chg=-1.0))
    return rows


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (_rows(up=70, down=30, limit_up=3), "强势"),
        (_rows(up=60, down=40), "结构性强势"),
        (_rows(up=50, down=50), "震荡"),
        (_rows(up=41, down=59), "弱势"),
    ],
)
def test_path_a_uses_production_breadth_regime_thresholds(rows, expected):
    runner = object.__new__(PathABacktestRunner)
    runner._stock_meta = {}

    context = runner._market_context_for_date("2026-01-01", {"2026-01-01": rows})

    assert context["regime"] == expected
