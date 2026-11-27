from datetime import datetime

from app.models.market import ShareFloat
from app.services.datasource import eastmoney as em


def _row(code, float_date, holder, created, ratio=1.0):
    return ShareFloat(code=code, ann_date=None, float_date=float_date, float_share=1e6, float_ratio=ratio,
                      holder_name=holder, created_at=created)


def test_upcoming_floats_from_calendar_reads_only_eastmoney_rows_pit(db):
    db.add_all([
        _row("600001", "20261205", em.FLOAT_PREFIX + "首发原股东限售股份", datetime(2026, 11, 20, 21, 0), 6.5),
        _row("600001", "20261210", em.FLOAT_PREFIX + "股权激励限售股份", datetime(2026, 12, 2, 21, 0), 0.3),  # as_of 之后采集
        _row("600001", "20261207", "xiaoshi:定向增发", datetime(2026, 11, 20, 21, 0), 2.0),  # 非东财日历行
        _row("600002", "20270115", em.FLOAT_PREFIX + "首发原股东限售股份", datetime(2026, 11, 20, 21, 0), 9.0),  # 超出 30 天
        _row("600003", "20261203", em.FLOAT_PREFIX + "首发原股东限售股份", datetime(2026, 11, 20, 21, 0), 4.0),  # 未请求的代码
    ])
    db.flush()
    out = em.upcoming_floats_from_calendar(db, ["600001", "600002"], "2026-11-30", as_of="2026-11-30")
    assert set(out) == {"600001"}
    assert [r["float_date"] for r in out["600001"]] == ["2026-12-05"]
    assert out["600001"][0]["float_ratio"] == 6.5
    assert em.upcoming_floats_from_calendar(db, [], "2026-11-30") == {}


def test_sync_eastmoney_alt_refreshes_unlock_calendar_without_flag(db, monkeypatch):
    monkeypatch.setattr(em, "_call", lambda dataset, fn, *a, **k: [
        {"code": "600009", "ann_date": None, "float_date": "20261215", "float_share": 1e6, "float_ratio": 3.3,
         "holder_name": em.FLOAT_PREFIX + "首发原股东限售股份"},
        {"code": "600009", "ann_date": None, "float_date": "20261215", "float_share": 1e6, "float_ratio": 3.3,
         "holder_name": em.FLOAT_PREFIX + "首发原股东限售股份"},
    ] if dataset == "float.unlock" else [])
    monkeypatch.setattr("app.services.datasource.gateway.is_cutover", lambda name: False)
    first = em.sync_eastmoney_alt(db, "20261130")
    assert first["float_rows"] == 2 and first["float_inserted"] == 1
    assert first["margin_skipped"] == "flag eastmoney.alt off" and not first["errors"]
    again = em.sync_eastmoney_alt(db, "20261130")
    assert again["float_inserted"] == 0
    assert em.upcoming_floats_from_calendar(db, ["600009"], "2026-11-30", as_of="2026-11-30")["600009"][0]["float_date"] == "2026-12-15"
