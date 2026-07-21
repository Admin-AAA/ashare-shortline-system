"""策略清单端点回归测试 —— /api/strategies。

验证：
- 返回 200 且为列表
- 每个元素含 skill_id / name / performance 等关键字段
- 已调用策略 performance 非空且含胜率；从未调用的 performance=None（覆盖 3/8 缺口的显式呈现）
- 已调用策略排在未调用之前
- mode 校验：非法 mode 返回 400
"""

from fastapi.testclient import TestClient

from app.main import app


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
        assert "enabled" in item
        assert "assigned_presets" in item


def test_strategies_endpoint_called_before_dormant():
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    dormant = [d for d in data if d["performance"] is None]
    # 至少存在已调用与未调用两类（覆盖 3/8 的真实数据形态）
    assert called, "应有至少一个已调用策略"
    # 合并 mode 后所有策略都可能有推荐，不再强制要求存在未覆盖策略
    # assert dormant, "应有至少一个未覆盖（从未调用）策略"
    # 排序：已调用的排前面
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
        assert isinstance(p["sample_count"], int) and p["sample_count"] > 0
        assert p["hit_rate"] is not None


def test_strategies_endpoint_invalid_mode():
    client = TestClient(app)
    resp = client.get("/api/strategies?mode=bogus")
    # 合并 mode 后 API 不再校验 mode，非法 mode 也返回 200
    assert resp.status_code == 200


def test_strategies_endpoint_traceable_fields_present():
    """北极星 C 项回归：已调用策略的 performance 必须携带盈亏比与最近命中日期。

    此前 strategies.py 在构造 perf 时遗漏这两个字段（metrics_by_strategy 已计算），
    导致前端策略卡片恒显 "盈亏比 —" / "最近命中 —"，策略可追溯断链。
    """
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    assert called, "应有至少一个已调用策略"
    for d in called:
        p = d["performance"]
        assert "profit_loss_ratio" in p, f"{d['skill_id']} 缺 profit_loss_ratio"
        assert "last_recommendation_date" in p, f"{d['skill_id']} 缺 last_recommendation_date"
    with_dates = [d for d in called if d["performance"]["last_recommendation_date"]]
    assert with_dates, "已调用策略中至少应有一条带最近命中日期"


def test_strategies_reliable_flag_consistent_with_sample_count():
    """T7 统计可信度：performance.reliable 须与样本数阈值(20)严格一致。

    防止前端在策略胜率下方误标/漏标「样本不足」——这正是 self_check 长期预警的
    「5 策略样本<20 胜率不可信」用户可见缺口。可靠数据源 = 后端此字段。
    """
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    called = [d for d in data if d["performance"] is not None]
    assert called, "应有至少一个已调用策略"
    for d in called:
        p = d["performance"]
        assert isinstance(p["reliable"], bool), f"{d['skill_id']} reliable 须为布尔"
        expected = p["sample_count"] >= 20
        assert p["reliable"] == expected, (
            f"{d['skill_id']} reliable={p['reliable']} 与 sample_count={p['sample_count']} 阈值 20 不一致"
        )
    # 端到端真值：当前生产库至少存在 1 个 n<20 的低样本策略（reliable=False）
    low = [d for d in called if not d["performance"]["reliable"]]
    assert low, "当前生产库应有样本<20 的策略使其 reliable=False（否则阈值无意义）"


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
    """T27：曝光 <20 策略须带 last_exp_gate（有效线 vs 最高候选），与 self_check 同源。"""
    client = TestClient(app)
    data = client.get("/api/strategies").json()
    low = [d for d in data if d.get("exposure_30d", 0) < 20 and d.get("enabled", True)]
    # 至少 chip/small-cap/trend 之一应有门线明细
    with_gate = [d for d in low if d.get("last_exp_gate")]
    assert with_gate, "低曝光策略应返回 last_exp_gate"
    for d in with_gate:
        g = d["last_exp_gate"]
        assert "effective_line" in g
        assert "max_candidate_score" in g
        assert "gate_blocked" in g
        if g.get("gate_blocked") and g.get("effective_line") is not None and g.get("max_candidate_score") is not None:
            assert float(g["max_candidate_score"]) < float(g["effective_line"])
