"""沪深300收益曲线对齐：起点归零、交易日 as-of、缺数据不丢日期。"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.benchmarks import router
from app.db.session import get_db
from app.services.benchmark_service import align_benchmark_curve, benchmark_curve_for_dates, benchmark_daily_curve


def test_curve_rebases_to_zero_on_the_earliest_date_not_the_first_element():
    curve = align_benchmark_curve(
        {"2026-01-05": 100.0, "2026-01-06": 110.0},
        ["2026-01-06", "2026-01-05"],
    )
    assert curve["status"] == "ok"
    assert curve["base_date"] == "2026-01-05"
    assert curve["base_close"] == 100.0
    assert [point["date"] for point in curve["points"]] == ["2026-01-06", "2026-01-05"]
    by_date = {point["date"]: point for point in curve["points"]}
    assert by_date["2026-01-05"]["nav"] == 1.0
    assert by_date["2026-01-05"]["cum_pct"] == 0.0
    assert by_date["2026-01-06"]["cum_pct"] == 10.0
    assert by_date["2026-01-06"]["nav"] == 1.1
    assert by_date["2026-01-05"]["filled"] is False


def test_missing_day_uses_previous_close_and_keeps_every_requested_date():
    curve = align_benchmark_curve(
        {"2026-01-05": 100.0, "2026-01-07": 103.0},
        ["2026-01-05", "2026-01-06", "2026-01-07"],
    )
    assert [point["date"] for point in curve["points"]] == ["2026-01-05", "2026-01-06", "2026-01-07"]
    gap = curve["points"][1]
    assert gap["filled"] is True
    assert gap["index_date"] == "2026-01-05"
    assert gap["cum_pct"] == 0.0
    assert gap["stale"] is False
    assert curve["points"][2]["cum_pct"] == 3.0
    assert curve["points"][2]["filled"] is False
    assert curve["status"] == "ok"


def test_dates_before_any_close_stay_null_and_do_not_invent_zero():
    curve = align_benchmark_curve(
        {"2026-01-06": 200.0, "2026-01-07": 210.0},
        ["2026-01-05", "2026-01-06", "2026-01-07"],
    )
    assert curve["status"] == "partial"
    assert curve["points"][0]["cum_pct"] is None
    assert curve["points"][0]["nav"] is None
    assert curve["points"][1]["cum_pct"] == 0.0
    assert curve["points"][2]["cum_pct"] == 5.0
    assert "不画基准点" in curve["message"]
    assert curve["aligned_count"] == 2


def test_stale_gap_is_carried_forward_and_flagged():
    curve = align_benchmark_curve(
        {"2026-01-05": 100.0},
        ["2026-01-05", "2026-03-01"],
    )
    assert curve["status"] == "partial"
    assert curve["stale_count"] == 1
    carried = curve["points"][1]
    assert carried["cum_pct"] == 0.0
    assert carried["stale"] is True
    assert carried["index_date"] == "2026-01-05"
    assert "沿用更早收盘价" in curve["message"]


def test_no_local_closes_marks_unavailable_without_a_fake_flat_line():
    curve = align_benchmark_curve({}, ["2026-01-05", "2026-01-06"])
    assert curve["status"] == "unavailable"
    assert curve["ts_code"] == "000300.SH"
    assert curve["name"] == "沪深300"
    assert [point["cum_pct"] for point in curve["points"]] == [None, None]
    assert "index_daily" in curve["message"]
    assert curve["aligned_count"] == 0


def test_compact_dates_and_non_positive_closes_are_normalized():
    curve = align_benchmark_curve(
        {"20260105": 100.0, "2026-01-06": 0.0, "2026-01-07": 102.0},
        ["2026-01-05", "2026-01-06", "2026-01-07"],
    )
    assert curve["base_date"] == "2026-01-05"
    assert curve["points"][0]["index_date"] == "2026-01-05"
    assert curve["points"][0]["cum_pct"] == 0.0
    # 1 月 6 日收盘价无效，沿用 1 月 5 日，不能把线画成 0 涨幅以外的插值。
    assert curve["points"][1]["index_date"] == "2026-01-05"
    assert curve["points"][1]["filled"] is True
    assert curve["points"][2]["cum_pct"] == 2.0


def test_empty_dates_are_empty_not_unavailable():
    curve = align_benchmark_curve({"2026-01-05": 100.0}, [])
    assert curve["status"] == "empty"
    assert curve["points"] == []
    assert curve["message"] is None


def test_aligned_route_reads_closes_through_the_service(monkeypatch):
    monkeypatch.setattr(
        "app.services.benchmark_service.load_index_closes",
        lambda _db, _ts_code, start=None, end=None: {"2026-01-05": 100.0, "2026-01-08": 101.0},
    )
    curve = benchmark_curve_for_dates(SimpleNamespace(), ["2026-01-05", "2026-01-08"])
    assert curve["points"][0]["cum_pct"] == 0.0
    assert curve["points"][1]["cum_pct"] == 1.0

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    with TestClient(app) as client:
        response = client.post(
            "/api/benchmarks/hs300/aligned",
            json={"dates": ["2026-01-05", "2026-01-08"]},
        )
        daily = client.get("/api/benchmarks/hs300", params={"start": "2026-01-05", "end": "2026-01-08"})
        rejected = client.get("/api/benchmarks/hs300", params={"start": "2026-02-01", "end": "2026-01-01"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["method"] == "buy_hold_asof"
    assert [point["cum_pct"] for point in body["points"]] == [0.0, 1.0]
    assert daily.status_code == 200
    assert daily.json()["base_date"] == "2026-01-05"
    assert rejected.status_code == 400


def test_daily_curve_without_bars_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.benchmark_service.load_index_closes",
        lambda _db, _ts_code, start=None, end=None: {},
    )
    curve = benchmark_daily_curve(SimpleNamespace(), "2026-01-01", "2026-01-31")
    assert curve["status"] == "unavailable"
    assert curve["points"] == []
    assert "2026-01-01" in curve["message"]
