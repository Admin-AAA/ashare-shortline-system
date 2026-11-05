"""出票前按策略检查依赖数据（strategy_data_readiness）：判法、依赖清单完整、中长线/短线接入、出票检查读得到原因。"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services import strategy_data_readiness as sdr

OPEN = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE trade_calendar (cal_date TEXT, is_open INT)"))
        for d in OPEN:
            c.execute(text("INSERT INTO trade_calendar VALUES (:d, 1)"), {"d": d})
        for t in ("daily_kbars", "daily_basic", "capital_flow", "stock_quotes", "concept_sector",
                  "concept_sector_stock", "limit_up_board", "dragon_tiger"):
            c.execute(text(f"CREATE TABLE {t} (code TEXT, trade_date TEXT)"))
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _fill(db, table: str, day: str, n: int) -> None:
    db.execute(text(f"INSERT INTO {table} (code, trade_date) VALUES " + ",".join(f"('{i:06d}', '{day}')" for i in range(n))))
    db.commit()


# ---------------------------------------------------------------- 判法
def test_fresh_table_ok_and_stale_table_reason(db):
    for d in OPEN[-6:]:
        _fill(db, "daily_kbars", d, 100)
        _fill(db, "capital_flow", d, 100) if d <= "2026-10-23" else None
    k = sdr.check_db_dataset(db, "daily_kbars", "2026-10-27", 0, OPEN)
    assert k["ok"] and k["latest"] == "2026-10-27" and k["latest_count"] == 100
    cf = sdr.check_db_dataset(db, "capital_flow", "2026-10-27", 0, OPEN)
    assert not cf["ok"] and cf["lag_trade_days"] == 2
    assert cf["reason"] == "资金流向 最新 10-23，应为 10-27"


def test_allowed_lag_one_trade_day(db):
    _fill(db, "daily_basic", "2026-10-26", 10)
    assert sdr.check_db_dataset(db, "daily_basic", "2026-10-27", 1, OPEN)["ok"]
    r = sdr.check_db_dataset(db, "daily_basic", "2026-10-27", 0, OPEN)
    assert not r["ok"] and "最新 10-26，应为 10-27" in r["reason"]
    # 晚 2 个交易日（10-23 → 10-27，中间隔 10-26）超过允许的 1
    db.execute(text("DELETE FROM daily_basic"))
    db.commit()
    _fill(db, "daily_basic", "2026-10-23", 10)
    r = sdr.check_db_dataset(db, "daily_basic", "2026-10-27", 1, OPEN)
    assert not r["ok"] and "最多可晚 1 个交易日" in r["reason"]


def test_only_data_on_or_before_as_of_counts(db):
    """历史日判断：比 as_of 新的数据不算（MAX(日期) WHERE 日期<=as_of）。"""
    _fill(db, "dragon_tiger", "2026-10-21", 5)
    _fill(db, "dragon_tiger", "2026-10-27", 5)
    r = sdr.check_db_dataset(db, "dragon_tiger", "2026-10-23", 0, OPEN)
    assert not r["ok"] and r["latest"] == "2026-10-21"


def test_partial_write_is_not_ready(db):
    for d in OPEN[-6:-1]:
        _fill(db, "capital_flow", d, 100)
    _fill(db, "capital_flow", "2026-10-27", 30)
    r = sdr.check_db_dataset(db, "capital_flow", "2026-10-27", 0, OPEN)
    assert not r["ok"] and "只写进 30 条，平时约 100 条" in r["reason"]
    # 涨停板条数天然波动，不做条数判断
    for d in OPEN[-6:-1]:
        _fill(db, "limit_up_board", d, 100)
    _fill(db, "limit_up_board", "2026-10-27", 5)
    assert sdr.check_db_dataset(db, "limit_up_board", "2026-10-27", 0, OPEN)["ok"]


def test_calendar_missing_as_of_counts_as_lag(db):
    """日历没续上：as_of 当交易日补进去，旧数据至少算滞后 1，不会被当成最新。"""
    _fill(db, "daily_kbars", "2026-10-27", 10)
    days = sdr._open_days_with(db, "2026-10-28")
    assert "2026-10-28" in days
    r = sdr.check_db_dataset(db, "daily_kbars", "2026-10-28", 0, days)
    assert not r["ok"] and r["lag_trade_days"] == 1


def test_dir_dataset_blocks_only_past_red_line(tmp_path):
    d = tmp_path / "midterm" / "fina"
    d.mkdir(parents=True)
    for i in range(3):
        (d / f"{i:06d}.parquet").write_bytes(b"x")
    now = dt.datetime(2026, 10, 27, 18, 0, tzinfo=sdr.dq._CST)
    ok = sdr.check_dir_dataset("fina_hist", "2026-10-27", now=now, base=tmp_path)
    assert ok["ok"]
    old = time.time() - 130 * 86400
    for f in d.iterdir():
        os.utime(f, (old, old))
    bad = sdr.check_dir_dataset("fina_hist", "2026-10-27", now=now, base=tmp_path)
    assert not bad["ok"] and "超过 120 天没更新" in bad["reason"] and bad["max_age_days"] == 120
    assert not sdr.check_dir_dataset("forecast_hist", "2026-10-27", now=now, base=tmp_path)["ok"]  # 目录不存在


def test_gate_checks_each_dataset_once_and_builds_reason(db, monkeypatch):
    for d in OPEN[-6:]:
        for t in ("daily_kbars", "stock_quotes", "limit_up_board", "concept_sector", "concept_sector_stock"):
            _fill(db, t, d, 50)
    _fill(db, "capital_flow", "2026-10-23", 50)
    calls = []
    real = sdr.check_db_dataset
    monkeypatch.setattr(sdr, "check_db_dataset", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    g = sdr.ReadinessGate(db, "2026-10-27")
    a = g.check("chao-gu-yang-jia-skill", "short")
    b = g.check("oversold-capital-reversal", "short")
    assert not a["ready"] and not b["ready"]
    assert a["reason"] == "缺数据：资金流向 最新 10-23，应为 10-27"
    assert calls.count("capital_flow") == 1
    c = g.check("chen-xiaoqun-skill", "short")  # 多依赖龙虎榜
    assert "龙虎榜 最新 无，应为 10-27" in c["reason"]
    raw = sdr.blocked_raw(a)
    assert raw["reason"] == a["reason"] and raw["data_not_ready"][0]["key"] == "capital_flow"


# ---------------------------------------------------------------- 依赖清单要覆盖全部现役策略
def test_every_live_midlong_kind_is_declared():
    from app.services import midterm_service as ms

    undeclared = sorted({str(m.get("kind") or "smallcap") for m in ms.STRATEGIES.values()} - set(sdr.MID_KIND_DEPS))
    assert not undeclared, f"新的中长线 kind 要在 MID_KIND_DEPS 里声明依赖：{undeclared}"
    for sid in ms.STRATEGIES:
        keys = [k for k, _ in sdr.deps_for(sid)]
        assert "daily_kbars" in keys
        for k in keys:
            assert k in sdr.PLAIN_LABELS


def test_every_enabled_short_strategy_is_declared():
    from app.services.standalone_strategies import NON_PICKING_SKILL_IDS, RETIRED_STRATEGY_IDS

    live = []
    for f in sorted((BACKEND / "skills").glob("*/strategy.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        sid = str(d.get("id") or f.parent.name)
        if d.get("enabled") and sid not in RETIRED_STRATEGY_IDS and sid not in NON_PICKING_SKILL_IDS:
            live.append(sid)
    assert live, "没找到现役短线策略"
    missing = [s for s in live if s not in sdr.SHORT_DEPS]
    assert not missing, f"新的短线策略要在 SHORT_DEPS 里声明依赖：{missing}"


# ---------------------------------------------------------------- 落盘 / 出票检查
def test_record_overwrites_per_strategy(tmp_path):
    sdr.record("2026-10-27", {"a": {"horizon": "short", "ready": False, "missing": [], "reason": "缺数据：X"},
                              "b": {"horizon": "mid", "ready": True, "missing": [], "reason": ""}}, store_dir=tmp_path)
    assert sdr.blocked_on("2026-10-27", tmp_path) == {"a": "缺数据：X"}
    sdr.record("2026-10-27", {"a": {"horizon": "short", "ready": True, "missing": [], "reason": ""}}, store_dir=tmp_path)
    assert sdr.blocked_on("2026-10-27", tmp_path) == {}  # 补数后补跑：记录变成已就绪
    assert sdr.latest_recorded(tmp_path)[0] == "2026-10-27"


def test_daily_output_reports_data_not_ready_reason(monkeypatch, tmp_path):
    from app.services import daily_output_check as doc
    from app.services import midterm_service as ms

    monkeypatch.setattr(sdr, "STORE_DIR", tmp_path)
    sdr.record("2026-10-26", {"b": {"ready": False, "reason": "缺数据：资金流向 最新 10-23，应为 10-26"},
                              "mid-earnings-accel": {"ready": False, "reason": "缺数据：日K行情 最新 10-23，应为 10-26"}})
    monkeypatch.setattr(doc, "expected_short_strategies", lambda db: ["a", "b"])
    monkeypatch.setattr(doc, "_live_short_runs", lambda db, d: {"a": {"run_id": "r1", "recommended_count": 0}})
    stored = {sid: {"status": "no_signal"} for sid in ms.STRATEGIES}
    stored["mid-earnings-accel"] = {"status": "data_not_ready"}
    monkeypatch.setattr(doc, "_midlong_status", lambda d: {"status_file": True, "stored": stored})
    st = doc.daily_output_status(None, "2026-10-26", now=dt.datetime(2026, 10, 26, 19, 0))
    assert not st["ok"] and st["status"] == "failed"
    assert st["short_data_not_ready"] == ["b"] and st["midlong_data_not_ready"] == ["mid-earnings-accel"]
    assert "数据未就绪" in st["reason"] and "资金流向 最新 10-23，应为 10-26" in st["reason"]
    assert "短线缺实盘出票" not in st["reason"]  # b 的原因已单列，不再笼统写「缺出票」


# ---------------------------------------------------------------- 中长线接入
def test_classify_data_not_ready():
    from app.services import midterm_service as ms

    assert ms.classify_daily_status({"data_not_ready": [], "reason": "x"}) == "data_not_ready"
    assert ms.classify_daily_status({"error": "x"}) == "data_error"


def test_generate_picks_gate_blocks_one_and_runs_others(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    monkeypatch.setattr(sdr, "STORE_DIR", tmp_path / "readiness")
    monkeypatch.setattr(ms, "STRATEGIES", {
        "s-blocked": {"kind": "earnings_accel", "schedule": "daily", "horizon": "mid", "hold_days": 20},
        "s-ok": {"kind": "earnings_accel", "schedule": "daily", "horizon": "mid", "hold_days": 20},
    })
    monkeypatch.setattr(ms, "earnings_accel_candidates", lambda db, as_of: [])
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, as_of: set())
    monkeypatch.setattr(ms, "check_daily_basic_staleness", lambda db, as_of: {})

    class FakeGate:
        def __init__(self, db, as_of):
            self.results = {}

        def check(self, sid, horizon=""):
            ready = sid != "s-blocked"
            miss = [] if ready else [{"key": "daily_kbars", "label": "日K行情", "latest": "2026-10-23",
                                      "expected": "2026-10-26", "reason": "日K行情 最新 10-23，应为 10-26", "ok": False}]
            res = {"ready": ready, "missing": miss, "reason": sdr.reason_text(miss), "horizon": horizon}
            self.results[sid] = res
            return res

    monkeypatch.setattr(sdr, "ReadinessGate", FakeGate)
    res = ms.generate_picks(None, "2026-10-26", data_gate=True)
    assert ms.classify_daily_status(res["s-blocked"]) == "data_not_ready"
    assert res["s-ok"] == 0  # 其它策略照常跑
    status = json.loads((tmp_path / "status" / "2026-10-26.json").read_text())
    assert status["s-blocked"]["status"] == "data_not_ready"
    latest = ms.latest_daily_status("s-blocked")
    assert latest["status"] == "data_not_ready" and "日K行情 最新 10-23" in latest["reason"]
    assert sdr.blocked_on("2026-10-26") == {"s-blocked": "缺数据：日K行情 最新 10-23，应为 10-26"}
    # 不开闸门（历史回放 / 滚动重算）：照旧跑，不拦
    res2 = ms.generate_picks(None, "2026-10-26")
    assert res2["s-blocked"] == 0


def test_gate_failure_fails_open(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    monkeypatch.setattr(ms, "STRATEGIES", {"s": {"kind": "earnings_accel", "schedule": "daily", "horizon": "mid"}})
    monkeypatch.setattr(ms, "earnings_accel_candidates", lambda db, as_of: [])
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, as_of: set())
    monkeypatch.setattr(ms, "check_daily_basic_staleness", lambda db, as_of: {})
    alerts = []
    monkeypatch.setattr(sdr, "alert_gate_failure", lambda where, exc: alerts.append(where))

    class Broken:
        results: dict = {}

        def __init__(self, db, as_of):
            pass

        def check(self, sid, horizon=""):
            raise RuntimeError("boom")

    monkeypatch.setattr(sdr, "ReadinessGate", Broken)
    assert ms.generate_picks(None, "2026-10-26", data_gate=True)["s"] == 0
    assert alerts == ["midlong:s"]


# ---------------------------------------------------------------- 短线接入
def test_standalone_gate_skips_blocked_strategy(monkeypatch, tmp_path):
    from app.services import scheduler_service as ss

    monkeypatch.setattr(sdr, "STORE_DIR", tmp_path)

    class FakeGate:
        def __init__(self, db, as_of):
            self.as_of = as_of
            self.results = {}

        def check(self, sid, horizon=""):
            ready = sid != "chen-xiaoqun-skill"
            miss = [] if ready else [{"key": "dragon_tiger", "label": "龙虎榜", "latest": "2026-10-23",
                                      "expected": self.as_of, "reason": "龙虎榜 最新 10-23，应为 10-26", "ok": False}]
            self.results[sid] = {"ready": ready, "missing": miss, "reason": sdr.reason_text(miss), "horizon": horizon}
            return self.results[sid]

    monkeypatch.setattr(sdr, "ReadinessGate", FakeGate)
    items = [{"single_skill_id": "chen-xiaoqun-skill"}, {"single_skill_id": "oversold-capital-reversal"}]
    runs: list = []
    kept = ss._gate_standalone_payloads(None, {"trade_date_override": "20261026"}, items, runs)
    assert [k["single_skill_id"] for k in kept] == ["oversold-capital-reversal"]
    assert runs[0]["skill_id"] == "chen-xiaoqun-skill" and runs[0]["status"] == "data_not_ready"
    assert "龙虎榜 最新 10-23，应为 10-26" in runs[0]["error"]
    assert sdr.blocked_on("2026-10-26") == {"chen-xiaoqun-skill": "缺数据：龙虎榜 最新 10-23，应为 10-26"}


def test_standalone_gate_failure_lets_all_run(monkeypatch):
    from app.services import scheduler_service as ss

    class Broken:
        def __init__(self, db, as_of):
            raise RuntimeError("boom")

    monkeypatch.setattr(sdr, "ReadinessGate", Broken)
    monkeypatch.setattr(sdr, "alert_gate_failure", lambda where, exc: None)
    items = [{"single_skill_id": "a"}, {"single_skill_id": "b"}]
    runs: list = []
    assert ss._gate_standalone_payloads(None, {"trade_date_override": "2026-10-26"}, items, runs) == items
    assert runs == []


def test_safety_net_reruns_data_not_ready_midlong(monkeypatch, tmp_path):
    """盘前安全网：data_not_ready 的中长线策略也补跑（数据补上后正常出票，幂等覆盖同一文件）。"""
    from app.services import midterm_service as ms
    from app.services import scheduler_service as ss

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    monkeypatch.setattr(ss, "_latest_allowed_bar_date", lambda: "2026-10-26")
    sd = ms._status_dir()
    sd.mkdir(parents=True)
    rows = {sid: {"status": "no_signal"} for sid in ms.STRATEGIES}
    rows["mid-earnings-accel"] = {"status": "data_not_ready"}
    (sd / "2026-10-26.json").write_text(json.dumps(rows))
    seen = {}

    def fake_generate(db, as_of, strategy_ids=None, apply_daily_cap=False, data_gate=False, **k):
        seen.update(ids=strategy_ids, gate=data_gate)
        return {"mid-earnings-accel": 2}

    monkeypatch.setattr(ms, "generate_picks", fake_generate)
    out = ss._safety_net_midlong_repair(None)
    assert seen == {"ids": ["mid-earnings-accel"], "gate": True}
    assert out["still_error"] == [] and out["picks"] == {"mid-earnings-accel": 2}


def test_overview_marks_live_and_recorded(db, monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    monkeypatch.setattr(sdr, "STORE_DIR", tmp_path)
    monkeypatch.setattr(ms, "STRATEGIES", {"mid-x": {"kind": "earnings_accel", "horizon": "mid", "name": "X"}})
    for d in OPEN[-6:]:
        for t in ("daily_kbars", "daily_basic", "stock_quotes", "limit_up_board", "concept_sector", "concept_sector_stock"):
            _fill(db, t, d, 20)
    _fill(db, "capital_flow", "2026-10-23", 20)
    sdr.record("2026-10-27", {"mid-x": {"horizon": "mid", "ready": False, "reason": "缺数据：日K行情 最新 10-23，应为 10-27"}})
    now = dt.datetime(2026, 10, 27, 22, 0, tzinfo=sdr.dq._CST)
    out = sdr.overview(db, ["oversold-capital-reversal"], {"oversold-capital-reversal": "超跌资金反包"}, now=now, use_cache=False)
    assert out["expected_trade_date"] == "2026-10-27"
    s = out["strategies"]["oversold-capital-reversal"]
    assert s["name"] == "超跌资金反包" and not s["live"]["ready"] and "资金流向 最新 10-23，应为 10-27" in s["live"]["reason"]
    m = out["strategies"]["mid-x"]
    assert m["live"]["ready"] and m["recorded"]["ready"] is False and m["recorded"]["trade_date"] == "2026-10-27"
    assert [d["key"] for d in m["deps"]] == ["daily_kbars", "daily_basic"] and m["deps"][1]["allowed_lag"] == 1


# ---------------------------------------------------------------- 21:00 晚间补跑（_late_repair_repick）
def test_late_repick_midlong_includes_data_not_ready(monkeypatch, tmp_path):
    from app.services import midterm_service as ms
    from app.services import scheduler_service as ss

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    sd = ms._status_dir()
    sd.mkdir(parents=True)
    rows = {sid: {"status": "no_signal"} for sid in ms.STRATEGIES}
    rows["mid-earnings-accel"] = {"status": "data_not_ready"}
    rows["mid-incentive-vol"] = {"status": "data_error"}
    (sd / "2026-11-04.json").write_text(json.dumps(rows))
    assert sorted(ss._midlong_error_strategies("2026-11-04")) == ["mid-earnings-accel", "mid-incentive-vol"]
    assert ss.MIDLONG_RETRY_STATUSES == (None, "data_error", "data_not_ready")


def test_late_repick_reruns_short_strategy_blocked_by_gate(monkeypatch):
    """短线被数据闸门拦下 = 当天没建 run：真实的 _missing_standalone_strategies 认它缺批次，晚间补跑只补它一个。"""
    import asyncio
    from types import SimpleNamespace

    from app.services import midterm_live_seal as seal
    from app.services import scheduler_service as ss
    from app.services import standalone_strategies as st
    from app.services import workbench_service as wb

    td = "2026-11-04"
    live = ["chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader"]
    blocked = "chen-xiaoqun-skill"
    monkeypatch.setattr(wb, "sync_skills", lambda db: [SimpleNamespace(skill_id=s, enabled=True) for s in live])
    monkeypatch.setattr(st, "standalone_strategy_ids", lambda skills: [s.skill_id for s in skills])
    # 17:35 那次：其余 3 个 completed，被拦的没有 run
    monkeypatch.setattr(ss, "_completed_standalone_runs",
                        lambda db, p: {s: {"run_id": f"r-{s}", "recommended_count": 0} for s in live if s != blocked})
    monkeypatch.setattr(ss, "_latest_allowed_bar_date", lambda: td)
    monkeypatch.setattr(ss, "_midlong_error_strategies", lambda d: [])
    monkeypatch.setattr(ss, "_formal_inflight_count", lambda db, d: 0)
    monkeypatch.setattr(seal, "generated_before_entry_open", lambda d, now: True)
    monkeypatch.setattr(ss, "_diagnose_postclose_readiness", lambda db, d: {"can_run_local_recommendation": True})
    monkeypatch.setattr(ss, "_run_outcome_label", lambda db, p: {})
    from app.services.market_pool import kbar_cache
    monkeypatch.setattr(kbar_cache, "enabled", lambda: False)
    seen = []

    async def standalone(db, payload):
        seen.append(payload)
        return {"final_run": {"status": "completed"}, "strategy_count": 1, "completed_count": 1}

    monkeypatch.setattr(ss, "_run_standalone_recommendations", standalone)
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda *a, **k: None)

    class DB:
        def get(self, _m, _k):
            return SimpleNamespace(payload_json="{}")

        def rollback(self):
            pass

    out = asyncio.run(ss._late_repair_repick(DB(), "postclose_late_data_repair"))
    assert out["missing_strategies"] == [blocked] and out["status"] == "completed"
    (p,) = seen
    assert p["standalone_skill_ids"] == [blocked] and p["trade_date_override"] == td
