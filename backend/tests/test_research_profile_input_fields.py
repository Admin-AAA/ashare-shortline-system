"""回归测试：profile 回测输入必须包含策略依赖的衍生字段。

历史 bug（2026-08 修复）：9 个策略（chao-gu-yang-jia / chen-xiaoqun /
high-range / overnight / sector-relative / small-cap / limit-up /
dragon-tiger / high-position）的 profile backtest 产生 0 条交易，根因是
回测路径 `_stock_as_of` 构建的 stock dict 缺少策略 prefilter 依赖的字段：
industry_capacity_like_count / upper_shadow_count20 / is_limit_up /
dragon_tiger_is_on_list / free_mv_yi 等，导致 include_any 条件全 False。

修复三件套（全部在 research_runner.py 内，不动生产路径）：
1. `_stock_as_of` 从日K直接计算 upper_shadow_count20 / is_limit_up（板位感知）；
2. `capture()` 注入本地辅助表（limit_up_board / dragon_tiger / stock_quotes）
   并调用 `_apply_relative_features`（industry 相对特征）；
3. `_evaluate_snapshot` 复刻生产 build_features 的因子管线
   （`_apply_shared_factors`），让 sector_ladder / capital_resonance /
   limit_up_overheat / dragon_tiger 衍生字段在 evaluate 前可用。
"""

from types import SimpleNamespace

from app.services.backtest.research_runner import (
    LocalResearchSnapshotBuilder,
    RESEARCH_INPUT_SCHEMA_VERSION,
    _apply_shared_factors,
    _stock_as_of,
)
from app.services.backtest.strategy_profile import StrategyBacktestProfiles
from app.services.factors.registry import bootstrap_default_factor_registry


def _builder():
    return LocalResearchSnapshotBuilder.__new__(LocalResearchSnapshotBuilder)


