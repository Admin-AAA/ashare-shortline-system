"""``Signal`` 契约「零变化」等价对拍（任务 3.1，需求 4.4 / 1.1）。

核心验收：把 ``run_strategy`` 的返回类型收敛为 typed ``Signal`` 后，其在**相同输入**下
产出的 dict 必须与迁移**前**逐字节一致——键集合、键顺序、每个键的值都不变，
``aggregator`` 及任何 ``signal["xxx"]`` 访问零感知。

对拍方式（内嵌期望键序 / 值）：
- 直接构造 ``StrategySkill``（frozen dataclass）+ 手写内联 contract，**不从磁盘 / yaml
  加载**，覆盖 ``run_strategy`` 的多条 return 路径；
- 逐场景把 ``run_strategy(...)`` 的返回 dict 与手算的期望 ``Signal`` **整体相等**比对，
  并单独锁定「运行时是普通 dict」「键序 == 规范键序」两条结构不变量。

纯 in-process：只依赖 ``strategy_runner`` / ``evaluator`` / ``skill_loader``（后者仅 import
``yaml`` 解析，本测试不触其解析路径），不吃 DB / 网络。本环境用
``ASHARE_DATABASE_URL=sqlite`` 让 conftest 的生产库锁 fixture 空转即可运行（无需
``production_mysql`` 标注）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.signal import SIGNAL_FIELDS, Signal
from app.engine.skill_loader import StrategySkill
from app.engine.strategy_runner import run_strategy

# ── 内联 contract：覆盖 universe / prefilter / phases / scoring / setups / risk ──
# 表达式字段一律取自 evaluator.ALLOWED_NAMES（否则 UnsafeExpression→求值 False）。
_BASE_CONTRACT: dict = {
    "id": "equiv-test-strategy",
    "name": "等价测试策略",
    "weight": 1.2,
    "meta": {"strategy_independence": 0.9},
    "universe": {
        "include": ["amount_yi >= 1"],
        "exclude": ["is_st == true"],
    },
    "prefilter": {"market": {"exclude": ["avg_cost > 200"]}},
    "phases": [
        {"when": "pct >= 5", "name": "上涨", "score_cap": 90, "hard_veto": False, "risk_flags": ["涨幅偏高"]},
    ],
    "scoring": {
        "base": 50,
        "score_fields": [
            {"field": "pct", "multiplier": 2.0, "cap": 20, "reason": "涨幅贡献"},
        ],
        "rules": [
            {"when": "vol5_20 >= 1", "delta": 10, "reason": "量比放大"},
        ],
    },
    "setups": [
        {"when": "breakout20 == true", "name": "突破买点", "score_delta": 6,
         "entry_mode": "next_open", "entry_rule": "突破确认后进场", "invalid_rule": "跌破 MA20"},
    ],
    "risk": {
        "rules": [
            {"when": "bias_ma20 >= 15", "flag": "乖离率偏高", "level": "high"},
        ],
    },
    "require_setup": False,
}

# require_setup 变体：其余规则一致，仅打开 require_setup（覆盖「未命中 setup → 硬否决」路径）。
_REQUIRE_SETUP_CONTRACT: dict = {**_BASE_CONTRACT, "require_setup": True}

_CONTRACT_HASH = "0123456789abcdef0123456789abcdef"  # 只用到 [:8] = "01234567"
_VERSION_ID = "equiv-test-strategy:1.0.0:01234567"


def _make_skill(contract: dict) -> StrategySkill:
    """直接实例化 frozen ``StrategySkill``，绕开磁盘 / yaml 加载。"""
    return StrategySkill(
        skill_id="equiv-test-strategy",
        name="等价测试策略",
        version="1.0.0",
        path=Path("/nonexistent/equiv-test-strategy"),  # run_strategy 不访问 path
        manifest={},
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        reference_files=[],
        contract=contract,
        contract_hash=_CONTRACT_HASH,
        enabled=True,
    )


def _full_quality(**extra) -> dict:
    """样本 / 完整度 / 证据均满格（coverage_cap 保持 100，不触发降权）。"""
    base = {
        "code": "600000",
        "name": "测试股",
        "market_sample_count": 200,
        "data_completeness_score": 100,
        "evidence_quality_score": 100,
    }
    base.update(extra)
    return base


# ── 五个场景：每个覆盖一条 run_strategy return 路径，附手算期望 Signal ──────────
_SCENARIOS: list[dict] = [
    # 路径 1：universe 否决（include 未过 → 提前 return _signal(score=0, "排除", ...)）
    {
        "name": "universe_veto_include_fail",
        "contract": _BASE_CONTRACT,
        "stock": _full_quality(amount_yi=0.5, pct=8, breakout20=True),
        "main_industries": [],
        "skip_universe_check": False,
        "expected": {
            "strategy_id": "equiv-test-strategy",
            "strategy_name": "等价测试策略",
            "version_id": _VERSION_ID,
            "strategy_independence": 0.9,
            "weight": 1.2,
            "phase": "排除",
            "score": 0,
            "score_cap": 100.0,
            "confidence": 0.2,
            "setup_match": "",
            "entry_rule": "",
            "invalid_rule": "",
            "entry_mode": "target_touch",
            "setup_quality": 50.0,
            "hard_veto": True,
            "veto_reason": "不在策略股票池",
            "risk_flags": ["不符合股票池"],
            "evidence": [],
        },
    },
    # 路径 2：正常打分（phase 命中 + score_fields + rules + setup 命中 + high 风险扣分）
    {
        "name": "full_scoring_phase_setup_risk",
        "contract": _BASE_CONTRACT,
        "stock": _full_quality(
            amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5,
            breakout20=True, bias_ma20=20,
        ),
        "main_industries": [],
        "skip_universe_check": False,
        "expected": {
            "strategy_id": "equiv-test-strategy",
            "strategy_name": "等价测试策略",
            "version_id": _VERSION_ID,
            "strategy_independence": 0.9,
            "weight": 1.2,
            "phase": "上涨",
            "score": 70.0,
            "score_cap": 90.0,
            "confidence": 0.7,
            "setup_match": "突破买点",
            "entry_rule": "突破确认后进场",
            "invalid_rule": "跌破 MA20",
            "entry_mode": "next_open",
            "setup_quality": 66.0,
            "hard_veto": False,
            "veto_reason": "",
            "risk_flags": ["涨幅偏高", "乖离率偏高"],
            "evidence": ["阶段命中: 上涨", "涨幅贡献: 16.0", "量比放大", "买点命中: 突破买点"],
        },
    },
    # 路径 3：require_setup 否决（未命中任何 setup + require_setup=True → hard_veto）
    {
        "name": "require_setup_veto",
        "contract": _REQUIRE_SETUP_CONTRACT,
        "stock": _full_quality(
            amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5,
            breakout20=False, bias_ma20=5,
        ),
        "main_industries": [],
        "skip_universe_check": False,
        "expected": {
            "strategy_id": "equiv-test-strategy",
            "strategy_name": "等价测试策略",
            "version_id": _VERSION_ID,
            "strategy_independence": 0.9,
            "weight": 1.2,
            "phase": "上涨",
            "score": 76.0,
            "score_cap": 90.0,
            "confidence": 0.76,
            "setup_match": "",
            "entry_rule": "",
            "invalid_rule": "",
            "entry_mode": "target_touch",
            "setup_quality": 50.0,
            "hard_veto": True,
            "veto_reason": "未命中任何入场 setup（策略要求 require_setup）",
            "risk_flags": ["涨幅偏高"],
            "evidence": ["阶段命中: 上涨", "涨幅贡献: 16.0", "量比放大"],
        },
    },
    # 路径 4：coverage cap 降权（低样本 → cap=68<82 → score_cap 收紧 + 追加风险/证据）
    {
        "name": "coverage_cap_downgrade",
        "contract": _BASE_CONTRACT,
        "stock": _full_quality(
            amount_yi=5, is_st=False, avg_cost=50, pct=8, vol5_20=1.5,
            breakout20=True, bias_ma20=5,
            market_sample_count=10,  # <20 → coverage_cap=68
        ),
        "main_industries": [],
        "skip_universe_check": False,
        "expected": {
            "strategy_id": "equiv-test-strategy",
            "strategy_name": "等价测试策略",
            "version_id": _VERSION_ID,
            "strategy_independence": 0.9,
            "weight": 1.2,
            "phase": "上涨",
            "score": 68.0,
            "score_cap": 68.0,
            "confidence": 0.68,
            "setup_match": "突破买点",
            "entry_rule": "突破确认后进场",
            "invalid_rule": "跌破 MA20",
            "entry_mode": "next_open",
            "setup_quality": 66.0,
            "hard_veto": False,
            "veto_reason": "",
            "risk_flags": ["涨幅偏高", "样本或证据覆盖不足，评分已降权"],
            "evidence": [
                "阶段命中: 上涨", "涨幅贡献: 16.0", "量比放大", "买点命中: 突破买点",
                "诚实性上限: 68.0",
            ],
        },
    },
    # 路径 5：skip_universe_check=True（跳过 universe 否决）+ 无 phase / 无 setup 命中
    {
        "name": "skip_universe_no_phase_no_setup",
        "contract": _BASE_CONTRACT,
        "stock": _full_quality(
            amount_yi=0.5, is_st=False, pct=3, vol5_20=0.5,
            breakout20=False, bias_ma20=5,
        ),
        "main_industries": [],
        "skip_universe_check": True,
        "expected": {
            "strategy_id": "equiv-test-strategy",
            "strategy_name": "等价测试策略",
            "version_id": _VERSION_ID,
            "strategy_independence": 0.9,
            "weight": 1.2,
            "phase": "未分类",
            "score": 56.0,
            "score_cap": 100.0,
            "confidence": 0.56,
            "setup_match": "",
            "entry_rule": "",
            "invalid_rule": "",
            "entry_mode": "target_touch",
            "setup_quality": 50.0,
            "hard_veto": False,
            "veto_reason": "",
            "risk_flags": [],
            "evidence": ["涨幅贡献: 6.0"],
        },
    },
]


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s["name"])
def test_run_strategy_signal_matches_canonical_contract(scenario):
    """逐字段对拍：run_strategy 返回值与手算的当前契约完全一致。"""
    skill = _make_skill(scenario["contract"])

    result = run_strategy(
        skill,
        dict(scenario["stock"]),
        main_industries=scenario["main_industries"],
        skip_universe_check=scenario["skip_universe_check"],
    )

    # 运行时仍是普通 dict（TypedDict 不改变运行时类型），下游 signal["xxx"] 零感知。
    assert type(result) is dict
    # 键集合 + 键顺序锁死为规范键序（新增/删除/改序都会在此被抓到）。
    assert list(result.keys()) == list(SIGNAL_FIELDS), (
        f"[{scenario['name']}] 键序漂移：{list(result.keys())} vs {list(SIGNAL_FIELDS)}"
    )
    # 整体逐字段等价（值层零变化）。
    assert result == scenario["expected"], f"[{scenario['name']}] 字段值与期望不一致"


def test_signal_typeddict_matches_runtime_keys():
    """结构不变量：Signal 的注解字段 == run_strategy 运行时产出的键（防两处漂移）。"""
    skill = _make_skill(_BASE_CONTRACT)
    result = run_strategy(skill, _full_quality(amount_yi=5, pct=8), main_industries=[])

    assert list(SIGNAL_FIELDS) == list(Signal.__annotations__.keys())
    assert list(result.keys()) == list(SIGNAL_FIELDS)
    assert len(SIGNAL_FIELDS) == 18
