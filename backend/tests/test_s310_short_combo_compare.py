"""§310：短线合并 vs 单技能比较脚本的纯函数与落盘结果自洽。"""
import json
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
import s310_short_combo_compare as S  # noqa: E402


def _t(sid, dt, code, net):
    return {"sid": sid, "dt": dt, "code": code, "net": net, "en": "20250102", "ex": "20250103"}


def test_dedup_keeps_priority_and_counts():
    ts = [_t("sector-relative-strength-leader", "2025-01-01", "000001", 1.0), _t("chao-gu-yang-jia-skill", "2025-01-01", "000001", 1.0),
          _t("chen-xiaoqun-skill", "2025-01-01", "000002", 2.0)]
    kept, st = S.dedup(ts)
    assert len(kept) == 2 and st["n_before"] == 3 and st["dup_keys"] == 1
    assert [k["sid"] for k in kept if k["code"] == "000001"] == ["chao-gu-yang-jia-skill"]


def test_metrics_drawdown_is_additive_daily_mean():
    ts = [_t("a", "2025-01-01", "1", 2.0), _t("a", "2025-01-02", "2", -3.0), _t("a", "2025-01-02", "3", -1.0), _t("a", "2025-01-03", "4", 1.0)]
    m = S.metrics(ts)
    assert m["n"] == 4 and m["mdd"] == -2.0 and abs(m["pf"] - 3 / 4) < 1e-6


def test_wavg_by_trade_count():
    assert S.wavg([{"n": 1, "avg": 0.0}, {"n": 3, "avg": 4.0}], "avg") == 3.0


def test_saved_result_consistent_with_rule():
    p = B / "output/research/s310/compare.json"
    if not p.exists():
        return
    r = json.loads(p.read_text())
    assert r["all_pass"] == all(r["criteria"].values())
    for w in ("R12", "N3"):
        assert r[w]["dedup"]["n_after"] == r[w]["combo"]["n"]
        assert r[w]["dedup"]["n_before"] == r[w]["combo_nodedup"]["n"] == sum(s["n"] for s in r[w]["singles"].values())
