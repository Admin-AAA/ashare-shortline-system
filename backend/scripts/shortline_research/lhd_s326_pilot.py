"""§326 追龙方法试点：首板次日用分钟线判断 一字/开板/可买，保守成交。只读本地 data/xiaoshi_min1，不写它。
用法：cd backend && PYTHONPATH=. .venv/bin/python scripts/shortline_research/lhd_s326_pilot.py run   # 逐股读分钟线（串行，省内存）
      PYTHONPATH=. .venv/bin/python scripts/shortline_research/lhd_s326_pilot.py eval  # 评估并写 output/research/s326/eval.json
规则见 docs/strategy-research.md §326（预注册，不得改）。"""
from __future__ import annotations
import json, sys, glob, os, gc
from pathlib import Path
import numpy as np

B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
OUT = B / "output/research/s326"
MIN = B / "data/xiaoshi_min1"
EVENTS = B / "output/research/s325/events.parquet"
SLIP, COST_PCT = 0.001, 0.3
EPS = 0.005
HOLDS = (1, 2, 3)
PRIMARY = 2


# ---------------- 纯函数（有单测） ----------------
def snap_ratio(u: float, close_t: float) -> float:
    r = u / close_t - 1.0
    return min((0.05, 0.10, 0.20, 0.30), key=lambda x: abs(x - r))


def classify_next_day(o, h, l, c, v, u):
    """T+1 分钟条 -> (类别, 买入下标, 买价)。类别 yizi / open_sealed_filled / open_sealed_nofill / open_sealed_noopen / other。
    other = 开盘 < U×0.998（不是追龙对象）；买入 = 条 j（j>=1，第一根之后）第一次 close<U-0.005 时，在 j+1 条开盘价×(1+滑点)买。"""
    n = len(o)
    if n == 0:
        return "nodata", None, None
    on = l >= u - EPS
    if bool(on.all()):
        return "yizi", None, None
    if o[0] < u * 0.998:
        return "other", None, None
    for j in range(1, n):
        if c[j] < u - EPS:
            if j + 1 >= n:
                return "open_sealed_nofill", None, None
            px = o[j + 1] * (1 + SLIP)
            if o[j + 1] < u - EPS and px < u and v[j + 1] > 0:
                return "open_sealed_filled", j + 1, float(px)
            return "open_sealed_nofill", None, None
    return "open_sealed_noopen", None, None


def ctrl_entry(o, u):
    """对照：次日首根开盘 < U×0.998 则按开盘×(1+滑点)买。"""
    if len(o) and 0 < o[0] < u * 0.998:
        return float(o[0] * (1 + SLIP))
    return None


def exit_price(closes: list[float], prev_close: float, ratio: float, h: int, max_defer: int = 2):
    """closes[0]=T+1 收盘，closes[k]=T+1+k 收盘。第 h 天收盘卖；收盘在跌停价则顺延（最多 max_defer 次）。
    返回 (价格, 实际 k) 或 (None, None)。"""
    k = h
    while k < len(closes) and k <= h + max_defer:
        pc = closes[k - 1]
        down = round(pc * (1 - ratio), 2)
        if closes[k] > down + EPS:
            return float(closes[k]), k
        k += 1
    return None, None


def net_pct(entry: float, exitp: float) -> float:
    return (exitp / entry - 1.0) * 100.0 - COST_PCT


# ---------------- 取数 ----------------
def _to_date(df):
    import polars as pl
    if "trade_date" in df.columns and df.schema["trade_date"] == pl.String:
        df = df.with_columns(pl.col("trade_date").str.slice(0, 10).str.to_date("%Y-%m-%d"))
    return df


