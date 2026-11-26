"""[中线 §1065–§1068] 新数据源中线想法（2026-11-26 第 2 轮；预注册见 docs/strategy-research.md §1065–§1068）。
  ARU_h20 : 分析师盈利预测上调扩散（report_rc）—— 30 天内 ≥2 家机构把当年净利预测上调 ≥5%、0 家下调。
  BGS_h20 : 券商月度金股多家共荐（broker_recommend）—— 当月 ≥3 家券商同推。
  PLR_h20 : 大股东大幅解押（pledge_stats 中登周度）—— 质押比例一周内从 ≥10% 降 ≥5 个百分点。
  ACI_h20 : 冷门股首次覆盖（report_rc）—— 标题含「首次覆盖」且评级看多、此前 180 天无任何研报。
引擎：realistic.run_real，T 收盘信号、T+1 开盘买（开盘涨停买不进、不递补），持 20 个交易日开盘卖，跌停锁死顺延，往返 0.3%；
K=20、max_new=5（仅回测口径）。股池 liq（20 日均额>5000 万、上市>250 日、非 ST PIT、非北交所）& T 收盘未封涨停/跌停。
数据（只读）：backend/data/research_cache/{report_rc,broker_recommend,pledge_stats}/（fetch_mid1126.py / export_pledge_s1068.py 拉取）。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. <venv>/python mid1126_s1065.py count|"<phase>[,<phase>]" <out.json>"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import deque
from datetime import date, timedelta

import polars as pl

import lt_data as L
from lt_research import build, fixed
from lt_research46 import PH, full_metrics
from realistic import run_real

c = pl.col
RC = L.D.parent / "research_cache"
PH = dict(PH)
PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
HOLD, K, MAX_NEW = 20, 20, 5
# 预注册阈值（写死，不扫描）
ARU_REV, ARU_WIN, ARU_NORG, ARU_DEDUP, ARU_PREV_MAX = 0.05, 30, 2, 60, 180
BGS_MIN, BGS_TDAY = 3, 3
PLR_BASE, PLR_DROP, PLR_LAG, PLR_DEDUP = 10.0, 5.0, 3, 60
ACI_QUIET, ACI_DEDUP = 180, 180
ACI_RATING = {"买入", "增持", "强烈推荐", "推荐", "强推", "优于大市", "跑赢行业", "强于大市", "审慎增持", "谨慎增持", "持有-买入", "超配", "跑赢大市"}
NAMES = {"ARU_h20": ("ARU", "sc_aru"), "BGS_h20": ("BGS", "sc_bgs"), "PLR_h20": ("PLR", "sc_plr"), "ACI_h20": ("ACI", "sc_aci")}


def _next_td(ev: pl.DataFrame, td: list[str], col: str = "d", lag: int = 1) -> pl.DataFrame:
    """事件日 d（YYYY-MM-DD）→ 严格晚于 d 的第 lag 个交易日 T（T 收盘出信号）。"""
    import bisect

    out = []
    for d in ev[col].to_list():
        i = bisect.bisect_right(td, d) + lag - 1
        out.append(td[i] if i < len(td) else None)
    return ev.with_columns(pl.Series("trade_date", out, dtype=pl.Utf8)).drop_nulls("trade_date")


def rc_rows() -> pl.DataFrame:
    fs = sorted(glob.glob(str(RC / "report_rc" / "*.parquet")))
    cols = ["ts_code", "report_date", "report_title", "report_type", "org_name", "quarter", "np", "rating"]
    d = pl.concat([pl.read_parquet(f).select([x for x in cols if x in pl.read_parquet_schema(f)]) for f in fs], how="diagonal_relaxed")
    d = d.with_columns(c("report_date").cast(pl.Utf8)).filter(c("ts_code").is_not_null() & (c("report_date").str.len_chars() == 8))
    return d.with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("report_date").alias("d"),
                          c("np").cast(pl.Float64, strict=False))


def aru_events(rc: pl.DataFrame, td: list[str]) -> pl.DataFrame:
    x = rc.filter((c("quarter") == c("d").str.slice(0, 4) + "Q4") & (c("np") > 0) & c("org_name").is_not_null())
    x = x.group_by("code", "org_name", "quarter", "d").agg(c("np").median()).sort("code", "org_name", "quarter", "d")
    x = x.with_columns(c("np").shift(1).over("code", "org_name", "quarter").alias("np0"),
                       c("d").shift(1).over("code", "org_name", "quarter").alias("d0"))
    x = x.filter(c("np0").is_not_null() & ((c("d").str.to_date() - c("d0").str.to_date()).dt.total_days() <= ARU_PREV_MAX))
    x = x.with_columns((c("np") / c("np0") - 1).alias("rev"))
    x = x.filter((c("rev") >= ARU_REV) | (c("rev") <= -ARU_REV)).select("code", "org_name", "d", "rev").sort("code", "d")
    rows = []
    for (code,), g in x.partition_by("code", as_dict=True, maintain_order=True).items():
        win: deque = deque()
        last_sig = None
        ds, orgs, revs = g["d"].to_list(), g["org_name"].to_list(), g["rev"].to_list()
        i = 0
        while i < len(ds):
            d = ds[i]
            while i < len(ds) and ds[i] == d:
                win.append((date.fromisoformat(ds[i]), orgs[i], revs[i]))
                i += 1
            dd = date.fromisoformat(d)
            while win and (dd - win[0][0]).days >= ARU_WIN:
                win.popleft()
            up = {o for _, o, r in win if r >= ARU_REV}
            dn = {o for _, o, r in win if r <= -ARU_REV}
            if len(up) >= ARU_NORG and not dn and (last_sig is None or (dd - last_sig).days > ARU_DEDUP):
                last_sig = dd
                mrev = sum(min(r, 0.99) for _, o, r in win if r >= ARU_REV) / max(1, sum(1 for _, o, r in win if r >= ARU_REV))
                rows.append((code, d, len(up) + mrev))
    ev = pl.DataFrame(rows, schema=["code", "d", "sc_aru"], orient="row")
    return _next_td(ev, td).group_by("code", "trade_date").agg(c("sc_aru").max())


def aci_events(rc: pl.DataFrame, td: list[str]) -> pl.DataFrame:
    allr = rc.select("code", "d").unique().sort("code", "d")
    first = rc.filter(c("report_title").str.contains("首次覆盖") & c("rating").is_in(list(ACI_RATING)) & (c("d") >= "2019-07-01"))
    first = first.select("code", "d").unique().sort("code", "d")
    hist = {k[0]: g["d"].to_list() for k, g in allr.partition_by("code", as_dict=True, maintain_order=True).items()}
    import bisect

    rows, last = [], {}
    for code, d in first.iter_rows():
        h = hist.get(code, [])
        i = bisect.bisect_left(h, d)
        prev = h[i - 1] if i > 0 else None
        dd = date.fromisoformat(d)
        if prev is not None and (dd - date.fromisoformat(prev)).days <= ACI_QUIET:
            continue
        if code in last and (dd - last[code]).days <= ACI_DEDUP:
            continue
        last[code] = dd
        rows.append((code, d))
    ev = pl.DataFrame(rows, schema=["code", "d"], orient="row").with_columns(pl.lit(True).alias("aci_ev"))
    return _next_td(ev, td).group_by("code", "trade_date").agg(c("aci_ev").any())


def bgs_events(td: list[str]) -> pl.DataFrame:
    fs = sorted(glob.glob(str(RC / "broker_recommend" / "*.parquet")))
    b = pl.concat([pl.read_parquet(f) for f in fs if pl.read_parquet_schema(f)], how="diagonal_relaxed")
    b = b.filter(c("ts_code").is_not_null()).with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("month").cast(pl.Utf8))
    g = b.group_by("code", "month").agg(c("broker").n_unique().alias("nb")).filter(c("nb") >= BGS_MIN)
    # T = 当月第 BGS_TDAY 个交易日
    tdf = pl.DataFrame({"trade_date": td}).with_columns(c("trade_date").str.replace_all("-", "").str.slice(0, 6).alias("month"))
    tdf = tdf.with_columns(pl.int_range(pl.len()).over("month").alias("k")).filter(c("k") == BGS_TDAY - 1)
    return g.join(tdf.select("month", "trade_date"), on="month").select("code", "trade_date", c("nb").cast(pl.Float64).alias("sc_bgs"))


def plr_events(td: list[str]) -> pl.DataFrame:
    p = pl.read_parquet(RC / "pledge_stats" / "pledge_stats.parquet").with_columns(c("pledge_ratio").cast(pl.Float64)).sort("code", "end_date")
    p = p.with_columns(c("pledge_ratio").shift(1).over("code").alias("prev"))
    ev = p.filter((c("prev") >= PLR_BASE) & (c("prev") - c("pledge_ratio") >= PLR_DROP)).with_columns(
        (c("prev") - c("pledge_ratio")).alias("drop")).select("code", c("end_date").alias("d"), "drop").sort("code", "d")
    rows, last = [], {}
    for code, d, dr in ev.iter_rows():
        dd = date.fromisoformat(d)
        if code in last and (dd - last[code]).days <= PLR_DEDUP:
            continue
        last[code] = dd
        rows.append((code, d, float(dr)))
    ev = pl.DataFrame(rows, schema=["code", "d", "sc_plr"], orient="row")
    return _next_td(ev, td, lag=PLR_LAG).group_by("code", "trade_date").agg(c("sc_plr").max())


def prep() -> pl.DataFrame:
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    df = df.with_columns(((c("close") <= pl.coalesce(c("down_limit"), pc * (1 - band)) + 0.005).fill_null(False)).alias("ldc"),
                         ((c("close") >= pl.coalesce(c("up_limit"), pc * (1 + band)) - 0.005).fill_null(False)).alias("luc"))
    td = sorted(df["trade_date"].unique().to_list())
    rc = rc_rows()
    for ev in (aru_events(rc, td), aci_events(rc, td), bgs_events(td), plr_events(td)):
        df = df.join(ev, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    pool = c("liq") & ~c("ldc") & ~c("luc")
    return df.with_columns(
        (pool & c("sc_aru").is_not_null()).fill_null(False).alias("ARU"),
        (pool & c("sc_bgs").is_not_null()).fill_null(False).alias("BGS"),
        (pool & c("sc_plr").is_not_null()).fill_null(False).alias("PLR"),
        (pool & c("aci_ev").fill_null(False)).fill_null(False).alias("ACI"),
        (c("sc_aru").fill_null(0) - tie).alias("sc_aru"), (c("sc_bgs").fill_null(0) - tie).alias("sc_bgs"),
        (c("sc_plr").fill_null(0) - tie).alias("sc_plr"), (-c("total_mv").fill_null(0) / 1e9 - tie).alias("sc_aci"),
    ).sort("code", "trade_date")


def count(df: pl.DataFrame, out: str) -> None:
    res = {}
    for ph, (s, e) in PH.items():
        x = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        res[ph] = {n: [int(x[sig].sum()), int(x.filter(c(sig))["trade_date"].n_unique())] for n, (sig, _) in NAMES.items()}
        print(ph, res[ph], flush=True)
    json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    phases, out = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    df = prep()
    if phases == "count":
        count(df, out)
        sys.exit(0)
    only = [n for n in os.environ.get("ONLY", "").split(",") if n] or list(NAMES)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases.split(","):
        s, e = PH[ph]
        for name in only:
            sig, sc = NAMES[name]
            t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
