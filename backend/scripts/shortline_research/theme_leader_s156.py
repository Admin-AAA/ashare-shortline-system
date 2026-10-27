"""[短线研究 §156] 当日热点题材里的龙头 / 次龙（预注册 docs/strategy-research.md §156，结果出来前写定，不调参）。
题材 = 开盘啦涨停榜 kpl_list 的 theme（逐日、T 日收盘后可见）；行情 = midterm 长面板 + realistic 引擎。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/theme_leader_s156.py out.json"""
import sys, json, re, glob
from pathlib import Path
import numpy as np
import polars as pl
import lt_data as L
from realistic import add_limits, run_real, metrics_real
from lt_research import PH

c = pl.col
KPL = Path(__file__).resolve().parents[2] / "data/research_cache/kpl_list"
BAD = ("报", "业绩", "预增", "扭亏", "ST", "摘帽", "新股", "次新")
TOP_THEMES, MIN_N, MAX_NEW, K = 3, 5, 3, 15
PHASES = dict(PH)
PHASES["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
for q, (a, b) in {"Q1 2025-10~12": ("2025-10-01", "2025-12-31"), "Q2 2026-01~03": ("2026-01-01", "2026-03-31"),
                  "Q3 2026-04~06": ("2026-04-01", "2026-06-30"), "Q4 2026-07~10": ("2026-07-01", "2026-10-16")}.items():
    PHASES[q] = (a, b)
P8 = list(PH)
import os
START, END = os.environ.get("S156_START", "2019-01-01"), os.environ.get("S156_END", "2026-10-16")


def boards(s):
    if s is None:
        return None
    if s == "首板":
        return 1
    m = re.match(r"(\d+)连板", s)
    if m:
        return int(m.group(1))
    m = re.match(r"\d+天(\d+)板", s)
    return int(m.group(1)) if m else None


def load_kpl():
    k = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "lu_time", "last_time", "status", "theme", "amount"])
                   for f in sorted(glob.glob(str(KPL / "kpl_*.parquet")))], how="diagonal_relaxed")
    k = k.with_columns(c("ts_code").str.slice(0, 6).alias("code"),
                       L._dash("trade_date").alias("trade_date"),
                       c("amount").cast(pl.Float64, strict=False),
                       c("status").map_elements(boards, return_dtype=pl.Int32).alias("nb"))
    k = k.filter(c("status") != "新上市未开板").unique(["code", "trade_date"])
    return k


