"""D 项 canonical 基线口径守卫（L-002 / L-094）单元测试。

背景：`scripts/self_check.py` 的 D 项三块基线（`WIN_RATE_BASELINE` /
`MAX_LOSS_BASELINE` / `PL_RATIO_BASELINE`）原先硬编码为 `None`，导致
`check_d_rule_coverage` 与 `_forward_verification_flag` 永久短路——诊断能力静默失效。
本轮把它们接到 self-improve state 的 `canonical_production`，并加口径守卫：
只有「当前代码重放未修改 production」+「canonical live_forward 口径」+「重放身份一致」
的基线才允许算 Δ；任何跨 evidence_class 汇总或身份漂移都必须被拒绝（L-094）。

本测试全为纯函数/临时文件，不碰 DB。
"""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_canonical_baseline", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def _good_state() -> dict:
    """一份通过全部守卫的最小 state 快照（形态与 self-improve-state.json 一致）。"""
    return {
        "canonical_production": {
            "commit": "0df3bbf",
            "replay_code_revision": "0df3bbf",
            "provisional": True,
            "provisional_reason": "canonical 前瞻 n=9 (<100)",
            "selection_policy": {"policy_id": "production-selection", "version": 3},
            "metrics": {
                "source": "champion_replay",
                "caliber": (
                    "canonical LIVE_FORWARD (official_sample_conditions), 20cm, "
                    "net cost 0.07%/trade, horizon=5, production stop -4.5%"
                ),
                "population_all": {
                    "n": 9, "trade_days": 4, "win_rate_pct": 55.6,
                    "avg_net_return_pct": -0.839, "profit_factor": 0.68,
                    "max_loss_pct": -7.43,
                },
                "population_clean_ge_72": {
                    "n": 5, "win_rate_pct": 40.0, "profit_factor": 0.21,
                    "max_loss_pct": -6.2,
                },
                "population_legacy_wide": {
                    "n": 368, "win_rate_pct": 42.1,
                },
            },
        }
    }


def _write(tmp_path: Path, state: dict | object) -> str:
    p = tmp_path / "self-improve-state.json"
    if isinstance(state, str):
        p.write_text(state)
    else:
        p.write_text(json.dumps(state, ensure_ascii=False))
    return str(p)


# ── 通过路径 ────────────────────────────────────────────────────────────────

def test_good_state_passes_and_exposes_baselines(tmp_path):
    res = sc.load_canonical_baseline(_write(tmp_path, _good_state()))
    assert res["ok"], res["reason"]
    b = res["baseline"]
    assert b["win_rate_pct"] == 55.6
    assert b["max_loss_pct"] == -7.43
    assert b["profit_factor"] == 0.68
    assert b["n"] == 9
    assert b["source"] == "champion_replay"
    assert b["policy_version"] == 3
    # 同口径辅助块必须暴露出来，供 check_d_rule_coverage 用 legacy 口径
    assert b["legacy_wide"]["win_rate_pct"] == 42.1
    assert b["clean_ge_72"]["win_rate_pct"] == 40.0


def test_provisional_and_revision_are_soft_warnings(tmp_path):
    """provisional / 代码修订不一致只提示、不阻断（否则永远无法用基线）。"""
    res = sc.load_canonical_baseline(_write(tmp_path, _good_state()))
    assert res["ok"]
    joined = " ".join(res["warnings"])
    assert "provisional" in joined
    assert "0df3bbf" in joined


# ── 硬门禁：任一不过都必须拒绝，且不得返回 baseline ─────────────────────────

def test_missing_state_file_rejected(tmp_path):
    res = sc.load_canonical_baseline(str(tmp_path / "nope.json"))
    assert not res["ok"]
    assert res["baseline"] is None
    assert "不存在" in res["reason"]


def test_unparsable_state_rejected(tmp_path):
    res = sc.load_canonical_baseline(_write(tmp_path, "{not json"))
    assert not res["ok"]
    assert res["baseline"] is None


def test_missing_canonical_production_rejected(tmp_path):
    res = sc.load_canonical_baseline(_write(tmp_path, {"schema_version": 4}))
    assert not res["ok"]
    assert "canonical_production" in res["reason"]


