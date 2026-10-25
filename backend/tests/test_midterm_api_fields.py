"""中长线前端支持：rule_score/selection_reason/cancel 字段、分页、手动补跑过滤（不连库、不联网）。"""
import json

from app.services import midterm_service as ms


def test_annotate_each_kind():
    cases = {
        "mid-small-cap-weekly": {"code": "1", "total_mv": 166416.0, "pe_ttm": 12.0},
        "mid-lowprice-smallcap-weekly": {"code": "1", "total_mv": 181000.0, "close": 2.87, "pe_ttm": None,
                                         "price_rank": 12, "mv_rank": 1},
        "mid-repurchase-plan": {"code": "1", "ratio": 0.01157, "repurchase_amount": 6e7},
        "mid-earnings-accel": {"code": "1", "score": 300.0, "dt_netprofit_yoy": 592.1},
        "mid-forecast-beat": {"code": "1", "score": 300.0, "p_change_min": 3318.0},
        "mid-incentive-option": {"code": "1", "r20": -0.0512, "score": 0.0512, "title": "x草案"},
        "mid-block-trade-premium": {"code": "1", "ratio": 0.0061, "premium": 0.01, "n_trades": 2},
        "long-lowturn-lowvol": {"code": "1", "score": 12.0, "turn60": 0.5, "vol60": 1.1},
        "long-dividend-lowvol": {"code": "1", "score": 9.0, "dv_ttm": 5.2, "vol60": 1.0},
    }
    expect = {"mid-small-cap-weekly": 16.64, "mid-lowprice-smallcap-weekly": 18.1, "mid-repurchase-plan": 1.157, "mid-incentive-option": -5.12,
              "mid-block-trade-premium": 0.61}
    for sid, p in cases.items():
        out = ms.annotate_pick(sid, dict(p), 1)
        assert out["rule_score"] is not None and out["rule_score_label"] and out["selection_reason"], sid
        assert out["rule_score_better"] in ("higher", "lower")
        if sid in expect:
            assert out["rule_score"] == expect[sid], sid
    assert ms.annotate_pick("mid-repurchase-plan", {"code": "1"})["rule_score"] is None


def test_cancel_fields():
    p = ms.annotate_pick("mid-repurchase-plan", {"code": "1", "ratio": 0.01, "status": "skipped",
                                                 "reason": "开盘涨停买不进", "cancel_code": "limit_up_open"})
    assert p["cancel_code"] == "limit_up_open" and p["cancel_reason"] == "开盘涨停买不进"
    q = ms.annotate_pick("mid-repurchase-plan", {"code": "1", "ratio": 0.01, "status": "closed", "ret": 1.0})
    assert q["cancel_code"] is None and q["cancel_reason"] is None and q["ret_includes_cost"] is True


def test_pagination_and_horizon(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    for d in ("2026-10-12", "2026-10-13", "2026-10-14"):
        for sid in ("mid-repurchase-plan", "long-lowturn-lowvol"):
            (tmp_path / f"{d}_{sid}.json").write_text(json.dumps(
                {"strategy_id": sid, "signal_date": d, "picks": [{"code": "000001", "ratio": 0.02}]}))
    monkeypatch.setattr(ms, "_realize", lambda db, c, d, h: {"status": "pending"})
    out = ms.list_picks_page(None, None, "mid", page=1, page_size=2)
    assert out["total"] == 3 and len(out["batches"]) == 2
    assert out["batches"][0]["signal_date"] == "2026-10-14"
    assert out["batches"][0]["picks"][0]["selection_rank"] == 1
    out2 = ms.list_picks_page(None, None, "mid", page=2, page_size=2)
    assert len(out2["batches"]) == 1
    assert len(ms.list_picks(None, None, 30, horizon="long")) == 3
    assert ms.list_picks_page(None, "long-lowturn-lowvol", None, 1, 30)["total"] == 3


def test_generate_picks_horizon_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    seen = []
    monkeypatch.setattr(ms, "_is_month_end", lambda db, d: True)
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: seen.append(sid) or set())
    monkeypatch.setattr(ms, "repurchase_plan_candidates", lambda db, d: None)
    monkeypatch.setattr(ms, "lowturn_lowvol_candidates", lambda db, d, n: None)
    monkeypatch.setattr(ms, "dividend_lowvol_candidates", lambda db, d, n: None)
    from app.services import midterm_fina_events as fe
    for fn in ("cld_candidates", "dinc_candidates", "udp_candidates", "ncs_candidates", "cg_candidates"):
        monkeypatch.setattr(fe, fn, lambda db, d: None)
    res = ms.generate_picks(None, "2026-10-16", horizon="long")
    assert set(res) == {"long-lowturn-lowvol", "long-dividend-lowvol", "long-growth-streak", "long-net-cash-switch"}
    res = ms.generate_picks(None, "2026-10-16", strategy_ids=["long-lowturn-lowvol"])
    assert set(res) == {"long-lowturn-lowvol"}


def test_retired_strategies_not_live():
    """§278/§282：新标准重评仍不达标的 /long 策略已下线（long-net-cash-switch 已于 §282 恢复上线），不在现役 STRATEGIES，也不出票。"""
    retired = {"long-repurchase-big", "long-current-debt-down", "long-dividend-increase",
               "long-undist-profit-growth"}
    assert set(ms.RETIRED_STRATEGIES) == retired
    assert not retired & set(ms.STRATEGIES)


def test_next_batch_note(tmp_path, monkeypatch):
    """strategies_overview 带动态 next_batch_note；无批次前缀「尚未出票；」。"""
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "_next_month_end_trade_date", lambda db, today=None: "2026-10-30")
    # 无批次：全部前缀尚未出票
    notes = {s["id"]: s["next_batch_note"] for s in ms.strategies_overview("mid", db=None)}
    assert notes["mid-repurchase-plan"] == "尚未出票；每个交易日盘后（17:35）选股，次日开盘买"
    assert notes["mid-small-cap-weekly"] == "尚未出票；每个周五收盘后选股，次日开盘买"
    assert notes["mid-block-trade-premium"] == "尚未出票；每晚约 21:00 大宗数据发布后选股，次日开盘买"
    long_notes = {s["id"]: s["next_batch_note"] for s in ms.strategies_overview("long", db=None)}
    assert long_notes["long-lowturn-lowvol"] == "尚未出票；每月最后一个交易日收盘后选股，下一批：2026-10-30，次日开盘买"
    assert long_notes["long-dividend-lowvol"].startswith("尚未出票；每月最后一个交易日")
    assert "2026-10-30" in long_notes["long-dividend-lowvol"]
    # 有批次后去掉前缀
    (tmp_path / "2026-10-16_mid-repurchase-plan.json").write_text(
        json.dumps({"strategy_id": "mid-repurchase-plan", "signal_date": "2026-10-16", "picks": []}))
    notes2 = {s["id"]: s["next_batch_note"] for s in ms.strategies_overview("mid", db=None)}
    assert notes2["mid-repurchase-plan"] == "每个交易日盘后（17:35）选股，次日开盘买"
    assert notes2["mid-incentive-option"].startswith("尚未出票；")