def panel():
    df = L.prices(START, END)
    df = df.with_columns(((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac"))
    kf = c("ac") / c("close")
    pc = c("close").shift(1).over("code")
    df = df.with_columns((c("open") * kf).alias("ao"), (c("low") * kf).alias("al"),
                         ((c("high") - c("close")) / pc >= 0.04).cast(pl.Int32).alias("chong"),
                         pl.int_range(pl.len()).over("code").alias("age"))
    df = df.with_columns(c("chong").rolling_sum(10).over("code").alias("chong10"))
    st = pl.read_parquet(L.D / "st_hist.parquet").rename({"effective_from": "trade_date"}).sort("trade_date")
    df = df.sort("trade_date").join_asof(st, on="trade_date", by="code", strategy="backward").with_columns(c("st").fill_null(False))
    df = add_limits(df.sort("code", "trade_date"))
    return df.select("code", "trade_date", "close", "ao", "ac", "al", "amount", "chong10", "age", "st", "lim_up_open", "lock_down")


def signals(df, k):
    # 股性：过去 120 个交易日（不含 T）涨停次数，按面板逐股滚动
    lu = k.select("code", "trade_date").with_columns(pl.lit(1).alias("is_lu"))
    df = df.join(lu, on=["code", "trade_date"], how="left").with_columns(c("is_lu").fill_null(0))
    df = df.with_columns(c("is_lu").shift(1).rolling_sum(120, min_samples=1).over("code").fill_null(0).alias("gx"))
    # 题材展开
    th = k.with_columns(c("theme").fill_null("").str.split("、").alias("th")).explode("th").with_columns(c("th").str.strip_chars())
    th = th.filter((c("th").str.len_chars() > 0) & ~pl.any_horizontal([c("th").str.contains(b, literal=True) for b in BAD]))
    th = th.join(df.select("code", "trade_date", "gx", "chong10", "age", "st", pl.col("amount").alias("pamt")), on=["code", "trade_date"], how="left")
    th = th.with_columns(pl.coalesce("amount", "pamt").alias("amt"))
    heat = th.group_by("trade_date", "th").agg(pl.len().cast(pl.Int64).alias("n"), c("amt").sum().alias("tamt"))
    # 前一交易日热度
    days = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(1).alias("prev"))
    heat = heat.join(days, on="trade_date", how="left")
    heat = heat.join(heat.select(c("trade_date").alias("prev"), "th", c("n").alias("n_prev")), on=["prev", "th"], how="left").with_columns(c("n_prev").fill_null(0))
    heat = heat.with_columns(pl.struct(n=-c("n"), a=-c("tamt")).rank("ordinal").over("trade_date").alias("hr"))
    hot = heat.filter((c("hr") <= TOP_THEMES) & (c("n") >= MIN_N))
    t = th.join(hot.select("trade_date", "th", "n", "n_prev"), on=["trade_date", "th"], how="inner")
    t = t.with_columns(c("lu_time").fill_null("99:99:99"), c("last_time").fill_null(""))
    t = t.with_columns(pl.struct(a=c("lu_time"), b=c("code")).rank("ordinal").over("trade_date", "th").alias("r_lu"),
                       (-c("amt").fill_null(0)).rank("min").over("trade_date", "th").alias("r_amt"),
                       (-c("gx").fill_null(0)).rank("min").over("trade_date", "th").alias("r_gx"))
    t = t.with_columns((c("r_lu") + c("r_amt") + c("r_gx")).alias("ident"))
    t = t.with_columns(pl.struct(a=c("ident"), b=c("lu_time"), d=c("code")).rank("ordinal").over("trade_date", "th").alias("r_id"))
    uni = (~c("st").fill_null(True)) & (c("age").fill_null(0) >= 60) & (c("amt").fill_null(0) >= 1e8) \
        & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    # 开盘啦 lu_time/last_time 有秒级误差（完全相等只占 0.3%），一次封死按两者相差 ≤ 60 秒
    sec = lambda x: c(x).str.slice(0, 2).cast(pl.Int32, strict=False) * 3600 + c(x).str.slice(3, 2).cast(pl.Int32, strict=False) * 60 \
        + c(x).str.slice(6, 2).cast(pl.Int32, strict=False)
    one_seal = ((sec("last_time") - sec("lu_time")).abs() <= 60).fill_null(False)
    qual = one_seal & (c("chong10").fill_null(99) <= 1)
    persist = (c("n_prev") >= 3) & (c("n") >= c("n_prev"))
    lu_key = c("lu_time").str.replace_all(":", "").cast(pl.Float64, strict=False)
    ver = {
        "V1_FIRST": (c("r_lu") <= 2, -lu_key),
        "V2_IDENT": (c("r_id") <= 2, -c("ident").cast(pl.Float64)),
        "V3_QUAL": ((c("r_id") <= 2) & qual, -c("ident").cast(pl.Float64)),
        "V4_PERSIST": ((c("r_id") <= 2) & qual & persist, -c("ident").cast(pl.Float64)),
        "V4a_start": ((c("r_id") <= 2) & qual & persist & (c("nb") == 1), -c("ident").cast(pl.Float64)),
        "V4b_ferment": ((c("r_id") <= 2) & qual & persist & c("nb").is_between(2, 3), -c("ident").cast(pl.Float64)),
        "V4c_accel": ((c("r_id") <= 2) & qual & persist & (c("nb") >= 4), -c("ident").cast(pl.Float64)),
        "FOLLOW": (c("r_lu") >= 4, -lu_key),
    }
    out = df
    for name, (cond, score) in ver.items():
        s = t.filter(uni & cond).with_columns(score.alias("sc")).group_by("code", "trade_date").agg(c("sc").max())
        out = out.join(s.with_columns(pl.lit(True).alias(f"sig_{name}")).rename({"sc": f"sc_{name}"}), on=["code", "trade_date"], how="left")
        out = out.with_columns(c(f"sig_{name}").fill_null(False))
    stats = heat.filter(c("trade_date").is_between(pl.lit(START), pl.lit(END))).group_by("trade_date").agg(
        ((c("hr") <= TOP_THEMES) & (c("n") >= MIN_N)).sum().alias("nhot"))
    return out.sort("code", "trade_date"), stats


def ex_main(a, e, i):
    return a["ac"][i] < a["al"][i - 1] or a["ac"][i] < a["ao"][e] * 0.95 or i - e >= 2


def ex_e1(a, e, i):
    return True


def split(t, curve):
    res = {}
    ds, eq = curve
    ds = np.array(ds)
    for p, (a, b) in PHASES.items():
        tt = t.filter(c("entry").is_between(pl.lit(a), pl.lit(b)))
        m = (ds >= a) & (ds <= b)
        if tt.height == 0 or m.sum() == 0:
            res[p] = {"n": tt.height}
            continue
        i0 = int(np.argmax(m))
        seg = np.concatenate([[1.0], eq[m] / (eq[i0 - 1] if i0 > 0 else 1.0)])
        r = tt["ret"].to_numpy(); w = r[r > 0].sum(); l = -r[r < 0].sum()
        res[p] = {"n": len(r), "win": round(100 * (r > 0).mean(), 1), "avg": round(100 * r.mean(), 2),
                  "pf": round(w / l, 2) if l > 0 else 99, "days": round(float(tt["days"].mean()), 2),
                  "tot": round(100 * (seg[-1] - 1), 1), "mdd": round(100 * (seg / np.maximum.accumulate(seg) - 1).min(), 1)}
    return res


def main(out_path):
    k = load_kpl()
    print("kpl rows", k.height, "days", k["trade_date"].n_unique(), k["trade_date"].min(), k["trade_date"].max(), flush=True)
    df = panel()
    print("panel", df.shape, flush=True)
    df, stats = signals(df, k)
    print("days with >=1 hot theme:", stats.filter(c("nhot") > 0).height, "/", stats.height, flush=True)
    res = {}
    for name in ("V1_FIRST", "V2_IDENT", "V3_QUAL", "V4_PERSIST", "V4a_start", "V4b_ferment", "V4c_accel", "FOLLOW"):
        for ex_name, fn, mh in (("main", ex_main, 3), ("E1", ex_e1, 1)):
            t, curve = run_real(df, f"sig_{name}", f"sc_{name}", fn, START, END, k_slots=K, max_new=MAX_NEW, max_hold=mh)
            res[f"{name}|{ex_name}"] = split(t, curve) if t is not None and t.height else {}
            r = res[f"{name}|{ex_name}"]
            print(name, ex_name, " ".join(f"{p.split()[0]}:{r.get(p, {}).get('n', 0)}/{r.get(p, {}).get('avg', 0):+.2f}" for p in PHASES), flush=True)
            if ex_name == "main":
                t.write_parquet(Path(out_path).with_name(f"trades_{name}.parquet"))
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