def test_source_must_be_champion_replay(tmp_path):
    """challenger / research 重构（L-088 金标准禁用）不得作基线。"""
    st = _good_state()
    st["canonical_production"]["metrics"]["source"] = "challenger"
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert not res["ok"]
    assert "champion_replay" in res["reason"]
    assert res["baseline"] is None


@pytest.mark.parametrize("caliber", [
    "historical_reconstruction ALL evidence_class pooled",
    "canonical backtest run (historical_reconstruction)",
    "live_forward official_sample_conditions only",
    "",
])
def test_caliber_must_be_canonical_live_forward(tmp_path, caliber):
    """L-094：跨 evidence_class 汇总（如 ALL）一律判污染，必须拒绝。"""
    st = _good_state()
    st["canonical_production"]["metrics"]["caliber"] = caliber
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert not res["ok"]
    assert res["baseline"] is None
    assert "口径" in res["reason"]


@pytest.mark.parametrize("drop", ["win_rate_pct", "max_loss_pct", "profit_factor", "n"])
def test_incomplete_population_all_rejected(tmp_path, drop):
    st = _good_state()
    st["canonical_production"]["metrics"]["population_all"].pop(drop)
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert not res["ok"]
    assert res["baseline"] is None


def test_zero_sample_rejected(tmp_path):
    """n=0 的「基线」不是基线，拒绝（否则 Δ 会对着空样本算）。"""
    st = _good_state()
    st["canonical_production"]["metrics"]["population_all"]["n"] = 0
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert not res["ok"]
    assert res["baseline"] is None


def test_non_numeric_metric_rejected(tmp_path):
    """bool/字符串混进数值位必须当缺失处理（None 不是 0）。"""
    st = _good_state()
    st["canonical_production"]["metrics"]["population_all"]["win_rate_pct"] = True
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert not res["ok"]
    assert res["baseline"] is None


# ── 守卫 5：重放身份漂移 ────────────────────────────────────────────────────

def test_replay_identity_drift_rejected(tmp_path):
    """基线重放身份与当前生产退出状态机不一致 → 口径脱钩，禁止算 Δ。"""
    st = _good_state()
    st["canonical_production"]["replay_identity"] = {
        "horizon": 3, "production_stop_loss_pct": -4.5, "net_cost_pct": 0.07,
    }
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert not res["ok"]
    assert "重放身份" in res["reason"]
    assert res["baseline"] is None


def test_replay_identity_match_accepted(tmp_path):
    st = _good_state()
    st["canonical_production"]["replay_identity"] = deepcopy(sc.PRODUCTION_REPLAY_IDENTITY)
    res = sc.load_canonical_baseline(_write(tmp_path, st))
    assert res["ok"], res["reason"]


def test_production_replay_identity_matches_rebuild_script():
    """守卫里的重放身份必须与基线重放脚本的常量一致，否则守卫形同虚设。"""
    src = (_SCRIPTS.parents[1] / "backend" / "scripts"
           / "si_champion_baseline_rebuild.py").read_text()
    assert "NET_COST_PCT = 0.07" in src
    assert "HORIZON = 5" in src
    # 止损沿用生产默认常量，不在脚本里硬编码；守卫必须与同一个源对齐
    assert "DEFAULT_STOP_LOSS_PCT" in src
    from app.services.outcome_simulation import DEFAULT_STOP_LOSS_PCT  # type: ignore

    assert sc.PRODUCTION_REPLAY_IDENTITY["net_cost_pct"] == sc.ADDITIONAL_COST_PCT
    assert float(sc.PRODUCTION_REPLAY_IDENTITY["production_stop_loss_pct"]) == float(
        DEFAULT_STOP_LOSS_PCT)


def test_status_printer_never_raises_and_reports_reason(tmp_path, capsys):
    res = sc.print_canonical_baseline_status(
        sc.load_canonical_baseline(_write(tmp_path, {"schema_version": 4})))
    out = capsys.readouterr().out
    assert res["ok"] is False
    assert "口径守卫未通过" in out
