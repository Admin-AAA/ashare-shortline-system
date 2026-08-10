"""Point-in-time metadata override on the read-only snapshot builder."""
from __future__ import annotations

from app.services.backtest.candidate_snapshot_builder import (
    ProductionRuleReplayCandidateBuilder,
)
from app.services.backtest.stock_metadata import (
    StockMetadataResolution,
    POINT_IN_TIME,
)


class _FakeProvider:
    def __init__(self, metadata_by_code):
        self._metadata_by_code = metadata_by_code

    def resolve(self, codes, signal_date):
        return StockMetadataResolution(
            signal_date=signal_date,
            temporality=POINT_IN_TIME,
            source_version="fake/v1",
            metadata_by_code=self._metadata_by_code,
            missing_codes=(),
            fingerprint="f" * 64,
        )


def _builder(provider):
    # db is unused by _apply_point_in_time_metadata
    builder = ProductionRuleReplayCandidateBuilder.__new__(
        ProductionRuleReplayCandidateBuilder
    )
    builder._stock_metadata_provider = provider
    return builder


def test_point_in_time_metadata_overrides_industry_is_st_name() -> None:
    provider = _FakeProvider(
        {
            "000004": {"industry": "软件服务", "is_st": True, "name": "ST国华"},
        }
    )
    stocks = [{"code": "000004", "industry": "银行", "is_st": False, "name": "国华退"}]
    out = _builder(provider)._apply_point_in_time_metadata(stocks, "2022-06-01")
    assert out[0]["industry"] == "软件服务"
    assert out[0]["is_st"] is True
    assert out[0]["name"] == "ST国华"


def test_point_in_time_metadata_missing_code_keeps_current_value() -> None:
    provider = _FakeProvider({})
    stocks = [{"code": "000001", "industry": "银行", "is_st": False, "name": "平安银行"}]
    out = _builder(provider)._apply_point_in_time_metadata(stocks, "2025-01-15")
    assert out[0]["industry"] == "银行"
    assert out[0]["is_st"] is False
    assert out[0]["name"] == "平安银行"


def test_no_provider_keeps_stocks_unchanged() -> None:
    stocks = [{"code": "000001", "industry": "银行", "is_st": False, "name": "平安银行"}]
    out = _builder(None)._apply_point_in_time_metadata(stocks, "2025-01-15")
    assert out[0]["industry"] == "银行"
    assert out[0]["is_st"] is False


def test_provider_failure_falls_back_to_current_metadata() -> None:
    class _Boom:
        def resolve(self, codes, signal_date):
            raise RuntimeError("metadata source unavailable")

    stocks = [{"code": "000001", "industry": "银行", "is_st": False, "name": "平安银行"}]
    out = _builder(_Boom())._apply_point_in_time_metadata(stocks, "2025-01-15")
    assert out[0]["industry"] == "银行"
    assert out[0]["is_st"] is False
