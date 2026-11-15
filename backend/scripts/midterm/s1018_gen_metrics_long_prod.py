"""§1018 处置：把长线现役策略的展示指标改成「生产实际口径」（无并发持仓上限）。

owner 决定（2026-11-07）：不动实盘选股行为，先把 `midterm_backtest_metrics.json` 里
`long-net-cash-switch` / `long-growth-streak` 的 recent_12m / phases / quality 换成
与实盘同一口径（k_slots 无上限、max_new=5）的数字，让页面展示与达标判定都基于实盘真正在跑的配置；
上线时的 k_slots=20 研究快照原样保留在 `onboarding_research_k20` 字段里（上线证据与审计链不丢）。

与 s9xx_onboard_metrics.py 的差别（重要）：
- 那些脚本 `assert v["pass"]`，只写达标策略；本脚本**不 assert**。生产口径下两个策略都过不了
  §282 长线标准，这里如实写入 `quality.pass=False` 与 `quality.fail`，让页面显示真实结论。
- 无上限口径下引擎权益曲线除数是 1e6，`tot`/`mdd` 会被压成 0 而没有意义；
  改用按「窗口内实际峰值并发持仓数」归一的 `tot_peak`/`mdd_peak` 填入 tot/mdd 两个展示位，
  并在 config/note 里写明这个口径。

数字来源：`output/research/s1018/prod_caliber.json`（scripts/midterm/gen_long_prod_caliber.py 产出）。
本脚本只额外重跑 R12/N3 两个窗口以拿到逐笔 trades（算 §285 尾部指标与 §309 沪深300 超额需要）。

用法：PYTHONPATH=../..:. python s1018_gen_metrics_long_prod.py [--dry]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lt_research import fixed  # noqa: E402
from lt_research46 import PH  # noqa: E402
from realistic import run_real  # noqa: E402
from s282_quality import tail_metrics, overall_label, verdict  # noqa: E402
from s309_excess_hs300 import load_hs300, summarize, idx_returns, METRICS, T  # noqa: E402
import lt_research98 as R98  # noqa: E402
import lt_research102 as R102  # noqa: E402

c = pl.col
HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "output" / "research" / "s1018"
PH = dict(PH)
PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")

BASE_K = 1_000_000          # 等效无并发上限 = 实盘行为
HOLD, MAX_NEW = 60, 5
KEYS = ("n", "win", "avg", "pf", "days", "tot", "mdd", "bench", "excess", "ex_win")
PHASES8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12",
           "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
R12K, N3K = "R12 2025-10~2026-10", "N3 2024-01~2026-10"

TARGETS = {
    "long-net-cash-switch": {
        "prep": R98.prep, "sig": "NCS", "score": "sc_ncs",
        "src": "scripts/midterm/lt_research98.py NCS_h60（§261 预注册，未调参）",
        "rule": ("去年同期净负债>0且有息负债>0、本期净负债≤0（净现金）、本期扣非净利>0，"
                 "按（去年净负债−本期净负债）/去年有息负债 封顶3.0 降序"),
    },
    "long-growth-streak": {
        "prep": R102.prep, "sig": "CG4", "score": "sc_cg",
        "src": "scripts/midterm/lt_research102.py CG4_h60（§269 预注册，未调参）",
        "rule": ("本期及之前连续≥4个报告期（逐期相连）营收同比≥5% 且扣非净利同比≥5%，"
                 "按 min(连续期数,12)+min(扣非同比,100)/1000 降序"),
    },
}


def display_phase(m: dict) -> dict:
    """把无上限口径的指标整理成页面展示用的 KEYS 结构：tot/mdd 用峰值并发归一值。"""
    out = {k: m.get(k) for k in KEYS if k in m}
    out["tot"] = m.get("tot_peak")      # 引擎 tot 在 k_slots=1e6 下恒约 0，无意义
    out["mdd"] = m.get("mdd_peak")      # 同理，用按峰值并发归一的回撤
    out["peak_conc"] = m.get("peak_conc")
    return out


def trades_for(sid: str, cfg: dict, phase: str) -> pd.DataFrame:
    """重跑单个窗口拿逐笔 trades（tail_metrics / hs300 超额需要）。"""
    df = cfg["_df"]
    s, e = PH[phase]
    t, _ = run_real(df, cfg["sig"], cfg["score"], fixed(HOLD), s, e,
                    k_slots=BASE_K, max_new=MAX_NEW, max_hold=HOLD)
    return t.to_pandas().sort_values(["entry", "exit", "code"])


def build_quality(sid: str, cfg: dict, cal: dict) -> dict:
    ix = load_hs300()
    q = {"standard": "§282",
         "basis": ("生产实际口径（无并发持仓上限、每日新推荐≤5），与实盘选股行为一致；"
                   "真实成交、已扣 0.3%/笔；§1018 重算")}
    for w, pk in (("R12", R12K), ("N3", N3K)):
        d = trades_for(sid, cfg, pk)
        T.mkdir(parents=True, exist_ok=True)
        d.to_parquet(T / f"s1018prod__{sid}__{w}.parquet", index=False)
        tm = tail_metrics((d["ret"] * 100).tolist())
        m = cal[pk]
        assert tm["n"] == m["n"] and abs(tm["avg"] - m["avg"]) < 0.011, (w, tm["n"], m["n"], tm["avg"], m["avg"])
        hs = summarize((d["ret"] * 100).tolist(), idx_returns(ix, d["entry"], d["exit"], "open"))
        q[w] = {"n": tm["n"], "avg": m["avg"], "pf": m["pf"], "mdd": m["mdd_peak"], "win": m["win"],
                "streak": tm["streak"], "median": round(tm["median"], 2), "trim5": round(tm["trim5"], 2),
                "trim_both5": round(tm["trim_both5"], 2),
                "top10_share": None if tm["top10_share"] is None else round(tm["top10_share"], 3),
                "top10_red": tm["top10_red"], "label": tm["label"], "robust": tm["robust"],
                "excess": m.get("excess"), **hs}
    p8 = {k.split()[0]: cal[k]["avg"] for k in PHASES8}
    v = verdict("long", q["R12"], q["N3"], p8)
    q.update({"label": overall_label(q["R12"], q["N3"]), "pass": v["pass"], "fail": v["fail"],
              "pos_phases": v["pos_phases"], "n_phases": v["n_phases"],
              "bear": {"W0": p8["W0"], "W00": p8["W00"]}, "phase_avg": p8,
              "label_rule": "§285：两头各去5%后均值>0 且 中位数>0（R12、N3 都满足才是稳健型）",
              "hs300_basis": "逐笔：策略净收益(已扣0.3%) − 同持有期沪深300（买入日开盘→卖出日开盘，不扣成本）；§309，只展示"})
    return q


def main(dry=False):
    cal_all = json.loads((SRC / "prod_caliber.json").read_text())
    metrics = json.loads(METRICS.read_text())
    for sid, cfg in TARGETS.items():
        cal = cal_all[sid]["prod_uncap"]
        k20 = cal_all[sid]["research_k20"]
        old = metrics.get(sid) or {}
        cfg["_df"] = cfg["prep"]()
        q = build_quality(sid, cfg, cal)
        r12, n3 = cal[R12K], cal[N3K]
        ex = [round(cal[k]["excess"], 2) for k in PHASES8 if cal[k].get("excess") is not None]
        entry = {
            "recent_12m": display_phase(r12),
            "phases": {k: display_phase(cal[k]) for k in PHASES8 + [N3K]},
            "config": (f"{cfg['src']}：{cfg['rule']}；realistic 引擎、liq 股池、次日开盘买持 {HOLD} 日。"
                       f"**指标口径 = 生产实际行为：不设并发持仓上限（k_slots 无上限）、每日新推荐≤{MAX_NEW}**。"
                       f"tot/mdd 按窗口内实际峰值并发持仓数（R12 {r12['peak_conc']} 只 / N3 {n3['peak_conc']} 只）等权归一，"
                       f"因为无上限口径下引擎默认的 1/k_slots 归一会把总收益与回撤压成 0。"),
            "window": "2018-01~2026-10-16 分阶段（P19~R12/N3）",
            "generated": "2026-11-07",
            "note": ("成本0.3%/笔；开盘涨停不买、跌停封死顺延卖出；回撤按逐日盯市。"
                     "§1018 起本条目改为**生产实际口径**（无并发持仓上限），与实盘选股行为一致；"
                     "此前展示的是上线时 k_slots=20 的研究快照，那个配置实盘并没有跑，已原样保留在 onboarding_research_k20。"
                     + (("同日同市值五分位超额(参考) " + " / ".join(f"{x:+.2f}" for x in ex) + "（只展示）。") if ex else "")
                     + f"最近12个月 {int(r12['n'])} 笔 {r12['avg']:+.2f}%/笔 PF{r12['pf']} 回撤(峰值并发口径) {r12['mdd_peak']}%；"
                       f"近3年 {int(n3['n'])} 笔 {n3['avg']:+.2f}%/笔 PF{n3['pf']} 回撤(峰值并发口径) {n3['mdd_peak']}%。"
                       f"§282 长线标准在本口径下 **{'达标' if q['pass'] else '不达标'}**（未过条款：{', '.join(q['fail']) or '无'}）；"
                       f"8 段每笔 {q['pos_phases']}/{q['n_phases']} 为正。"
                       "生产口径滚动健康度（rolling_r12，独立管线）给出同向结论，可交叉核对。"
                       "§1018 已判定「给实盘加并发上限」不是跨制度稳健的每笔改进（REJECTED），故实盘行为未改。"),
            "quality": q,
            "notional_basis": {
                "divisor": "peak_conc（各窗口内实际达到的最大并发持仓数，逐窗口不同）",
                "r12": r12["peak_conc"], "n3": n3["peak_conc"],
                "why_not_20": ("无并发上限口径下不存在固定的 20 仓位资金结构；按 strategy_rank.notional_scale 线性折算到 "
                               f"20 仓位需要乘 {round(r12['peak_conc'] / 20, 2)}，会把回撤折算成 "
                               f"{round(r12['mdd_peak'] * r12['peak_conc'] / 20, 1)}% 这种超过 100% 的无意义值，"
                               "故本条目**不登记进 NOTIONAL_SLOTS**、不做折算。tot/mdd 一律为「按峰值并发等权」口径，"
                               "每窗口除数见 phases[*].peak_conc；收益/回撤是比值，折算前后相同，排序的 calmar 项不受影响。"),
                "per_trade_first": "跨口径可比的是每笔指标（n/avg/pf/win/median/trim5），与 rolling_r12 健康度同义；tot/mdd 只在本口径内有意义。",
            },
            "backtest_position_caps": {"k_slots": None, "max_new": MAX_NEW,
                                       "peak_conc_r12": r12["peak_conc"], "peak_conc_n3": n3["peak_conc"],
                                       "note": "与实盘同口径：不设并发持仓上限，只有每日新推荐上限"},
            "onboarding_research_k20": {
                "caliber": "上线时的研究快照口径 k_slots=20 / max_new=5；实盘从未按此配置运行，仅作审计留档",
                "recent_12m": {k: k20[R12K].get(k) for k in KEYS if k in k20[R12K]} | {"tot": k20[R12K].get("tot_peak"), "mdd": k20[R12K].get("mdd_peak")},
                "phases": {p: {k: k20[p].get(k) for k in KEYS if k in k20[p]} for p in PHASES8 + [N3K]},
                "previous_entry": {kk: old.get(kk) for kk in ("recent_12m", "note", "generated", "quality") if kk in old},
            },
        }
        print(f"\n=== {sid} ===")
        print(f"  生产口径 R12: n={int(r12['n'])} avg={r12['avg']} pf={r12['pf']} mdd_peak={r12['mdd_peak']} peak_conc={r12['peak_conc']}")
        print(f"  生产口径 N3 : n={int(n3['n'])} avg={n3['avg']} pf={n3['pf']} mdd_peak={n3['mdd_peak']} peak_conc={n3['peak_conc']}")
        print(f"  §282 长线 pass={q['pass']} fail={q['fail']} 正段={q['pos_phases']}/{q['n_phases']} label={q['label']}")
        print(f"  旧展示(k20) R12: {json.dumps((old.get('recent_12m') or {}), ensure_ascii=False)[:160]}")
        if not dry:
            metrics[sid] = entry
        del cfg["_df"]
    if not dry:
        METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=1))
        print("\nwritten", METRICS)
    else:
        print("\n[dry] 未写入")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
