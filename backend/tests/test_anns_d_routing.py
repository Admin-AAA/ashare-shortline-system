def test_anns_d_has_no_supported_tushare_provider(monkeypatch):
    from app.services.data_gap_remediation_service import _tushare_api_has_supported_provider
    from app.services.datasource import akshare_backup as ab

    # tushare has no anns_d upstream; only the akshare backup (when enabled) can serve it
    monkeypatch.setattr(ab, "backup_enabled", lambda: False)
    assert _tushare_api_has_supported_provider("anns_d") is False
    assert _tushare_api_has_supported_provider("block_trade") is True
    monkeypatch.setattr(ab, "backup_enabled", lambda: True)
    assert _tushare_api_has_supported_provider("anns_d") is True


def test_stock_news_goes_through_cninfo(monkeypatch):
    import app.services.cninfo_news as cn
    import app.services.market_auxiliary as ma

    calls = []

    def fake(code, *, start_date, end_date, max_pages=3, **_k):
        calls.append((code, start_date, end_date))
        return [{"source": "cninfo", "article_id": "1", "code": code, "announce_date": end_date, "title": "t"}]

    monkeypatch.setattr(cn, "fetch_announcements_for_code", fake)
    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: (_ for _ in ()).throw(AssertionError("anns_d used")))
    rows = ma._fetch_tushare_news("600000", None, trade_date="2026-10-16")
    assert rows and rows[0]["source"] == "cninfo" and calls == [("600000", "2026-10-09", "2026-10-16")]
