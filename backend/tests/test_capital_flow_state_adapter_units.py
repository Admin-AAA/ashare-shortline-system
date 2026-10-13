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
    # 主力 = 超大单 + 大单（与全部历史行同口径，L-165）
    # = (40999.10-46580.0 + 86142.64-98529.15)/1e4 = -1.7967 亿，绝不是 /1e8 的 -0.00018
    assert row["main_net_inflow_yi"] == pytest.approx(-1.7967, abs=1e-3)
    # 超大单 = (buy_elg - sell_elg)/1e4 = (40999.10-46580.0)/1e4 = -0.558 亿
    assert row["super_large_net_inflow_yi"] == pytest.approx(-0.558, abs=1e-3)


def test_capital_flow_does_not_write_volume_as_ratio():
    # 旧实现把 net_mf_vol（手）写进 main_net_inflow_ratio，规则「占比>8」几乎恒真。
    provider = TushareStateProvider(client=_FakeClient(), session=None)
    row = provider.capital_flow(_request(["000021"]))[0]
    assert row["main_net_inflow_ratio"] is None


def test_capital_flow_cumulative_inflows_use_local_history(tmp_path):
    # 旧实现写 0.0 占位等「每日同步」重算，而那个同步从未排进调度：09-09 起累计全是 0（L-165）。
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.tables import Base, CapitalFlow

    engine = create_engine(f"sqlite:///{tmp_path / 'cf.db'}")
    Base.metadata.create_all(engine, tables=[CapitalFlow.__table__])
    with Session(engine) as session:
        history = [("2026-08-05", 1.0), ("2026-08-06", 2.0), ("2026-08-07", 3.0), ("2026-08-10", 4.0),
                   ("2026-08-11", 5.0), ("2026-08-12", 6.0), ("2026-08-13", 7.0)]
        session.add_all(CapitalFlow(code="000021", trade_date=d, main_net_inflow_yi=v) for d, v in history)
        # 目标日当天及之后的行不能进窗口
        session.add(CapitalFlow(code="000021", trade_date="2026-08-17", main_net_inflow_yi=100.0))
        session.commit()
        provider = TushareStateProvider(client=_FakeClient(), session=session)
        row = provider.capital_flow(_request(["000021"]))[0]
    today = row["main_net_inflow_yi"]
    assert row["cumulative_inflow_3d_yi"] == pytest.approx(today + 7.0 + 6.0, abs=1e-4)
    assert row["cumulative_inflow_5d_yi"] == pytest.approx(today + 7.0 + 6.0 + 5.0 + 4.0, abs=1e-4)
    # 只有 7 条历史：10 日窗口取全部可用行，不补零也不越过目标日
    assert row["cumulative_inflow_10d_yi"] == pytest.approx(today + 28.0, abs=1e-4)


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
