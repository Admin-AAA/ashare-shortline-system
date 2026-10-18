from app.services.datasource import gateway as gw
from app.services.datasource import web_backups as wb

QT = 'v_sh600000="1~浦发银行~600000~9.73~9.64~9.60~922942~1~2' + "~0" * 21 + '~20261016161456~0.09~0.93~9.79~9.53~x~922942~89616~0.28";'


def test_parse_qt():
    r = wb.parse_qt(QT)["600000"]
    assert (r["price"], r["pre_close"], r["open"], r["high"], r["low"], r["volume"], r["amount"], r["trade_date"], r["quote_time"]) == (
        9.73, 9.64, 9.6, 9.79, 9.53, 92294200.0, 896160000.0, "2026-10-16", "16:14:56")


def test_parse_minute_rows():
    s = wb.parse_sina_kline("000001", [{"day": "2026-10-16 15:00:00", "open": "11.53", "high": "11.55", "low": "11.50", "close": "11.50", "volume": "3138241", "amount": "36140245.9"}])
    t = wb.parse_tencent_mkline("000001", [["202610161500", "11.54", "11.50", "11.54", "11.50", "31859.00", {}, "1.64"]])
    assert s[0]["trade_time"] == t[0]["trade_time"] == "2026-10-16 15:00:00"
    assert (t[0]["open"], t[0]["close"], t[0]["volume"], t[0]["amount"]) == (11.54, 11.5, 3185900.0, 0.0)
    assert s[0]["amount"] == 36140245.9


def test_flags_default_off(monkeypatch):
    monkeypatch.setattr(gw, "_cutover_set", lambda: frozenset(), raising=False)
    monkeypatch.setattr(wb, "is_cutover", lambda d: False)
    q = {"code": "000001", "freq": "5min", "keys": ["000001"]}
    assert not wb.TencentQuoteProvider().supports("quote.realtime", q)
    assert not wb.SinaKlineProvider().supports("bar.minute", q)
    assert not wb.TencentMinuteProvider().supports("bar.minute", q)
    monkeypatch.setattr(wb, "is_cutover", lambda d: True)
    assert wb.SinaKlineProvider().supports("bar.minute", q) and not wb.SinaKlineProvider().supports("bar.minute", {**q, "freq": "1min"})
