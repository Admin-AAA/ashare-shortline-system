"""中长线前端支持：rule_score/selection_reason/cancel 字段、分页、手动补跑过滤（不连库、不联网）。"""
import json

from app.services import midterm_service as ms


def test_annotate_each_kind():
    cases = {
        "mid-small-cap-weekly": {"code": "1", "total_mv": 166416.0, "pe_ttm": 12.0},
        "mid-lowprice-smallcap-weekly": {"code": "1", "total_mv": 181000.0, "close": 2.87, "pe_ttm": None,
                                         "price_rank": 12, "mv_rank": 1},
        "mid-smallcap-dividend-weekly": {"code": "1", "total_mv": 281000.0, "pe_ttm": 15.2, "dv_ttm": 5.4645,
                                         "mv_rank": 370, "dv_snap": "2026-10-23"},
        "mid-smallcap-lowps-weekly": {"code": "1", "total_mv": 254000.0, "pe_ttm": 38.5, "ps_ttm": 0.3861,
                                      "mv_rank": 210, "dv_snap": "2026-10-23"},
        "mid-smallcap-reversal-weekly": {"code": "1", "total_mv": 254000.0, "pe_ttm": 38.5, "r20": -25.317, "mv_rank": 210},
        "mid-repurchase-plan": {"code": "1", "ratio": 0.01157, "repurchase_amount": 6e7},
        "mid-earnings-accel": {"code": "1", "score": 300.0, "dt_netprofit_yoy": 592.1},
        "mid-forecast-beat": {"code": "1", "score": 300.0, "p_change_min": 3318.0},
        "mid-incentive-option": {"code": "1", "r20": -0.0512, "score": 0.0512, "title": "x草案"},
        "mid-block-trade-premium": {"code": "1", "ratio": 0.0061, "premium": 0.01, "n_trades": 2},
        "long-dividend-lowvol": {"code": "1", "score": 9.0, "dv_ttm": 5.2, "vol60": 1.0},
    }
    expect = {"mid-small-cap-weekly": 16.64, "mid-lowprice-smallcap-weekly": 18.1, "mid-smallcap-dividend-weekly": 5.46, "mid-smallcap-lowps-weekly": 0.39, "mid-smallcap-reversal-weekly": -25.32, "mid-repurchase-plan": 1.157, "mid-incentive-option": -5.12,
              "mid-block-trade-premium": 1.0, "mid-earnings-accel": 592.1, "mid-forecast-beat": 3318.0}
    for sid, p in cases.items():
        out = ms.annotate_pick(sid, dict(p), 1)
        assert out["rule_score"] is not None and out["rule_score_label"] and out["selection_reason"], sid
        assert out["rule_score_better"] in ("higher", "lower")
        if sid in expect:
            assert out["rule_score"] == expect[sid], sid
        if sid in ("mid-earnings-accel", "mid-forecast-beat"):
            assert out["score"] == 300.0 and out["rule_score"] != out["score"], sid
            assert "封顶" not in out["rule_score_label"]
            assert out["rule_score_note"] == "排序时≥300%同等优先，平手按代码"
        else:
            assert out.get("rule_score_note") is None
    assert ms.annotate_pick("mid-repurchase-plan", {"code": "1"})["rule_score"] is None


def test_capped_yoy_display_keeps_saved_rank(tmp_path, monkeypatch):
    """列表展示真实同比，批内名次仍按已落盘的封顶分（同分再按代码），不按展示值重排。"""
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "_realize", lambda db, c, d, h: {"status": "pending"})
    picks = [
        {"code": "000688", "name": "国城矿业", "score": 300.0, "dt_netprofit_yoy": 784.7, "or_yoy": 51.7, "prev_dt_netprofit_yoy": 100.0},
        {"code": "002265", "name": "建设工业", "score": 300.0, "dt_netprofit_yoy": 1750.7, "or_yoy": 35.7, "prev_dt_netprofit_yoy": 40.0},
        {"code": "600001", "name": "低于封顶", "score": 80.0, "dt_netprofit_yoy": 80.0, "or_yoy": 25.0, "prev_dt_netprofit_yoy": 30.0},
    ]
    (tmp_path / "2026-10-26_mid-earnings-accel.json").write_text(json.dumps(
        {"strategy_id": "mid-earnings-accel", "signal_date": "2026-10-26", "picks": picks}, ensure_ascii=False))
    batch = ms.list_picks(None, "mid-earnings-accel", 5)[0]
    assert [p["code"] for p in batch["picks"]] == ["000688", "002265", "600001"]
    assert [p["selection_rank"] for p in batch["picks"]] == [1, 2, 3]
    assert [p["score"] for p in batch["picks"]] == [300.0, 300.0, 80.0]
    assert [p["rule_score"] for p in batch["picks"]] == [784.7, 1750.7, 80.0]
    assert batch["picks"][0]["rule_score_label"] == "扣非净利同比(%)"
    assert "784.7%" in batch["picks"][0]["selection_reason"]
    # 旧批次没有原始同比时，列退回封顶分，避免空白
    old = ms.annotate_pick("mid-earnings-accel", {"code": "1", "score": 300.0})
    assert old["rule_score"] == 300.0 and "封顶" not in old["rule_score_label"]
    fb = ms.annotate_pick("mid-forecast-beat", {"code": "300002", "score": 300.0, "p_change_min": 900.0, "net_profit_min": 1e4})
    assert fb["rule_score"] == 900.0 and fb["score"] == 300.0
    assert fb["rule_score_label"] == "预告净利增幅下限(%)"


