"""验证 `app.services.backtest.result` —— trades → 展示用样本记录的收敛模块（S8.1，需求 9.1）。

覆盖：
1. `select_false_positive_trades`：筛选条件（resolved & not accurate）+ 排序（按
   net_return_pct 升序）+ 截取 limit，与 backtest_service/overnight_holding_service
   原实现逐字节一致。
2. `project_recommendation_record`：核心字段投影 + entry/exit/recommendation
   time key 差异吸收 + extra_fields 挂载。
3. `project_false_positive_sample`：同上，误判样本字段集。
4. 回归：backtest_service._backtest_recommendation_records /
   _backtest_false_positive_samples、overnight_holding_service._recommendation_records /
   _false_positive_samples 在收敛后行为不变（保留既有断言，防止收敛引入回归）。
"""

from __future__ import annotations

from app.services.backtest.result import (
    project_false_positive_sample,
    project_recommendation_record,
    select_false_positive_trades,
)


def _trade(**overrides):
    base = {
        "trade_date": "2026-05-18",
        "code": "000001",
        "name": "测试股",
        "entry_date": "2026-05-19",
        "entry_time": "2026-05-19T09:31:00",
        "recommendation_time": "2026-05-19T09:30:00",
        "entry_price": 10.0,
        "exit_date": "2026-05-20",
        "exit_time": "2026-05-20T09:35:00",
        "exit_price": 10.5,
        "net_return_pct": 5.0,
        "gross_return_pct": 5.2,
        "benchmark_return_pct": 1.0,
        "accurate": True,
        "exit_reason": "take_profit",
        "false_positive_reason": None,
        "resolved": True,
    }
    base.update(overrides)
    return base


class TestSelectFalsePositiveTrades:
    def test_filters_resolved_and_inaccurate_only(self):
        trades = [
            _trade(code="000001", accurate=True, resolved=True),
            _trade(code="000002", accurate=False, resolved=True, net_return_pct=-2.0),
            _trade(code="000003", accurate=False, resolved=False, net_return_pct=-9.0),
        ]

        losers = select_false_positive_trades(trades)

        assert [item["code"] for item in losers] == ["000002"]

    def test_sorts_by_net_return_pct_ascending(self):
        trades = [
            _trade(code="A", accurate=False, resolved=True, net_return_pct=-1.0),
            _trade(code="B", accurate=False, resolved=True, net_return_pct=-5.0),
            _trade(code="C", accurate=False, resolved=True, net_return_pct=-3.0),
        ]

        losers = select_false_positive_trades(trades)

        assert [item["code"] for item in losers] == ["B", "C", "A"]

    def test_respects_limit(self):
        trades = [
            _trade(code=str(i), accurate=False, resolved=True, net_return_pct=-float(i))
            for i in range(20)
        ]

        losers = select_false_positive_trades(trades, limit=3)

        assert len(losers) == 3

    def test_missing_net_return_pct_treated_as_zero(self):
        trades = [
            _trade(code="none_return", accurate=False, resolved=True, net_return_pct=None),
            _trade(code="negative", accurate=False, resolved=True, net_return_pct=-1.0),
        ]

        losers = select_false_positive_trades(trades)

        assert [item["code"] for item in losers] == ["negative", "none_return"]

    def test_empty_trades_returns_empty_list(self):
        assert select_false_positive_trades([]) == []


class TestProjectRecommendationRecord:
    def test_default_key_mapping_uses_entry_time_and_exit_time(self):
        trade = _trade()

        record = project_recommendation_record(trade)

        assert record["entry_time"] == trade["entry_time"]
        assert record["exit_time"] == trade["exit_time"]
        assert record["recommendation_time"] == trade["trade_date"]

    def test_custom_entry_exit_time_key_reads_alternate_fields(self):
        """backtest_service 的 trade dict 用 entry_date/exit_date 而非 entry_time/exit_time。"""
        trade = _trade()

        record = project_recommendation_record(
            trade, entry_time_key="entry_date", exit_time_key="exit_date"
        )

        assert record["entry_time"] == trade["entry_date"]
        assert record["exit_time"] == trade["exit_date"]

    def test_custom_recommendation_time_key(self):
        """overnight_holding_service 的 trade dict 自带独立 recommendation_time 字段。"""
        trade = _trade(recommendation_time="2026-05-19T09:25:00")

        record = project_recommendation_record(
            trade, recommendation_time_key="recommendation_time"
        )

        assert record["recommendation_time"] == "2026-05-19T09:25:00"

    def test_extra_fields_are_merged_into_record(self):
        trade = _trade()

        record = project_recommendation_record(
            trade, extra_fields={"conditions": ["cond_a", "cond_b"], "accuracy_failure_label": None}
        )

        assert record["conditions"] == ["cond_a", "cond_b"]
        assert record["accuracy_failure_label"] is None

    def test_core_fields_present(self):
        trade = _trade()

        record = project_recommendation_record(trade)

        expected_keys = {
            "trade_date", "code", "name", "recommendation_time", "entry_time",
            "entry_price", "exit_date", "exit_time", "exit_price", "net_return_pct",
            "gross_return_pct", "benchmark_return_pct", "accurate", "exit_reason",
            "false_positive_reason",
        }
        assert expected_keys.issubset(record.keys())


