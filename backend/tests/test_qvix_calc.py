import math

import pytest

from app.services.datasource.qvix_calc import term_variance, vix_from_chain


def _bs(spot: float, k: float, t: float, r: float, sigma: float, call: bool) -> float:
    d1 = (math.log(spot / k) + (r + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    if call:
        return spot * n(d1) - k * math.exp(-r * t) * n(d2)
    return k * math.exp(-r * t) * n(-d2) - spot * n(-d1)


def _chain(expiry: str, days: int, sigma: float, spot: float = 3.0, r: float = 0.015) -> list[dict]:
    t = days / 365.0
    out = []
    for i in range(-40, 41):
        k = round(spot + i * 0.05, 3)
        for cp in ("C", "P"):
            out.append({"maturity_date": expiry, "exercise_price": k, "call_put": cp,
                        "price": _bs(spot, k, t, r, sigma, cp == "C")})
    return out


def test_term_variance_recovers_flat_volatility():
    chain = _chain("20261223", 26, 0.18)
    s2 = term_variance(chain, 26 / 365.0, 0.015)
    assert math.sqrt(s2) == pytest.approx(0.18, abs=0.003)


def test_vix_interpolates_two_terms_to_30_days():
    # 近月 26 天 σ=0.16，次月 61 天 σ=0.20：30 天方差按时间加权插值
    quotes = _chain("20261223", 26, 0.16) + _chain("20270127", 61, 0.20)
    vix = vix_from_chain(quotes, "20261127", 0.015)
    w1 = (61 - 30) / (61 - 26)
    expected = 100 * math.sqrt((26 * 0.16**2 * w1 + 61 * 0.20**2 * (1 - w1)) / 30)
    assert vix == pytest.approx(expected, abs=0.3)


def test_vix_needs_two_expiries():
    with pytest.raises(ValueError):
        vix_from_chain(_chain("20261223", 26, 0.16), "20261127", 0.015)