def test_cancel_fields():
    p = ms.annotate_pick("mid-repurchase-plan", {"code": "1", "ratio": 0.01, "status": "skipped",
                                                 "reason": "开盘涨停买不进", "cancel_code": "limit_up_open"})
    assert p["cancel_code"] == "limit_up_open" and p["cancel_reason"] == "开盘涨停买不进"
    q = ms.annotate_pick("mid-repurchase-plan", {"code": "1", "ratio": 0.01, "status": "closed", "ret": 1.0})
    assert q["cancel_code"] is None and q["cancel_reason"] is None and q["ret_includes_cost"] is True


def test_pagination_and_horizon(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    for d in ("2026-10-12", "2026-10-13", "2026-10-14"):
        for sid in ("mid-earnings-accel", "long-dividend-lowvol"):
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
    assert ms.list_picks_page(None, "long-dividend-lowvol", None, 1, 30)["total"] == 3


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
    assert set(res) == {"long-dividend-lowvol", "long-growth-streak"}
    res = ms.generate_picks(None, "2026-10-16", strategy_ids=["long-dividend-lowvol"])
    assert set(res) == {"long-dividend-lowvol"}
    # §338 已下线的低换手低波：按 id 指名也不再出票（generate_picks 只遍历现役 STRATEGIES）
    assert ms.generate_picks(None, "2026-10-30", strategy_ids=["long-lowturn-lowvol"]) == {}


def test_retired_strategies_not_live():
    """§278/§282/§1055：不达标的 /long 策略已下线，不在现役 STRATEGIES，也不出票。
    §338：long-lowturn-lowvol 近12个月满期卖出每笔为负，下线。
    §1055：long-net-cash-switch 生产滚动 R12 每笔 −7.16%、verdict=negative，重新下线。
    2026-11-03：SUE∩质量家族非冠军一并退出日频，定义留在 RETIRED_STRATEGIES。"""
    long_retired = {"long-repurchase-big", "long-current-debt-down", "long-dividend-increase",
                    "long-undist-profit-growth", "long-lowturn-lowvol", "long-net-cash-switch"}
    sue_demoted = set(ms.SUE_LADDER_DEMOTED)
    near_dup = set(ms.NON_SUE_NEAR_DUP_DEMOTED)
    assert long_retired <= set(ms.RETIRED_STRATEGIES)
    assert not long_retired & set(ms.STRATEGIES)
    assert set(ms.RETIRED_STRATEGIES) == long_retired | sue_demoted | near_dup


def test_sue_ladder_daily_pool_is_six_champions(tmp_path, monkeypatch):
    """日频只留 6 个 SUE 冠军；其余 35 条不进 STRATEGIES / generate_picks / /mid 列表。历史标注仍走 RETIRED。"""
    champs = set(ms.SUE_LADDER_CHAMPIONS)
    demoted = set(ms.SUE_LADDER_DEMOTED)
    assert len(champs) == 6 and len(demoted) == 35
    assert not champs & demoted
    live_sue = {sid for sid in ms.STRATEGIES if "sue" in sid}
    assert live_sue == champs
    assert demoted <= set(ms.RETIRED_STRATEGIES)
    assert set(ms.SUE_LADDER_DEMOTE_REASONS) == demoted
    for sid in ("mid-forecast-beat", "mid-repurchase-plan-underreact", "mid-earnings-accel",
                "mid-ea-td-vol", "mid-turn-days-vol"):
        assert sid in ms.STRATEGIES
    for sid in ("mid-repurchase-plan", "mid-ncs-td-vol"):
        assert sid not in ms.STRATEGIES and sid in ms.RETIRED_STRATEGIES
    overview = {s["id"] for s in ms.strategies_overview("mid", db=None)}
    assert champs <= overview and not demoted & overview
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    assert ms.generate_picks(None, "2026-10-30", strategy_ids=["mid-qsexp-sue-vol"]) == {}
    ann = ms.annotate_pick("mid-sue-accel", {
        "code": "000001", "score": 3.0, "sue": 2.4, "q_sq": 1e8,
        "dt_netprofit_yoy": 60, "prev_dt_netprofit_yoy": 40, "or_yoy": 25,
        "ann_date": "20261030", "end_date": "20260930",
    }, 1)
    assert ann["rule_score_label"] == "SUE(封顶20)"
    assert "SUE 2.40" in ann["selection_reason"]
    rows = ms.sue_ladder_rejected_rows()
    assert rows[0]["name"].startswith("SUE∩质量家族")
    assert {r["name"] for r in rows[1:]} == {
        f"{ms.RETIRED_STRATEGIES[sid]['name']}（{sid}）" for sid in demoted
    }


def test_non_sue_near_dup_cull_leaves_champions(tmp_path, monkeypatch):
    """回购三张卡、预告弱母集、激励弱反应子集、资产负债表三兄弟和弱尾退出日频。SUE 六冠军不动。"""
    demoted = set(ms.NON_SUE_NEAR_DUP_DEMOTED)
    champs = set(ms.NON_SUE_NEAR_DUP_CHAMPIONS)
    protected = set(ms.NON_SUE_CULL_PROTECTED)
    sue = set(ms.SUE_LADDER_CHAMPIONS)
    assert len(demoted) == 17 and len(champs) == 8
    assert not demoted & champs and not demoted & protected and not demoted & sue
    assert demoted <= set(ms.RETIRED_STRATEGIES)
    assert not demoted & set(ms.STRATEGIES)
    assert champs | protected | sue <= set(ms.STRATEGIES)
    live_mid = {sid for sid, meta in ms.STRATEGIES.items() if meta.get("horizon") == "mid"}
    # 47→30 收敛后 +3：§993 mid-repurchase-incentive-dip（持10）、§997 mid-divplan-dip、§1000 mid-earnings-accel-dip（持5）
    # +1：§1011 mid-sector-dip-stabilize（持5）
    assert len(live_mid) == 34
    assert live_mid & sue == sue
    overview = {s["id"] for s in ms.strategies_overview("mid", db=None)}
    assert live_mid == overview
    assert not demoted & overview
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    assert ms.generate_picks(None, "2026-11-03", strategy_ids=["mid-repurchase-plan"]) == {}
    assert ms.generate_picks(None, "2026-11-03", strategy_ids=["mid-repurchase-underreact"]) == {}
    rows = ms.non_sue_near_dup_rejected_rows()
    assert rows[0]["name"].startswith("非 SUE 近重复")
    assert "47→30" in rows[0]["name"]
    assert "仓位" in rows[0]["reason"]
    assert {r["name"] for r in rows[1:]} == {
        f"{ms.RETIRED_STRATEGIES[sid]['name']}（{sid}）" for sid in demoted
    }


def test_next_batch_note(tmp_path, monkeypatch):
    """strategies_overview 带动态 next_batch_note；无批次前缀「尚未出票；」。"""
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ms, "_next_month_end_trade_date", lambda db, today=None: "2026-10-30")
    # 无批次：全部前缀尚未出票
    notes = {s["id"]: s["next_batch_note"] for s in ms.strategies_overview("mid", db=None)}
    assert "mid-repurchase-plan" not in notes
    assert notes["mid-repurchase-plan-underreact"] == "尚未出票；每个交易日盘后（17:35）选股，次日开盘买"
    assert notes["mid-small-cap-weekly"] == "尚未出票；每个周五收盘后选股，次日开盘买"
    assert notes["mid-block-trade-premium"] == "尚未出票；每晚约 21:00 大宗数据发布后选股，次日开盘买"
    long_notes = {s["id"]: s["next_batch_note"] for s in ms.strategies_overview("long", db=None)}
    assert "long-lowturn-lowvol" not in long_notes  # §338 已下线
    assert long_notes["long-dividend-lowvol"] == "尚未出票；每月最后一个交易日收盘后选股，下一批：2026-10-30，次日开盘买"
    assert "2026-10-30" in long_notes["long-dividend-lowvol"]
    # 有批次后去掉前缀
    (tmp_path / "2026-10-16_mid-repurchase-plan-underreact.json").write_text(
        json.dumps({"strategy_id": "mid-repurchase-plan-underreact", "signal_date": "2026-10-16", "picks": []}))
    notes2 = {s["id"]: s["next_batch_note"] for s in ms.strategies_overview("mid", db=None)}
    assert notes2["mid-repurchase-plan-underreact"] == "每个交易日盘后（17:35）选股，次日开盘买"
    assert notes2["mid-incentive-option"].startswith("尚未出票；")


