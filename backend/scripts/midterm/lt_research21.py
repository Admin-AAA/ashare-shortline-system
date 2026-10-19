"""[中线] §105 机构调研关注度漂移（stk_surv 30 日机构家数激增）— 预注册 3 版（/workspace/strategy-research.md §105，结果出来前写定，不调参）。
数据：output/research/sprint4/stk_surv/YYYYMMDD.parquet（scripts/fetch_stk_surv_cache.py 经统一数据网关只读缓存；历史 2021-08-12 起）。
事件 (code, S=surv_date)：计数 = surv_date ∈ [S-29, S] 的 rece_org 去重家数。
  机构口径：org_type 非空且不为 '--' / '其它'；基金/券商口径：org_type ∈ {基金管理公司, 证券公司}。
PIT：可见日 sd = (>= S 的首个交易日) 之后第 3 个交易日；sd 收盘出信号、次日开盘买（run_real：开盘涨停不买、跌停锁死顺延、0.3% 成本）。
首次激增：该股前 90 自然日 [S-90, S) 内无同口径达阈值事件；且 S >= 数据首日 + 90 天。同一 (code, sd) 取最大计数。
  "数据首日 + 90 天"的实现（2026-10-19 07:55 CST 写于跑任何变体之前，见日志 §105 结果）：上游 stk_surv 在 2025-09-12~2025-11-06
  整段为空（promax 实测返回 0 行，非缓存问题），2021-09~2023-09 视回补进度可能未缓存。按预注册"保证首次判断有 90 天历史"的本意，
  把 ≥10 个连续缺数据交易日视为数据断档，S 须 >= 所在连续数据段首日 + 90 天（主口径）。另报 *_lit 信息版（只按全局首日 + 90 天，忽略断档），不参与判定。
  S_cnt_h20  : 机构家数 >= 30，首次，持 20 -> /mid
  S_fund_h20 : 基金+券商家数 >= 15，首次，持 20 -> /mid
  S_cnt_h60  : 同 S_cnt，持 60 -> /long
排序分 = 计数降序，平手按代码升序。liq，k_slots=20, max_new=5。cohort_bench 同市值五分位对照。
参照（算重叠）：预注册 EA_h20、F_any_h20、RP_r05；信息项 INC_opt_h20、BT_prem_h20（§109/§114 后上线的 /mid）。
用法：LT_START=.. LT_END=.. python lt_research21.py "<phase>,<phase>" out.json"""
import sys, json, os, datetime as dt
from collections import Counter, deque
from pathlib import Path
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
import lt_research20 as R20
import lt_research25 as R25
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
SURV = Path(__file__).resolve().parents[2] / "output/research/sprint4/stk_surv"
NON_INST = {"", "--", "其它", "None", "nan", "none", "null"}
FUND = {"基金管理公司", "证券公司"}
THR_C, THR_F, LAG, FIRST_D, GAP_TD = 30, 15, 3, 90, 10


def load_surv():
    fs = sorted(SURV.glob("*.parquet"))
    rows, parts = {}, []
    for f in fs:
        x = pl.read_parquet(f)
        rows[f.stem] = x.height
        if x.height:
            parts.append(x.select([c(k).cast(pl.Utf8) for k in ("ts_code", "surv_date", "rece_org", "org_type")]))
    d = pl.concat(parts, how="vertical_relaxed").with_columns([c(k).str.strip_chars() for k in ("ts_code", "surv_date", "rece_org", "org_type")])
    d = d.filter((c("surv_date").str.len_chars() == 8) & c("rece_org").is_not_null() & ~c("rece_org").is_in(list(NON_INST)))
    d = d.filter(c("ts_code").str.slice(0, 6).str.contains(r"^\d{6}$") & ~c("ts_code").str.ends_with(".BJ"))
    d = d.with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("surv_date").str.to_date("%Y%m%d").alias("d"),
                       (~c("org_type").fill_null("").is_in(list(NON_INST))).alias("inst"),
                       c("org_type").is_in(list(FUND)).fill_null(False).alias("fund"))
    return d, rows


def segments(rows: dict, tdays: set):
    """返回 [(seg_start_date, seg_end_date)]：连续数据段。工作日状态：有行=data；无文件或(0 行且是交易日)=missing；
    0 行且非交易日（节假日）=neutral。missing 连续 >= GAP_TD 个（中间 neutral 不打断，data 打断）即断档。"""
    first = min(dt.date(int(k[:4]), int(k[4:6]), int(k[6:])) for k, v in rows.items() if v > 0)
    last = max(dt.date(int(k[:4]), int(k[4:6]), int(k[6:])) for k in rows)
    segs, cur, miss, day = [], first, 0, first
    last_data = first
    while day <= last:
        if day.weekday() < 5:
            k = day.strftime("%Y%m%d")
            n = rows.get(k)
            if n:
                if miss >= GAP_TD and cur is None:
                    cur = day
                miss = 0; last_data = day
                if cur is None: cur = day
            elif n is None or day.isoformat() in tdays:
                miss += 1
                if miss >= GAP_TD and cur is not None:
                    segs.append((cur, last_data)); cur = None
        day += dt.timedelta(days=1)
    if cur is not None: segs.append((cur, last_data))
    return segs


