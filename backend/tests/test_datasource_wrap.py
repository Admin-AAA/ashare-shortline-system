import pytest

from app.services.datasource import gateway as gw
from app.services.datasource import wrap


class EastmoneyBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    wrap._breakers.clear()
    monkeypatch.setattr(wrap, "_limiter", lambda n, r: type("L", (), {"acquire": lambda self: None})())
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())


def test_passthrough_when_not_cutover(monkeypatch):
    monkeypatch.setattr(gw, "is_cutover", lambda d: False)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: {("eastmoney", "news")})
    assert wrap.gw_call("ann.list", "eastmoney", lambda x, k=0: [x, k], 1, k=2) == [1, 2]


def test_cutover_identical_result_and_disabled(monkeypatch):
    monkeypatch.setattr(gw, "is_cutover", lambda d: True)
    out = [{"article_id": "a"}]
    assert wrap.gw_call("ann.list", "eastmoney", lambda: out) is out
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: {("eastmoney", "news")})
    with pytest.raises(gw.DataSourceError) as e:
        wrap.gw_call("ann.list", "eastmoney", lambda: out)
    assert e.value.kind is gw.ErrorKind.DISABLED


def test_original_exception_passes_and_block_opens_breaker(monkeypatch):
    monkeypatch.setattr(gw, "is_cutover", lambda d: True)

    def boom():
        raise EastmoneyBlockedError("567")

    with pytest.raises(EastmoneyBlockedError):
        wrap.gw_call("ann.list", "eastmoney", boom)
    with pytest.raises(gw.DataSourceError) as e:
        wrap.gw_call("ann.list", "eastmoney", lambda: [1])
    assert e.value.kind is gw.ErrorKind.CIRCUIT_OPEN
    assert wrap.gw_call("ann.list", "cninfo", lambda: [1]) == [1]
