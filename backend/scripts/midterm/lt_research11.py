"""[中线] §90 mid-small-cap-weekly + 行业 60 日反转过滤 — 预注册单一候选 + 1 个随机对照（不调参）。
动机：§83 发现 A 股行业在 1–3 个月尺度偏反转（60 日行业反转龙头 6/8 优于随机行业）；检验把它作为
已上线 mid-small-cap-weekly 的行业过滤，能否提高每笔收益。
live      : 现行规则（每周五、小盘等权指数>MA20、合格池折算市值最小 10 只，持 20 日，K20/max_new10）
ind_rev   : 候选。剔除"行业 60 日收益中位数排名前 1/3（近期热门）"行业的股票后，取最小 10 只
ctrl_rand : 对照（非候选）。剔除按 hash(行业,周) 伪随机 1/3 行业后取最小 10 只（区分"换票本身"的效应）
行业：data/industry_cache.json（tushare industry 静态映射，已知局限）；行业中位数用 U 池成员（成交额20日>3000万、
上市>250日、非ST、非北交所），行业须 >=8 只成员才排名；未排名行业的股票不剔除。
采纳标准（预先写定）：每笔均值优于 live ≥6/8 阶段，R12 与 W00 不低于 live，且优于 ctrl_rand ≥5/8 阶段。"""
import json
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
c = pl.col
ind = json.load(open(__file__.rsplit("/scripts/", 1)[0] + "/data/industry_cache.json"))
indf = pl.DataFrame({"code": list(ind), "ind": [str(v) if v else None for v in ind.values()]}).filter(c("ind").is_not_null())
df = build()
px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
df = df.join(px, on=["code", "bd"], how="left").with_columns(
    (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
df = df.join(indf, on="code", how="left")
g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
r = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4"))
idx = (r.filter(c("r4") <= 400).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
       .with_columns((1 + c("s") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(20).alias("scid20"))
       .select("trade_date", "scid", "scid20"))
df = df.join(idx, on="trade_date", how="left")
wk = (c("trade_date").str.to_date().dt.weekday() == 5)
U = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
     & c("mvd").is_not_null()).fill_null(False)
df = df.with_columns(U.alias("U"), wk.fill_null(False).alias("wk"))
gi = (df.filter(c("wk") & c("U") & c("ind").is_not_null()).group_by("trade_date", "ind")
      .agg(pl.len().alias("n"), c("r60").clip(-0.5, 1.5).median().alias("m60i")).filter(c("n") >= 8)
      .with_columns(pl.concat_str([c("ind"), c("trade_date")]).hash(13).cast(pl.Float64).alias("hr")))
gi = gi.with_columns(c("m60i").rank("ordinal", descending=True).over("trade_date").alias("rk_hot"),
                     c("hr").rank("ordinal").over("trade_date").alias("rk_rand"), pl.len().over("trade_date").alias("ni"))
gi = gi.with_columns((c("rk_hot") <= c("ni") / 3).alias("hot"), (c("rk_rand") <= c("ni") / 3).alias("rnd"))
df = df.join(gi.select("trade_date", "ind", "hot", "rnd"), on=["trade_date", "ind"], how="left")
uni = (c("U") & (c("pe_ttm") > 0))
on = c("scid") > c("scid20")
def rk(extra):
    return pl.when((uni & extra).fill_null(False)).then(c("mvd")).rank("ordinal").over("trade_date")
df = df.with_columns(rk(pl.lit(True)).alias("rk_live"), rk(~c("hot").fill_null(False)).alias("rk_ir"),
                     rk(~c("rnd").fill_null(False)).alias("rk_rd"), (-c("mvd")).alias("sc_small"))
df = df.with_columns(*[(c("wk") & on & (c(k) <= 10)).fill_null(False).alias(s)
                       for k, s in (("rk_live", "S_live"), ("rk_ir", "S_ir"), ("rk_rd", "S_rd"))]).sort("code", "trade_date")
ov = df.filter(c("S_live") | c("S_ir")).group_by("trade_date").agg(c("S_live").sum(), (c("S_live") & c("S_ir")).sum().alias("both"))
print("weeks", ov.height, "overlap live∩ir per week", ov["both"].mean(), "of", ov["S_live"].mean(), flush=True)
V = {"live": "S_live", "ind_rev": "S_ir", "ctrl_rand": "S_rd"}
res = {}
for n, s_ in V.items():
    print("##", n, flush=True)
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, s_, "sc_small", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        mm = metrics_real(t, cur); res.setdefault(n, {})[ph] = {a: float(v) for a, v in mm.items()}
        print(f"  {ph:22s} " + " ".join(f"{a}={float(v):g}" for a, v in mm.items()), flush=True)
json.dump(res, open("/tmp/lt11.json", "w"), indent=1)
print("all done", flush=True)
