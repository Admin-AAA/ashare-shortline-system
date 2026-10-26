"""统一数据质量监控（data_quality_monitor）：阈值、文件数据集、数据库覆盖突降、调度漏跑、日志扫描、告警同步、不阻塞主流程。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services import data_quality_monitor as dq

CST = timezone(timedelta(hours=8))
# 2026-10-19(周一)..10-23(周五)、10-26(周一)、10-27 是交易日；10-24/25 周末
OPEN = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23",
        "2026-10-26", "2026-10-27"]


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=CST)


# ---------------------------------------------------------------- 纯函数
def test_expected_trade_date_before_and_after_evening_ready():
    assert dq.expected_trade_date(OPEN, _t("2026-10-26 14:00")) == "2026-10-23"  # 周一盘中：上周五
    assert dq.expected_trade_date(OPEN, _t("2026-10-26 21:29")) == "2026-10-23"
    assert dq.expected_trade_date(OPEN, _t("2026-10-26 21:30")) == "2026-10-26"
    assert dq.expected_trade_date(OPEN, _t("2026-10-25 12:00")) == "2026-10-23"  # 周日


def test_trade_lag_and_severity_thresholds():
    assert dq.trade_lag(OPEN, "2026-10-23", "2026-10-23") == 0
    assert dq.trade_lag(OPEN, "2026-10-22", "2026-10-23") == 1
    assert dq.trade_lag(OPEN, "2026-10-20", "2026-10-23") == 3
    assert dq.trade_lag(OPEN, None, "2026-10-23") is None
    assert dq.lag_severity(0) == dq.GREEN
    assert dq.lag_severity(1) == dq.YELLOW      # 滞后 1 黄
    assert dq.lag_severity(2) == dq.RED         # 滞后 >1 红
    assert dq.lag_severity(1, allowed=1) == dq.GREEN
    assert dq.lag_severity(2, allowed=1) == dq.YELLOW
    assert dq.lag_severity(None) == dq.RED


def test_cliff_severity_drop_and_spike():
    base = [100.0] * 20
    assert dq.cliff_severity(100, 100, base)[0] == dq.GREEN
    assert dq.cliff_severity(81, 100, base)[0] == dq.GREEN
    assert dq.cliff_severity(79, 100, base)[0] == dq.YELLOW   # 降 >20% 黄
    assert dq.cliff_severity(49, 100, base)[0] == dq.RED      # 降 >50% 红
    assert dq.cliff_severity(400, 5, [5.0] * 20)[0] == dq.YELLOW  # 口径暴增
    assert dq.cliff_severity(400, 390, [5.0] * 20)[0] == dq.GREEN  # 已连续两天高位：不再重复报
    assert dq.cliff_severity(10, 10, [])[0] == dq.GREEN


# ---------------------------------------------------------------- 文件数据集
def _write_pq(path: Path, col: str, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({col: dates, "v": range(len(dates))}).to_parquet(path, index=False)


def test_file_dataset_daily_lag_levels_and_caps(tmp_path):
    spec = {"key": "k", "label": "L", "mode": "daily", "glob": "x/a_2026.parquet", "cols": ("trade_date",),
            "max": dq.RED, "impact": "i"}
    _write_pq(tmp_path / "x/a_2026.parquet", "trade_date", ["20261021", "20261023"])
    assert dq.check_file_dataset(spec, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.GREEN
    _write_pq(tmp_path / "x/a_2026.parquet", "trade_date", ["20261022"])
    assert dq.check_file_dataset(spec, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.YELLOW
    _write_pq(tmp_path / "x/a_2026.parquet", "trade_date", ["20261016"])
    it = dq.check_file_dataset(spec, OPEN, "2026-10-23", base=tmp_path)
    assert it["severity"] == dq.RED and it["lag_trade_days"] == 5
    # 研究用数据集封顶黄；回补中的数据集（provisional）封顶黄
    assert dq.check_file_dataset({**spec, "max": dq.YELLOW}, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.YELLOW
    prov = dq.check_file_dataset({**spec, "provisional": True}, OPEN, "2026-10-23", base=tmp_path)
    assert prov["severity"] == dq.YELLOW and prov["provisional"] is True
    # 文件不存在：红（受 max 封顶）
    gone = {**spec, "glob": "nope/*.parquet"}
    assert dq.check_file_dataset(gone, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.RED


def test_file_dataset_event_mode_uses_calendar_days_and_mixed_types(tmp_path):
    spec = {"key": "k", "label": "L", "mode": "event", "glob": "e/r_2026.parquet", "cols": ("ann_date",),
            "event_days": (5, 10), "max": dq.RED, "impact": "i"}
    p = tmp_path / "e/r_2026.parquet"
    p.parent.mkdir(parents=True)
    pd.DataFrame({"ann_date": ["20261020", None, "20260101"]}).to_parquet(p, index=False)  # 含 None 的混合列
    assert dq.check_file_dataset(spec, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.GREEN  # 3 天
    pd.DataFrame({"ann_date": ["20261015"]}).to_parquet(p, index=False)
    assert dq.check_file_dataset(spec, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.YELLOW  # 8 天
    pd.DataFrame({"ann_date": ["20260901"]}).to_parquet(p, index=False)
    assert dq.check_file_dataset(spec, OPEN, "2026-10-23", base=tmp_path)["severity"] == dq.RED


def test_xiaoshi_min1_detects_stale_cache(tmp_path):
    root = tmp_path / "xiaoshi_min1"
    for code, d in (("000001", "2026-08-07"), ("000002", "2026-08-05")):
        p = root / code / "2026" / "update.parquet"
        p.parent.mkdir(parents=True)
        pd.DataFrame({"date": ["2026-08-01", d]}).to_parquet(p, index=False)
    (root / "000003" / "base").mkdir(parents=True)  # 只有 base、没有 update 的股票目录
    mx, n_dirs, n_ok = dq.xiaoshi_min1_latest(root)
    assert (mx, n_dirs, n_ok) == ("2026-08-07", 3, 2)
    it = dq.check_xiaoshi_min1(OPEN, "2026-10-23", root)
    assert it["severity"] == dq.RED and it["latest"] == "2026-08-07"
    pd.DataFrame({"date": ["2026-10-23"]}).to_parquet(root / "000001" / "2026" / "update.parquet", index=False)
    assert dq.check_xiaoshi_min1(OPEN, "2026-10-23", root)["severity"] == dq.GREEN


def test_xiaoshi_min_all_is_provisional_and_capped(tmp_path):
    import json

    st = tmp_path / "state.json"
    st.write_text(json.dumps({"files": {"a": {"last_date": "2026-10-09"}},
                              "last_run": {"ts": "2026-10-26 13:38:27", "downloaded": 12, "errors": 0}}))
    it = dq.check_xiaoshi_min_all(OPEN, "2026-10-23", _t("2026-10-26 14:00"), st)
    assert it["severity"] == dq.YELLOW and it["provisional"] is True and it["latest"] == "2026-10-09"
    assert dq.check_xiaoshi_min_all(OPEN, "2026-10-23", None, tmp_path / "missing.json")["provisional"] is True


def test_dir_and_age_file_and_capability_matrix(tmp_path):
    import json
    import os

    d = tmp_path / "fina"
    d.mkdir()
    for i in range(3):
        (d / f"{i}.parquet").write_bytes(b"x")
    spec = {"key": "k", "label": "L", "dir": "fina", "yellow_days": 45, "red_days": 120, "min_files": 3, "max": dq.RED, "impact": "i"}
    assert dq.check_dir_dataset(spec, _t("2026-10-26 14:00"), tmp_path)["severity"] == dq.GREEN
    old = (_t("2026-10-26 14:00") - timedelta(days=60)).timestamp()
    for f in d.iterdir():
        os.utime(f, (old, old))
    assert dq.check_dir_dataset(spec, _t("2026-10-26 14:00"), tmp_path)["severity"] == dq.YELLOW
    assert dq.check_dir_dataset({**spec, "min_files": 10, "yellow_days": 999}, _t("2026-10-26 14:00"), tmp_path)["severity"] == dq.YELLOW

    f = tmp_path / "industry_cache.json"
    f.write_text("{}")
    os.utime(f, (old, old))
    aspec = {"key": "a", "label": "A", "path": "industry_cache.json", "yellow_days": 30, "red_days": 90, "max": dq.YELLOW, "impact": "i"}
    assert dq.check_age_file(aspec, _t("2026-10-26 14:00"), tmp_path)["severity"] == dq.YELLOW
    assert dq.check_age_file({**aspec, "red_days": 40}, _t("2026-10-26 14:00"), tmp_path)["severity"] == dq.YELLOW  # 封顶

    m = tmp_path / "m.json"
    m.write_text(json.dumps({"probed_at": "2026-10-24T11:32:40+0800", "rows": [{"provider": "p", "api": "a", "status": "error"}]}))
    it = dq.check_capability_matrix(_t("2026-10-26 14:00"), m)
    assert it["severity"] == dq.YELLOW and "p/a" in it["detail"]
    m.write_text(json.dumps({"probed_at": "2026-10-26T11:32:40+0800", "rows": [{"provider": "p", "api": "a", "status": "ok"}]}))
    assert dq.check_capability_matrix(_t("2026-10-26 14:00"), m)["severity"] == dq.GREEN


# ---------------------------------------------------------------- 数据库（sqlite 内存库）
@pytest.fixture()
def sdb():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE minute_kbars (code TEXT, trade_date TEXT)"))
        c.execute(text("CREATE TABLE daily_kbars (code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, "
                       "volume REAL, pct_chg REAL)"))
        c.execute(text("CREATE TABLE index_daily (ts_code TEXT, trade_date TEXT, close REAL)"))
        c.execute(text("CREATE TABLE trade_calendar (exchange TEXT, cal_date TEXT, is_open INTEGER)"))
        for d in OPEN:
            c.execute(text("INSERT INTO trade_calendar VALUES ('SSE', :d, 1)"), {"d": d.replace("-", "")})
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


MIN_SPEC = {"key": "db:minute_kbars", "label": "分钟K", "table": "minute_kbars", "col": "trade_date",
            "entity": "COUNT(DISTINCT code)", "lag": 0, "cliff": True, "impact": "i"}


def _fill_minutes(db, per_day: dict[str, int]) -> None:
    for d, n in per_day.items():
        for i in range(n):
            db.execute(text("INSERT INTO minute_kbars VALUES (:c, :d)"), {"c": f"{i:06d}", "d": d})
    db.commit()


def test_open_days_loaded_from_calendar(sdb):
    assert dq.load_open_days(sdb) == OPEN


def test_db_minute_coverage_cliff_is_flagged(sdb):
    # 以前只查「最大日期」：最新日期正常、覆盖股票数只剩 40% 时全绿。现在红。
    days = OPEN[:-2]  # 10-15..10-26 之前
    _fill_minutes(sdb, {**{d: 300 for d in days[:-1]}, days[-1]: 120})
    it = dq.check_db_dataset(sdb, MIN_SPEC, OPEN, days[-1])
    assert it["severity"] == dq.RED and "中位数" in it["detail"]
    assert it["latest"] == days[-1] and it["lag_trade_days"] == 0


def test_db_minute_lag_is_flagged_and_healthy_is_green(sdb):
    _fill_minutes(sdb, {d: 300 for d in OPEN[:5]})
    healthy = dq.check_db_dataset(sdb, MIN_SPEC, OPEN, OPEN[4])
    assert healthy["severity"] == dq.GREEN
    stale = dq.check_db_dataset(sdb, MIN_SPEC, OPEN, "2026-10-23")  # 最新 10-21，期望 10-23：滞后 2 个交易日（>1 红）
    assert stale["severity"] == dq.RED and stale["lag_trade_days"] == 2


def test_daily_kbar_value_anomalies(sdb):
    rows = [("000001", "2026-10-23", 10, 11, 9, 10, 100, 1.0), ("002731", "2026-10-23", 0.34, 0.38, 0.3, 0.31, 5, -59.7),
            ("000002", "2026-10-22", 10, 9, 11, 10, 100, 0.0)]  # 最高<最低
    for r in rows:
        sdb.execute(text("INSERT INTO daily_kbars VALUES (:a,:b,:c,:d,:e,:f,:g,:h)"),
                    dict(zip("abcdefgh", r)))
    sdb.commit()
    it = dq.check_daily_kbar_values(sdb, OPEN, "2026-10-23")
    assert it["severity"] == dq.YELLOW and it["anomalous_rows"] == 2 and "002731" in it["detail"]


def test_index_daily_per_index_and_duplicates(sdb):
    for code in dq.INDEX_CODES:
        sdb.execute(text("INSERT INTO index_daily VALUES (:c, '2026-10-23', 1)"), {"c": code})
    sdb.execute(text("DELETE FROM index_daily WHERE ts_code='000300.SH'"))
    sdb.execute(text("INSERT INTO index_daily VALUES ('000300.SH', '2026-10-16', 1)"))
    sdb.execute(text("INSERT INTO index_daily VALUES ('000905.SH', '2026-10-23', 1)"))  # 重复键
    sdb.commit()
    items = {i["key"]: i for i in dq.check_index_daily(sdb, OPEN, "2026-10-23")}
    assert items["db:index_daily:000300.SH"]["severity"] == dq.RED
    assert items["db:index_daily:000905.SH"]["severity"] == dq.GREEN
    assert items["db:index_daily:dup"]["severity"] == dq.RED


def test_calendar_horizon():
    assert dq.check_calendar_horizon(["2026-12-25"], _t("2026-10-26 14:00"))["severity"] == dq.GREEN
    assert dq.check_calendar_horizon(["2026-11-10"], _t("2026-10-26 14:00"))["severity"] == dq.YELLOW
    assert dq.check_calendar_horizon(["2026-10-28"], _t("2026-10-26 14:00"))["severity"] == dq.RED


# ---------------------------------------------------------------- 调度任务 / 日志
def test_cron_parsing_and_last_expected_run():
    assert dq.parse_simple_cron("0 18 * * mon-fri") == ([0], [18], {0, 1, 2, 3, 4})
    assert dq.parse_simple_cron("10 8,17 * * mon-fri") == ([10], [8, 17], {0, 1, 2, 3, 4})
    assert dq.parse_simple_cron("*/5 * * * *") is None
    # 周一 14:00：最近应跑时刻 08:10（08:10 与 17:10 两次，17:10 还没到）
    assert dq.last_expected_run("10 8,17 * * mon-fri", _t("2026-10-26 14:00"), OPEN) == _t("2026-10-26 08:10")
    # 周日：回退到上周五 21:00
    assert dq.last_expected_run("0 21 * * mon-fri", _t("2026-10-25 12:00"), OPEN) == _t("2026-10-23 21:00")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, tasks, runs):
        self.tasks, self.runs = tasks, runs

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM scheduled_tasks" in sql:
            return _Rows(self.tasks)
        return _Rows(self.runs.get((params or {}).get("t"), []))


def _utc(s: str) -> datetime:  # 库内为 UTC naive
    return _t(s).astimezone(timezone.utc).replace(tzinfo=None)


def test_scheduled_task_missed_run_failed_streak_and_new_task_grace():
    created = _utc("2026-10-01 00:00")
    db = _FakeDB(
        tasks=[("post_sync_formal_recommendation", "盘后", "35 17 * * mon-fri", created),
               ("postclose_late_data_repair", "晚间补数", "0 21 * * mon-fri", created),
               ("brand_new", "新任务", "40 19 * * mon-fri", _utc("2026-10-25 10:00")),
               ("ok_task", "ok", "10 8,17 * * mon-fri", created)],
        runs={"post_sync_formal_recommendation": [(_utc("2026-10-22 17:35"), "completed", "")],  # 10-23 的没跑
              "postclose_late_data_repair": [(_utc("2026-10-23 21:00"), "partial_failed", "失败数据集: fina_indicator）"),
                                             (_utc("2026-10-22 21:00"), "partial_failed", ""),
                                             (_utc("2026-10-21 21:00"), "completed", "")],
              "ok_task": [(_utc("2026-10-26 08:10"), "completed", "")]})
    now = _t("2026-10-26 14:00")
    items = {i["key"]: i for i in dq.check_scheduled_tasks(db, OPEN, now)}
    miss = items["job:post_sync_formal_recommendation"]
    assert miss["severity"] == dq.RED and "漏跑" in miss["detail"]  # 关键任务漏跑 → 红
    late = items["job:postclose_late_data_repair"]
    assert late["severity"] == dq.YELLOW and "fina_indicator" in late["detail"] and "连续 2 次" in late["detail"]
    assert items["job:brand_new"]["severity"] == dq.GREEN  # 注册之前的计划时刻不算漏跑
    assert items["job:ok_task"]["severity"] == dq.GREEN


LOG = """2026-10-26 09:00:00 [WARNING] [pid 1] app.services.tushare_transport: tushare provider fallback api=daily from=quantdata category=quota_exhausted upstream=-
2026-10-26 09:00:01 [WARNING] [pid 1] app.services.tushare_transport: tushare provider fallback success api=daily provider=promax
2026-10-26 09:01:00 [ERROR] [pid 1] ashare.alerts: [ALERT:datasource_all_failed] Tushare 全部上游失败 api=rt_k | key=rt_k
2026-10-24 09:01:00 [ERROR] [pid 1] ashare.alerts: [ALERT:old] 太老了 | key=x
这是续行
"""


def test_scan_log_signals_counts_recent_only():
    sig = dq.scan_log_signals(LOG, _t("2026-10-26 14:00"), hours=24)
    assert sig["fallbacks"] == {"quantdata:quota_exhausted": 1}
    assert sig["error_count"] == 1


def test_log_check_flags_quota_exhausted(tmp_path):
    f = tmp_path / "app.log"
    f.write_text(LOG)
    items = {i["key"]: i for i in dq.check_log_signals(_t("2026-10-26 14:00"), f)}
    assert items["log:provider_fallbacks"]["severity"] == dq.YELLOW and "额度耗尽" in items["log:provider_fallbacks"]["detail"]
    assert items["log:error_volume"]["severity"] == dq.GREEN
    assert dq.check_log_signals(_t("2026-10-26 14:00"), tmp_path / "none.log")[0]["severity"] == dq.YELLOW


# ---------------------------------------------------------------- 告警与不阻塞
def _report(*sevs):
    items = [{"key": f"k{i}", "label": f"L{i}", "severity": s, "detail": "d", "impact": "x", "category": "c"}
             for i, s in enumerate(sevs)]
    return {"items": items}


def test_sync_alerts_emit_and_resolve():
    from app.core import alerts

    alerts.reset_alerts()
    out = dq.sync_alerts(_report(dq.RED, dq.YELLOW, dq.GREEN))
    assert out["emitted"] == ["k0", "k1"]
    recs = {r["key"]: r for r in alerts.active_alert_records(dq.ALERT_KIND)}
    assert recs["k0"]["message"].startswith("[红]") and recs["k1"]["message"].startswith("[黄]")
    out = dq.sync_alerts(_report(dq.GREEN, dq.YELLOW))  # k0 恢复
    assert out["resolved"] == ["k0"]
    assert [r["key"] for r in alerts.active_alert_records(dq.ALERT_KIND)] == ["k1"]
    alerts.reset_alerts()


def test_safe_wrapper_turns_exceptions_into_yellow_item():
    def boom():
        raise RuntimeError("坏了")

    items = dq._safe("L", "k", "c", "db", boom)
    assert items[0]["severity"] == dq.YELLOW and "坏了" in items[0]["detail"]


def test_run_data_quality_check_never_raises(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dq, "run_checks", broken)
    out = dq.run_data_quality_check(SimpleNamespace(), alert=False, persist=False)
    assert out["overall"] == "error" and "db down" in out["error"]


def test_freshness_task_survives_quality_monitor_failure(monkeypatch):
    """数据质量巡检异常不得让巡检任务本身失败（更不能影响选股）。"""
    from app.services import scheduler_service

    monkeypatch.setattr(dq, "run_data_quality_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("monitor bug")))
    out = scheduler_service._run_data_freshness_check(SimpleNamespace(), {"quality_only": True})
    assert "data_quality" not in out and out["problems"] == {}


def test_freshness_task_quality_red_marks_partial_failed(monkeypatch):
    from app.services import scheduler_service

    monkeypatch.setattr(dq, "run_data_quality_check", lambda *a, **k: {
        "overall": "red", "summary": {"red": 1, "yellow": 2, "green": 3}, "expected_trade_date": "2026-10-23",
        "items": [{"key": "file:xiaoshi_min1", "severity": "red"}]})
    out = scheduler_service._run_data_freshness_check(SimpleNamespace(), {"quality_only": True})
    assert out["status"] == "partial_failed" and out["data_quality"]["red"] == ["file:xiaoshi_min1"]
    assert "数据质量巡检 红 1 / 黄 2 / 绿 3" in out["summary_text"]
