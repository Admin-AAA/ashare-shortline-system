"""§332/§333 附加输出（预注册写定）：
  1) 每持有日收益（每笔 ÷ 平均持有交易日），与现役 4 短线（§282 BASE 逐笔 output/research/s282/short_trades_base.json，12m=R12、EXT+12m=N3）对比；
  2) §333 附加判定：若 FDV_h5 的 N3 成交与 F_any_h20 同票同入场日重叠 ≥50%，需 R12 与 N3 每持有日都高于 F_any_h20，否则视为重复；
  3) 与现役 4 短线的相关性（只展示）：按入场月份汇总每笔收益之和，W0/W00/W2(≈EXT)/R12(≈12m) 重叠月份的皮尔逊相关；同日同票重叠率。
用法：python s332_eval.py <res.json>"""
import json, sys, os, statistics as st
from collections import defaultdict
import polars as pl
res = json.load(open(sys.argv[1])); d = os.path.dirname(sys.argv[1])
B = os.path.join(os.path.dirname(__file__), "../../output/research/s282/short_trades_base.json")
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
cal = sorted(pl.read_parquet(os.path.join(os.path.dirname(__file__), "../../data/midterm/index_daily.parquet"))["trade_date"].cast(pl.Utf8).unique().to_list())
cal = [x if "-" in x else f"{x[:4]}-{x[4:6]}-{x[6:8]}" for x in cal]
pos = {x: i for i, x in enumerate(cal)}


def dash(x): return x if "-" in x else f"{x[:4]}-{x[4:6]}-{x[6:8]}"


base = json.load(open(B))
print("现役 4 短线（§282 BASE 一次抽样）每笔 / 平均持有交易日 / 每持有日：")
for sid in sorted({r["sid"] for r in base["12m"]}):
    for tag, ws in (("R12", ("12m",)), ("N3", ("12m", "EXT"))):
        rows = [r for w in ws for r in base[w] if r["sid"] == sid]
        hd = [pos.get(dash(r["ex"]), 0) - pos.get(dash(r["en"]), 0) for r in rows]
        a = st.fmean(r["net"] for r in rows); h = st.fmean(hd)
        print(f"  {sid[:24]:24s} {tag}: n={len(rows)} 每笔 {a:+.2f} 持有 {h:.1f} 天 每持有日 {a / h:+.3f}")
for n in ("LVZ_h5", "FDV_h5", "F_any_h20"):
    v = res.get(n, {})
    print(f"{n}: 每持有日 R12 {v.get(R12, {}).get('per_day')} N3 {v.get(N3, {}).get('per_day')} | 持有 R12 {v.get(R12, {}).get('days')} N3 {v.get(N3, {}).get('days')}")
ov = res.get("FDV_h5", {}).get(N3, {}).get("ovl_F_any_h20")
dup = ov is not None and ov >= 0.5
better = all((res["FDV_h5"].get(p, {}).get("per_day") or -99) > (res["F_any_h20"].get(p, {}).get("per_day") or 99) for p in (R12, N3))
print(f"§333 附加：N3 与 F_any_h20 重叠 {ov}；{'≥50% 需每持有日更高 → ' + ('✓' if better else '✗') if dup else '<50% 不触发 ✓'}")
bm = defaultdict(float); bk = set()
for w in ("W0", "W00", "EXT", "12m"):
    for r in base[w]:
        bm[dash(r["en"])[:7]] += r["net"]; bk.add((dash(r["dt"]), r["code"]))
for n in ("LVZ_h5", "FDV_h5"):
    m = defaultdict(float); tot = hit = 0
    for tag in ("W0", "W00", "W2", "R12"):
        f = os.path.join(d, f"trades_{n}_{tag}.csv")
        if not os.path.exists(f): continue
        t = pl.read_csv(f, schema_overrides={"code": pl.Utf8, "entry": pl.Utf8})
        for code, en, ret in t.select("code", "entry", "ret").iter_rows():
            m[en[:7]] += 100 * ret; tot += 1
            i = pos.get(en)
            if i and (cal[i - 1], code) in bk: hit += 1
    ks = sorted(set(m) & set(bm))
    corr = None
    if len(ks) > 3:
        x = [m[k] for k in ks]; y = [bm[k] for k in ks]
        mx, my = st.fmean(x), st.fmean(y)
        sx = sum((a - mx) ** 2 for a in x) ** .5; sy = sum((b - my) ** 2 for b in y) ** .5
        corr = round(sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy), 2) if sx and sy else None
    print(f"{n}: 与现役 4 短线月度收益相关 {corr}（{len(ks)} 个月）；同日同票重叠 {hit}/{tot}")