def _bar(**kwargs):
    """构造一根 DailyKbar 兼容的轻量行（SimpleNamespace 鸭子类型）。"""
    defaults = {
        "code": "000001",
        "trade_date": "2026-07-01",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.3,
        "volume": 1000000,
        "amount": 50000000,
        "pct_chg": 2.5,
        "turnover_rate": 5.0,
        "ma5": 10.0,
        "ma10": 9.9,
        "ma20": 9.8,
        "ma60": 9.5,
        "macd_dif": 0.1,
        "macd_dea": 0.05,
        "rsi_12": 55.0,
        "breakout20": 0,
        "breakout60": 0,
        "chip_cost_15": 9.0,
        "chip_cost_85": 11.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _history(n=25, **kwargs):
    """构造近 n 日历史（无长上影、无涨停）。"""
    bars = []
    for i in range(1, n + 1):
        bars.append(
            _bar(
                trade_date=f"2026-06-{i:02d}",
                open=10.0,
                high=10.2,
                low=9.9,
                close=10.1,
                pct_chg=1.0,
                **kwargs,
            )
        )
    return bars


def _metadata(**kwargs):
    defaults = {"name": "测试股份", "industry": "半导体", "is_st": False}
    defaults.update(kwargs)
    return defaults


def test_stock_as_of_includes_upper_shadow_count20():
    bars = list(_history(25))
    # 最近 20 根里 3 根有长上影（上影/全幅 >= 0.45）
    for i in [3, 8, 15]:
        bars[-i] = _bar(trade_date=bars[-i].trade_date, open=10.0, high=10.8, low=9.9, close=10.1)
    stock = _stock_as_of(_bar(), bars, _metadata(), "2026-07-01")
    assert stock["upper_shadow_count20"] == 3


def test_stock_as_of_is_limit_up_by_board():
    # 主板 10cm：pct >= 9.8 触发涨停
    stock = _stock_as_of(_bar(pct_chg=9.9), _history(), _metadata(), "2026-07-01")
    assert stock["is_limit_up"] is True
    assert stock["limit_up_consecutive_days"] == 1
    # 创业板 20cm：pct=9.9 不算涨停
    stock = _stock_as_of(
        _bar(code="300001", pct_chg=9.9), _history(), _metadata(), "2026-07-01"
    )
    assert stock["is_limit_up"] is False
    # 创业板 pct >= 19.8 才算涨停
    stock = _stock_as_of(
        _bar(code="300001", pct_chg=19.9), _history(), _metadata(), "2026-07-01"
    )
    assert stock["is_limit_up"] is True


def test_stock_as_of_st_lower_limit_threshold():
    # ST 5cm：pct=5.1 触发涨停（比主板 10cm 阈值低）
    stock = _stock_as_of(
        _bar(pct_chg=5.1), _history(), _metadata(is_st=True), "2026-07-01"
    )
    assert stock["is_limit_up"] is True
    stock = _stock_as_of(
        _bar(pct_chg=5.1), _history(), _metadata(), "2026-07-01"
    )
    assert stock["is_limit_up"] is False


def test_stock_as_of_has_dragon_tiger_defaults():
    stock = _stock_as_of(_bar(), _history(), _metadata(), "2026-07-01")
    assert stock["dragon_tiger_is_on_list"] is False
    assert stock["dragon_tiger_net_buy_ratio"] == 0.0
    assert stock["dragon_tiger_inst_buy_ratio"] == 0.0


def test_stock_as_of_long_window_position_fields():
    """pos120 / off_high60_pct / bias_ma20 / eff20 必须存在且口径与生产一致。"""
    bars = list(_history(130))
    # 60 日窗口内（-50 根）、20 日窗口外的高点：close 远离 60 日高点但贴近 20 日高点
    bars[-50] = _bar(trade_date=bars[-50].trade_date, open=10.0, high=15.0, low=9.9, close=10.1)
    stock = _stock_as_of(_bar(), bars, _metadata(), "2026-07-01")
    assert stock["pos120"] >= 0.0
    assert stock["pos120"] <= 100.0
    # close=10.3 相对 60 日高点 15.0 → off_high60_pct = (10.3-15)/15*100 ≈ -31.3
    assert stock["off_high60_pct"] < -30.0
    # off_high_pct 是相对 20 日高点（此处无 20 日高点，close 即为高点 → ≈0）
    assert stock["off_high_pct"] > -1.0
    assert stock["bias_ma20"] == stock["dist20"]
    assert stock["eff20"] >= 0.0


def test_stock_as_of_bias_ma20_matches_dist20():
    """bias_ma20 必须与 dist20 同源（生产两者均为 (close-ma20)/ma20*100）。"""
    bars = list(_history(25))
    stock = _stock_as_of(_bar(close=11.0), bars, _metadata(), "2026-07-01")
    assert stock["bias_ma20"] == stock["dist20"]
    assert stock["bias_ma20"] > 5.0  # close 11.0 vs ma20≈10.0


def test_stock_as_of_declares_capital_flow_missing():
    """回测无资金流池：data_quality 必须声明 capital_flow 缺失，让资本共振因子
    走 price_volume_fallback degradation（否则 has_flow=True 用全零资金流算分，
    恒 39 无区分度，卡死依赖 capital_resonance_score>=45 的 setup）。"""
    stock = _stock_as_of(_bar(), _history(), _metadata(), "2026-07-01")
    dq = stock.get("data_quality") or {}
    assert "capital_flow" in set(dq.get("missing_fields") or [])
    assert dq.get("capital_flow_checked") is False


def test_capital_resonance_falls_back_without_flow():
    """capital_resonance 因子在声明缺失后必须走 fallback 而非恒 39。"""
    from app.services.factors import run_factors
    from app.services.factors.registry import bootstrap_default_factor_registry

    bootstrap_default_factor_registry()
    stocks = [
        _stock_as_of(
            _bar(code="000001", pct_chg=4.0, amount=8_0000_0000, volume=15000000),
            list(_history(25, volume=10000000)),
            _metadata(),
            "2026-07-01",
        )
    ]
    stocks[0]["vol5_20"] = 1.5  # 放量
    _apply_shared_factors(stocks, SimpleNamespace())
    score = stocks[0]["capital_resonance_score"]
    assert score != 39.0  # fallback 分支给出有区分度的分数
    assert stocks[0]["capital_resonance_source"] == "price_volume_fallback"


def test_inject_research_aux_limit_up_fields():
    stock = {
        "code": "000001",
        "amount_yi": 5.0,
        "is_limit_up": False,
        "limit_up_consecutive_days": 0,
        "limit_up_open_times": 0,
        "limit_up_fd_amount_yi": 0.0,
    }
    _builder()._inject_research_aux(
        [stock],
        {
            "000001": {
                "limit_up_consecutive_days": 2,
                "limit_up_open_times": 3,
                "limit_up_fd_amount_yi": 0.8,
                "limit_up_seal_time": "09:35:00",
            }
        },
    )
    assert stock["is_limit_up"] is True
    assert stock["limit_up_consecutive_days"] == 2
    assert stock["limit_up_open_times"] == 3
    assert stock["limit_up_fd_amount_yi"] == 0.8
    assert stock["limit_up_seal_time"] == "09:35:00"


def test_inject_research_aux_dragon_tiger_fields():
    stock = {
        "code": "000001",
        "amount_yi": 10.0,
        "dragon_tiger_is_on_list": False,
        "dragon_tiger_net_amount_yi": 0.0,
        "dragon_tiger_net_buy_ratio": 0.0,
    }
    _builder()._inject_research_aux(
        [stock],
        {
            "000001": {
                "dragon_tiger_is_on_list": True,
                "dragon_tiger_recent_count": 1,
                "dragon_tiger_latest_date": "2026-07-01",
                "dragon_tiger_buy_amount_yi": 1.0,
                "dragon_tiger_sell_amount_yi": 0.5,
                "dragon_tiger_net_amount_yi": 0.5,
                "dragon_tiger_institution_net_buy_yi": 0.2,
            }
        },
    )
    assert stock["dragon_tiger_is_on_list"] is True
    assert stock["dragon_tiger_net_buy_ratio"] == 0.05


def test_inject_research_aux_free_mv():
    stock = {"code": "000001", "free_mv_yi": 0.0, "total_mv_yi": 0.0}
    _builder()._inject_research_aux([stock], {"000001": {"free_mv_yi": 55.0, "total_mv_yi": 100.0}})
    assert stock["free_mv_yi"] == 55.0
    assert stock["total_mv_yi"] == 100.0


def test_apply_shared_factors_populates_derived_fields():
    """因子管线必须让 sector_ladder / capital_resonance 等衍生字段可用。"""
    bootstrap_default_factor_registry()  # 生产由 app 启动路径 bootstrap；测试显式补一次
    stocks = []
    for i in range(3):
        s = _stock_as_of(
            _bar(code=f"00000{i}", pct_chg=5.0 if i == 0 else 1.0),
            _history(),
            _metadata(industry="半导体" if i == 0 else "消费电子"),
            "2026-07-01",
        )
        s["is_limit_up"] = i == 0
        s["limit_up_consecutive_days"] = 1 if i == 0 else 0
        s["main_net_inflow_yi"] = 1.0
        stocks.append(s)
    # bundle 只有 snapshot/artifact 读取，因子只读 stocks 自身字段
    bundle = SimpleNamespace()
    _apply_shared_factors(stocks, bundle)
    for s in stocks:
        assert "sector_ladder_completeness_score" in s
        assert "capital_resonance_score" in s
        assert "limit_up_overheat_score" in s
        assert "is_main_theme" in s
    # 涨停股所在板块应有梯队分
    assert stocks[0]["sector_ladder_completeness_score"] >= 0
    # 因子字段不会被异常抛出打断
    assert "shortline_factor_explanations" in stocks[0] or "is_20cm_stock" in stocks[0]


def test_apply_shared_factors_keeps_signal_date_market_breadth_for_target_subset():
    """target_codes 评估不能把子集涨跌误当成全市场情绪。"""
    bootstrap_default_factor_registry()
    stocks = [
        _stock_as_of(
            _bar(code="000001", pct_chg=5.0),
            _history(),
            _metadata(),
            "2026-07-01",
        ),
        _stock_as_of(
            _bar(code="000002", pct_chg=-2.0),
            _history(),
            _metadata(industry="消费电子"),
            "2026-07-01",
        ),
    ]
    for stock in stocks:
        stock.update(
            {
                "market_sample_count": 1000,
                "up_count": 450,
                "down_count": 500,
                "limit_up_count": 1,
                "limit_down_count": 50,
            }
        )

    _apply_shared_factors(stocks, SimpleNamespace())

    assert {stock["market_sentiment_phase"] for stock in stocks} == {"平淡"}


def test_snapshot_matches_input_schema_accepts_current_version():
    """v2 manifest 必须被识别为可复用；v1 旧快照必须被拒绝。"""
    p = StrategyBacktestProfiles.__new__(StrategyBacktestProfiles)

    class _FakeDb:
        def __init__(self, manifest):
            self._manifest = manifest

        def scalar(self, stmt):
            return self._manifest

    current = f"local-daily-kbars/v{RESEARCH_INPUT_SCHEMA_VERSION}"
    v2_manifest = (
        '{"schema_version": 2, "partitions": [{"dataset": "daily_kbars", '
        '"source_version": "%s", "row_count": 10, "content_hash": "x"}], '
        '"artifacts_by_signal_date": {}}'
    ) % current
    v1_manifest = v2_manifest.replace(current, "local-daily-kbars/v1")
    malformed = '{"partitions": ['

    p._db = _FakeDb(v2_manifest)
    assert p._snapshot_matches_input_schema("snap-1") is True
    p._db = _FakeDb(v1_manifest)
    assert p._snapshot_matches_input_schema("snap-2") is False
    p._db = _FakeDb(malformed)
    assert p._snapshot_matches_input_schema("snap-3") is False
    p._db = _FakeDb(None)
    assert p._snapshot_matches_input_schema("snap-4") is False
