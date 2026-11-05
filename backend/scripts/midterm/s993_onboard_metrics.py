"""§993 CATA_DIP_h10 上线：写入 mid-repurchase-incentive-dip metrics（结构同 s973_onboard_metrics）。
逐笔来自 output/research/s992/（run_s992.sh）；回测 K=20、max_new=5、持 10 日。用法：python s993_onboard_metrics.py [--dry] [--src <s992 目录>]"""
import json, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s282_quality import tail_metrics, overall_label, verdict
from s309_excess_hs300 import load_hs300, summarize, idx_returns, METRICS, T

SID, NAME = "mid-repurchase-incentive-dip", "CATA_DIP_h10"
SRC = Path(__file__).resolve().parents[2] / "output/research/s992"
KEYS = ("n", "win", "avg", "pf", "days", "tot", "mdd", "bench", "excess", "ex_win")
P8 = ("P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12")


def main(dry=False, src=SRC):
    res = json.loads((src / "res.json").read_text())[NAME]
    res = {k: v for k, v in res.items() if not k.startswith("EXT")}  # EXT 只用于短线 4 窗口判定，不进卡片 8 段
    phases = {k: {x: v[x] for x in KEYS if x in v} for k, v in res.items()}
    ix = load_hs300()
    q = {"standard": "§282", "basis": "§993 短线第二批：回购预案(≥0.5%)∪首次期权激励草案 ∩ 公告前20日跌≥15% ∩ 信号日未封板；按r20升序；持10；真实成交"}
    for w, pk in (("R12", "R12 2025-10~2026-10"), ("N3", "N3 2024-01~2026-10")):
        d = pd.read_csv(src / f"trades_{NAME}_{w}.csv", dtype={"code": str})
        T.mkdir(parents=True, exist_ok=True)
        d.to_parquet(T / f"s992__{NAME}__{w}.parquet", index=False)
        d = d.sort_values(["entry", "exit", "code"])
        tm = tail_metrics((d["ret"] * 100).tolist())
        m = res[pk]
        assert tm["n"] == m["n"] and abs(tm["avg"] - m["avg"]) < 0.011, (w, tm, m)
        hs = summarize((d["ret"] * 100).tolist(), idx_returns(ix, d["entry"], d["exit"], "open"))
        q[w] = {"n": tm["n"], "avg": m["avg"], "pf": m["pf"], "mdd": m["mdd"], "win": m["win"], "streak": tm["streak"],
                "median": round(tm["median"], 2), "trim5": round(tm["trim5"], 2), "trim_both5": round(tm["trim_both5"], 2),
                "top10_share": None if tm["top10_share"] is None else round(tm["top10_share"], 3), "top10_red": tm["top10_red"],
                "label": tm["label"], "robust": tm["robust"], "excess": m.get("excess"), **hs}
    p8 = {k.split()[0]: v["avg"] for k, v in res.items() if k.split()[0] in P8}
    v = verdict("mid", q["R12"], q["N3"], p8)
    q.update({"label": overall_label(q["R12"], q["N3"]), "pass": v["pass"], "fail": v["fail"], "pos_phases": v["pos_phases"],
              "n_phases": v["n_phases"], "bear": {"W0": p8["W0"], "W00": p8["W00"]}, "phase_avg": p8,
              "label_rule": "§285：两头各去5%后均值>0 且 中位数>0（R12、N3 都满足才是稳健型）",
              "hs300_basis": "逐笔：策略净收益(已扣0.3%) − 同持有期沪深300（买入日开盘→卖出日开盘，不扣成本）；§309，只展示"})
    assert v["pass"], v
    ex = [round(res[k]["excess"], 2) for k in res if k.split()[0] in P8 and "excess" in res[k]]
    r12, n3 = res["R12 2025-10~2026-10"], res["N3 2024-01~2026-10"]
    entry = {
        "recent_12m": phases["R12 2025-10~2026-10"], "phases": phases,
        "config": ("scripts/shortline_research/sev_s992.py CATA_DIP_h10（§993：RP_r05∪IO ∩ r20≤−15% ∩ 信号日未封板，按r20升序）"
                   "realistic、回测 k_slots=20 max_new=5（实盘推荐不设此上限），次日开盘买持10日"),
        "window": "2019-01~2026-10-16 分阶段（P19~R12/N3）",
        "generated": "2026-11-05",
        "note": ("成本0.3%/笔；开盘涨停不买、跌停封死顺延卖出；回撤按逐日盯市。8 段每笔均为正。"
                 + (("同日同市值五分位超额(参考) " + " / ".join(f"{x:+.2f}" for x in ex) + "（只展示）。") if ex else "")
                 + f"最近12个月 {int(r12['n'])} 笔 +{r12['avg']:.2f}%/笔 PF{r12['pf']} 回撤 {r12['mdd']}%；"
                   f"近3年 {int(n3['n'])} 笔 +{n3['avg']:.2f}%/笔 PF{n3['pf']} 回撤 {n3['mdd']}%。"
                 "约95%信号来自回购预案；收益集中在市场急跌后的反弹月份（2024-02、2025-04、2026-07/08），2024-01 微盘踩踏当月每笔约 −9%。"
                 "P19–P21 每段只有 11–26 笔。实盘推荐不设仓位上限；回测指标按20仓位口径（k_slots=20、max_new=5）。"),
        "quality": q,
        "backtest_position_caps": {"k_slots": 20, "max_new": 5, "note": "仅回测；实盘推荐不设仓位上限"},
    }
    print(json.dumps({"pass": v["pass"], "label": q["label"], "R12": q["R12"]["avg"], "N3": q["N3"]["avg"],
                      "hs300": {w: q[w]["excess_hs300"] for w in ("R12", "N3")}}, ensure_ascii=False))
    if not dry:
        mt = json.loads(METRICS.read_text()); mt[SID] = entry
        METRICS.write_text(json.dumps(mt, ensure_ascii=False, indent=1)); print("written", METRICS)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(dry="--dry" in a, src=Path(a[a.index("--src") + 1]) if "--src" in a else SRC)
