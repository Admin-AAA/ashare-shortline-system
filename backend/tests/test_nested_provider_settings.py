from app.core.config import Settings


def test_providers_view_mirrors_flat_fields():
    s = Settings(tushare_promax_api_key="K")
    p = s.providers
    assert set(p) == {"promax", "quantdata", "teajoin"}
    assert p["promax"].credential == "K" and p["promax"].rate_limit_per_minute == s.tushare_promax_rate_limit_per_minute
    assert p["promax"].max_concurrency == s.tushare_promax_max_concurrency
    assert "official" not in p
    assert "K" not in repr(p["promax"])