def test_retired_strategies_not_in_horizon_lists(tmp_path, monkeypatch):
    """§306：已下线策略（RETIRED_STRATEGIES）与查不到的 id 不进 /mid、/long 批次列表，也不再默认当 mid；
    名称查「现役→已下线」表，不显示英文 id；历史文件按 strategy_id 显式查询仍可读，持有期按 60 日。"""
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path)
    d = "2026-10-14"
    for sid in ("mid-repurchase-plan-underreact", "mid-repurchase-plan", "long-dividend-lowvol",
                "long-repurchase-big", "long-lowturn-lowvol", "mystery-strategy"):
        (tmp_path / f"{d}_{sid}.json").write_text(json.dumps(
            {"strategy_id": sid, "signal_date": d, "picks": [{"code": "000001", "ratio": 0.02}]}))
    monkeypatch.setattr(ms, "_realize", lambda db, c, dd, h: {"status": "pending"})
    mid = ms.list_picks_page(None, None, "mid", page=1, page_size=30)
    assert [b["strategy_id"] for b in mid["batches"]] == ["mid-repurchase-plan-underreact"]
    rp = ms.list_picks_page(None, "mid-repurchase-plan", None, 1, 30)["batches"][0]
    assert rp["strategy_name"] == ms.RETIRED_STRATEGIES["mid-repurchase-plan"]["name"] and rp["hold_days"] == 20
    lng = ms.list_picks_page(None, None, "long", page=1, page_size=30)
    assert [b["strategy_id"] for b in lng["batches"]] == ["long-dividend-lowvol"]
    # §338 下线后历史文件保留，按 id 显式查询仍可读、持有期仍按 60 日结算
    lt = ms.list_picks_page(None, "long-lowturn-lowvol", None, 1, 30)["batches"][0]
    assert lt["strategy_name"] == ms.RETIRED_STRATEGIES["long-lowturn-lowvol"]["name"] and lt["hold_days"] == 60
    old = ms.list_picks_page(None, "long-repurchase-big", None, 1, 30)["batches"][0]
    assert old["strategy_name"] == ms.RETIRED_STRATEGIES["long-repurchase-big"]["name"]
    assert old["horizon"] == "long" and old["hold_days"] == 60
    ghost = ms.list_picks_page(None, "mystery-strategy", None, 1, 30)["batches"][0]
    assert ghost["strategy_name"] == "已下线策略" and "mystery" not in ghost["strategy_name"]
    assert ghost["horizon"] == "mid"


def test_r20_map_compounds_last_20_rows_and_skips_short_or_stale():
    class _R:
        def __init__(self, rows): self.rows = rows
        def all(self): return self.rows

    class _DB:
        def __init__(self, rows): self.rows = rows
        def execute(self, *a, **k): return _R(self.rows)

    d = [f"2026-09-{i:02d}" for i in range(1, 29)]
    rows = [("A", x, -1.0) for x in d[-20:]] + [("A", d[0], 50.0)]         # 只取最近20行：0.99**20-1
    rows += [("B", x, 1.0) for x in d[-19:]]                                # 不足20行
    rows += [("C", x, 1.0) for x in d[:20]]                                 # 最后一行不是 as_of（停牌）
    rows += [("D", x, -50.0) for x in d[-20:]]                              # 单日跌幅限幅 −35%
    out = ms._r20_map(_DB(rows), ["A", "B", "C", "D"], [d[-1]] * 251, d[-1])
    assert set(out) == {"A", "D"}
    assert abs(out["A"] - round((0.99 ** 20 - 1) * 100, 3)) < 1e-6
    assert abs(out["D"] - round((0.65 ** 20 - 1) * 100, 3)) < 1e-6
