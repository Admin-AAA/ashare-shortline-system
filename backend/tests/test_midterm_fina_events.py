"""CLD_h60 / DINC_h60 / UDP_h60 实盘候选：口径（阈值/首次公告/去年同期基准/排序）+ 出票与名额（不连库、不调网关）。"""
import datetime as dt

import pandas as pd

from app.services import midterm_fina_events as fe
from app.services import midterm_service as ms


def _dates(as_of="2026-10-23", n=251):
    d, out = dt.date.fromisoformat(as_of), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


def _setup(monkeypatch, fina=None, div=None):
    monkeypatch.setattr(fe, "_window", lambda db, as_of: (_dates(as_of), "20261023", "20261023"))
    monkeypatch.setattr(fe, "_attach", lambda db, as_of, dates, cands: sorted(
        cands.values(), key=lambda x: (-x["score"], x["code"])))
    fe._fina_cache.clear()
    if fina is not None:
        def fp(period):
            df = fina.get(period)
            return None if df is None else fe._fina_store(period, df)
        monkeypatch.setattr(fe, "_fina_period", fp)
    if div is not None:
        monkeypatch.setattr(fe, "_local_div", lambda: (fe._clean_div(div), "20261016"))
        monkeypatch.setattr(fe, "_div_day", lambda day: fe._clean_div(pd.DataFrame(
            [r for r in div.to_dict("records") if str(r["ann_date"]) == day], columns=div.columns)))
        monkeypatch.setattr(fe, "_div_code", lambda tc: fe._clean_div(div[div["ts_code"] == tc]))
        monkeypatch.setattr(ms, "_liq_ok", lambda db, as_of, dates, codes: {c: {} for c in codes})


def _f(ts, ann, end, cld=None, udp=None, npy=10.0):
    return {"ts_code": ts, "ann_date": ann, "end_date": end, "currentdebt_to_debt": cld,
            "undist_profit_ps": udp, "netprofit_yoy": npy}


def test_cld_rules_and_order(monkeypatch):
    cur = pd.DataFrame([
        _f("000001.SZ", "20261023", "20260930", cld=50.0),   # 降 10 -> 入选
        _f("000002.SZ", "20261023", "20260930", cld=60.0),   # 降 4 -> 不够
        _f("000003.SZ", "20261023", "20260930", cld=96.0),   # 本期>95 -> 不要
        _f("000004.SZ", "20261023", "20260930", cld=30.0),   # 降 45 -> 入选、封顶40
        _f("000005.SZ", "20261022", "20260930", cld=40.0),   # 公告日不在窗口 -> 不要
        _f("830001.BJ", "20261023", "20260930", cld=40.0),   # 北交所 -> 不要
        _f("000006.SZ", "20261023", "20260930", cld=25.0),   # 本期<30 -> 不要
        _f("000007.SZ", "20261024", "20260930", cld=50.0),   # 窗口外
    ])
    prev = pd.DataFrame([
        _f("000001.SZ", "20251025", "20250930", cld=60.0), _f("000002.SZ", "20251025", "20250930", cld=64.0),
        _f("000003.SZ", "20251025", "20250930", cld=99.0), _f("000004.SZ", "20251025", "20250930", cld=75.0),
        _f("000005.SZ", "20251025", "20250930", cld=70.0), _f("830001.BJ", "20251025", "20250930", cld=70.0),
        _f("000006.SZ", "20251025", "20250930", cld=50.0), _f("000007.SZ", "20251025", "20250930", cld=70.0),
    ])
    _setup(monkeypatch, {"20260930": cur, "20250930": prev,
                         "20260630": pd.DataFrame(columns=cur.columns), "20250630": pd.DataFrame(columns=cur.columns),
                         "20260331": pd.DataFrame(columns=cur.columns), "20250331": pd.DataFrame(columns=cur.columns),
                         "20251231": pd.DataFrame(columns=cur.columns), "20241231": pd.DataFrame(columns=cur.columns)})
    res = fe.cld_candidates(None, "2026-10-23")
    assert [p["code"] for p in res] == ["000004", "000001"]
    assert res[0]["score"] == 40.0 and res[1]["delta"] == 10.0


def test_first_announcement_only_and_null_metric_dropped(monkeypatch):
    # 同一报告期两次公告：只认最早一次；最早一次不在窗口 -> 不算新事件
    cur = pd.DataFrame([
        _f("000001.SZ", "20261010", "20260930", cld=50.0),
        _f("000001.SZ", "20261023", "20260930", cld=50.0),
    ])
    prev = pd.DataFrame([_f("000001.SZ", "20251025", "20250930", cld=70.0)])
    empty = pd.DataFrame(columns=cur.columns)
    _setup(monkeypatch, {"20260930": cur, "20250930": prev, "20260630": empty, "20250630": empty,
                         "20260331": empty, "20250331": empty, "20251231": empty, "20241231": empty})
    assert fe.cld_candidates(None, "2026-10-23") == []


