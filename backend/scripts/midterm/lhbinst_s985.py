"""§985 LHBINST_h20：龙虎榜机构专用净买入≥0.1亿，持20。受限窗口。用法: python lhbinst_s985.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD, K, MAX_NEW, NET_MIN = "LHBINST_h20", 20, 20, 5, 0.10
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "sprint4", "top_inst")


def lhb_net():
    parts = []
    for f in sorted(glob.glob(os.path.join(ROOT, "*.parquet"))):
        d = pl.read_parquet(f)
        d = d.filter(c("institution").str.contains("机构专用|机构投资者|^机构$"))
        if d.height == 0: continue
        parts.append(d.group_by(["code", "trade_date"]).agg(c("net_buy_yi").sum().alias("net")))
    if not parts:
        return pl.DataFrame({"code": [], "trade_date": [], "net": []})
    return pl.concat(parts).group_by(["code", "trade_date"]).agg(c("net").sum())


def prep():
    df = build()
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    ev = lhb_net()
    df = df.join(ev, on=["code", "trade_date"], how="left")
    sig = (c("liq") & c("net").is_not_null() & (c("net") >= NET_MIN)).fill_null(False)
    return df.with_columns(sig.alias("LHBINST"), (c("net").fill_null(0) - tie).alias("sc_net")).sort("code", "trade_date")


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("LHBINST/year", df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("LHBINST").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "LHBINST", "sc_net", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