class TestProjectFalsePositiveSample:
    def test_default_key_mapping(self):
        trade = _trade()

        sample = project_false_positive_sample(trade)

        assert sample["entry_time"] == trade["entry_time"]
        assert sample["exit_time"] == trade["exit_time"]

    def test_custom_entry_exit_time_key(self):
        trade = _trade()

        sample = project_false_positive_sample(
            trade, entry_time_key="entry_date", exit_time_key="exit_date"
        )

        assert sample["entry_time"] == trade["entry_date"]
        assert sample["exit_time"] == trade["exit_date"]

    def test_extra_fields_are_merged(self):
        trade = _trade()

        sample = project_false_positive_sample(
            trade, extra_fields={"conditions": ["cond_x"]}
        )

        assert sample["conditions"] == ["cond_x"]

    def test_core_fields_present_and_no_extra_by_default(self):
        trade = _trade()

        sample = project_false_positive_sample(trade)

        expected_keys = {
            "trade_date", "code", "name", "entry_time", "exit_time",
            "net_return_pct", "false_positive_reason",
        }
        assert set(sample.keys()) == expected_keys


class TestBacktestServiceIntegration:
    """回归：backtest_service 收敛到 backtest/result 后行为不变。"""

    def test_backtest_recommendation_records_expose_accuracy_gates(self):
        from app.services.backtest_service import (
            _backtest_false_positive_samples,
            _backtest_recommendation_records,
        )

        trades = [
            {
                "trade_date": "2026-05-18",
                "code": "000001",
                "name": "赢家",
                "entry_date": "2026-05-19",
                "entry_price": 10.0,
                "exit_date": "2026-05-20",
                "exit_price": 10.5,
                "net_return_pct": 5.0,
                "gross_return_pct": 5.2,
                "benchmark_return_pct": 1.0,
                "accurate": True,
                "signal_valid_at_recommend_time": True,
                "executed": True,
                "net_return_positive": True,
                "exit_reason": "take_profit",
                "false_positive_reason": None,
                "resolved": True,
            },
            {
                "trade_date": "2026-05-18",
                "code": "000002",
                "name": "未成交",
                "entry_date": "2026-05-19",
                "entry_price": 10.0,
                "exit_date": "2026-05-20",
                "exit_price": 10.0,
                "net_return_pct": 0.0,
                "gross_return_pct": 0.3,
                "benchmark_return_pct": 0.2,
                "accurate": False,
                "signal_valid_at_recommend_time": True,
                "executed": False,
                "net_return_positive": False,
                "exit_reason": "missed_entry",
                "false_positive_reason": "未触发买点或不可真实成交",
                "resolved": True,
            },
            {
                "trade_date": "2026-05-18",
                "code": "000003",
                "name": "信号失效",
                "entry_date": "2026-05-19",
                "entry_price": 10.0,
                "exit_date": "2026-05-20",
                "exit_price": 9.8,
                "net_return_pct": -2.0,
                "gross_return_pct": -1.6,
                "benchmark_return_pct": 0.0,
                "accurate": False,
                "signal_valid_at_recommend_time": False,
                "executed": True,
                "net_return_positive": False,
                "exit_reason": "stop_loss",
                "false_positive_reason": "开盘即失效",
                "resolved": True,
            },
        ]

        records = _backtest_recommendation_records(trades)
        failures = _backtest_false_positive_samples(trades)

        assert records[0]["accuracy_failure_label"] is None
        assert records[1]["accuracy_failure_label"] == "未触发买点或不可真实成交"
        assert records[2]["accuracy_failure_label"] == "信号当时真实不成立"
        assert failures[0]["code"] == "000003"
        assert failures[0]["accuracy_failure_label"] == "信号当时真实不成立"


class TestOvernightHoldingServiceIntegration:
    """回归：overnight_holding_service 收敛到 backtest/result 后行为不变。"""

    def _overnight_trade(self, **overrides):
        base = {
            "trade_date": "2026-05-18",
            "code": "000001",
            "name": "隔夜样本",
            "recommendation_time": "2026-05-18T14:31:00",
            "entry_time": "2026-05-18T14:32:00",
            "entry_price": 11.0,
            "exit_date": "2026-05-19",
            "exit_time": "2026-05-19T09:35:00",
            "exit_price": 11.2,
            "net_return_pct": 1.8,
            "gross_return_pct": 2.0,
            "benchmark_return_pct": 0.5,
            "accurate": True,
            "conditions": {"vwap_hold": True},
            "exit_reason": "next_morning_exit",
            "false_positive_reason": None,
            "resolved": True,
        }
        base.update(overrides)
        return base

    def test_recommendation_records_carry_conditions_field(self):
        from app.services.overnight_holding_service import _recommendation_records

        trades = [self._overnight_trade()]

        records = _recommendation_records(trades)

        assert records[0]["conditions"] == {"vwap_hold": True}
        assert records[0]["entry_time"] == trades[0]["entry_time"]
        assert records[0]["recommendation_time"] == trades[0]["recommendation_time"]

    def test_false_positive_samples_filters_and_carries_conditions(self):
        from app.services.overnight_holding_service import _false_positive_samples

        trades = [
            self._overnight_trade(code="win", accurate=True, resolved=True, net_return_pct=2.0),
            self._overnight_trade(
                code="loss",
                accurate=False,
                resolved=True,
                net_return_pct=-3.0,
                false_positive_reason="早盘兑现后净收益不为正",
                conditions={"vwap_hold": False},
            ),
        ]

        samples = _false_positive_samples(trades)

        assert len(samples) == 1
        assert samples[0]["code"] == "loss"
        assert samples[0]["conditions"] == {"vwap_hold": False}
        assert samples[0]["false_positive_reason"] == "早盘兑现后净收益不为正"
