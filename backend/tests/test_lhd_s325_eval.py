"""§325 追龙评估脚本：窗口按买入日切分、判定函数在亏损/达标两种合成数据上的行为。"""
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/shortline_research")); sys.path.insert(0, str(B / "scripts/midterm"))
import lhd_s325_eval as E  # noqa: E402


def _t(en, net, code="1"):
    return {"sid": "V", "dt": en, "code": code, "net": net, "en": en, "ex": en, "hold": 2, "L": 1}


def test_sel_uses_entry_date_window():
    ts = [_t("20251001", 1.0), _t("20250930", 2.0), _t("20261016", 3.0), _t("20261017", 4.0)]
    assert [t["net"] for t in E.sel(ts, "R12")] == [1.0, 3.0]
    assert [t["net"] for t in E.sel(ts, "EXT")] == [2.0]


def _m(avg, pf=2.0, n=60, mdd=-10, trim=1.0, med=1.0):
    return {"avg": avg, "pf": pf, "n": n, "mdd": mdd, "trim_both5": trim, "median": med}


def test_judge_fails_on_losing_and_passes_on_good():
    good = {w: _m(2.0) for w in ("R12", "N3", "EXT", "W0", "W00")}
    ov = {"N3_share_max": 0.0, "N3_overlapped_skill_avg": 1.0}
    c = E.judge(good, {"R12": 0.0, "N3": 0.0}, 1.5, ov)
    assert all(c.values()) and len(c) == 8
    bad = dict(good); bad["R12"] = _m(-0.5, pf=0.8, trim=-1, med=-1)
    assert not E.judge(bad, {"R12": 0.0, "N3": 0.0}, 1.5, ov)["1_avg_pos"]
    few = dict(good); few["N3"] = _m(2.0, n=10)
    assert not E.judge(few, {"R12": 0.0, "N3": 0.0}, 1.5, ov)["5_n"]
    heavy = {"N3_share_max": 0.8, "N3_overlapped_skill_avg": 3.0}
    assert not E.judge(good, {"R12": 0.0, "N3": 0.0}, 1.5, heavy)["8_overlap"]
