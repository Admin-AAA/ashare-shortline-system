"""§114 mid-block-trade-premium 实盘候选：汇总 / 溢价 / 占流通市值 / 排序 / 未发布 -> None（不连库、不联网）。"""
import datetime as dt

from app.services import midterm_block_trade as mb
from app.services import midterm_service as ms


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, dates):
        self.dates = dates

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "DISTINCT trade_date" in sql:
            return _Res([(d,) for d in self.dates])
        if "FROM stocks" in sql:
            return _Res([(c, f"N{c}") for c in (params or {}).get("codes", [])])
        raise AssertionError(sql)


def _dates(as_of="2026-10-16", n=251):
    d, out = dt.date.fromisoformat(as_of), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


def _bt(ts, price, vol, amount):
    return {"ts_code": ts, "trade_date": "20261016", "price": price, "vol": vol, "amount": amount, "buyer": "b", "seller": "s"}


def test_aggregate_vwap_and_filters():
    rows = [_bt("600001.SH", 10.0, 100, 1000), _bt("600001.SH", 11.0, 100, 1100),
            _bt("830001.BJ", 5.0, 10, 50), _bt("000002.SZ", 5.0, 0, 0), _bt("511990.SH", 100.0, 1, 100)]
    agg = mb.aggregate(rows)
    assert set(agg) == {"600001", "511990"}  # 北交所、零量额剔除（基金代码在后面 join 行情时自然丢掉）
    assert agg["600001"] == {"amt": 2100.0, "vol": 200.0, "n": 2}


def test_events_premium_and_ratio():
    agg = {"600001": {"amt": 2100.0, "vol": 200.0, "n": 2},   # vwp 10.5
           "600002": {"amt": 2100.0, "vol": 200.0, "n": 2},
           "600003": {"amt": 100.0, "vol": 10.0, "n": 1}}     # vwp 10.0
    px = {"600001": (10.5, 400000.0),   # 平价、2100/400000=0.525% -> 入选
          "600002": (10.6, 400000.0),   # 折价 -> 不选
          "600003": (9.0, 400000.0)}    # 溢价但 100/400000=0.025% -> 不选
    ev = mb.events(agg, px)
    assert sorted(ev) == ["600001"]
    assert ev["600001"]["premium"] == 0.0 and abs(ev["600001"]["ratio"] - 0.00525) < 1e-9


def test_candidates_sorted_by_ratio(monkeypatch):
    rows = [_bt("600001.SH", 10.0, 100, 1000), _bt("300001.SZ", 20.0, 100, 2000), _bt("000001.SZ", 8.0, 100, 800)]
    monkeypatch.setattr(mb, "_close_and_fmv", lambda db, as_of, codes: {
        "600001": (9.9, 100000.0), "300001": (19.0, 100000.0), "000001": (8.5, 100000.0)})
    monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: {"total_mv": 1.0, "amt20": 1e8} for c in codes})
    res = mb.block_trade_candidates(_FakeDB(_dates()), "2026-10-16", rows=rows)
    assert [r["code"] for r in res] == ["300001", "600001"]  # 000001 折价成交不选；按占比降序
    assert res[0]["ratio"] == 0.02 and res[0]["score"] == 0.02


def test_unpublished_or_failed_returns_none(monkeypatch):
    monkeypatch.setattr(mb, "fetch_block_trades", lambda d: [])
    assert mb.block_trade_candidates(_FakeDB(_dates()), "2026-10-16") is None
    monkeypatch.setattr(mb, "fetch_block_trades", lambda d: None)
    assert mb.block_trade_candidates(_FakeDB(_dates()), "2026-10-16") is None


def test_strategy_registered_and_no_data_not_written(monkeypatch, tmp_path):
    meta = ms.STRATEGIES["mid-block-trade-premium"]
    assert meta["horizon"] == "mid" and meta["kind"] == "block_trade_premium" and meta["hold_days"] == 20
    assert (meta["k_slots"], meta["max_new"], meta["schedule"]) == (20, 5, "daily")
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, as_of: set())
    monkeypatch.setattr(mb, "block_trade_candidates", lambda db, as_of: None)
    assert ms.generate_block_trade_picks(None, "2026-10-16") == {"mid-block-trade-premium": "no_data"}
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(mb, "block_trade_candidates", lambda db, as_of: [{"code": "600001"}, {"code": "600002"}])
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, as_of: {"600002"})
    assert ms.generate_block_trade_picks(None, "2026-10-16") == {"mid-block-trade-premium": 1}
    assert (tmp_path / "2026-10-16_mid-block-trade-premium.json").exists()
