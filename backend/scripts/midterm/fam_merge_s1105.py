"""中线家族合并（FAMMERGE，2026-11-05）— 预注册见 docs/strategy-research.md「治理 · 中线家族合并预注册」。

不重跑整套回测：只读各成员已有的逐笔成交文件 output/research/s<号>/trades_<变体>_<阶段>.csv，
按预注册规则拼出合并后的家族策略，再用本地日 K 逐日盯市算回撤。

用法（数据只读，输出写到 --out 目录）：
  ASHARE_BACKEND=<主工作树 backend> python fam_merge_s1105.py validate <家族>
  ASHARE_BACKEND=<主工作树 backend> python fam_merge_s1105.py overlap  <家族>
  ASHARE_BACKEND=<主工作树 backend> python fam_merge_s1105.py merge    <家族>
一次只算一个家族（内存）。
"""
from __future__ import annotations

import itertools
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

BACKEND = Path(os.environ.get("ASHARE_BACKEND") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(BACKEND / "scripts/midterm"))
from s282_quality import overall_label, tail_metrics, verdict  # noqa: E402

RES = BACKEND / "output/research"
PICKS = BACKEND / "output/midterm/picks"
OUT = Path(os.environ.get("FAM_OUT") or ".")
COST = 0.003
K_SLOTS, MAX_NEW, HOLD = 20, 5, 20
PHASES = ["P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12"]
WINDOWS = {"P19": ("2019-01-01", "2019-12-31"), "P20": ("2020-01-01", "2020-12-31"),
           "P21": ("2021-01-01", "2021-12-31"), "W0": ("2022-04-01", "2023-03-31"),
           "W00": ("2023-04-01", "2023-12-31"), "W1": ("2024-01-01", "2024-12-31"),
           "W2": ("2025-01-01", "2025-09-30"), "R12": ("2025-10-01", "2026-10-16"),
           "N3": ("2024-01-01", "2026-10-16")}

# ── 预注册：家族与成员（成员顺序固定，只用于平手兜底前的展示，不参与排序）──────────────
# 值 = 成员现行生产规则对应的逐笔文件前缀（与 midterm_backtest_metrics.json 各阶段 n/avg 核对过）。
FAMILIES: dict[str, dict] = {
    "A": {"sid": "mid-fam-earnings", "name": "业绩加速家族", "members": {
        "mid-earnings-accel": "rv1030/eng/trades_EA_combo_",           # 线上排序 §RV-C EA_combo
        "mid-earnings-accel-vol": "s975/trades_EAVOL_combo_h20_",      # 生产 §975 EA_combo 排序
        "mid-sue-accel-vol": "s604/trades_SUEGVOL_s22_h20_",
        "mid-sue-td-vol": "s782/trades_SUEGTDVOL_o25_h20_",
        "mid-ea-td-vol": "s712/trades_EATDVOL_d60_h20_",
        "mid-ea-cas-vol": "s763/trades_EACASVOL_h20_",
        "mid-sexp-sue-vol": "s810/trades_SEXPSUEGVOL_o45_h20_",
        "mid-ocfs-sue-vol": "s832/trades_OCFSSUEGVOL_o25_h20_",
        "mid-qn-sue-vol": "s944/trades_QNSUEGVOL_o40_h20_",
        "mid-curr-sue-vol": "s972/trades_CURRSUEGVOL_o25_h20_",
    }},
    "B": {"sid": "mid-fam-expense", "name": "费用率下降家族", "members": {
        "mid-sales-exp-drop": "s426/trades_SEXP_d05_h20_",
        "mid-sales-exp-vol": "s718/trades_SEXPVOL_v18_h20_",
        "mid-fina-exp-drop": "s396/trades_FINA_sc_raw_h20_",
        "mid-fina-exp-vol": "s527/trades_FINAVOL_h20_",
    }},
    "C": {"sid": "mid-fam-forecast", "name": "业绩预告家族", "members": {
        "mid-forecast-beat": "s348/trades_F_any_h20_",
        "mid-forecast-beat-underreact": "s455/trades_FAU_h20_",
        "mid-forecast-preincrease-underreact": "s451/trades_FZGU_h20_",
        "mid-forecast-preincrease-underreact-vol": "s549/trades_FZGUVOL_h20_",
    }},
    "D": {"sid": "mid-fam-incentive", "name": "股权激励家族", "members": {
        "mid-incentive-option": "s402/trades_IO_sc_pct_h20_",
        "mid-incentive-vol": "s722/trades_IOVOL_v18_h20_",
    }},
    "E": {"sid": "mid-fam-dividend", "name": "分红增加家族", "members": {
        "mid-dividend-increase": "s421/trades_DINC_h20_",
        "mid-dividend-increase-vol": "s535/trades_DINCVOL_h20_",
    }},
    "F": {"sid": "mid-fam-asset", "name": "资产结构家族", "members": {
        "mid-cas-vol": "s623/trades_CASVOL_h20_",
        "mid-turn-days-vol": "s599/trades_TDVOL_h20_",
    }},
}