def load_code_days(code: str, dates: set[str]):
    """返回 {YYYYMMDD: 当日分钟条 dict(arrays)}，只含 dates 里的日子。base 优先，update 补 base 没有的日子。"""
    import polars as pl
    days: dict[str, dict] = {}

    def take(df):
        if df.height == 0:
            return
        df = df.with_columns(pl.col("trade_date").cast(pl.Date).dt.strftime("%Y%m%d").alias("d")).sort("trade_time")
        for (d,), g in df.group_by(["d"], maintain_order=True):
            if d in dates and d not in days:
                days[d] = {k: g[k].to_numpy() for k in ("open", "high", "low", "close", "vol")}
                days[d]["adj"] = g["adj_factor"].drop_nulls().to_numpy()[-1:] if "adj_factor" in g.columns else np.array([])
    bp = MIN / code / "base/base.parquet"
    if bp.exists():
        lf = pl.scan_parquet(bp).select(["open", "high", "low", "close", "vol", "adj_factor", "trade_date", "trade_time"])
        lf = lf.filter(pl.col("trade_date").cast(pl.Date).dt.strftime("%Y%m%d").is_in(list(dates)))
        take(lf.collect())
    for up in sorted(glob.glob(str(MIN / code / "*/update.parquet"))):
        u = pl.read_parquet(up)
        ren = {"date": "trade_date", "datetime": "trade_time", "volume": "vol"}
        u = u.rename({a: b for a, b in ren.items() if a in u.columns and b not in u.columns})
        if "adj_factor" not in u.columns:
            u = u.with_columns(pl.lit(None, dtype=pl.Float64).alias("adj_factor"))
        take(_to_date(u.select(["open", "high", "low", "close", "vol", "adj_factor", "trade_date", "trade_time"])))
    return days


def next_dates(all_days: list[str], t: str, n: int):
    """T 之后的 n 个本股有分钟数据的交易日（按本股自己的日历）。"""
    import bisect
    i = bisect.bisect_right(all_days, t)
    return all_days[i:i + n]


def code_trading_days(code: str) -> list[str]:
    import polars as pl
    ds: set[str] = set()
    bp = MIN / code / "base/base.parquet"
    if bp.exists():
        s = pl.scan_parquet(bp).select(pl.col("trade_date").cast(pl.Date).dt.strftime("%Y%m%d")).unique().collect()
        ds |= set(s.to_series().to_list())
    for up in glob.glob(str(MIN / code / "*/update.parquet")):
        u = pl.read_parquet(up)
        col = "trade_date" if "trade_date" in u.columns else "date"
        s = u[col]
        if s.dtype == pl.String:
            s = s.str.slice(0, 10).str.to_date("%Y-%m-%d")
        ds |= set(s.cast(pl.Date).dt.strftime("%Y%m%d").unique().to_list())
    return sorted(ds)


def run():
    import polars as pl
    OUT.mkdir(parents=True, exist_ok=True)
    ev = pl.read_parquet(EVENTS)
    codes = sorted(os.path.basename(p) for p in glob.glob(str(MIN / "*")))
    ev = ev.filter(pl.col("code").is_in(codes))
    res = []
    for ci, code in enumerate(codes):
        e = ev.filter(pl.col("code") == code)
        if e.height == 0:
            continue
        tds = code_trading_days(code)
        need: set[str] = set()
        plan = []
        for r in e.iter_rows(named=True):
            t = r["trade_date"].replace("-", "")
            nd = next_dates(tds, t, 1 + max(HOLDS) + 2)
            if len(nd) < 2:
                continue
            plan.append((r, t, nd)); need.add(t); need.update(nd)
        days = load_code_days(code, need)
        for r, t, nd in plan:
            if t not in days or nd[0] not in days:
                continue
            d1 = days[nd[0]]
            ct = float(days[t]["close"][-1])
            u = float(r["uplim"])
            ratio = snap_ratio(u, ct)
            cls, bi, px = classify_next_day(d1["open"], d1["high"], d1["low"], d1["close"], d1["vol"], u)
            cpx = ctrl_entry(d1["open"], u)
            rec = {"code": code, "T": t, "d1": nd[0], "L": int(r["L"]), "cls": r["cls"], "kind": cls, "ratio": ratio,
                   "bar_idx": bi, "u": u}
            closes = [float(days[d]["close"][-1]) for d in nd if d in days]
            adj1 = days[nd[0]]["adj"]
            for tag, entry in (("evt", px), ("ctl", cpx)):
                if entry is None:
                    continue
                for h in HOLDS:
                    # 分钟线的日历：closes 须连续存在
                    if len(closes) < h + 1:
                        rec[f"{tag}_h{h}"] = None; continue
                    # 若 T+1 收盘为 closes[0]，则 prev 为 T+1 的上一日收盘 = ct 仅对 k=0 用；顺延判定从 k>=1 开始
                    xp, k = exit_price(closes, ct, ratio, h)
                    if xp is None:
                        rec[f"{tag}_h{h}"] = "unsellable"
                    else:
                        rec[f"{tag}_h{h}"] = {"net": round(net_pct(entry, xp), 4), "ex": nd[k], "k": k, "entry": entry,
                                               "exit": xp, "adj_changed": bool(len(adj1) and len(days[nd[k]]["adj"]) and abs(adj1[0] - days[nd[k]]["adj"][0]) > 1e-9)}
            res.append(rec)
        del days; gc.collect()
        if ci % 20 == 0:
            print(f"{ci}/{len(codes)} {code} events={len(res)}", flush=True)
    (OUT / "events_minute.json").write_text(json.dumps(res, ensure_ascii=False))
    print("done", len(res))


