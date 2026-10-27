"""中长线批量结算与缓存：结果必须与逐只单查完全一致；数据版本一变，未平仓缓存立即作废。"""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import text

from app.services import midterm_service as ms


@pytest.fixture(autouse=True)
def _reset_caches():
    ms._REALIZED_LIVE.clear()
    ms._LIVE_STATE.update(version=None, ts=0.0)
    ms._FILE_CACHE.clear()
    yield
    ms._REALIZED_LIVE.clear()
    ms._LIVE_STATE.update(version=None, ts=0.0)
    ms._FILE_CACHE.clear()


def test_pick_file_cache_rereads_rewritten_file(tmp_path):
    f = tmp_path / "2026-10-01_mid-x.json"
    f.write_text(json.dumps({"picks": [{"code": "A"}]}))
    assert ms._read_pick_file(f)[1] == 1
    f.write_text(json.dumps({"picks": [{"code": "A"}, {"code": "B"}]}))
    os.utime(f, ns=(1, 1))  # 强制 mtime 不同
    assert ms._read_pick_file(f)[1] == 2


def test_live_cache_dropped_when_version_changes(monkeypatch):
    versions = iter(["v1", "v2"])
    monkeypatch.setattr("app.services.data_version.data_version", lambda db, t: next(versions))
    assert ms._refresh_live_version(object()) == "v1"
    ms._remember_realized(("A", "2026-10-01", 20), {"status": "holding", "ret": 1.0}, "v1")
    assert ("A", "2026-10-01", 20) in ms._REALIZED_LIVE
    assert ms._refresh_live_version(object()) == "v2"
    assert not ms._REALIZED_LIVE
    # 旧版本算出的结果不能挂到新版本下
    ms._remember_realized(("A", "2026-10-01", 20), {"status": "holding", "ret": 1.0}, "v1")
    assert not ms._REALIZED_LIVE


@pytest.mark.integration
@pytest.mark.requires_market_data
def test_batch_settlement_matches_single_query(db):
    rows = db.execute(text(
        "SELECT code, trade_date FROM daily_kbars WHERE trade_date >= "
        "(SELECT MAX(trade_date) FROM daily_kbars) - INTERVAL 120 DAY ORDER BY code, trade_date LIMIT 4000"
    )).all()
    if not rows:
        pytest.skip("测试库没有日K")
    samples = sorted({(r.code, str(r.trade_date)) for r in rows[::97]})[:30]
    sid = next(k for k, v in ms.STRATEGIES.items() if v.get("horizon") == "long")
    hold = ms._hold(sid)
    expected = {(c, d): ms._realize_query(db, c, d, hold) for c, d in samples}
    ms._REALIZED_FINAL.clear()
    ms.prefetch_realized(db, [(sid, d, [c]) for c, d in samples])
    for (c, d), want in expected.items():
        assert ms._realize(db, c, d, hold) == want
