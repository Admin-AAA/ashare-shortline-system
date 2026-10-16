from app.core.config import Settings


def test_secret_fields_hidden_from_repr():
    s = Settings(tushare_token="SECRET_T", xiaoshi_api_key="SECRET_X", access_password="SECRET_P")
    text = repr(s) + str(s)
    for v in ("SECRET_T", "SECRET_X", "SECRET_P"):
        assert v not in text
    assert s.tushare_token == "SECRET_T"
