"""[中线] §350「大宗折价成交后漂移」BT_disc_h20 — 预注册（docs/strategy-research.md §350，结果出来前写定，不调参）。
  数据：output/research/sprint4/block_trade/*.parquet（同 lt_research25）。
  事件：当日大宗 vwp/close−1 ≤ −2% 且 bt_ratio=amt/调整后流通市值 ≥0.5%；T 收盘信号，T+1 开盘买、持 20 日。
  排序 bt_ratio 降序；liq；K=20、max_new=5（仅回测）。现役是溢价 BT_prem，本题只测折价。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research136.py "<phase>" out.json"""
import sys, json, os, glob
from pathlib import Path
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
BT = Path(__file__).resolve().parents[2] / "output/research/sprint4/block_trade"
RATIO_TH, DISC_TH = 0.005, -0.02
NAME, HOLD, K, MAX_NEW = "BT_disc_h20", 20, 20, 5


def load_block(start=None, end=None):
    fs = sorted(glob.glob(str(BT / "*.parquet")))
    if start:
        fs = [f for f in fs if Path(f).stem >= start.replace("-", "")]
    if end:
        fs = [f for f in fs if Path(f).stem <= end.replace("-", "")]
    parts = []
    for f in fs:
        x = pl.read_parquet(f)
        if x.height == 0:
            continue
        parts.append(x.select(c("ts_code").cast(pl.Utf8), c("trade_date").cast(pl.Utf8),
                              c("vol").cast(pl.Float64, strict=False), c("amount").cast(pl.Float64, strict=False)))
    if not parts:
        return pl.DataFrame({"code": pl.Series([], dtype=pl.Utf8), "trade_date": pl.Series([], dtype=pl.Utf8),
                             "bt_amt": pl.Series([], dtype=pl.Float64), "bt_vol": pl.Series([], dtype=pl.Float64)})
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["vol", "amount"]).filter((c("vol") > 0) & (c("amount") > 0))
    d = d.with_columns(c("ts_code").str.slice(0, 6).alias("code"),
                       pl.concat_str([c("trade_date").str.slice(0, 4), pl.lit("-"),
                                      c("trade_date").str.slice(4, 2), pl.lit("-"),
                                      c("trade_date").str.slice(6, 2)]).alias("trade_date"))
    d = d.filter(c("code").str.contains(r"^\d{6}$") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    return d.group_by("code", "trade_date").agg(c("amount").sum().alias("bt_amt"), c("vol").sum().alias("bt_vol"))


def prep():
    df = build().sort("code", "trade_date")
    b = load_block()
    px = df.select("code", c("trade_date").alias("bd"), c("close").alias("close_bd"))
    df = df.join(px, on=["code", "bd"], how="left")
    fmv = c("circ_mv").cast(pl.Float64, strict=False) * c("close") / c("close_bd")
    df = df.join(b, on=["code", "trade_date"], how="left").with_columns(fmv.alias("fmv"))
    df = df.with_columns(((c("bt_amt") / c("bt_vol")) / c("close") - 1).alias("bt_prem"),
                         (c("bt_amt") / c("fmv")).alias("bt_ratio")).drop("close_bd")
    ev = c("liq") & c("bt_amt").is_not_null() & (c("bt_ratio") >= RATIO_TH)
    cand = (ev & (c("bt_prem") <= DISC_TH)).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("bt_ratio")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns(
        (c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("BTD"),
        pl.when(c("cand")).then(c("bt_ratio")).otherwise(-1e9).alias("sc_btd"),
    ).drop("_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("BTD") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_cand": int(df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e))).height)}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "prem_med": round(float(w["bt_prem"].median()), 4),
                         "ratio_med": round(float(w["bt_ratio"].median()), 4),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1)})
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "BTD", "sc_btd", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
