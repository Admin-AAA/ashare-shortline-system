"""hydrate_panel_ivol：只补缺失的 ivol 分位，已有值不动；DB 也没有的保持缺失。"""
from __future__ import annotations

import scripts.hydrate_panel_ivol as H


def test_hydrate_fills_only_missing(monkeypatch):
    calls = []

    def fake(field_map):
        def _ld(_db, dates):
            calls.append(tuple(dates))
            return field_map
        return _ld

    monkeypatch.setattr(H, "FIELDS", {
        "ivol20_pct": fake({("2023-08-01", "000001"): 0.9, ("2023-08-01", "000002"): 0.1}),
        "ivol_dn20_pct": fake({}),
    })
    p = {"days": [{"signal_date": "2023-08-01", "cards": [
        {"code": "000001"},
        {"code": "000002", "ivol20_pct": 0.5, "ivol_dn20_pct": 0.4},
        {"code": "000003"},
    ]}]}
    st = H.hydrate_panel(p, db=None)
    cards = p["days"][0]["cards"]
    assert cards[0]["ivol20_pct"] == 0.9
    assert cards[1]["ivol20_pct"] == 0.5  # 已有值不覆盖
    assert cards[2].get("ivol20_pct") is None  # DB 也缺 → 保持缺失
    assert cards[0].get("ivol_dn20_pct") is None
    assert st == {"ivol20_pct": {"missing": 2, "filled": 1}, "ivol_dn20_pct": {"missing": 2, "filled": 0}}
    assert H.hydrate_panel(p, db=None)["ivol20_pct"]["filled"] == 0  # 幂等