def load_member(prefix: str, ph: str) -> pl.DataFrame | None:
    f = RES / f"{prefix}{ph}.csv"
    if not f.exists():
        return None
    t = pl.read_csv(f, schema_overrides={"code": pl.Utf8, "entry": pl.Utf8, "exit": pl.Utf8, "ret": pl.Float64, "days": pl.Int64})
    t = t.with_columns(pl.col("code").str.zfill(6), pl.int_range(pl.len()).alias("_row"))
    # 同一入场日内的文件行序 = 引擎按该成员自身分数降序写入的顺序（realistic.run_real），即该成员当日名次。
    return t.with_columns(pl.col("_row").rank("ordinal").over("entry").cast(pl.Int64).sub(1).alias("pos")).drop("_row")


# ── 预注册：合并排序与名额 ─────────────────────────────────────────────────────────────
def merged_candidates(member_frames: dict[str, pl.DataFrame]) -> tuple[dict[str, list[dict]], dict]:
    """入场日 -> 已排序候选。排序键：(被几个成员同时选中 降序, 各成员当日名次的最好值 升序, 代码 升序)。"""
    rows = []
    for sid, t in member_frames.items():
        for r in t.iter_rows(named=True):
            rows.append({**r, "sid": sid})
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r["entry"]][r["code"]].append(r)
    out, conflict = {}, 0
    for e, codes in by.items():
        cands = []
        for code, rs in codes.items():
            if len({(x["exit"], round(x["ret"], 9)) for x in rs}) > 1:
                conflict += 1
            base = rs[0]  # 同票同入场日各成员出场一致（持20、同一成交模拟）；不一致时记数并取首个
            cands.append({"code": code, "entry": e, "exit": base["exit"], "days": base["days"], "ret": base["ret"],
                          "k": len({x["sid"] for x in rs}), "best_pos": min(x["pos"] for x in rs),
                          "sids": sorted({x["sid"] for x in rs})})
        cands.sort(key=lambda x: (-x["k"], x["best_pos"], x["code"]))
        out[e] = cands
    return out, {"exit_conflict": conflict}


def simulate(cands: dict[str, list[dict]]) -> list[dict]:
    """与 realistic.run_real 相同的仓位口径：20 仓位、每天最多新开 5、已持有不重复买；出场日当天释放仓位。
    候选已是可成交笔（开盘涨停的成员当时就没买进，不占名额）。"""
    held: dict[str, str] = {}
    trades = []
    for e in sorted(cands):
        held = {c: x for c, x in held.items() if x >= e}  # run_real 在信号日 d 释放 exit>d；入场日 e=d+1，exit<=d ⇔ exit<e
        if len(held) >= K_SLOTS:
            continue
        new = 0
        for c in cands[e]:
            if new >= MAX_NEW or len(held) >= K_SLOTS:
                break
            if c["code"] in held:
                continue
            trades.append(c)
            held[c["code"]] = c["exit"]
            new += 1
    return trades


_PX: dict = {}


