from app.core.config import Settings


def test_providers_view_mirrors_flat_fields():
    s = Settings(tushare_promax_api_key="K", tushare_official_token="T")
    p = s.providers
    assert set(p) == {"fastapic", "ai_tool", "promax", "xiaodefa", "official", "quantdata"}
    assert p["promax"].credential == "K" and p["promax"].rate_limit_per_minute == s.tushare_promax_rate_limit_per_minute
    assert p["promax"].max_concurrency == s.tushare_promax_max_concurrency
    assert p["official"].credential == "T" and p["official"].url == s.tushare_official_url
    assert p["ai_tool"].api_rate_limits["stk_mins"] == s.tushare_ai_tool_stk_mins_rate_limit_per_minute
    assert "K" not in repr(p["promax"])
