"""§359 FINA_h20 上线：写入 midterm_backtest_metrics.json（mid-fina-exp-drop）。"""
import json, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s282_quality import tail_metrics, overall_label, verdict
from s309_excess_hs300 import load_hs300, summarize, idx_returns, METRICS, T

SID, NAME = "mid-fina-exp-drop", "FINA_h20"
SRC = Path(__file__).resolve().parents[2] / "output/research/s359"
KEYS = ("n", "win", "avg", "pf", "days", "tot", "mdd", "bench", "excess", "ex_win")


def main(dry=False):
    res = json.loads((SRC / "res.json").read_text())[NAME]
    phases = {k: {x: v[x] for x in KEYS if x in v} for k, v in res.items()}
    ix = load_hs300()
    q = {"standard": "§282", "basis": "§359 复判（选股=§203/lt_research70，ST=stock_metadata_history；真实成交、未调参）"}
    for w, pk in (("R12", "R12 2025-10~2026-10"), ("N3", "N3 2024-01~2026-10")):
        d = pd.read_csv(SRC / f"trades_{NAME}_{w}.csv", dtype={"code": str})
        T.mkdir(parents=True, exist_ok=True)
        d.to_parquet(T / f"r140__{NAME}__{w}.parquet", index=False)
        d = d.sort_values(["entry", "exit", "code"])
        tm = tail_metrics((d["ret"] * 100).tolist())
        m = res[pk]
        assert tm["n"] == m["n"] and abs(tm["avg"] - m["avg"]) < 0.011, (w, tm, m)
        hs = summarize((d["ret"] * 100).tolist(), idx_returns(ix, d["entry"], d["exit"], "open"))
        q[w] = {"n": tm["n"], "avg": m["avg"], "pf": m["pf"], "mdd": m["mdd"], "win": m["win"], "streak": tm["streak"],
                "median": round(tm["median"], 2), "trim5": round(tm["trim5"], 2), "trim_both5": round(tm["trim_both5"], 2),
                "top10_share": None if tm["top10_share"] is None else round(tm["top10_share"], 3), "top10_red": tm["top10_red"],
                "label": tm["label"], "robust": tm["robust"], "excess": None, **hs}
    p8 = {k.split()[0]: v["avg"] for k, v in res.items() if not k.startswith("N3")}
    v = verdict("mid", q["R12"], q["N3"], p8)
    q.update({"label": overall_label(q["R12"], q["N3"]), "pass": v["pass"], "fail": v["fail"], "pos_phases": v["pos_phases"],
              "n_phases": v["n_phases"], "bear": {"W0": p8["W0"], "W00": p8["W00"]}, "phase_avg": p8,
              "label_rule": "§285：两头各去5%后均值>0 且 中位数>0（R12、N3 都满足才是稳健型）",
              "hs300_basis": "逐笔：策略净收益(已扣0.3%) − 同持有期沪深300（买入日开盘→卖出日开盘，不扣成本）；§309，只展示"})
    assert v["pass"], v
    ex = [round(res[k]["excess"], 2) for k in res if not k.startswith("N3")]
    entry = {
        "recent_12m": phases["R12 2025-10~2026-10"], "phases": phases,
        "config": ("scripts/midterm/lt_research140.py FINA_h20（§359=§203 规则未调参；ST stock_metadata_history）："
                   "正式财报首次披露 finaexp_of_gr 本期∈[-2,15]、去年∈[-2,25]、降幅≥0.5，按降幅（封顶8）降序；"
                   "realistic、回测 k_slots=20 max_new=5（实盘推荐不设此上限），次日开盘买持20日"),
        "window": "2019-01~2026-10-16 分阶段（P19~R12/N3）",
        "generated": "2026-10-31",
        "note": ("成本0.3%/笔；开盘涨停不买、跌停封死顺延卖出；回撤按逐日盯市。8 个阶段每笔 6/8 为正（W0 −1.95、W00 −2.47，均≥−5）；"
                 "同日同市值五分位超额(参考) " + " / ".join(f"{x:+.2f}" for x in ex) + "（只展示）。"
                 "最近12个月 93 笔 +2.77%/笔 PF1.66 回撤 −6.0%；近3年 258 笔 +3.06%/笔 PF1.70 回撤 −13.1%。"
                 "收益来源标签按 §285：R12 稳健型、N3 彩票型（整体记彩票型）。原 §203/§279 因同市值超额标准否决；§359 按 §282 绝对收益标准复判达标后上线。"
                 "实盘推荐不设仓位上限；回测指标按20仓位口径（k_slots=20、max_new=5）。"),
        "quality": q,
        "backtest_position_caps": {"k_slots": 20, "max_new": 5, "note": "仅回测；实盘推荐不设仓位上限"},
    }
    print(json.dumps({"pass": v["pass"], "label": q["label"], "R12": q["R12"]["avg"], "N3": q["N3"]["avg"]}, ensure_ascii=False))
    if not dry:
        mt = json.loads(METRICS.read_text()); mt[SID] = entry
        METRICS.write_text(json.dumps(mt, ensure_ascii=False, indent=1)); print("written", METRICS)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
