"""TushareStateProvider.capital_flow 单位契约测试（L-059 回归）。

锁定 bug：state_adapters.capital_flow 曾用 /1e8 转万元→亿，导致
main_net_inflow_yi 小 4 个数量级（-0.0002 而非 -1.80），且把
cumulative_inflow_3d/5d/10d 硬编码为 0.0，覆盖每日同步重算的正确累计值。
正确契约与 backfill_moneyflow_concept.moneyflow_net_yi 一致：/1e4。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from app.services.auxiliary.contracts import AuxiliaryDatasetRequest, AuxiliaryInstrument
from app.services.auxiliary.state_adapters import TushareStateProvider


def _request(codes: list[str]) -> AuxiliaryDatasetRequest:
    return AuxiliaryDatasetRequest(
        dataset_key="capital_flow",
        instruments=tuple(AuxiliaryInstrument(code=c) for c in codes),
        trade_date=date(2026, 8, 14),
        refresh_enabled=True,
        requested_at=datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc),
    )


def _frame() -> pd.DataFrame:
    # 000021 真实接口量级：net_mf_amount=-16980.15 万元 ≈ -1.698 亿
    return pd.DataFrame(
        [
            {
                "ts_code": "000021.SZ",
                "trade_date": "20260814",
                "net_mf_amount": -16980.15,
                "buy_elg_amount": 40999.10,
                "sell_elg_amount": 46580.0,
                "buy_lg_amount": 86142.64,
                "sell_lg_amount": 98529.15,
                "buy_md_amount": 1000.0,
                "sell_md_amount": 2000.0,
                "buy_sm_amount": 500.0,
                "sell_sm_amount": 800.0,
                "net_mf_vol": 123.0,
            }
        ]
    )


class _FakeClient:
    def moneyflow(self, trade_date: str):
        assert trade_date == "20260814"
        return _frame()


def test_capital_flow_uses_wan_to_yi_1e4_not_1e8():
    provider = TushareStateProvider(client=_FakeClient(), session=None)
    rows = provider.capital_flow(_request(["000021"]))
    assert len(rows) == 1
    row = rows[0]
    # 主力净流入 = net_mf_amount(万元)/1e4 = -1.698 亿，绝不是 /1e8 的 -0.00017
    assert row["main_net_inflow_yi"] == pytest.approx(-1.698, abs=1e-3)
    # 超大单 = (buy_elg - sell_elg)/1e4 = (40999.10-46580.0)/1e4 = -0.558 亿
    assert row["super_large_net_inflow_yi"] == pytest.approx(-0.558, abs=1e-3)


def test_capital_flow_super_large_large_medium_small_units():
    provider = TushareStateProvider(client=_FakeClient(), session=None)
    rows = provider.capital_flow(_request(["000021"]))
    row = rows[0]
    # 全部 5 个分档都用 /1e4（万元→亿），不是 /1e8
    # large = (86142.64-98529.15)/1e4 = -1.2386 亿
    assert row["large_net_inflow_yi"] == pytest.approx(-1.2386, abs=1e-3)
    # medium = (1000-2000)/1e4 = -0.1 亿
    assert row["medium_net_inflow_yi"] == pytest.approx(-0.1, abs=1e-3)
    # small = (500-800)/1e4 = -0.03 亿
    assert row["small_net_inflow_yi"] == pytest.approx(-0.03, abs=1e-3)
    # 量级断言：任何分档都不应落在 /1e8 的 ~1e-4 量级
    for f in ("main_net_inflow_yi", "super_large_net_inflow_yi", "large_net_inflow_yi"):
        assert abs(row[f]) > 0.01, f"{f} 落入 /1e8 错误量级（~1e-4）"