def prices(codes: set[str], ph: str) -> dict[str, dict]:
    """阶段窗口起点到终点后 75 天（覆盖持 20 日与跌停顺延）的日 K；同一阶段只读一次。"""
    import datetime as _dt

    import lt_data as L
    if ph not in _PX:
        _PX.clear()
        s, e = WINDOWS[ph]
        e2 = (_dt.date.fromisoformat(e) + _dt.timedelta(days=75)).isoformat()
        _PX[ph] = (L.prices(s, e2).select("code", "trade_date", "open", "close", "pct_chg")
                   .filter(pl.col("trade_date") >= s))
    df = _PX[ph].filter(pl.col("code").is_in(sorted(codes))).sort("code", "trade_date")
    df = df.with_columns(((1 + pl.col("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    df = df.with_columns((pl.col("open") * pl.col("ac") / pl.col("close")).alias("ao"))
    out = {}
    for (code,), part in df.partition_by("code", as_dict=True, maintain_order=True).items():
        d = part["trade_date"].to_list()
        out[code] = {"d": d, "i": {x: j for j, x in enumerate(d)}, "ac": part["ac"].to_numpy(), "ao": part["ao"].to_numpy()}
    return out


def curve_mdd(trades: list[dict], ph: str) -> tuple[float, float, int]:
    """逐日盯市（同 run_real）：买入日按收盘对开盘、其后收盘对收盘、出场日开盘对前收；每仓 1/20 名义。返回 (mdd%, tot%, 缺价笔数)。"""
    if not trades:
        return 0.0, 0.0, 0
    px = prices({t["code"] for t in trades}, ph)
    pnl = defaultdict(float)
    miss = 0
    for t in trades:
        a = px.get(t["code"])
        if not a or t["entry"] not in a["i"] or t["exit"] not in a["i"]:
            miss += 1
            pnl[t["exit"]] += t["ret"]
            continue
        i0, xi = a["i"][t["entry"]], a["i"][t["exit"]]
        p0 = a["ao"][i0]
        prev = p0
        for j in range(i0, xi):
            pnl[a["d"][j]] += (a["ac"][j] - prev) / p0
            prev = a["ac"][j]
        pnl[a["d"][xi]] += (a["ao"][xi] - prev) / p0 - COST
    ds = sorted(pnl)
    eq = 1 + np.cumsum([pnl[x] / K_SLOTS for x in ds])
    mdd = float((eq / np.maximum.accumulate(np.maximum(eq, 1)) - 1).min())
    return round(100 * mdd, 1), round(100 * (eq[-1] - 1), 1), miss


def stats(trades: list[dict], ph: str, with_curve: bool) -> dict:
    if not trades:
        return {"n": 0}
    ts = sorted(trades, key=lambda x: (x["entry"], x["exit"], x["code"]))
    r = np.array([x["ret"] for x in ts])
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    m = {"n": len(r), "win": round(100 * float((r > 0).mean()), 1), "avg": round(100 * float(r.mean()), 2),
         "pf": round(float(w / l), 2) if l > 0 else 99.0,
         "days": round(float(np.mean([x["days"] for x in ts])), 1)}
    m["per_day"] = round(m["avg"] / m["days"], 3) if m["days"] else None
    if with_curve:
        m["mdd"], m["tot"], m["px_miss"] = curve_mdd(ts, ph)
    q = tail_metrics([100 * x for x in r.tolist()])
    for k in ("median", "trim5", "trim_both5", "top10_share", "robust", "label", "streak"):
        v = q.get(k)
        m[k] = round(v, 3) if isinstance(v, float) else v
    return m


def member_trades(prefix: str, ph: str) -> list[dict]:
    t = load_member(prefix, ph)
    return [] if t is None else [dict(r) for r in t.iter_rows(named=True)]


def run_family(fam: str, mode: str) -> dict:
    F = FAMILIES[fam]
    res: dict = {"family": fam, "sid": F["sid"], "members": list(F["members"]), "phases": {}, "member_stats": {}}
    for ph in PHASES + ["N3"]:
        frames = {sid: t for sid, p in F["members"].items() if (t := load_member(p, ph)) is not None}
        curve = ph in ("R12", "N3") or mode == "validate"
        if mode == "validate":
            for sid, t in frames.items():
                own, _ = merged_candidates({sid: t})
                sim = simulate(own)
                m_raw = stats([dict(r) for r in t.iter_rows(named=True)], ph, True)
                m_sim = stats(sim, ph, False)
                res["member_stats"].setdefault(sid, {})[ph] = {"raw": m_raw, "sim_n": m_sim["n"], "sim_avg": m_sim.get("avg")}
                print(fam, ph, sid, "raw", m_raw["n"], m_raw["avg"], m_raw.get("mdd"), "| resim", m_sim["n"], m_sim.get("avg"), flush=True)
            continue
        for sid, t in frames.items():
            res["member_stats"].setdefault(sid, {})[ph] = stats([dict(r) for r in t.iter_rows(named=True)], ph, curve)
        cands, info = merged_candidates(frames)
        sim = simulate(cands)
        m = stats(sim, ph, curve)
        m["exit_conflict"] = info["exit_conflict"]
        m["union_cands"] = sum(len(v) for v in cands.values())
        m["k_dist"] = {str(k): sum(1 for x in sim if x["k"] == k) for k in sorted({x["k"] for x in sim})}
        m["by_member"] = {sid: sum(1 for x in sim if sid in x["sids"]) for sid in F["members"]}
        res["phases"][ph] = m
        print(fam, ph, {k: m.get(k) for k in ("n", "avg", "pf", "mdd", "median", "trim_both5", "k_dist")}, flush=True)
    if mode == "merge":
        res["verdict"] = judge(res)
    return res


# ── 预注册：判定 ─────────────────────────────────────────────────────────────────────
def judge(res: dict) -> dict:
    P = res["phases"]
    r12, n3 = P["R12"], P["N3"]
    v = verdict("mid", r12, n3, {ph: P[ph].get("avg") if P[ph].get("n") else None for ph in PHASES})
    ms = res["member_stats"]
    mean = lambda ph, k: statistics.fmean(ms[s][ph][k] for s in ms if ph in ms[s] and ms[s][ph].get("n"))  # noqa: E731
    ref = {"R12_avg": round(mean("R12", "avg"), 2), "N3_avg": round(mean("N3", "avg"), 2), "N3_mdd": round(mean("N3", "mdd"), 1),
           "R12_mdd": round(mean("R12", "mdd"), 1), "R12_n": round(mean("R12", "n"), 1), "N3_n": round(mean("N3", "n"), 1)}
    vs = {"R12_avg_ge_mean": r12["avg"] >= ref["R12_avg"], "N3_avg_ge_mean": n3["avg"] >= ref["N3_avg"],
          "N3_mdd_not_deeper": n3["mdd"] >= ref["N3_mdd"]}
    label = overall_label(r12, n3)
    return {"s282": v, "member_mean": ref, "vs_mean": vs, "label": label,
            "merge": bool(v["pass"] and all(vs.values()))}


# ── 重叠度（只描述，不作门槛）────────────────────────────────────────────────────────
def overlap(fam: str) -> dict:
    F = FAMILIES[fam]
    sids = list(F["members"])
    bt = {}
    for sid, p in F["members"].items():
        t = load_member(p, "N3")
        bt[sid] = set() if t is None else {(r["code"], r["entry"]) for r in t.select("code", "entry").iter_rows(named=True)}
    live: dict[str, set] = {s: set() for s in sids}
    cand: dict[str, set] = {s: set() for s in sids}
    days: dict[str, set] = {s: set() for s in sids}
    for s in sids:
        for f in PICKS.glob(f"*_{s}.json"):
            if f.name.split("_", 1)[1] != f"{s}.json":
                continue
            doc = json.loads(f.read_text())
            d = doc.get("signal_date") or f.name.split("_", 1)[0]
            days[s].add(d)
            kept = {str(p.get("code")) for p in doc.get("picks") or []}
            live[s] |= {(c, d) for c in kept}
            trunc = set((doc.get("daily_cap") or {}).get("truncated_codes") or [])
            cand[s] |= {(c, d) for c in kept | trunc}
    out = {"pairs": []}
    for a, b in itertools.combinations(sids, 2):
        both = days[a] & days[b]
        la = {x for x in live[a] if x[1] in both}
        lb = {x for x in live[b] if x[1] in both}
        ca = {x for x in cand[a] if x[1] in both}
        cb = {x for x in cand[b] if x[1] in both}
        j = lambda x, y: round(len(x & y) / len(x | y), 3) if (x | y) else None  # noqa: E731
        c = lambda x, y: round(len(x & y) / min(len(x), len(y)), 3) if x and y else None  # noqa: E731
        out["pairs"].append({"a": a, "b": b, "bt_n": [len(bt[a]), len(bt[b])], "bt_inter": len(bt[a] & bt[b]),
                             "bt_jaccard": j(bt[a], bt[b]), "bt_contain": c(bt[a], bt[b]),
                             "live_days": len(both), "live_n": [len(la), len(lb)], "live_jaccard": j(la, lb),
                             "live_contain": c(la, lb), "cand_jaccard": j(ca, cb)})
    allbt = defaultdict(int)
    for s in sids:
        for x in bt[s]:
            allbt[x] += 1
    out["bt_multi_share"] = round(sum(1 for v in allbt.values() if v >= 2) / len(allbt), 3) if allbt else None
    for p in out["pairs"]:
        print(fam, p, flush=True)
    print(fam, "N3 回测里被≥2 个成员同时买入的 (票,入场日) 占比", out["bt_multi_share"])
    return out


if __name__ == "__main__":
    mode, fam = sys.argv[1], sys.argv[2]
    OUT.mkdir(parents=True, exist_ok=True)
    r = overlap(fam) if mode == "overlap" else run_family(fam, mode)
    (OUT / f"fam_{mode}_{fam}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    if mode == "merge":
        print(json.dumps(r["verdict"], ensure_ascii=False))
