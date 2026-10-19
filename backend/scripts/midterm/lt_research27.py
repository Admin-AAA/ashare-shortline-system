"""[中线/长线] §116 回购质量过滤（凯旺科技 301182 引发；预注册写于 /workspace/strategy-research.md §116，结果出来前写定，不调参）。
基线：MID = 现行 mid-repurchase-plan（RP，金额上限/信号日总市值 >= 0.5%，持 20）；LONG = 现行 long-repurchase-big（>= 1%，持 60）。
PIT：回购公告 ann_date -> >= ann_date 首个交易日 sd 收盘出信号，次日开盘买；财务 fina 只用 ann_date < sd 的最新一期（严格早于信号日）。
  a  用途=注销：tushare repurchase 无用途字段，全历史需逐条巨潮公告文本 -> data-unavailable，不测。
  b  NP  : 剔除最新已披露 eps(累计) < 0（亏损）
  c  OCF : 剔除最新已披露 ocfps(累计 YTD) < 0（TTM 需四期拼接、缺失多，预注册用 YTD 代理）
  d  SH  : 口径改为 金额上限 / 价格上限 / 总股本 >= 阈值（总股本 = total_mv*1e4/close）；high_limit 缺失 = 不满足
  e  NPOCF: b & c（组合预注册为 b&c，不按结果挑）
财务缺失 = 保留（不因缺数据剔除）。排序同基线 sc_r 降序；realistic k20/max_new5。
用法：LT_START=.. LT_END=.. python lt_research27.py "<phase>,<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import build, fixed, PH
import lt_data as L
from lt_research18 import repo_sig_q, removed
from realistic import run_real, metrics_real
c = pl.col
PH = dict(PH); PH["N3 2023-10~2026-10"] = ("2023-10-01", "2026-10-16")


def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(repo_sig_q(td), on=["code", "trade_date"], how="left")
    f = L.fina_pit().select("code", "trade_date", c("eps").cast(pl.Float64, strict=False), c("ocfps").cast(pl.Float64, strict=False))
    f = f.with_columns((c("trade_date").str.to_date() + pl.duration(days=1)).dt.strftime("%Y-%m-%d").alias("trade_date")) \
         .unique(["code", "trade_date"], keep="last").sort("trade_date")
    df = df.sort("trade_date").join_asof(f, on="trade_date", by="code", strategy="backward")
    tmv = c("total_mv").cast(pl.Float64, strict=False) * 1e4
    ratio = (c("rp_amt") / tmv).fill_null(0)
    sh = (c("rp_amt") / c("rp_hl") / (tmv / c("close"))).fill_null(0)
    RP = (c("liq") & c("rp").fill_null(False)).fill_null(False)
    np_ok = ~(c("eps") < 0).fill_null(False); ocf_ok = ~(c("ocfps") < 0).fill_null(False)
    df = df.with_columns(RP.alias("RP"), ratio.alias("sc_r"), sh.alias("sh"), np_ok.alias("np_ok"), ocf_ok.alias("ocf_ok"))
    cols = {}
    for tag, th in (("M", 0.005), ("L", 0.01)):
        b = c("RP") & (c("sc_r") >= th)
        cols[f"{tag}_base"] = b; cols[f"{tag}_NP"] = b & c("np_ok"); cols[f"{tag}_OCF"] = b & c("ocf_ok")
        cols[f"{tag}_SH"] = c("RP") & (c("sh") >= th); cols[f"{tag}_NPOCF"] = b & c("np_ok") & c("ocf_ok")
    df = df.with_columns([v.fill_null(False).alias(k) for k, v in cols.items()])
    return df.sort("code", "trade_date"), td


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df, td = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for tag, h in (("M", 20), ("L", 60)):
            t0, cur0 = run_real(df, f"{tag}_base", "sc_r", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            m = {"base": {a: float(v) for a, v in metrics_real(t0, cur0).items()}}
            dfb = df.with_columns(c(f"{tag}_base").alias("RP"))
            for v in ("NP", "OCF", "SH", "NPOCF"):
                t, cur = run_real(df, f"{tag}_{v}", "sc_r", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
                mm = {a: float(x) for a, x in metrics_real(t, cur).items()}
                mm.update(removed(t0, dfb, td, f"{tag}_{v}")); m[v] = mm
            res.setdefault(tag, {})[ph] = m
            print(ph, tag, json.dumps(m, ensure_ascii=False), flush=True)
        json.dump(res, open(out, "w"), indent=1)
    print("done", flush=True)
