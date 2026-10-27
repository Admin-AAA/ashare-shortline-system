"""§315-D SMR_W 上线：把 output/research/s315/res.json + 逐笔写入 midterm_backtest_metrics.json（mid-smallcap-reversal-weekly），
收益来源用 s282_quality（§282/§285），沪深300超额用 s309 口径；逐笔另存 output/research/s282/trades/r120__SMR_W__*.parquet。
用法：cd backend && PYTHONPATH=.:scripts/midterm .venv/bin/python scripts/midterm/s315_onboard_metrics.py [--dry]"""
import json, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from s282_quality import tail_metrics, overall_label, verdict
from s309_excess_hs300 import load_hs300, summarize, idx_returns, METRICS, T

SID, NAME = "mid-smallcap-reversal-weekly", "SMR_W"
SRC = Path(__file__).resolve().parents[2] / "output/research/s315"
KEYS = ("n", "win", "avg", "pf", "days", "tot", "mdd", "bench", "excess", "ex_win")


def main(dry=False):
    res = json.loads((SRC / "res.json").read_text())[NAME]
    phases = {k: {x: v[x] for x in KEYS if x in v} for k, v in res.items()}
    ix = load_hs300()
    q = {"standard": "§282", "basis": "§315 预注册真实成交口径（ST 用 stock_metadata_history.is_st），已扣 0.3%/笔"}
    for w, pk in (("R12", "R12 2025-10~2026-10"), ("N3", "N3 2024-01~2026-10")):
        d = pd.read_csv(SRC / f"trades_{NAME}_{w}.csv", dtype={"code": str})
        T.mkdir(parents=True, exist_ok=True)
        d.to_parquet(T / f"r120__{NAME}__{w}.parquet", index=False)
        d = d.sort_values(["entry", "exit", "code"])
        tm = tail_metrics((d["ret"] * 100).tolist())
        m = res[pk]
        assert tm["n"] == m["n"] and abs(tm["avg"] - m["avg"]) < 0.011, (w, tm["n"], m["n"])
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
        "config": ("scripts/midterm/lt_research120.py SMR_W（§315-D 预注册，未调参；ST 口径 stock_metadata_history.is_st，LPS_ST_SRC=db）：每周五、小盘等权指数>MA20 时，"
                   "合格池（20日均额>3000万、上市>250日、非ST、非北交所、PE_ttm>0）按折算市值升序前400只里，近20个交易日复权涨跌幅最低（跌最多）的10只；"
                   "realistic引擎、回测按 k_slots=20 max_new=10（实盘推荐不设此上限），次日开盘买持20日"),
        "window": "2019-01~2026-10-16 分阶段（P19~R12/N3）",
        "generated": "2026-10-25",
        "note": ("成本0.3%/笔；开盘涨停不买、跌停封死顺延卖出；回撤按逐日盯市。8 个阶段每笔 8/8 为正，但多数阶段只有 +0.2~+0.6%（P20 +0.19、P21 +0.52、W00 +0.55），"
                 "高收益集中在 P19/W1/R12/W2；同日同市值五分位超额(参考) " + " / ".join(f"{x:+.2f}" for x in ex) + "（阶段顺序同上表，仅 3/8 为正）——"
                 "即相当一部分收益来自小盘整体行情，而不是「选中的超跌票比同市值的票更强」。"
                 "与现役「小市值周度」「低价小市值周度」「小盘高股息周度」「小盘低市销率周度」同周选股重叠极低（N3 笔重合 ≤2.3%）。"
                 "R12 中位数仅 +0.24、前10笔占利润约 91%（R12 偏集中），标签按 §285 为稳健型（R12、N3 两头各去5%与中位数均为正）。"
                 "2026-10-25 起按「回测达标直接上线」接入每日选股流程。"
                 "实盘推荐不设仓位上限（不限持仓只数/每日新进只数），回测指标按20仓位口径（k_slots=20、max_new=10）。"),
        "quality": q,
    }
    print(json.dumps(entry, ensure_ascii=False, indent=1)[:4000])
    if not dry:
        mt = json.loads(METRICS.read_text()); mt[SID] = entry
        METRICS.write_text(json.dumps(mt, ensure_ascii=False, indent=1)); print("written", METRICS)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
