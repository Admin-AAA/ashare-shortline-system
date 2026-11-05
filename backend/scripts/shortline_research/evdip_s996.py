"""[短线研究 §996–§1000] 第三批：「强事件 × 公告前已深跌」，换成与 §993（回购/期权激励）不同的事件源，持 5 日。
预注册 docs/strategy-research.md §996–§1000（规则、参数写死后提交，再跑任何收益；每个只一版，不扫参）。

公共（与 §993 同一个深跌门槛与成交口径，门槛直接沿用 §993、不另调）：
  行情与旗标复用 lt_research25.prep()（liq：amt20>5000万、上市>250日、非ST、非北交所；ST 口径 st_hist.parquet）。
  深跌 = 信号日 T 的近 20 日复权涨幅 r20 ≤ −15%；not_lim = T 收盘既非涨停也非跌停（距涨跌停价>0.005，缺价按前收±9.8%/±19.8%）。
  每个想法：T 满足「事件 & liq & 深跌 & not_lim」即为信号，按 r20 升序（跌得越深越先，平手按代码）；
  引擎 realistic.run_real：T+1 开盘买（开盘涨停＝一字买不进、不递补）、持 5 个交易日开盘卖、跌停锁死顺延、往返 0.3%；K=20、max_new=5。
  §996 FA_DIP_h5   ：首次业绩预喜（FA：预增/扭亏/略增、增幅下限>0、净利下限>0，同报告期只认第一次；T = sd）。
  §997 DIVP_DIP_h5 ：现金分红预案（div_proc=预案、同报告期第一次，T = sd）且 每股税前派现 / T 收盘 ≥1%。
  §998 BADF_DIP_h5 ：首次业绩预告为利空（type ∈ 预减/略减/首亏/续亏/增亏，同报告期只认第一次；T = sd）——「利空出尽」。
  §999 MIX_DIP_h5  ：稀疏催化并集：重要股东/高管增持合计≥0.5% 总股本（§340 口径，T = 公告日之后第一个交易日）
                      ∪ 大宗溢价成交（BTP，T = 成交日）∪ 业绩快报首次正增长（lt_research26 xa，T = sd）。
  §1000 EA_DIP_h5  ：财报业绩加速（G：扣非同比≥50%、营收同比≥20%、扣非为正且同比高于上期；T = sd）。
  sd = 公告日当天或之后第一个交易日（与现役中线同口径）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=<backend>:.:../shortline_research python ../shortline_research/evdip_s996.py "<phase>" out.json"""
import sys, json, os
from datetime import timedelta
import polars as pl
import lt_data as L
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
from lt_research13 import div_sig
from lt_research26 import ex_sig
from inb_s340 import in_events
c = pl.col
HOLD, K, MAX_NEW, R20_MAX = 5, 20, 5, -0.15
BAD = ["预减", "略减", "首亏", "续亏", "增亏"]
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
IDEAS = {"FA_DIP_h5": "FA", "DIVP_DIP_h5": "E_div", "BADF_DIP_h5": "E_bad", "MIX_DIP_h5": "E_mix", "EA_DIP_h5": "G"}


def bad_forecast(td):
    f = L.forecast_events().filter(c("ann").str.len_chars() == 10).sort("ann")
    f = f.unique(["code", "end_date"], keep="first", maintain_order=True).filter(c("type").is_in(BAD))
    f = f.join_asof(td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("ann2")), left_on="ann", right_on="ann2",
                    strategy="forward").drop_nulls("sd")
    return f.group_by("code", "sd").agg(pl.lit(True).alias("E_bad")).rename({"sd": "trade_date"})


def prep():
    df = R25.prep().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    ev = in_events().with_columns((c("a") + timedelta(days=1)).alias("a1"))
    tdd = td.with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a1").join_asof(tdd, left_on="a1", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.group_by("code", "trade_date").agg(c("cr").max().alias("inb_cr"))
    df = (df.join(ev, on=["code", "trade_date"], how="left")
            .join(div_sig(td), on=["code", "trade_date"], how="left")
            .join(ex_sig(td).select("code", "trade_date", "xa"), on=["code", "trade_date"], how="left")
            .join(bad_forecast(td), on=["code", "trade_date"], how="left")).sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    df = df.with_columns(
        ((c("close") > dl + 0.005) & (c("close") < ul - 0.005)).fill_null(False).alias("not_lim"),
        (c("cd").is_not_null() & (c("cd") / c("close") >= 0.01)).fill_null(False).alias("E_div"),
        c("E_bad").fill_null(False),
        (c("inb_cr").is_not_null() | c("BTP").fill_null(False) | c("xa").fill_null(False)).alias("E_mix"),
        (-c("r20").fill_null(9) - tie).alias("sc_dip"))
    base = c("liq") & (c("r20") <= R20_MAX) & c("not_lim")
    return df.with_columns([(c(ev) & base).fill_null(False).alias(f"S_{n}") for n, ev in IDEAS.items()]).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    keep = ["code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "liq", "total_mv", "r20", "sc_dip"] + [f"S_{n}" for n in IDEAS]
    df = df.select(keep)
    od = os.path.dirname(out) or "."
    os.makedirs(od, exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for name in IDEAS:
            sig = f"S_{name}"
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            info = {}
            if w.height:
                info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "uniq_codes": w["code"].n_unique(),
                        "r20_med": round(float(w["r20"].median()), 3), "max_per_day": int(w.group_by("trade_date").len()["len"].max()),
                        "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()}
            res.setdefault("_info", {}).setdefault(name, {})[ph] = info
            print("INFO", name, ph, info, flush=True)
            t, cur = run_real(df, sig, "sc_dip", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
