"""补充对照（预注册之外，只展示）：推荐口径下随机排序 30 次的分布，看 EA_combo 的提升是否只是「打散同分」。"""
import os, numpy as np, polars as pl
from rv1030_reco import pick, stats, PH, D
c = pl.col
for S in ("EA", "FA"):
    x = pl.read_parquet(os.path.join(D, f"sig_{S}.parquet"))
    acc = {p: [] for p in PH}
    for seed in range(30):
        y = x.with_columns(pl.Series("rnd", np.random.default_rng(seed).random(x.height)))
        p = pick(y, "rnd", True)
        for ph, (a, b) in PH.items():
            acc[ph].append(stats(p.filter(c("trade_date").is_between(pl.lit(a), pl.lit(b)) & ~c("partial")))["avg"])
    print(S, "随机排序 30 次 均值[5%,95%]: " + " ".join(f"{ph} {np.mean(v):+.2f}[{np.percentile(v,5):+.2f},{np.percentile(v,95):+.2f}]" for ph, v in acc.items()))
