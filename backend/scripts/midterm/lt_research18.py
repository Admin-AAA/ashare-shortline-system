"""[中线] §100 mid-repurchase-plan 回购质量过滤（预注册写于 /workspace/strategy-research.md §100，结果出来前写定，不调参）。
基线 = 现行 /mid mid-repurchase-plan（§91 R_plan_h20：回购预案、365 天内首次、公告日 PIT 映射到 >= ann_date 首个交易日收盘出信号、
次日开盘买、持 20 日；liq；按 金额上限/总市值 降序；realistic k20/max_new5）。
  RP_r05  : 只保留 回购金额上限 / 信号日总市值 >= 0.5%（同实盘 ratio、§91 R_big_h60 口径）
  RP_prem : 只保留 回购价格上限 high_limit >= 1.20 x 信号日不复权收盘（同 (code,sd) 多条取最大；缺失 = 不满足）；
            N3 内合格事件 high_limit 缺失 > 30% 则放弃本变体（预注册规则，脚本自动执行）
语义：事件型候选池过滤，不满足的事件删除、后续候选按原排序补位。另报基线成交中被删部分（笔数、每笔均值）。
用法：LT_START=.. LT_END=.. python lt_research18.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import build, fixed, PH
from lt_research12 import _pit, EV
from realistic import run_real, metrics_real
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
N3 = PH["N3 2024-01~2026-10"]


def repo_sig_q(td):
    """lt_research12.repo_sig + 同 (code, sd) 的最大 high_limit。"""
    r = pl.concat([pl.read_parquet(f) for f in sorted(glob.glob(str(EV / "repurchase_*.parquet")))], how="diagonal_relaxed")
    r = r.filter(c("proc").cast(pl.Utf8).str.contains("预案")).unique(["ts_code", "ann_date"])
    r = _pit(r, td).sort("code", "sd")
    r = r.with_columns(c("sd").str.to_date().alias("d"))
    r = r.with_columns(c("d").shift(1).over("code").alias("pd"))
    r = r.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > 365))
    return (r.group_by("code", "sd").agg(c("amount").cast(pl.Float64, strict=False).max().alias("rp_amt"),
                                         c("high_limit").cast(pl.Float64, strict=False).max().alias("rp_hl"))
             .with_columns(pl.lit(True).alias("rp")).rename({"sd": "trade_date"}))


def prep():
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(repo_sig_q(td), on=["code", "trade_date"], how="left")
    ratio = c("rp_amt") / (c("total_mv").cast(pl.Float64, strict=False) * 1e4)
    prem = c("rp_hl") / c("close")
    RP = (c("liq") & c("rp").fill_null(False)).fill_null(False)
    df = df.with_columns(RP.alias("RP"), ratio.fill_null(0).alias("sc_r"), prem.alias("prem"))
    df = df.with_columns((c("RP") & (c("sc_r") >= 0.005)).fill_null(False).alias("RP_r05"),
                         (c("RP") & (c("prem") >= 1.20)).fill_null(False).alias("RP_prem"))
    return df.sort("code", "trade_date"), td


def removed(t0, df, td, vcol):
    if t0 is None or t0.height == 0: return {"n_rm": 0}
    prev = td.select(c("trade_date").alias("entry"), c("trade_date").shift(1).alias("trade_date"))
    fl = df.filter(c("RP")).select("code", "trade_date", vcol)
    x = t0.join(prev, on="entry", how="left").join(fl, on=["code", "trade_date"], how="left")
    rm = x.filter(~c(vcol).fill_null(False))["ret"].to_numpy(); kp = x.filter(c(vcol).fill_null(False))["ret"].to_numpy()
    return {"n_rm": int(len(rm)), "avg_rm": round(100 * float(rm.mean()), 2) if len(rm) else None,
            "win_rm": round(100 * float((rm > 0).mean()), 1) if len(rm) else None,
            "avg_kept": round(100 * float(kp.mean()), 2) if len(kp) else None}


def coverage(df, s, e):
    ev = df.filter(c("RP") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
    miss = ev.filter(c("rp_hl").is_null() | (c("rp_hl") <= 0)).height
    return {"events": ev.height, "hl_missing": miss, "miss_frac": round(miss / max(ev.height, 1), 4),
            "r05_events": int(ev["RP_r05"].sum()), "prem_events": int(ev["RP_prem"].sum()),
            "prem_med": round(float(ev["prem"].drop_nulls().median() or 0), 3)}


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df, td = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    if "N3 2024-01~2026-10" in phases:
        res["_coverage_N3"] = coverage(df, *N3)
        print("coverage N3", res["_coverage_N3"], flush=True)
    cov = res.get("_coverage_N3")
    drop_prem = bool(cov and cov["miss_frac"] > 0.30)
    if cov is None: print("WARN: N3 coverage not yet computed; run N3 group first", flush=True)
    V = ["RP_r05"] + ([] if drop_prem else ["RP_prem"])
    if drop_prem: print("RP_prem dropped (high_limit missing > 30% in N3)", flush=True)
    for ph in phases:
        s, e = PH[ph]
        m = {"_cov": coverage(df, s, e)}
        t0, cur0 = run_real(df, "RP", "sc_r", fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
        m["base"] = {a: float(v) for a, v in metrics_real(t0, cur0).items()}
        for vn in V:
            t, cur = run_real(df, vn, "sc_r", fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
            mm = {a: float(v) for a, v in metrics_real(t, cur).items()}
            mm.update(removed(t0, df, td, vn))
            m[vn] = mm
        res.setdefault("mid-repurchase-plan", {})[ph] = m
        print(ph, json.dumps(m, ensure_ascii=False), flush=True)
        json.dump(res, open(out, "w"), indent=1)
    print("done", flush=True)
