import pytest


@pytest.fixture
def _router_with_all_providers(monkeypatch):
    """用假凭据建出真实的路由器（真实 routing_table + 各 provider 的 included/excluded_apis）。

    不能依赖 backend/.env：CI 和新 worktree 没有凭据，get_pro() 会抛「上游未配置」，
    被测函数按「未知=有源」返回 True，断言就和数据源能力无关了。
    """
    from app.core.config import get_settings
    from app.services import tushare_client as tc

    for key in ("PROMAX_API_KEY", "QUANTDATA_TOKEN", "TEAJOIN_TOKEN"):
        monkeypatch.setenv(f"ASHARE_TUSHARE_{key}", "test-fake-credential")
    # 运行时启停/优先级覆盖读 data_source_config 表；这里只测代码 + 路由表的能力声明。
    monkeypatch.setattr(tc, "_provider_enabled_override", lambda: {})
    monkeypatch.setattr(tc, "_provider_priority_override", lambda: {})
    get_settings.cache_clear()
    tc.clear_pro_cache()
    yield
    tc.clear_pro_cache()
    get_settings.cache_clear()


def test_anns_d_has_no_supported_tushare_provider(monkeypatch, _router_with_all_providers):
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
