"""§RV-A~F 推荐口径：每天按排序取前 5（开盘涨停买不进不递补），每只持 20 日，按每只推荐平均（预注册 e77578b1）。
数据来自 rv1030_diag.py 输出的 sig_{EA,FA}.parquet。用法：python rv1030_reco.py"""
import os, json
import polars as pl
c = pl.col
D = os.path.join(os.path.dirname(__file__), "../../output/research/rv1030")
PH = {"P19": ("2019-01-01", "2019-12-31"), "P20": ("2020-01-01", "2020-12-31"), "P21": ("2021-01-01", "2021-12-31"),
      "W0": ("2022-04-01", "2023-03-31"), "W00": ("2023-04-01", "2023-12-31"), "W1": ("2024-01-01", "2024-12-31"),
      "W2": ("2025-01-01", "2025-09-30"), "R12": ("2025-10-01", "2026-10-16"), "N3": ("2024-01-01", "2026-10-16"),
      "R3m": ("2026-07-16", "2026-10-16")}
TOP = 5


def pick(x, key, desc, filt=None):
    if filt is not None: x = x.filter(filt)
    x = x.with_columns(c("code").cast(pl.Int64).alias("ci"))
    x = x.sort(["trade_date", key, "ci"], descending=[False, desc, False], nulls_last=True)
    return x.with_columns(pl.int_range(pl.len()).over("trade_date").alias("rk")).filter(c("rk") < TOP)


def stats(p):
    p = p.filter(~c("lu_e").fill_null(False))
    if p.height == 0: return {"n": 0}
    r = p["fwd"]; w = r.filter(r > 0).sum(); l = -r.filter(r < 0).sum()
    return {"n": p.height, "avg": round(100 * r.mean(), 2), "win": round(100 * (r > 0).mean(), 1), "med": round(100 * r.median(), 2),
            "pf": round(w / l, 2) if l > 0 else 99.0, "ex": round(100 * p["ex"].mean(), 2)}


def combo(x):
    n = pl.len().over("trade_date")
    return x.with_columns(((c("d0").rank().over("trade_date") / n) + ((-c("total_mv")).rank().over("trade_date") / n)).alias("cb"))


if __name__ == "__main__":
    out = {}
    for S in ("EA", "FA"):
        x = combo(pl.read_parquet(os.path.join(D, f"sig_{S}.parquet")))
        V = {f"{S}_base": ("score", True, None), f"{S}_d0": ("d0", True, None), f"{S}_mv": ("total_mv", False, None)}
        if S == "EA":
            V["EA_combo"] = ("cb", True, None); V["EA_first"] = ("score", True, c("prev_dtp") <= 50)
        for n, (k, desc, f) in V.items():
            p = pick(x, k, desc, f)
            row = {}
            for ph, (a, b) in PH.items():
                q = p.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)) & ~c("partial"))
                row[ph] = stats(q)
            row["LIVE10-16+"] = stats(p.filter(c("trade_date") >= "2026-10-16"))
            out[n] = row
            p.filter(c("trade_date") >= "2026-09-01").select("code", "trade_date", "rk", "fwd", "partial", "lu_e", "d0", "total_mv", "score").write_csv(
                os.path.join(D, f"reco_recent_{n}.csv"))
    json.dump(out, open(os.path.join(D, "reco.json"), "w"), indent=1, ensure_ascii=False)
    P8 = ["P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12"]
    for n, row in out.items():
        print(f"{n:9s} " + " ".join(f"{p} {row[p].get('avg', 0):+.2f}({row[p].get('n', 0)})" for p in P8 + ["N3", "R3m", "LIVE10-16+"]))
    print()
    for S in ("EA", "FA"):
        b = out[f"{S}_base"]
        for n, row in out.items():
            if not n.startswith(S) or n.endswith("base"): continue
            ge = [row[p].get("avg", -99) >= b[p].get("avg", -99) for p in P8]
            ok = row["R12"]["avg"] >= b["R12"]["avg"] and row["N3"]["avg"] >= b["N3"]["avg"] and sum(ge) >= 6 and row["W0"]["avg"] >= b["W0"]["avg"]
            print(f"C {n}: ≥原版段 {sum(ge)}/8 R12 {row['R12']['avg']:+.2f} vs {b['R12']['avg']:+.2f} N3 {row['N3']['avg']:+.2f} vs {b['N3']['avg']:+.2f} "
                  f"W0 {row['W0']['avg']:+.2f} vs {b['W0']['avg']:+.2f} 胜率N3 {row['N3']['win']} vs {b['N3']['win']} → {'C过' if ok else 'C不过'}")
