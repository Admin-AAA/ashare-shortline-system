"""§306：中线策略卡的 recent_12m / phases / excess 已刷新为修正 ST 口径，与同文件 quality 块（§282）一致。"""
import datetime as dt
import json
from pathlib import Path

from app.services.strategy_rank import rank_horizon_strategies

F = Path(__file__).resolve().parents[1] / "app/services/midterm_backtest_metrics.json"
MID = ["mid-small-cap-weekly", "mid-incentive-option", "mid-earnings-accel", "mid-forecast-beat",
       "mid-repurchase-plan", "mid-block-trade-premium", "mid-lowprice-smallcap-weekly"]


def _load():
    return json.loads(F.read_text())


def test_cards_match_quality_block():
    m = _load()
    for sid in MID:
        e, q = m[sid], m[sid]["quality"]
        r12 = e["recent_12m"]
        assert int(r12["n"]) == q["R12"]["n"] and abs(r12["avg"] - q["R12"]["avg"]) < 0.011, sid
        assert abs(r12["pf"] - q["R12"]["pf"]) < 0.011 and abs(r12["mdd"] - q["R12"]["mdd"]) < 0.11, sid
        n3 = next(v for k, v in e["phases"].items() if k.startswith("N3"))  # 每个策略卡都有近3年行
        assert int(n3["n"]) == q["N3"]["n"] and abs(n3["avg"] - q["N3"]["avg"]) < 0.011, sid
        assert abs(n3["pf"] - q["N3"]["pf"]) < 0.011, sid
        for k, v in e["phases"].items():
            tag = k.split()[0]
            if tag in q["phase_avg"]:
                assert abs(v["avg"] - q["phase_avg"][tag]) < 0.011, (sid, tag)
        pos = sum(1 for k, v in e["phases"].items() if not k.startswith("N3") and v["avg"] > 0)
        assert pos == q["pos_phases"], sid  # 卡面「几段为正」与收益来源块一致
        assert "观察档" not in e["note"], sid


def test_notes_use_corrected_numbers():
    m = _load()
    assert "381 笔" in m["mid-block-trade-premium"]["note"] and "−21.0%" in m["mid-block-trade-premium"]["note"]
    assert "+3.09%" in m["mid-earnings-accel"]["note"] and "−24.2%" in m["mid-earnings-accel"]["note"]
    assert "−4.84%" in m["mid-forecast-beat"]["note"]
    assert "实盘推荐不设该上限" in m["mid-small-cap-weekly"]["note"]


def test_rank_uses_real_n3_for_small_cap():
    items = [{"id": s, "metrics": _load()[s]} for s in MID]
    ranked = rank_horizon_strategies(items, dt.date(2026, 10, 25))
    sc = next(x for x in ranked if x["id"] == "mid-small-cap-weekly")
    assert sc["rank_components"]["n3_source"] == "N3"
    assert abs(sc["rank_components"]["n3_avg"] - 4.07) < 0.011
