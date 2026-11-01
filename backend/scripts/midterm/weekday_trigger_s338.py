"""§338 中线周度策略：触发日（周几）对比 — 只读研究，不改线上。
对象：SCW / LPS_W / SDY_W / SPS_W / SMR_W（5 个现役小盘周度策略）。
共同：小盘指数 scid>scid20 才开新仓；选股逻辑不变；只换「哪些交易日允许出信号」。
引擎：realistic（T+1 开盘买、涨停买不进、跌停锁死顺延、0.3% 成本），K=20、max_new=10、持20日。
ST：LPS_ST_SRC=db（stock_metadata_history）。
窗口：标准 8 段 + N3_3y=2023-10-27~2026-10-26（用户指定近3年）。
"""
from __future__ import annotations
import json, os, sys, gc
from pathlib import Path
import polars as pl
from lt_research import fixed
from realistic import run_real, metrics_real
from lt_research46 import PH as PH0, full_metrics
from lt_research104 import prep as prep104
from s282_quality import tail_metrics, verdict, overall_label
import lt_data as L

c = pl.col
OUT = Path(__file__).resolve().parents[2] / "output/research/s338"
OUT.mkdir(parents=True, exist_ok=True)

PH = dict(PH0)
PH["N3_3y 2023-10-27~2026-10-26"] = ("2023-10-27", "2026-10-26")
# R12 与现役登记对齐；N3_3y 单独报
R12 = "R12 2025-10~2026-10"
N3 = "N3_3y 2023-10-27~2026-10-26"
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12",
      "W1 2024", "W2 2025-01~09", R12]

# polars weekday: Mon=1 .. Fri=5
SCHEMES = {
    "fri":        {"wd": {5}, "label": "仅周五(现役)"},
    "mon":        {"wd": {1}, "label": "仅周一"},
    "tue":        {"wd": {2}, "label": "仅周二"},
    "wed":        {"wd": {3}, "label": "仅周三"},
    "thu":        {"wd": {4}, "label": "仅周四"},
    "first_half": {"wd": {1, 2, 3}, "label": "每周前半(一二三)"},
    "second_half":{"wd": {4, 5}, "label": "每周后半(四五)"},
    "daily":      {"wd": {1, 2, 3, 4, 5}, "label": "每个交易日"},
    "week_last":  {"wd": "week_last", "label": "每周最后一个交易日"},
}

STRATS = {
    "SCW":   {"rank": "rk_s",  "top": 10, "score": "sc_small", "extra": None, "label": "小市值周度"},
    "LPS":   {"rank": "rk_lp", "top": 10, "score": "sc_small", "extra": None, "label": "低价小市值周度"},
    "SDY":   {"rank": "rk_y",  "top": 10, "score": "sc_dy",    "extra": None, "label": "小盘高股息周度"},
    "SPS":   {"rank": "rk_p",  "top": 10, "score": "sc_ps",    "extra": None, "label": "小盘低市销率周度"},
    "SMR":   {"rank": "rk_lo", "top": 10, "score": "sc_r",     "extra": None, "label": "小盘超跌反转周度"},
}