def test_udp_rules(monkeypatch):
    cur = pd.DataFrame([
        _f("000001.SZ", "20261023", "20260930", udp=2.0),    # 2/1-1=100% -> 入选
        _f("000002.SZ", "20261023", "20260930", udp=1.2),    # 20% -> 不够
        _f("000003.SZ", "20261023", "20260930", udp=0.4),    # 本期<0.5 -> 不要
        _f("000004.SZ", "20261023", "20260930", udp=9.0),    # 去年<=0 -> 不要
        _f("000005.SZ", "20261023", "20260930", udp=9.0),    # 900% -> 封顶 2.0，与 000006 并列按代码
        _f("000006.SZ", "20261023", "20260930", udp=30.0),
    ])
    prev = pd.DataFrame([
        _f("000001.SZ", "20251025", "20250930", udp=1.0), _f("000002.SZ", "20251025", "20250930", udp=1.0),
        _f("000003.SZ", "20251025", "20250930", udp=0.2), _f("000004.SZ", "20251025", "20250930", udp=-1.0),
        _f("000005.SZ", "20251025", "20250930", udp=0.9), _f("000006.SZ", "20251025", "20250930", udp=1.0),
    ])
    empty = pd.DataFrame(columns=cur.columns)
    _setup(monkeypatch, {"20260930": cur, "20250930": prev, "20260630": empty, "20250630": empty,
                         "20260331": empty, "20250331": empty, "20251231": empty, "20241231": empty})
    res = fe.udp_candidates(None, "2026-10-23")
    assert [p["code"] for p in res] == ["000005", "000006", "000001"]
    assert res[0]["score"] == 2.0 and abs(res[2]["growth"] - 1.0) < 1e-9


def test_fina_gateway_failure_is_none(monkeypatch):
    empty = pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "currentdebt_to_debt", "undist_profit_ps", "netprofit_yoy"])
    _setup(monkeypatch, {"20260930": None})
    assert fe.cld_candidates(None, "2026-10-23") is None


def _d(ts, end, ann, proc, dps):
    return {"ts_code": ts, "end_date": end, "ann_date": ann, "div_proc": proc, "cash_div_tax": dps}


def test_dinc_rules(monkeypatch):
    rows = [
        # 000001：去年有实施记录 0.10，今年首次预案 0.20（+100%）-> 入选
        _d("000001.SZ", "20251231", "20260320", "实施", 0.10),
        _d("000001.SZ", "20251231", "20260301", "预案", 0.08),
        _d("000001.SZ", "20261231", "20261023", "预案", 0.20),
        # 000002：去年只有预案 0.10（无实施），今年 0.12（+20%）-> 不够
        _d("000002.SZ", "20251231", "20260301", "预案", 0.10),
        _d("000002.SZ", "20261231", "20261023", "预案", 0.12),
        # 000003：今年预案不是首次（之前已有预案）-> 不算
        _d("000003.SZ", "20251231", "20260301", "预案", 0.10),
        _d("000003.SZ", "20261231", "20261001", "预案", 0.20),
        _d("000003.SZ", "20261231", "20261023", "预案", 0.30),
        # 000004：去年无记录 -> 不要
        _d("000004.SZ", "20261231", "20261023", "预案", 0.30),
        # 000005：+50% 入选，排在 000001(+100%) 之后
        _d("000005.SZ", "20251231", "20260301", "预案", 0.10),
        _d("000005.SZ", "20261231", "20261023", "预披露", 0.15),
        # 000006：派现为 0 -> 不要
        _d("000006.SZ", "20251231", "20260301", "预案", 0.10),
        _d("000006.SZ", "20261231", "20261023", "预案", 0.0),
    ]
    _setup(monkeypatch, div=pd.DataFrame(rows))
    res = fe.dinc_candidates(None, "2026-10-23")
    assert [p["code"] for p in res] == ["000001", "000005"]
    assert abs(res[0]["inc"] - 1.0) < 1e-9 and res[0]["prev_dps"] == 0.10


def test_new_strategies_registered_and_pick_written(monkeypatch, tmp_path):
    for sid in ("long-current-debt-down", "long-dividend-increase", "long-undist-profit-growth"):
        meta = ms.STRATEGIES[sid]
        assert meta["horizon"] == "long" and meta["hold_days"] == 60 and meta["schedule"] == "daily"
        assert "k_slots" not in meta and "max_new" not in meta  # 实盘推荐不设仓位上限
    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    monkeypatch.setattr(ms, "_held_codes", lambda db, sid, d: {"000002"})
    cands = [{"code": f"00000{i}", "name": "x", "score": 10.0 - i, "ann_date": "20261023", "end_date": "20260930",
              "cur": 40.0, "prev": 55.0, "delta": 15.0} for i in range(1, 9)]
    from app.services import midterm_fina_events as fe2
    monkeypatch.setattr(fe2, "cld_candidates", lambda db, d: cands)
    monkeypatch.setattr(fe2, "dinc_candidates", lambda db, d: None)
    monkeypatch.setattr(fe2, "udp_candidates", lambda db, d: [])
    res = ms.generate_picks(None, "2026-10-23", force=True, strategy_ids=["long-current-debt-down", "long-dividend-increase", "long-undist-profit-growth"])
    assert res["long-current-debt-down"] == 7            # 不设每日新进上限：8 只候选去掉已持有的 000002，其余全出
    assert res["long-dividend-increase"] == "no_data" and res["long-undist-profit-growth"] == 0
    import json
    got = json.loads((tmp_path / "picks" / "2026-10-23_long-current-debt-down.json").read_text())
    assert [p["code"] for p in got["picks"]] == ["000001", "000003", "000004", "000005", "000006", "000007", "000008"]
    ann = ms.annotate_pick("long-current-debt-down", got["picks"][0], 1)
    assert ann["rule_score"] == 9.0 and "同比下降 15.0 个百分点" in ann["selection_reason"]
