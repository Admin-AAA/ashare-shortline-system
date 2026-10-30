"""[中长线] §349 现役彩票型策略的时间止损（TS）与分散（DIV）— 预注册（docs/strategy-research.md §349，结果出来前写定，不调参）。
STRAT=FA|BT|SDY|NCS 选择策略；同进程跑 原版 base / TS / DIV。
  TS：持有第 h/2 日收盘（i-e == h//2-1）若 ac <= 买入开盘价 ao[e] -> 次日开盘卖，否则持满 h。
  DIV：K=40、max_new×2，其余同原版。
用法：STRAT=FA LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research135.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
STRAT = os.environ["STRAT"]
# name: (prep module, sig, score, hold, max_new)
CFG = {"FA": ("lt_research20", "FA", "sc_a", 20, 5), "BT": ("lt_research25", "BTP", "sc_bt", 20, 5),
       "SDY": ("lt_research118", "SDY_W", "sc_dy", 20, 10), "NCS": ("lt_research98", "NCS", "sc_ncs", 60, 5)}


def ts_exit(h):
    half = h // 2 - 1
    return lambda a, e, i: (i - e >= h - 1) or (i - e == half and a["ac"][i] <= a["ao"][e])


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    mod, sig, sc, h, mn = CFG[STRAT]
    df = __import__(mod).prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    runs = ((f"{STRAT}_base", fixed(h), 20, mn), (f"{STRAT}_TS", ts_exit(h), 20, mn), (f"{STRAT}_DIV", fixed(h), 40, 2 * mn))
    for ph in phases:
        s, e = PH[ph]
        for n, fn, k, m_ in runs:
            t, cur = run_real(df, sig, sc, fn, s, e, k_slots=k, max_new=m_, max_hold=h)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                m["per_day"] = round(m["avg"] / max(m["days"], 1e-9), 3)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