def _window_counts(rec: pl.DataFrame) -> dict:
    """rec: code, d, org (去重). 返回 {(code, d): [S-29, S] 去重家数}（每个出现过的 S）。"""
    out = {}
    for (code,), g in rec.sort("code", "d").group_by(["code"], maintain_order=True):
        ds, orgs = g["d"].to_list(), g["org"].to_list()
        cnt, q, i = Counter(), deque(), 0
        for S in sorted(set(ds)):
            while i < len(ds) and ds[i] <= S:
                cnt[orgs[i]] += 1; q.append((ds[i], orgs[i])); i += 1
            lo = S - dt.timedelta(days=29)
            while q and q[0][0] < lo:
                _, o = q.popleft(); cnt[o] -= 1
                if cnt[o] == 0: del cnt[o]
            out[(code, S)] = len(cnt)
    return out


def surv_events(tdays: set):
    d, rows = load_surv()
    segs = segments(rows, tdays)
    print("stk_surv data segments:", [(a.isoformat(), b.isoformat()) for a, b in segs], flush=True)
    res = {}
    for flag, name in (("inst", "n_inst"), ("fund", "n_fund")):
        rec = d.filter(c(flag)).select("code", "d", c("rece_org").alias("org")).unique()
        for k, v in _window_counts(rec).items():
            res.setdefault(k, {})[name] = v
    ev = pl.DataFrame({"code": [k[0] for k in res], "d": [k[1] for k in res],
                       "n_inst": [v.get("n_inst", 0) for v in res.values()],
                       "n_fund": [v.get("n_fund", 0) for v in res.values()]}).sort("code", "d")
    for col, thr, flag in (("n_inst", THR_C, "first_c"), ("n_fund", THR_F, "first_f")):
        hit = ev.filter(c(col) >= thr).select("code", "d").sort("code", "d")
        hit = hit.with_columns(c("d").shift(1).over("code").alias("pd"))
        hit = hit.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > FIRST_D))
        ev = ev.join(hit.select("code", "d", pl.lit(True).alias(flag)), on=["code", "d"], how="left")
    ev = ev.with_columns(c("first_c").fill_null(False), c("first_f").fill_null(False))
    # 主口径：S 在某连续数据段内且 >= 段首 + 90 天；信息口径 lit：S >= 全局首日 + 90 天
    seg_df = pl.DataFrame({"s0": [a for a, _ in segs], "s1": [b for _, b in segs]}).sort("s0")
    ev = ev.sort("d").join_asof(seg_df, left_on="d", right_on="s0", strategy="backward")
    ok = (c("d") <= c("s1")) & (c("d") >= c("s0") + pl.duration(days=FIRST_D))
    g0 = segs[0][0]
    ev = ev.with_columns(ok.fill_null(False).alias("ok_seg"), (c("d") >= pl.lit(g0 + dt.timedelta(days=FIRST_D))).alias("ok_lit"))
    return ev.drop("s0", "s1")


def surv_sig(td: pl.DataFrame) -> pl.DataFrame:
    tdd = td.with_columns(c("trade_date").str.to_date().alias("t")).sort("t")
    ev = surv_events(set(td["trade_date"].to_list()))
    ev = ev.filter((c("first_c") | c("first_f")) & c("ok_lit") & (c("d") >= tdd["t"].min()))
    tdd = tdd.with_columns(c("trade_date").shift(-LAG).alias("sd"))
    ev = ev.sort("d").join_asof(tdd.select("t", "sd"), left_on="d", right_on="t", strategy="forward").drop_nulls("sd")
    agg = []
    for suf, okc in (("", "ok_seg"), ("_lit", "ok_lit")):
        agg += [pl.when(c("first_c") & c(okc)).then(c("n_inst")).max().alias("cnt_c" + suf),
                pl.when(c("first_f") & c(okc)).then(c("n_fund")).max().alias("cnt_f" + suf)]
    return ev.group_by("code", "sd").agg(agg).rename({"sd": "trade_date"})


def prep():
    df = R25.prep()  # = R23.prep()（EA/F_any/RP_r05/INC_opt/SCW）+ 大宗交易特征（BTP）
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(surv_sig(td), on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    out = []
    for suf in ("", "_lit"):
        out += [(c("liq") & c("cnt_c" + suf).is_not_null()).fill_null(False).alias("SC" + suf),
                (c("liq") & c("cnt_f" + suf).is_not_null()).fill_null(False).alias("SF" + suf),
                (c("cnt_c" + suf).fill_null(0) - tie).alias("sc_c" + suf), (c("cnt_f" + suf).fill_null(0) - tie).alias("sc_f" + suf)]
    return df.with_columns(out).sort("code", "trade_date")


C = {"S_cnt_h20": ("SC", "sc_c", 20), "S_fund_h20": ("SF", "sc_f", 20), "S_cnt_h60": ("SC", "sc_c", 60),
     "S_cnt_h20_lit": ("SC_lit", "sc_c_lit", 20), "S_fund_h20_lit": ("SF_lit", "sc_f_lit", 20), "S_cnt_h60_lit": ("SC_lit", "sc_c_lit", 60),
     "EA_h20": ("G", "sc_g", 20), "F_any_h20": ("FA", "sc_a", 20), "RP_r05": ("RP_r05", "sc_r", 20),
     "INC_opt_h20": ("IO", "sc_i", 20), "BT_prem_h20": ("BTP", "sc_bt", 20)}
REFS = ("EA_h20", "F_any_h20", "RP_r05", "INC_opt_h20", "BT_prem_h20")
VARS = ("S_cnt_h20", "S_fund_h20", "S_cnt_h60", "S_cnt_h20_lit", "S_fund_h20_lit", "S_cnt_h60_lit")

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signal stock-days per year (liq):", df.group_by(yr).agg(c("SC").sum(), c("SF").sum(), c("SC_lit").sum(), c("SF_lit").sum())
          .sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        trades = {}
        for n, (sig, sc, h) in C.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
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
