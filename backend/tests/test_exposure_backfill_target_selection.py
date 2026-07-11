"""曝光补跑目标选择回归测试（T3.6 E 项闭环）。

锁定 scheduler_service._run_strategy_exposure 的目标选择不变量：
1. 只补跑 enabled 且近 30 天 formal+experiment 真实样本 < 阈值的策略
2. 永不复跑 disabled 策略（即便其样本为 0）
3. 永不复跑已达标（样本≥阈值）策略
4. 入队 payload 必为 run_mode='experiment' 且 disable_agent_review=True（不进记录中心/驾驶舱，免 LLM）
5. 样本查询口径必须是 ('formal','experiment')——排除 backtest 合成回测，
   这是历史上 high-range-breakout-momentum 被 13 笔 backtest 误判 sample≥20、
   真实 formal+experiment 仅 16、长期卡 n<20 的根因回归点。

⚠️ 口径对齐（与 parallel commit 557e196 的测试约定一致，但修正其样本窗口基准）：
- 样本计数函数已改为 outcome_service.exposure_sample_counts（而非旧 metrics_by_strategy），
  按 recommended_at 近 N 天、run_mode IN (formal,experiment)、JSON_TABLE 展开 skill_breakdown_json
  去重统计——与 scripts/self_check.py 的 E 项曝光分布**完全一致**（E 项触发口径）。
- 旧版误用 metrics_by_strategy 的 trade_date 窗口，与 self_check E 项（recommended_at）严重偏离，
  导致 daily-stock 真实 115 样本被误判为 7、曝光补跑错把高覆盖策略也选入、真正低覆盖策略
  反被淹没（实盘 cron 曾喂 sector/limit-up 而 chip 得 0 样本即为铁证）。本测试随修正改为
  mock exposure_sample_counts（返回 dict 口径），其余不变量（只补低样本 enabled、跳过 disabled/达标、
  experiment 模式禁 LLM、formal+experiment 排除 backtest）全部保留。

注意：_run_strategy_exposure 在函数体内局部 import 三个服务函数，
故此处 patch 的是源模块（app.services.workbench_service / outcome_service）的属性。
所有 DB 访问均被 mock，因此本测试不依赖真实数据库、也不争夺生产库测试锁，
可与其他并行 self-improve 实例的 pytest 安全共存。
"""
import asyncio

import pytest

from app.services import scheduler_service


class _FakeSkill:
    def __init__(self, skill_id, name, enabled):
        self.skill_id = skill_id
        self.name = name
        self.enabled = enabled


@pytest.fixture
def fake_backfill(monkeypatch):
    skills = [
        _FakeSkill("daily-stock-analysis-skill", "每日股票分析", True),  # 达标(>=20) -> 跳过
        _FakeSkill("chip-single-peak-breakout", "筹码单峰突破", True),    # <20 -> 补跑
        _FakeSkill("small-cap-thematic-momentum", "小盘题材动量", True),  # <20 -> 补跑
        _FakeSkill("disabled-strategy", "已停用策略", False),             # disabled -> 永不补跑
    ]

    def _fake_sync_skills(db):
        return list(skills)

    calls = {}

    def _fake_exposure_counts(db, window_days=30, run_mode=None):
        calls["window_days"] = window_days
        calls["run_mode"] = run_mode
        # 与 exposure_sample_counts 口径一致：返回 {strategy_id: count} dict。
        # chip=12, small-cap=3, daily=115；disabled-strategy 不在结果中 -> 视为 0（但 disabled）
        return {
            "daily-stock-analysis-skill": 115,
            "chip-single-peak-breakout": 12,
            "small-cap-thematic-momentum": 3,
        }

    captured = []

    def _fake_enqueue(db, payload):
        captured.append(payload)
        return {"run": {"run_id": "run_x"}, "queued": True}

    monkeypatch.setattr("app.services.workbench_service.sync_skills", _fake_sync_skills)
    monkeypatch.setattr("app.services.outcome_service.exposure_sample_counts", _fake_exposure_counts)
    monkeypatch.setattr("app.services.workbench_service.enqueue_workbench_run", _fake_enqueue)

    return {"calls": calls, "captured": captured}


def test_exposure_selects_only_low_sample_enabled(fake_backfill):
    result = asyncio.run(
        scheduler_service._run_strategy_exposure(None, {"window_days": 30, "min_sample_threshold": 20})
    )
    selected = {r["skill_id"] for r in result["exposed_strategies"]}
    assert selected == {"chip-single-peak-breakout", "small-cap-thematic-momentum"}
    assert "disabled-strategy" not in selected          # disabled 永不补跑
    assert "daily-stock-analysis-skill" not in selected  # 已达标永不补跑
    assert result["skipped"] is False


def test_exposure_enqueue_uses_experiment_mode_and_disables_llm(fake_backfill):
    asyncio.run(
        scheduler_service._run_strategy_exposure(None, {"window_days": 30, "min_sample_threshold": 20})
    )
    assert fake_backfill["captured"], "应有入队调用"
    for p in fake_backfill["captured"]:
        assert p["run_mode"] == "experiment"
        assert p["disable_agent_review"] is True
        assert p["single_skill_id"] in {"chip-single-peak-breakout", "small-cap-thematic-momentum"}


def test_exposure_query_excludes_backtest(fake_backfill):
    """锁定历史 high-range bug：补跑只统计 formal+experiment，必须排除 backtest 合成回测。

    现由 exposure_sample_counts(run_mode=('formal','experiment')) 承接该口径。
    """
    asyncio.run(
        scheduler_service._run_strategy_exposure(None, {"window_days": 30, "min_sample_threshold": 20})
    )
    assert fake_backfill["calls"]["run_mode"] == ("formal", "experiment")
    assert fake_backfill["calls"]["window_days"] == 30


def test_exposure_skips_when_all_covered(monkeypatch):
    monkeypatch.setattr(
        "app.services.workbench_service.sync_skills",
        lambda db: [_FakeSkill("only-skill", "唯一策略", True)],
    )
    monkeypatch.setattr(
        "app.services.outcome_service.exposure_sample_counts",
        lambda db, window_days=30, run_mode=None: {"only-skill": 50},
    )
    captured = []
    monkeypatch.setattr(
        "app.services.workbench_service.enqueue_workbench_run",
        lambda db, payload: (captured.append(payload) or {"run": {"run_id": "r"}, "queued": True}),
    )
    result = asyncio.run(
        scheduler_service._run_strategy_exposure(None, {"window_days": 30, "min_sample_threshold": 20})
    )
    assert result["skipped"] is True
    assert captured == []