# ---------------- 评估 ----------------
WIN = {"R12": ("20251001", "20261016"), "N3": ("20240101", "20261016"), "ALL": ("20190101", "20261016")}


def trades(res, tag, h, cond=None):
    out = []
    for r in res:
        x = r.get(f"{tag}_h{h}")
        if not isinstance(x, dict) or (cond and not cond(r)):
            continue
        out.append({"dt": r["d1"], "en": r["d1"], "ex": x["ex"], "code": r["code"], "net": x["net"], "hold": x["k"], "L": r["L"], "cls": r["cls"]})
    return out


def sel(ts, w):
    a, b = WIN[w]
    return [t for t in ts if a <= t["en"] <= b]


def mm(ts):
    if not ts:
        return None
    from s310_short_combo_compare import metrics
    return metrics(ts)


def evaluate():
    res = json.loads((OUT / "events_minute.json").read_text())
    n_all = len(res)
    def frac(f, sub=None):
        s = [r for r in res if (sub is None or sub(r))]
        return {"n": len(s), "share": round(sum(1 for r in s if f(r)) / max(len(s), 1), 4)}
    groups = {"ALL": lambda r: True, "ONE": lambda r: r["cls"] == "ONE", "TWO": lambda r: r["cls"] == "TWO",
              "LEAD3+": lambda r: r["cls"] in ("LEAD3", "LEAD4"), "LEAD4": lambda r: r["cls"] == "LEAD4"}
    comp = {}
    for g, sub in groups.items():
        s = [r for r in res if sub(r)]
        k = {}
        for kind in ("yizi", "other", "open_sealed_filled", "open_sealed_nofill", "open_sealed_noopen", "nodata"):
            k[kind] = round(sum(1 for r in s if r["kind"] == kind) / max(len(s), 1), 4)
        k["n"] = len(s)
        k["opened_any"] = round(sum(1 for r in s if r["kind"] in ("open_sealed_filled", "open_sealed_nofill")) / max(len(s), 1), 4)
        comp[g] = k
    perf = {}
    for h in HOLDS:
        for tag in ("evt", "ctl"):
            ts = trades(res, tag, h)
            perf[f"{tag}_H{h}"] = {w: mm(sel(ts, w)) for w in WIN}
    # 成交笔按类别
    by_cls = {}
    for g, sub in groups.items():
        ts = trades(res, "evt", PRIMARY, sub)
        by_cls[g] = mm(ts)
    unsell = sum(1 for r in res for tag in ("evt", "ctl") for h in HOLDS if r.get(f"{tag}_h{h}") == "unsellable")
    adjc = sum(1 for r in res for tag in ("evt", "ctl") for h in HOLDS if isinstance(r.get(f"{tag}_h{h}"), dict) and r[f"{tag}_h{h}"]["adj_changed"])
    # 判定（主指标 H2）
    e, c = perf[f"evt_H{PRIMARY}"], perf[f"ctl_H{PRIMARY}"]
    chk = {}
    for w in ("R12", "N3"):
        m = e[w]
        chk[f"{w}_n>=30"] = bool(m and m["n"] >= 30)
        chk[f"{w}_avg>0"] = bool(m and m["avg"] > 0)
    m = e["N3"]
    chk["N3_pf>=1.3"] = bool(m and m["pf"] >= 1.3)
    chk["N3_trim>0_and_median>0"] = bool(m and (m["trim_both5"] or -1) > 0 and m["median"] > 0)
    chk["beats_ctrl_N3"] = bool(m and c["N3"] and m["avg"] > c["N3"]["avg"])
    insufficient = not (chk["R12_n>=30"] and chk["N3_n>=30"])
    verdict = "样本不足，不能判断" if insufficient else ("建议补A档首板日分钟线" if all(chk.values()) else "试点未通过")
    out = {"n_events": n_all, "composition": comp, "perf": perf, "primary_by_cls": by_cls, "unsellable": unsell,
           "adj_changed_trades": adjc, "checks": chk, "verdict": verdict}
    (OUT / "eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    {"run": run, "eval": evaluate}[sys.argv[1]]()