def ps_basic():
    start, end = L._range(None, None)
    fs = [f for f in sorted(L.D.glob("basic_[0-9]*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "ps_ttm"]) for f in fs], how="diagonal_relaxed")
    b = b.filter(L._win("trade_date", start, end, dash=False))
    return (b.select(c("ts_code").str.slice(0, 6).alias("code"), L._dash("trade_date").alias("bd"),
                     c("ps_ttm").cast(pl.Float64, strict=False)).unique(["code", "bd"]))


def build_ranks():
    """一次建面板：排名列与闸门，不含触发日。"""
    os.environ.setdefault("LPS_ST_SRC", "db")
    df = prep104()
    # SDY
    Uy = ((c("rk_s") <= 400) & (c("dv_ttm") > 0)).fill_null(False)
    df = df.with_columns(
        pl.when(Uy).then(-c("dv_ttm")).rank("ordinal").over("trade_date").alias("rk_y"),
        c("dv_ttm").fill_null(-1).alias("sc_dy"),
    )
    # SPS
    df = df.join(ps_basic(), on=["code", "bd"], how="left")
    Up = ((c("rk_s") <= 400) & (c("ps_ttm") > 0)).fill_null(False)
    df = df.with_columns(
        pl.when(Up).then(c("ps_ttm")).rank("ordinal").over("trade_date").alias("rk_p"),
        (-c("ps_ttm")).fill_null(-1e9).alias("sc_ps"),
    )
    # SMR
    Ur = ((c("rk_s") <= 400) & c("r20").is_not_null()).fill_null(False)
    df = df.with_columns(
        pl.when(Ur).then(c("r20")).rank("ordinal").over("trade_date").alias("rk_lo"),
        (-c("r20")).fill_null(-1e9).alias("sc_r"),
    )
    # weekday + week_last flag
    wd = c("trade_date").str.to_date().dt.weekday()
    # last trading day of ISO week: next row's ISO week differs (or no next)
    td = (df.select("trade_date").unique().sort("trade_date")
            .with_columns(
                c("trade_date").str.to_date().dt.week().alias("iso_w"),
                c("trade_date").str.to_date().dt.year().alias("iso_y"),
            ))
    td = td.with_columns(
        (c("iso_w").shift(-1).is_null() | (c("iso_w") != c("iso_w").shift(-1))
         | (c("iso_y") != c("iso_y").shift(-1))).alias("week_last")
    )
    df = df.join(td.select("trade_date", "week_last"), on="trade_date", how="left")
    df = df.with_columns(wd.alias("wd"), (c("scid") > c("scid20")).fill_null(False).alias("gate"))
    return df.sort("code", "trade_date")


def apply_scheme(df, scheme: str):
    """给 5 个策略生成信号列 S_{strat}_{scheme}。"""
    spec = SCHEMES[scheme]
    if spec["wd"] == "week_last":
        day_ok = c("week_last").fill_null(False)
    else:
        day_ok = c("wd").is_in(list(spec["wd"]))
    on = c("gate") & day_ok
    cols = []
    for name, st in STRATS.items():
        cols.append((on & (c(st["rank"]) <= st["top"])).fill_null(False).alias(f"S_{name}_{scheme}"))
    return df.with_columns(cols)


def years_span(s, e):
    from datetime import date
    a, b = date.fromisoformat(s), date.fromisoformat(e)
    return max((b - a).days / 365.25, 1e-9)


def enrich(t, cur, s, e):
    m = metrics_real(t, cur)
    if t is None or t.height == 0:
        m.update({"trim_both5": None, "trim5": None, "median": None, "top10_share": None,
                  "label": None, "streak": 0, "ann_trades": 0.0, "ann_turn": 0.0, "sigdays": 0})
        return m
    rets = [100.0 * x for x in t.sort("entry")["ret"].to_list()]
    tm = tail_metrics(rets)
    m["trim_both5"] = None if tm["trim_both5"] is None else round(tm["trim_both5"], 2)
    m["trim5"] = None if tm["trim5"] is None else round(tm["trim5"], 2)
    m["median"] = round(tm["median"], 2)
    m["top10_share"] = None if tm["top10_share"] is None else round(tm["top10_share"], 3)
    m["label"] = tm["label"]
    m["streak"] = tm["streak"]
    m["robust"] = tm["robust"]
    yr = years_span(s, e)
    m["ann_trades"] = round(m["n"] / yr, 1)
    # 年化换手：一年新开仓次数 / 回测仓位数（K=20）≈ 组合年周转次数
    m["ann_turn"] = round(m["n"] / yr / 20.0, 2)
    m["sigdays"] = t["entry"].n_unique()  # 买入日数（≈信号日数，涨停未买会少）
    return {k: (float(v) if isinstance(v, (int, float)) and k != "label" else v) for k, v in m.items()}


def main():
    only = sys.argv[1:]  # optional: scheme names or strat names to subset
    print("building ranks...", flush=True)
    df = build_ranks()
    print("built", df.height, "cols", len(df.columns), flush=True)
    # sanity: Fri+gate SCW count on R12
    fri_scw = df.filter((c("wd") == 5) & c("gate") & (c("rk_s") <= 10)
                        & c("trade_date").is_between(pl.lit("2025-10-01"), pl.lit("2026-10-16")))
    print("sanity fri SCW rows R12 window", fri_scw.height, "days", fri_scw["trade_date"].n_unique(), flush=True)

    schemes = list(SCHEMES) if not only else [x for x in SCHEMES if x in only]
    strats = list(STRATS) if not only else [x for x in STRATS if x in only]
    if not schemes:
        schemes = list(SCHEMES)
    if not strats:
        strats = list(STRATS)
    # if only passed scheme names, keep all strats
    if all(x in SCHEMES for x in only) and only:
        schemes, strats = only, list(STRATS)
    if all(x in STRATS for x in only) and only:
        schemes, strats = list(SCHEMES), only

    res_path = OUT / "res.json"
    res = json.load(open(res_path)) if res_path.exists() else {"_meta": {"note": "weekday trigger study"}}

    for sch in schemes:
        print(f"\n==== scheme {sch} {SCHEMES[sch]['label']} ====", flush=True)
        df = apply_scheme(df, sch)
        for st in strats:
            sig = f"S_{st}_{sch}"
            sc = STRATS[st]["score"]
            key = f"{st}__{sch}"
            print(f"## {key}", flush=True)
            for ph in P8 + [N3]:
                if key in res and ph in res[key] and not os.environ.get("FORCE"):
                    print(f"  skip {ph} (cached)", flush=True)
                    continue
                s, e = PH[ph]
                t, cur = run_real(df, sig, sc, fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
                m = enrich(t, cur, s, e)
                res.setdefault(key, {})[ph] = m
                print(f"  {ph.split()[0]:8s} n={m.get('n',0)} avg={m.get('avg')} pf={m.get('pf')} mdd={m.get('mdd')} "
                      f"ann_tr={m.get('ann_trades')} ann_turn={m.get('ann_turn')} med={m.get('median')} "
                      f"trim5={m.get('trim_both5')}", flush=True)
                del t, cur
            # §282 verdict using R12 + N3_3y + 8 phase avgs
            r12 = res[key].get(R12, {})
            n3 = res[key].get(N3, {})
            phases = {p.split()[0]: res[key].get(p, {}).get("avg") for p in P8}
            if r12.get("n") and n3.get("n"):
                v = verdict("mid", r12, n3, phases)
                res[key]["_verdict"] = v
                res[key]["_label"] = overall_label(
                    {"robust": r12.get("robust")}, {"robust": n3.get("robust")})
                print(f"  VERDICT pass={v['pass']} fail={v.get('fail')} pos={v['pos_phases']}/8 "
                      f"label={res[key]['_label'][:4]}", flush=True)
            json.dump(res, open(res_path, "w"), indent=1, ensure_ascii=False)
            gc.collect()
        # drop scheme signal cols to limit width
        drop = [f"S_{st}_{sch}" for st in STRATS]
        df = df.drop([x for x in drop if x in df.columns])
        gc.collect()

    # summary table
    rows = []
    for sch in SCHEMES:
        for st in STRATS:
            key = f"{st}__{sch}"
            if key not in res:
                continue
            r12, n3 = res[key].get(R12, {}), res[key].get(N3, {})
            v = res[key].get("_verdict", {})
            rows.append({
                "strat": st, "scheme": sch, "scheme_label": SCHEMES[sch]["label"],
                "r12_n": r12.get("n"), "r12_avg": r12.get("avg"), "r12_pf": r12.get("pf"), "r12_mdd": r12.get("mdd"),
                "r12_trim": r12.get("trim_both5"), "r12_med": r12.get("median"),
                "n3_n": n3.get("n"), "n3_avg": n3.get("avg"), "n3_pf": n3.get("pf"), "n3_mdd": n3.get("mdd"),
                "n3_trim": n3.get("trim_both5"), "n3_med": n3.get("median"),
                "n3_ann_tr": n3.get("ann_trades"), "n3_ann_turn": n3.get("ann_turn"),
                "pos8": v.get("pos_phases"), "pass": v.get("pass"), "fail": ",".join(v.get("fail") or []),
                "label": res[key].get("_label"),
            })
    pl.DataFrame(rows).write_csv(OUT / "summary.csv")
    json.dump(rows, open(OUT / "summary.json", "w"), indent=1, ensure_ascii=False)
    print("DONE", OUT, flush=True)


if __name__ == "__main__":
    main()
