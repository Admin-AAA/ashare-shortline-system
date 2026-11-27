"""L-241 mid-unlock-dip 上线：写入 midterm_backtest_metrics.json 的指标卡。

逐笔来自 output/mid-newsignal-20261127/cand_N1_unlock_after_drop.parquet（cands4.py N1，预注册参数）：
解禁日同日合计占总股本≥5%、解禁日前 20 日跌≥10%，流动性口径同 _liq_ok、剔除北交所，按比例取前 5，
次日开盘买（开盘涨停不买）、第 20 个交易日开盘卖（跌停封死顺延），扣 0.07% 往返税费；超额对全市场等权 EWALL.A。
数据从 2022-04 起（本地日K不早于此），没有 P19~P21 三段。
回撤口径：每笔占 1/20 仓位，按平仓日累加成净值（不复利），取最大回撤。

用法：python unlock_dip_onboard_metrics.py --src <cand_N1 parquet> [--dry]
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from s282_quality import overall_label, tail_metrics, verdict  # noqa: E402
from s309_excess_hs300 import idx_returns, load_hs300, summarize  # noqa: E402

SID = "mid-unlock-dip"
METRICS = Path(__file__).resolve().parents[2] / "app/services/midterm_backtest_metrics.json"
PHASES = {
    "W0 2022-04~2023-03": ("2022-04-01", "2023-03-31"),
    "W00 2023-04~12": ("2023-04-01", "2023-12-31"),
    "W1 2024": ("2024-01-01", "2024-12-31"),
    "W2 2025-01~09": ("2025-01-01", "2025-09-30"),
    "R12 2025-10~2026-10": ("2025-10-01", "2026-10-31"),
    "N3 2024-01~2026-10": ("2024-01-01", "2026-10-31"),
}


def _mdd(d: pd.DataFrame) -> float:
    # 每笔占 1/20 仓位（回测 20 仓位口径），按平仓日累加、不复利；逐日连乘 20 日收益会把回撤放大数倍。
    eq = 1 + d.groupby("exit")["net"].sum().sort_index().cumsum() / 100 / 20
    return round(float((eq / eq.cummax() - 1).min() * 100), 1)


def _stats(d: pd.DataFrame) -> dict:
    r = d["net"]
    pos, neg = r[r > 0].sum(), -r[r <= 0].sum()
    return {"n": float(len(d)), "win": round(float((r > 0).mean() * 100), 1), "avg": round(float(r.mean()), 2),
            "pf": round(float(pos / neg), 2) if neg > 0 else None, "days": 20.0,
            "tot": round(float(r.sum()), 1), "mdd": _mdd(d),
            "bench": round(float((d["net"] - d["ex"]).mean()), 2), "excess": round(float(d["ex"].mean()), 2),
            "ex_win": round(float((d["ex"] > 0).mean() * 100), 1)}


def main(src: Path, dry: bool) -> None:
    t = pd.read_parquet(src).sort_values(["sd", "exit", "code"])
    t["sd"], t["exit"] = t["sd"].astype(str), t["exit"].astype(str)
    phases = {k: _stats(t[t["sd"].between(a, b)]) for k, (a, b) in PHASES.items()}
    ix = load_hs300()
    days = list(ix.index)
    # 逐笔没有买入日列：买入日 = 信号日后第一个交易日（沪深300 日线日历）
    t["entry"] = [days[next(i for i, x in enumerate(days) if x > s)] for s in t["sd"]]
    q = {"standard": "§282",
         "basis": "L-241：同日解禁合计≥总股本5% ∩ 解禁日前20日跌≥10%；按解禁比例降序；持20；真实成交；扣0.07%税费"}
    for w, pk in (("R12", "R12 2025-10~2026-10"), ("N3", "N3 2024-01~2026-10")):
        a, b = PHASES[pk]
        d = t[t["sd"].between(a, b)]
        tm = tail_metrics(d["net"].tolist())
        m = phases[pk]
        q[w] = {"n": tm["n"], "avg": m["avg"], "pf": m["pf"], "mdd": m["mdd"], "win": m["win"], "streak": tm["streak"],
                "median": round(tm["median"], 2), "trim5": round(tm["trim5"], 2), "trim_both5": round(tm["trim_both5"], 2),
                "top10_share": None if tm["top10_share"] is None else round(tm["top10_share"], 3),
                "top10_red": tm["top10_red"], "label": tm["label"], "robust": tm["robust"], "excess": m["excess"],
                **summarize(d["net"].tolist(), idx_returns(ix, d["entry"], d["exit"], "open"))}
    p = {k.split()[0]: v["avg"] for k, v in phases.items() if not k.startswith("N3")}
    v = verdict("mid", q["R12"], q["N3"], p)
    q.update({"label": overall_label(q["R12"], q["N3"]), "pass": v["pass"], "fail": v["fail"],
              "pos_phases": v["pos_phases"], "n_phases": v["n_phases"], "bear": {"W0": p["W0"], "W00": p["W00"]},
              "phase_avg": p, "label_rule": "§285：两头各去5%后均值>0 且 中位数>0（R12、N3 都满足才是稳健型）",
              "hs300_basis": "逐笔：策略净收益(已扣0.07%) − 同持有期沪深300（买入日开盘→卖出日开盘，不扣成本）；§309，只展示"})
    r12, n3 = phases["R12 2025-10~2026-10"], phases["N3 2024-01~2026-10"]
    ex = " / ".join(f"{k.split()[0]} {v['excess']:+.2f}" for k, v in phases.items() if not k.startswith("N3"))
    entry = {
        "recent_12m": r12, "phases": phases,
        "config": "output/mid-newsignal-20261127/cands4.py N1_unlock_after_drop（预注册：解禁≥5%总股本∩r20≤−10%，按比例降序）真实成交、每日前5，次日开盘买持20日",
        "window": "2022-04~2026-10 分阶段（本地日K不早于 2022-04，无 P19~P21）",
        "generated": "2026-11-28",
        "note": (f"成本0.07%/笔；开盘涨停不买、跌停封死顺延卖出；回撤按每笔 1/20 仓位、平仓日累加。对全市场等权 EWALL.A 超额 {ex}。"
                 f"最近12个月 {int(r12['n'])} 笔 {r12['avg']:+.2f}%/笔；近3年 {int(n3['n'])} 笔 {n3['avg']:+.2f}%/笔。"
                 "相对现役 29 条的独有信号 504 笔，扣0.07% 合计 +1503%、0.27% 合计 +1402%，每笔超额 +1.16%（三段 +0.65/+1.84/+1.18），"
                 "扣同日同跌幅对照后剩 +1.13%。上线依据是 L-238 独有信号标准；§282 绝对收益卡不达标"
                 f"（{'、'.join(v['fail'])}：近12个月 PF {q['R12']['pf']}，且 2022-04 前无数据只有 5 段），如实保留。胜率约46%、中位超额为负，收益靠少数大涨票；去掉最好的5个信号日每笔超额降到 +0.51%。"),
        "quality": q,
    }
    print(json.dumps({"pass": v["pass"], "fail": v["fail"], "label": q["label"],
                      "R12": q["R12"], "N3": q["N3"], "phase_avg": p}, ensure_ascii=False))
    if not dry:
        mt = json.loads(METRICS.read_text())
        mt[SID] = entry
        METRICS.write_text(json.dumps(mt, ensure_ascii=False, indent=1))
        print("written", METRICS)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(Path(a[a.index("--src") + 1]), dry="--dry" in a)
