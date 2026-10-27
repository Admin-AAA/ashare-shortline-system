"""每策略每日新推荐上限（默认 5）：超限取前 N、未超不变、配置生效、审计留痕、仅实盘路径生效。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import daily_pick_cap as dpc
from app.services import midterm_service as ms


@pytest.fixture(autouse=True)
def _clock_before_entry(monkeypatch):
    """固定在 2026-10-26 18:00（信号日盘后、次日开盘前）。

    实盘封签（midterm_live_seal）过了 D+1 开盘就不再覆盖已落盘批次；不固定时钟，本文件用 2026-10-26 当信号日的
    用例在 10-27 开盘后就会因「不覆盖」而失败（时间炸弹，与每日上限逻辑无关）。"""
    import datetime as _dt

    from app.services import midterm_live_seal as seal

    monkeypatch.setattr(seal, "now_cn", lambda: _dt.datetime(2026, 10, 26, 18, 0, tzinfo=seal.CN_TZ))


def _picks(n: int) -> list[dict]:
    return [{"code": f"{i:06d}", "name": f"s{i}", "score": 100 - i} for i in range(1, n + 1)]


def _set_cap(monkeypatch, cap: int) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(max_daily_picks_per_strategy=cap))


@pytest.fixture(autouse=True)
def _seal_open(monkeypatch):
    # 测试里写死的信号日会随真实时间过 D+1 09:15 封签；这里只测上限，固定为未封签。
    monkeypatch.setattr("app.services.midterm_live_seal.past_deadline", lambda *_a, **_k: False)


def test_default_cap_is_five_in_settings():
    from app.core.config import Settings

    assert Settings.model_fields["max_daily_picks_per_strategy"].default == 5


def test_over_cap_keeps_top5_in_given_order_and_audits():
    kept, audit = dpc.cap_ordered_picks(_picks(8), 5)
    assert [p["code"] for p in kept] == ["000001", "000002", "000003", "000004", "000005"]
    assert audit["truncated_count"] == 3
    assert audit["truncated_codes"] == ["000006", "000007", "000008"]
    assert audit["cap"] == 5 and audit["candidates_before_cap"] == 8


def test_ties_extend_all_same_score_kept():
    picks = [{"code": f"{i:06d}", "score": 100} for i in range(1, 11)]
    kept, audit = dpc.cap_ordered_picks(picks, 5)
    assert len(kept) == 10 and audit is None


def test_top5_then_ties_with_fifth():
    scores = [100, 100, 90, 90, 90, 80, 70, 60, 60, 50]
    picks = [{"code": f"{i:06d}", "score": s} for i, s in enumerate(scores, 1)]
    kept, audit = dpc.cap_ordered_picks(picks, 5)
    assert [p["score"] for p in kept] == [100, 100, 90, 90, 90]
    assert audit["truncated_count"] == 5 and audit["cap_unit"] == "top_n_with_ties"
    # 第 5 名后面还有同分的 → 一起带上
    scores = [100, 100, 90, 90, 90, 90, 80]
    picks = [{"code": f"{i:06d}", "score": s} for i, s in enumerate(scores, 1)]
    kept, audit = dpc.cap_ordered_picks(picks, 5)
    assert [p["score"] for p in kept] == [100, 100, 90, 90, 90, 90]
    assert audit["truncated_codes"] == ["000007"]


def test_missing_score_falls_back_to_top_n():
    kept, audit = dpc.cap_ordered_picks([{"code": f"{i:06d}"} for i in range(8)], 5)
    assert len(kept) == 5 and audit["truncated_count"] == 3


@pytest.mark.parametrize("n", [0, 3, 5])
def test_not_over_cap_unchanged(n):
    kept, audit = dpc.cap_ordered_picks(_picks(n), 5)
    assert kept == _picks(n) and audit is None


def test_cap_comes_from_config_and_zero_disables(monkeypatch):
    _set_cap(monkeypatch, 2)
    assert dpc.daily_pick_cap() == 2
    kept, audit = dpc.cap_ordered_picks(_picks(4))
    assert len(kept) == 2 and audit["cap"] == 2
    _set_cap(monkeypatch, 0)
    kept, audit = dpc.cap_ordered_picks(_picks(40))
    assert len(kept) == 40 and audit is None


# ---- 中长线落盘 ----

def test_save_picks_only_caps_when_live_flag_on(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    _set_cap(monkeypatch, 5)
    assert ms._save_picks("mid-x", "2026-10-26", _picks(8)) == 8  # 默认（回放/历史口径）不截断
    doc = json.loads((tmp_path / "2026-10-26_mid-x.json").read_text())
    assert len(doc["picks"]) == 8 and "daily_cap" not in doc
    assert ms._save_picks("mid-x", "2026-10-27", _picks(8), True) == 5
    doc = json.loads((tmp_path / "2026-10-27_mid-x.json").read_text())
    assert [p["code"] for p in doc["picks"]] == [f"{i:06d}" for i in range(1, 6)]
    assert doc["daily_cap"]["truncated_codes"] == ["000006", "000007", "000008"]
    assert ms._save_picks("mid-x", "2026-10-28", _picks(3), True) == 3
    assert "daily_cap" not in json.loads((tmp_path / "2026-10-28_mid-x.json").read_text())


def test_generate_picks_end_to_end_cap_only_when_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    _set_cap(monkeypatch, 5)
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: {"000001"})  # 已持有的先剔除，再取前 5
    monkeypatch.setattr(ms, "earnings_accel_candidates", lambda db, d: _picks(9))
    res = ms.generate_picks(None, "2026-10-26", force=True, strategy_ids=["mid-earnings-accel"])
    assert res == {"mid-earnings-accel": 8}  # 不带开关：不截断（rebuild_replay_picks 即此口径）
    res = ms.generate_picks(None, "2026-10-26", force=True, strategy_ids=["mid-earnings-accel"], apply_daily_cap=True)
    assert res == {"mid-earnings-accel": 5}
    doc = json.loads((tmp_path / "2026-10-26_mid-earnings-accel.json").read_text())
    assert [p["code"] for p in doc["picks"]] == ["000002", "000003", "000004", "000005", "000006"]
    assert doc["daily_cap"]["truncated_count"] == 3


def test_block_trade_picks_capped_when_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    _set_cap(monkeypatch, 5)
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: set())
    import app.services.midterm_block_trade as bt

    monkeypatch.setattr(bt, "block_trade_candidates", lambda db, d: _picks(7))
    sid = "mid-block-trade-premium"
    assert ms._block_trade_picks(None, sid, ms.STRATEGIES[sid], "2026-10-26") == 7
    assert ms._block_trade_picks(None, sid, ms.STRATEGIES[sid], "2026-10-26", True) == 5


def test_live_callers_pass_flag():
    import inspect

    from app.api import midterm as api_mod
    from app.services import scheduler_service as ss

    assert "apply_daily_cap=True" in inspect.getsource(api_mod)
    src = inspect.getsource(ss)
    assert "_mid_generate_picks(db, trade_date, apply_daily_cap=True)" in src
    assert "generate_block_trade_picks(db, trade_date, apply_daily_cap=True)" in src


# ---- 短线卡片 ----

def _cards(n: int, sid: str = "s1") -> list[dict]:
    return [
        {"code": f"{i:06d}", "final_score": 90 - i, "strategy_signals": [{"strategy_id": sid, "score": 80}]}
        for i in range(1, n + 1)
    ]


def test_shortline_single_strategy_over_cap_truncates_and_marks_audit():
    formal = _cards(7)
    scored = [dict(c) for c in formal] + [{"code": "999999"}]
    kept, audit = dpc.cap_cards_per_strategy(formal, strategy_ids=["s1"], cap=5, already_counts={"s1": 0}, scored_cards=scored)
    assert [c["code"] for c in kept] == [f"{i:06d}" for i in range(1, 6)]
    assert audit["truncated_codes"] == ["000006", "000007"] and audit["truncated_count"] == 2
    dropped_scored = [c for c in scored if c["code"] in ("000006", "000007")]
    assert all(c["policy_drop_owner"] == "daily_strategy_cap" for c in dropped_scored)
    assert all(c["policy_verdict_steps"][-1]["rule_id"] == "daily_strategy_cap" for c in dropped_scored)
    assert "policy_drop_owner" not in scored[0]


def test_shortline_ties_extend_by_portfolio_rank_score():
    scores = [95, 95, 95, 95, 95, 95, 95, 88, 80]  # 7 张同为 95 分
    cards = [{"code": f"{i:06d}", "portfolio_rank_score": s, "final_score": 99} for i, s in enumerate(scores, 1)]
    kept, audit = dpc.cap_cards_per_strategy(cards, strategy_ids=["s1"], cap=5, already_counts={"s1": 0})
    assert [c["code"] for c in kept] == [f"{i:06d}" for i in range(1, 8)]  # 前 5 + 同为 95 的 2 张
    assert audit["truncated_codes"] == ["000008", "000009"]
    kept, _ = dpc.cap_cards_per_strategy(cards, strategy_ids=["s1"], cap=1, already_counts={"s1": 0})
    assert len(kept) == 7
    # 同日已推过 4 只：还剩 1 个名额，第 5 只 95 分，同分的一起顺延
    kept, _ = dpc.cap_cards_per_strategy(cards, strategy_ids=["s1"], cap=5, already_counts={"s1": 4})
    assert len(kept) == 7
    # 之前 run 已满额：本次不再顺延
    kept, _ = dpc.cap_cards_per_strategy(cards, strategy_ids=["s1"], cap=5, already_counts={"s1": 5})
    assert kept == []


def test_shortline_under_cap_unchanged():
    formal = _cards(3)
    kept, audit = dpc.cap_cards_per_strategy(formal, strategy_ids=["s1"], cap=5, already_counts={"s1": 0})
    assert kept == formal and audit is None


def test_shortline_counts_same_day_already_recommended():
    kept, audit = dpc.cap_cards_per_strategy(_cards(4), strategy_ids=["s1"], cap=5, already_counts={"s1": 3})
    assert [c["code"] for c in kept] == ["000001", "000002"]
    assert audit["truncated_count"] == 2
    kept, audit = dpc.cap_cards_per_strategy(_cards(2), strategy_ids=["s1"], cap=5, already_counts={"s1": 5})
    assert kept == [] and audit["truncated_count"] == 2


def test_shortline_multi_strategy_drops_only_when_all_supporters_full():
    cards = [
        {"code": "000001", "strategy_signals": [{"strategy_id": "a"}]},
        {"code": "000002", "strategy_signals": [{"strategy_id": "a"}]},
        {"code": "000003", "strategy_signals": [{"strategy_id": "a"}, {"strategy_id": "b"}]},  # a 满额但 b 未满 → 保留
        {"code": "000004", "strategy_signals": [{"strategy_id": "a"}]},  # a 已满 → 丢
        {"code": "000005", "strategy_signals": [{"strategy_id": "b", "hard_veto": True}, {"strategy_id": "a"}]},  # 仅 a 有效 → 丢
    ]
    kept, audit = dpc.cap_cards_per_strategy(cards, strategy_ids=["a", "b"], cap=2)
    assert [c["code"] for c in kept] == ["000001", "000002", "000003"]
    # cap=2：a 在 000001/000002 后已满；000003 因 b 未满保留；其后 a 相关全部被丢
    assert audit["truncated_codes"] == ["000004", "000005"]


def test_is_live_formal_run_gate():
    def db_with(row):
        return SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(first=lambda: row))

    assert dpc.is_live_formal_run(db_with(("formal", "live_forward", None)), "r1")
    assert dpc.is_live_formal_run(db_with(("formal", None, None)), "r1")
    assert not dpc.is_live_formal_run(db_with(None), "backtest-rule-replay-2026-01-01")  # 无 run 行：回测回放/单股分析
    assert not dpc.is_live_formal_run(db_with(("experiment", "live_forward", None)), "r1")
    assert not dpc.is_live_formal_run(db_with(("formal", "historical_reconstruction", None)), "r1")
    assert not dpc.is_live_formal_run(db_with(("formal", "live_forward", "recon-v1-x")), "r1")
