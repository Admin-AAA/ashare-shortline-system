"""回归测试：L-086 三层毒区口径审计脚本的结构契约与可证伪性。

锁定四件事，防止未来改动悄悄把「口径审计」退化成永远输出 0 或永远输出赢的工具：

1. base/treat 两个 bank 的构造必须精确：base 只剥离三层毒区、treat 把三层
   前置到 phases 最前面（因为 ``strategy_runner`` 是「首个 when 命中即 break」，
   插在末尾会被更早的宽松软 cap 抢占 —— 这正是 L-086 自己发现并修复的陷阱）。
2. 前置毒区必须对既有 ``hard_veto`` 让位（``not (...)`` guard），否则软 cap 会
   架空核心风控。这是 L-086 明确否决 "raw 无 guard" 方案的理由，必须锁住。
3. ``poison_firing`` 只在已结算行上计 WR/avg，且对空命中返回 None 而不是除零。
4. ``counterfactual`` 是**有效探测器**：既能在毒区压中高分输家时给出正 Δ
   （可证伪性对照 A），也能在毒区只压到窗口外非竞争者时给出精确 0.000/0.0pp
   （可证伪性对照 B，即 canonical LIVE_FORWARD 层实测到的情形）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.engine.skill_loader import StrategySkill  # noqa: E402

SCRIPT_PATH = BACKEND_ROOT / "scripts" / "si_l086_caliber_audit.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("si_l086_caliber_audit", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_script_module()


def _skill(skill_id: str, contract: dict) -> StrategySkill:
    return StrategySkill(
        skill_id=skill_id,
        name=skill_id,
        version="0.0.1",
        path=Path(f"/tmp/{skill_id}"),
        manifest={},
        skill_frontmatter={},
        skill_body="",
        skill_hash=None,
        reference_files=[],
        contract=contract,
        contract_hash=None,
        enabled=True,
    )


def _phase(name: str, when: str, *, cap=None, hard_veto=False) -> dict:
    p: dict = {"name": name, "when": when, "hard_veto": hard_veto}
    if cap is not None:
        p["score_cap"] = cap
    return p


# --------------------------------------------------------------------------
# 1. base / treat bank 构造
# --------------------------------------------------------------------------
def test_strip_poison_removes_only_named_poison_phases(mod):
    contract = {
        "phases": [
            _phase("强势多头发散毒区", "dsa_trend_score == 30", cap=58),
            _phase("既有弱市软降权", "market_regime == '弱势'", cap=68),
            _phase("长周期RSI低位毒区", "rsi_24 > 0 and rsi_24 < 48", cap=58),
            _phase("既有硬否决", "x == 1", hard_veto=True),
            _phase("短线龙头低分降权", "shortline_leader_score > 0", cap=62),
        ]
    }
    stripped = mod._strip_poison(contract)
    names = [p["name"] for p in stripped["phases"]]
    assert names == ["既有弱市软降权", "既有硬否决"]
    # 不得就地修改输入
    assert len(contract["phases"]) == 5


def test_strip_poison_is_total_on_missing_or_non_list_phases(mod):
    # 缺少 phases 键 -> 不新增该键（只处理 list 型 phases）
    assert mod._strip_poison({}).get("phases") is None
    assert mod._strip_poison({"phases": None})["phases"] is None
    assert mod._strip_poison({"phases": "oops"})["phases"] == "oops"


def test_build_banks_base_has_no_poison_and_treat_prepends_in_order(mod):
    """treat 的三层必须严格按 POISON_ORDER 前置，且在既有 phase 之前。"""
    skills = {
        # chao 同属 S8/R7/L12 三层成员 -> 应被注入完整三层
        "chao-gu-yang-jia-skill": _skill(
            "chao-gu-yang-jia-skill",
            {"phases": [_phase("既有软降权", "market_regime == '弱势'", cap=68)]},
        ),
    }
    base, treat = mod.build_banks(skills)

    base_names = [p["name"] for p in base["chao-gu-yang-jia-skill"].contract["phases"]]
    assert base_names == ["既有软降权"]

    treat_phases = treat["chao-gu-yang-jia-skill"].contract["phases"]
    treat_names = [p["name"] for p in treat_phases]
    assert treat_names == mod.POISON_ORDER + ["既有软降权"]
    # cap 与 hard_veto 语义：三层一律 soft，不得变成整票否决
    for p in treat_phases[:3]:
        assert p["hard_veto"] is False
        assert p["score_cap"] == mod.POISON_DEF[p["name"]][1]
    assert [p["score_cap"] for p in treat_phases[:3]] == [58, 58, 62]


def test_build_banks_injects_only_layers_whose_member_set_contains_skill(mod):
    """small-cap 不在 S8 -> 不得被注入强势多头发散层；但在 R7/L12 -> 得两层。"""
    skills = {"small-cap-thematic-momentum": _skill("small-cap-thematic-momentum", {"phases": []})}
    base, treat = mod.build_banks(skills)
    assert base["small-cap-thematic-momentum"].contract["phases"] == []
    names = [p["name"] for p in treat["small-cap-thematic-momentum"].contract["phases"]]
    assert "强势多头发散毒区" not in names
    assert names == ["长周期RSI低位毒区", "短线龙头低分降权"]


def test_build_banks_does_not_mutate_input_contracts(mod):
    original = {"phases": [_phase("既有软降权", "a == 1", cap=68)]}
    skills = {"chao-gu-yang-jia-skill": _skill("chao-gu-yang-jia-skill", original)}
    _, treat = mod.build_banks(skills)
    # 输入对象仍是原样（deepcopy 生效）
    assert [p["name"] for p in original["phases"]] == ["既有软降权"]
    assert len(treat["chao-gu-yang-jia-skill"].contract["phases"]) == 4


# --------------------------------------------------------------------------
# 2. hard_veto 让位 guard
# --------------------------------------------------------------------------
def test_guarded_when_appends_not_guard_for_every_hard_veto(mod):
    contract = {
        "phases": [
            _phase("软降权", "market_regime == '弱势'", cap=68),
            _phase("硬否决A", "regime == '弱势'", hard_veto=True),
            _phase("硬否决B", "追高 == 1", hard_veto=True),
        ]
    }
    got = mod._guarded_when(contract, "dsa_trend_score == 30")
    assert got == "dsa_trend_score == 30 and not (regime == '弱势') and not (追高 == 1)"


def test_guarded_when_ignores_soft_phases_and_empty_when(mod):
    contract = {
        "phases": [
            _phase("软降权", "market_regime == '弱势'", cap=68),
            _phase("空硬否决", "", hard_veto=True),
            _phase("无when硬否决", "  ", hard_veto=True),
        ]
    }
    assert mod._guarded_when(contract, "rsi_24 > 0") == "rsi_24 > 0"


def test_guarded_when_tolerates_missing_phases(mod):
    assert mod._guarded_when({}, "x == 1") == "x == 1"
    assert mod._guarded_when({"phases": None}, "x == 1") == "x == 1"


# --------------------------------------------------------------------------
# 3. poison_firing
# --------------------------------------------------------------------------
def test_poison_firing_counts_settled_rows_and_computes_rates(mod):
    rows = [
        {"ret": -1.0, "f": {"dsa_trend_score": 30}},
        {"ret": -2.0, "f": {"dsa_trend_score": 30}},
        {"ret": 3.0, "f": {"dsa_trend_score": 30}},
        # 未结算 -> 必须被排除
        {"ret": None, "f": {"dsa_trend_score": 30}},
        {"ret": -9.0, "f": {"dsa_trend_score": 18}},
    ]
    out = mod.poison_firing(rows)["dsa_trend_score==30"]
    assert out["n"] == 3
    assert out["win_rate_pct"] == 33.3
    assert out["avg_net_pct"] == 0.0


def test_poison_firing_returns_none_rates_when_no_hit(mod):
    rows = [{"ret": -1.0, "f": {"dsa_trend_score": 18}}]
    out = mod.poison_firing(rows)["dsa_trend_score==30"]
    assert out == {"n": 0, "win_rate_pct": None, "avg_net_pct": None}


def test_poison_firing_guards_zero_valued_fields(mod):
    """rsi_24/leader 为 0 或缺失时必须不命中（生产缺失字段会被 _coerce 成 0）。"""
    rows = [
        {"ret": -1.0, "f": {"rsi_24": 0}},
        {"ret": -1.0, "f": {}},
        {"ret": -1.0, "f": {"shortline_leader_score": 0}},
        {"ret": -1.0, "f": {"shortline_leader_score": 35}},
    ]
    firing = mod.poison_firing(rows)
    assert firing["rsi_24 in (0,48)"]["n"] == 0
    assert firing["leader in (0,35]"]["n"] == 1


# --------------------------------------------------------------------------
# 4. counterfactual 的语义 + 可证伪性对照
# --------------------------------------------------------------------------
def test_counterfactual_skips_unsettled_and_counts_veto_separately(mod, monkeypatch):
    rows = [
        {"ret": 1.0, "f": {}, "brk": [{"strategy_id": "s1"}]},
        {"ret": -1.0, "f": {}, "brk": [{"strategy_id": "s1"}]},
        {"ret": None, "f": {}, "brk": [{"strategy_id": "s1"}]},  # 未结算 -> skip
        {"ret": 5.0, "f": {}, "brk": [{"strategy_id": "s1"}]},  # veto -> 不计入 n
    ]
    scores = {0: (90.0, False), 1: (80.0, False), 2: (99.0, True)}
    calls = {"i": 0}

    def fake_score_pair(row, bank):
        idx = calls["i"]
        calls["i"] += 1
        return scores[idx]

    monkeypatch.setattr(mod, "_score_pair", fake_score_pair)
    out = mod.counterfactual(rows, {})
    assert out["n"] == 2
    assert out["vetoed"] == 1


def test_counterfactual_windows_are_top_k_by_descending_score(mod, monkeypatch):
    rets = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, -50.0]
    rows = [{"ret": r, "f": {}, "brk": [{"strategy_id": "s1"}]} for r in rets]
    calls = {"i": 0}
    rets_iter = list(rets)

    def fake_score_pair(row, bank):
        i = calls["i"]
        calls["i"] += 1
        # 分高者收益高 —— 于是 top30(3/10) 应取 10/9/8
        return (float(rets_iter[i]), False)

    monkeypatch.setattr(mod, "_score_pair", fake_score_pair)
    out = mod.counterfactual(rows, {})
    # TOP_FRACTIONS: top_half=0.5 -> k=5, top40=0.4 -> k=4, top30=0.3 -> k=3
    assert out["windows"]["top_half"]["n"] == 5
    assert out["windows"]["top40"]["n"] == 4
    assert out["windows"]["top30"]["n"] == 3
    # 分高者收益高 -> top3 = 10,9,8
    assert out["windows"]["top30"]["avg_net_pct"] == 9.0
    assert out["windows"]["top30"]["win_rate_pct"] == 100.0


def test_counterfactual_detects_effect_when_poison_caps_high_scoring_losers(mod, monkeypatch):
    """可证伪性对照 A：毒区若压中高分输家，Δ 必须为正 —— 审计不是永远输出 0。"""
    # 10 行：前 5 只亏损、后 5 只盈利
    rows = [
        {"ret": -1.0 if i < 5 else 1.0, "f": {}, "brk": [{"strategy_id": "s1"}], "_i": i}
        for i in range(10)
    ]

    def base_score(row, bank):
        # 名次与收益无关：把 5 只亏损票排在前面 -> base top3 全为亏损票
        return (float(100 - row["_i"]), False)

    monkeypatch.setattr(mod, "_score_pair", base_score)
    base = mod.counterfactual(rows, {})
    assert base["windows"]["top30"]["win_rate_pct"] == 0.0
    assert base["windows"]["top30"]["avg_net_pct"] == -1.0

    def treat_score(row, bank):
        # 反转为按收益排序 -> treat top3 全为盈利票
        return (float(row["_i"]), False)

    monkeypatch.setattr(mod, "_score_pair", treat_score)
    treat = mod.counterfactual(rows, {})
    assert treat["windows"]["top30"]["win_rate_pct"] == 100.0
    assert treat["windows"]["top30"]["avg_net_pct"] == 1.0
    assert (
        treat["windows"]["top30"]["avg_net_pct"] - base["windows"]["top30"]["avg_net_pct"]
    ) == pytest.approx(2.0)


def test_counterfactual_zero_delta_when_capping_only_non_contenders(mod, monkeypatch):
    """可证伪性对照 B：毒区只压到窗口外非竞争者时，Δ 必须精确为 0。

    这正是 canonical LIVE_FORWARD 层实测到的情形：三层 never 移动任何一只票
    跨过 top-half/top40/top30 边界 -> base 与 treat 逐窗完全相等。
    """
    rows = [{"ret": float(i), "f": {}, "brk": [{"strategy_id": "s1"}], "_i": i} for i in range(10)]

    def score(row, bank):
        return (float(100 - row["_i"]), False)  # 严格递减，名次固定

    monkeypatch.setattr(mod, "_score_pair", score)
    base = mod.counterfactual(rows, {})

    # 只给排名后的 5 只票降权（严格在最大窗口 top_half=5 之外），名次必不变
    def capped(row, bank):
        return (float(100 - row["_i"]) if row["_i"] < 5 else 0.0, False)

    monkeypatch.setattr(mod, "_score_pair", capped)
    treat = mod.counterfactual(rows, {})

    for w in ("top_half", "top40", "top30"):
        assert treat["windows"][w] == base["windows"][w]
