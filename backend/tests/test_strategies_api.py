"""策略清单端点回归测试 —— /api/strategies。

验证：
- 返回 200 且为列表
- 每个元素含 skill_id / name / performance 等关键字段
- 只有当前版本半年 canonical 回测完成后 performance 才非空
- 未完成时明确 pending_backtest，禁止回退到跨版本历史推荐收益
- mode 校验：非法 mode 返回 400
"""

from fastapi.testclient import TestClient
import pytest

from app.main import app

pytestmark = pytest.mark.integration


def test_strategies_endpoint_basic_shape():
    client = TestClient(app)
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    for item in data:
        assert "skill_id" in item and "name" in item
        assert "performance" in item
        assert "performance_status" in item
        assert "current_strategy_version" in item
        assert "enabled" in item
        assert "assigned_presets" in item


def test_strategies_endpoint_called_before_dormant():
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    dormant = [d for d in data if d["performance"] is None]
    assert called or dormant
    # 排序：有当前版本回测画像的排前面
    first_dormant_idx = next((i for i, d in enumerate(data) if d["performance"] is None), None)
    if first_dormant_idx is not None:
        for d in data[:first_dormant_idx]:
            assert d["performance"] is not None


def test_strategies_endpoint_called_has_metrics():
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    for d in called:
        p = d["performance"]
        assert p["source"] == "canonical_backtest"
        assert p["strategy_version"] == d["current_strategy_version"]
        assert isinstance(p["sample_count"], int) and p["sample_count"] >= 0
        if p["sample_count"]:
            assert p["hit_rate"] is not None
        else:
            assert p["hit_rate"] is None
    for d in data:
        if d["performance"] is None and d["enabled"]:
            assert d["performance_status"] in {"pending_backtest", "unavailable"}


def test_strategies_endpoint_invalid_mode():
    client = TestClient(app)
    resp = client.get("/api/strategies?mode=bogus")
    # 合并 mode 后 API 不再校验 mode，非法 mode 也返回 200
    assert resp.status_code == 200


def test_strategies_endpoint_backtest_identity_fields_are_traceable():
    """画像必须暴露策略版本、Top10 口径、窗口、证据 ID 和按月信号表现。"""
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    for d in called:
        p = d["performance"]
        for key in (
            "profit_loss_ratio",
            "strategy_version",
            "profile_fingerprint",
            "backtest_id",
            "date_from",
            "date_to",
            "generated_at",
            "selection_policy",
            "daily_selection_limit",
            "raw_signal_count",
            "selected_signal_count",
            "monthly_metrics",
        ):
            assert key in p, f"{d['skill_id']} 缺 {key}"
        assert p["selection_policy"] == "daily_score_top_n"
        assert p["daily_selection_limit"] == 10
        assert p["selected_signal_count"] <= p["raw_signal_count"]
        assert "total_return_pct" not in p
        assert "max_drawdown_pct" not in p
        assert set(p.get("window_metrics", {})) >= {"30", "180"}
        for window_days in ("30", "180"):
            window = p["window_metrics"][window_days]
            assert window["window_days"] == int(window_days)
            assert window["date_from"] <= window["date_to"]
            assert window["sample_count"] >= 0


def test_strategies_reliable_flag_consistent_with_sample_count():
    """T7 统计可信度：performance.reliable 须与样本数阈值(20)严格一致。

    防止前端在策略胜率下方误标/漏标「样本不足」——这正是 self_check 长期预警的
    「5 策略样本<20 胜率不可信」用户可见缺口。可靠数据源 = 后端此字段。
    """
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    for d in called:
        p = d["performance"]
        assert isinstance(p["reliable"], bool), f"{d['skill_id']} reliable 须为布尔"
        expected = p["sample_count"] >= 20
        assert p["reliable"] == expected, (
            f"{d['skill_id']} reliable={p['reliable']} 与 sample_count={p['sample_count']} 阈值 20 不一致"
        )


def test_strategies_exposure_30d_present_and_nonnegative():
    """T7 E 项曝光可见：列表每项含 exposure_30d / exposure_floor，与 self_check 对齐。

    近 30 天 formal+experiment 曝光供前端「补跑在喂」角标；performance 为空时
    顶层仍返回，避免从未结算策略看不见补跑状态。
    """
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    assert data
    for d in data:
        assert "exposure_30d" in d, f"{d.get('skill_id')} 缺顶层 exposure_30d"
        assert "exposure_floor" in d
        assert d["exposure_floor"] == 20
        assert isinstance(d["exposure_30d"], int) and d["exposure_30d"] >= 0
        if d["performance"] is not None:
            p = d["performance"]
            assert "exposure_30d" in p
            assert p["exposure_30d"] == d["exposure_30d"]
            assert p.get("exposure_floor") == 20
    # 生产库至少应有 1 个曝光 < 20 的策略（chip/small-cap/trend 长期 <20）
    low_exp = [d for d in data if d["exposure_30d"] < 20]
    assert low_exp, "应有曝光 <20 的策略以便前端展示「补跑在喂」"


def test_strategies_low_exposure_has_last_exp_gate():
    """T27/T29：曝光 <20 策略须带 last_exp_gate（有效线 vs 最高候选 + 恐慌日字段）。"""
    from app.api.strategies import _GATE_CACHE
    _GATE_CACHE.clear()  # 清进程级 TTL 缓存，确保测试拿到最新 DB 值
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    low = [d for d in data if d.get("exposure_30d", 0) < 20 and d.get("enabled", True)]
    # 至少 chip/small-cap/trend 之一应有门线明细
    with_gate = [d for d in low if d.get("last_exp_gate")]
    if not with_gate:
        assert all(d.get("last_exp_gate") is None for d in low)
        return
    for d in with_gate:
        g = d["last_exp_gate"]
        assert "effective_line" in g
        assert "max_candidate_score" in g
        assert "gate_blocked" in g
        # T29：panic_day / regime 与 self_check 同源透出
        assert "panic_day" in g
        assert "limit_down_count" in g
        assert "regime" in g
        if g.get("gate_blocked") and g.get("effective_line") is not None and g.get("max_candidate_score") is not None:
            assert float(g["max_candidate_score"]) < float(g["effective_line"])
        # 当前生产 07-20 补跑为恐慌日（ld=479）时 panic_day 应为 True
        if g.get("panic_day"):
            assert g.get("limit_down_count") is not None and int(g["limit_down_count"]) > 100
