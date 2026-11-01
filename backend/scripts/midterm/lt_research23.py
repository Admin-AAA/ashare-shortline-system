"""[中线] §109 股权激励草案公告后漂移 — 预注册 3 版（/workspace/strategy-research.md §109，结果出来前写定，不调参）。
数据：output/research/sprint4/incentive/YYYYMM.parquet（scripts/midterm/fetch_incentive_cache.py，巨潮 category_gqjl_szsh + 「草案」）。
事件：标题含「激励计划」且含「草案」、不含 修订/更正/调整/终止/撤销/取消/延期；按 (code, ann_date CST) 聚合；
  首次 = 该股前 180 自然日内无同类事件；opt = 当日任一标题含「股票期权」。
PIT：ann_date -> >= ann_date 的首个交易日 sd，sd 收盘出信号，次日开盘买（run_real：开盘涨停不买、跌停锁死顺延、0.3% 成本）。
  INC_h20     : 全部首次事件，持 20 -> /mid
  INC_opt_h20 : opt=True，持 20 -> /mid
  INC_h60     : 全部，持 60 -> /long
排序分 = r20（信号日前 20 日复权涨幅）升序，平手按代码升序。liq，k_slots=20, max_new=5。cohort_bench 同市值五分位对照。
参照（算重叠）：EA_h20、F_any_h20、RP_r05（同 lt_research21 口径）、SCW（mid-small-cap-weekly，lt_research11 live 规则近似重算）。
用法：LT_START=.. LT_END=.. python lt_research23.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
from pathlib import Path
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
import lt_research20 as R20
from lt_research18 import repo_sig_q
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
INC = Path(__file__).resolve().parents[2] / "output/research/sprint4/incentive"
BAD = "修订|更正|调整|终止|撤销|取消|延期"
FIRST_DAYS = 180


def load_events() -> pl.DataFrame:
    d = pl.concat([pl.read_parquet(f) for f in sorted(glob.glob(str(INC / "*.parquet")))], how="vertical_relaxed")
    d = d.unique("ann_id").filter(c("title").str.contains("激励计划") & c("title").str.contains("草案")
                                  & ~c("title").str.contains(BAD) & c("code").str.contains(r"^\d{6}$"))
    ev = (d.group_by("code", "ann_date").agg(c("title").str.contains("股票期权").any().alias("opt"))
           .with_columns(c("ann_date").str.to_date("%Y%m%d").alias("d")).sort("code", "d"))
    ev = ev.with_columns(c("d").shift(1).over("code").alias("pd"))
    first = ev.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > FIRST_DAYS))
    d0 = ev["d"].min()  # 首日后 180 天内无法判断"首次"，只认之后的事件
    return first.filter(c("d") >= (d0 + pl.duration(days=FIRST_DAYS)) if d0 is not None else pl.lit(True))


def inc_sig(td):
    e = load_events().with_columns(c("d").dt.strftime("%Y-%m-%d").alias("ann")).sort("ann")
    e = e.join_asof(td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("ann2")), left_on="ann", right_on="ann2",
                    strategy="forward").drop_nulls("sd")
    return (e.group_by("code", "sd").agg(pl.lit(True).alias("inc"), c("opt").any().alias("opt"))
             .rename({"sd": "trade_date"}))


def scw(df):
    """mid-small-cap-weekly（lt_research11 live）近似：周五、小盘等权指数>MA20、U 池且 pe>0 的折算市值最小 10 只。"""
    px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
    df = df.join(px, on=["code", "bd"], how="left").with_columns(
        (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
    g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    r = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4"))
    idx = (r.filter(c("r4") <= 400).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
           .with_columns((1 + c("s") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(20).alias("scid20"))
           .select("trade_date", "scid", "scid20"))
    df = df.join(idx, on="trade_date", how="left")
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    U = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
         & c("mvd").is_not_null() & (c("pe_ttm") > 0)).fill_null(False)
    rk = pl.when(U).then(c("mvd")).rank("ordinal").over("trade_date")
    return df.with_columns((wk & (c("scid") > c("scid20")) & (rk <= 10)).fill_null(False).alias("SCW"),
                           (-c("mvd")).fill_null(0).alias("sc_small")).drop("bd_right", "ac_bd", strict=False)


def prep():
    df = R20.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(inc_sig(td), on=["code", "trade_date"], how="left").join(repo_sig_q(td), on=["code", "trade_date"], how="left")
    ratio = c("rp_amt") / (c("total_mv").cast(pl.Float64, strict=False) * 1e4)
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.with_columns(
        (c("liq") & c("inc").fill_null(False)).fill_null(False).alias("IA"),
        (c("liq") & c("inc").fill_null(False) & c("opt").fill_null(False)).fill_null(False).alias("IO"),
        (-c("r20").fill_null(0) - tie).alias("sc_i"),
        (c("liq") & c("rp").fill_null(False) & (ratio.fill_null(0) >= 0.005)).fill_null(False).alias("RP_r05"),
        ratio.fill_null(0).alias("sc_r"))
    return scw(df).sort("code", "trade_date")


C = {"INC_h20": ("IA", "sc_i", 20, 5), "INC_opt_h20": ("IO", "sc_i", 20, 5), "INC_h60": ("IA", "sc_i", 60, 5),
     "EA_h20": ("G", "sc_g", 20, 5), "F_any_h20": ("FA", "sc_a", 20, 5), "RP_r05": ("RP_r05", "sc_r", 20, 5),
     "SCW": ("SCW", "sc_small", 20, 10)}
REFS = ("EA_h20", "F_any_h20", "RP_r05", "SCW")
VARS = ("INC_h20", "INC_opt_h20", "INC_h60")

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("signal days per year:", df.filter(c("IA")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("IA").sum(), c("IO").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        trades = {}
        for n, (sig, sc, h, mn) in C.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=mn, max_hold=h)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = cohort_bench(df, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                r = t["ret"].to_numpy()
                m["avg_win"] = round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0
                m["avg_loss"] = round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0
                streak = mx = 0
                for x in t.sort("entry")["ret"].to_list():
                    streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
                m["max_loss_streak"] = mx
            trades[n] = t
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(n, ph, m, flush=True)
        for n in VARS:
            for rname in REFS:
                res[n][ph][f"overlap_{rname}"] = R20.overlap(trades[n], trades[rname])
            print("  overlap", n, {r: res[n][ph][f"overlap_{r}"]["frac_hold_overlap"] for r in REFS}, flush=True)
        if ph in ("R12 2025-10~2026-10", "N3 2024-01~2026-10"):
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            for n, t in trades.items():
                if n in VARS and t is not None and t.height:
                    t.write_csv(out.replace(".json", f"_{n}_{ph.split()[0]}_trades.csv"))
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
