"""等权基准 EWALL.A：点位连续、样本不足停在前一天、持有期基准收益窗口与成本口径（SQLite，不连 MySQL）。"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.market import IndexDaily
from app.services import market_benchmark as mb


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    IndexDaily.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE daily_kbars (code TEXT, trade_date TEXT, close REAL, pct_chg REAL)"))
    return Session(engine)


def _fill_day(db: Session, d: str, pcts: list[float]) -> None:
    db.execute(text("INSERT INTO daily_kbars VALUES (:c, :d, 10, :p)"),
               [{"c": f"{i:06d}", "d": d, "p": p} for i, p in enumerate(pcts)])
    db.commit()


def test_upsert_chains_level_and_stops_before_insufficient_day(monkeypatch):
    monkeypatch.setattr(mb, "MIN_SAMPLE", 3)
    monkeypatch.setattr(mb, "EW_START_DATE", "2026-01-05")
    db = _session()
    _fill_day(db, "2026-01-05", [1.0, 3.0, 2.0])      # 均值 2.0
    _fill_day(db, "2026-01-06", [-1.0, -1.0, -1.0])   # 均值 -1.0
    _fill_day(db, "2026-01-07", [5.0])                # 样本不足 → 停
    _fill_day(db, "2026-01-08", [1.0, 1.0, 1.0])
    out = mb.upsert_equal_weight(db, "2026-01-08")
    assert out["written"] == ["2026-01-05", "2026-01-06"] and out["skipped_insufficient"] == ["2026-01-07"]
    dates, pct = mb.equal_weight_curve(db)
    assert dates == ["2026-01-05", "2026-01-06"] and pct["2026-01-06"] == -1.0
    rows = {r.trade_date: r for r in db.query(IndexDaily).all()}
    assert abs(rows["2026-01-05"].close - 1020.0) < 1e-6 and abs(rows["2026-01-06"].close - 1009.8) < 1e-6
    # 01-07 补齐后再跑：从上次点位续乘，不重算已有行
    _fill_day(db, "2026-01-07", [5.0, 5.0])
    out = mb.upsert_equal_weight(db, "2026-01-08")
    assert out["written"] == ["2026-01-07", "2026-01-08"]
    rows = {r.trade_date: r for r in db.query(IndexDaily).all()}
    assert abs(rows["2026-01-08"].close - 1009.8 * 1.05 * 1.01) < 1e-6
    mb.invalidate_curve_cache()
    assert mb.equal_weight_curve(db)[0] == ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


def test_holding_benchmark_return_window_and_cost():
    dates = [f"2026-01-{d:02d}" for d in range(5, 13)]  # 8 个交易日
    pct = {d: 1.0 for d in dates}
    # 信号日 T=01-05，hold=3：用 T+2..T+4（01-07/08/09）三根复利，扣 0.3
    r = mb.holding_benchmark_return(dates, pct, "2026-01-05", 3)
    assert abs(r - ((1.01 ** 3 - 1) * 100 - 0.3)) < 0.011
    assert mb.holding_benchmark_return(dates, pct, "2026-01-05", 3, cost_pct=0) == round((1.01 ** 3 - 1) * 100, 2)
    # 持有期未走完 / 信号日不在曲线里 → None
    assert mb.holding_benchmark_return(dates, pct, "2026-01-09", 3) is None
    assert mb.holding_benchmark_return(dates, pct, "2026-01-04", 3) is None
