"""[§1065–§1068 评估] §282 中线判定（预注册写死）+ 与现役策略近 12 个月同日同票重叠（L-221 a）+ 收益来源 + 互相重叠。
判定（全部满足才采纳）：1) R12、N3 扣成本每笔 >0；2) R12、N3 PF≥1.3；3) R12、N3 最大回撤不深于 −25%；
  4) 8 段（P19/P20/P21/W0/W00/W1/W2/R12）每笔 >0 的 ≥6 段（n=0 记不为正），且 W0、W00 每笔 ≥−5%（n=0 记失败）；
  5) R12、N3 各 ≥30 笔；6) R12 成交与现役全部策略近 12 个月出票同日同票重叠 <20%。
现役出票（只读）：中长线 output/strategy_health/rolling_r12/<最新月>/picks/*.json（rolling_r12_latest.json midlong 里的 id）；
  短线 MySQL recommendations ⨝ recommendation_runs（completed）的 skill_breakdown_json，只 SELECT。
用法：cd <主工作树>/backend && PYTHONPATH=.:<worktree>/backend/scripts/midterm .venv/bin/python <本文件> <结果目录>"""
from __future__ import annotations

import glob
import json
import os
import sys

import polars as pl

c = pl.col
B = "/Users/wangao/Desktop/ashare-shortline-system/backend"
D = sys.argv[1]
NAMES = sys.argv[2:] or ["ARU_h20", "BGS_h20", "PLR_h20", "ACI_h20"]
P8 = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12", "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10"]
R12, N3 = "R12 2025-10~2026-10", "N3 2024-01~2026-10"
R12_S, R12_E = "2025-10-01", "2026-10-16"
MDD_CAP = 25.0


def trades(name: str, tag: str) -> pl.DataFrame | None:
    f = os.path.join(D, f"trades_{name}_{tag}.csv")
    return pl.read_csv(f, schema_overrides={"code": pl.Utf8}) if os.path.exists(f) else None


def quality(name: str, tag: str) -> dict:
    t = trades(name, tag)
    if t is None:
        return {}
    r = t["ret"].sort(descending=True).to_list()
    n = len(r)
    k = max(1, round(0.05 * n))
    tot = sum(r)
    tb5 = 100 * sum(r[k:n - k]) / (n - 2 * k) if n > 2 * k else None
    sr = sorted(r)
    med = 100 * sr[n // 2] if n % 2 else 100 * (sr[n // 2 - 1] + sr[n // 2]) / 2
    return {"n": n, "trim_both5": None if tb5 is None else round(tb5, 2), "trim_top5": round(100 * sum(r[k:]) / max(1, n - k), 2),
            "median": round(med, 2), "top10_share": round(100 * sum(r[:10]) / tot, 1) if tot > 0 else None,
            "label": "稳健型" if (tb5 is not None and tb5 > 0 and med > 0) else "彩票型"}


def live_mid() -> pl.DataFrame:
    latest = json.load(open(os.path.join(B, "output/strategy_health/rolling_r12_latest.json")))
    sids = set(latest.get("midlong", {}))
    month = sorted(os.listdir(os.path.join(B, "output/strategy_health/rolling_r12")))[-1]
    rows = []
    for f in glob.glob(os.path.join(B, "output/strategy_health/rolling_r12", month, "picks", "*.json")):
        b = json.load(open(f))
        if b.get("strategy_id") not in sids:
            continue
        for p in b.get("picks", []):
            rows.append({"sid": b["strategy_id"], "code": str(p.get("code", "")).zfill(6), "sig": b["signal_date"]})
    return pl.DataFrame(rows, schema={"sid": pl.Utf8, "code": pl.Utf8, "sig": pl.Utf8}, orient="row")


def live_short() -> pl.DataFrame:
    from sqlalchemy import text

    from app.db.session import SessionLocal

    latest = json.load(open(os.path.join(B, "output/strategy_health/rolling_r12_latest.json")))
    sids = set(latest.get("short", {}).get("strategies", {}))
    db = SessionLocal()
    try:
        rs = db.execute(text(
            "SELECT DISTINCT jt.strategy_id, rec.code, rec.trade_date FROM recommendations rec "
            "JOIN recommendation_runs u ON rec.run_id = u.run_id "
            "JOIN JSON_TABLE(rec.skill_breakdown_json, '$[*]' COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')) jt "
            "WHERE u.status = 'completed' AND rec.trade_date BETWEEN :s AND :e"), {"s": R12_S, "e": R12_E}).fetchall()
    finally:
        db.close()
    rows = [{"sid": str(r[0]), "code": str(r[1]).zfill(6), "sig": str(r[2])[:10]} for r in rs if str(r[0]) in sids]
    return pl.DataFrame(rows, schema={"sid": pl.Utf8, "code": pl.Utf8, "sig": pl.Utf8}, orient="row")


def g(v: dict, p: str, k: str, dflt=-99.0):
    x = v.get(p, {}).get(k)
    return dflt if x is None else x


if __name__ == "__main__":
    allres = json.load(open(os.path.join(D, "res.json")))
    cal = sorted(pl.scan_parquet(os.path.join(B, "data/cache/daily_kbar.parquet")).select(c("trade_date").cast(pl.Utf8)).unique().collect()["trade_date"].to_list())
    prev = {cal[i]: cal[i - 1] for i in range(1, len(cal))}
    live = pl.concat([live_mid(), live_short()]).unique()
    print("live rows", live.height, live.group_by("sid").len().sort("len", descending=True).head(5).to_dicts())
    summary = {"live_rows": live.height}
    keysN3 = {}
    for name in NAMES:
        v = allres.get(name, {})
        a8 = [g(v, p, "avg") if g(v, p, "n", 0) > 0 else None for p in P8]
        w0, w00 = a8[3], a8[4]
        t = trades(name, "R12")
        ov = None
        if t is not None:
            tt = t.with_columns(c("entry").replace_strict(prev, default="").alias("sig")).select("code", "sig").unique()
            j = tt.join(live, on=["code", "sig"], how="inner")
            ov = {"R12_trades": tt.height, "hit": j.select("code", "sig").unique().height,
                  "pct": round(100 * j.select("code", "sig").unique().height / max(1, tt.height), 1),
                  "by_sid": {k: int(x) for k, x in j.group_by("sid").len().iter_rows()}}
        tn = trades(name, "N3")
        if tn is not None:
            keysN3[name] = set(zip(tn["code"].to_list(), tn["entry"].to_list()))
        ok = [g(v, R12, "avg") > 0 and g(v, N3, "avg") > 0,
              g(v, R12, "pf", 0) >= 1.3 and g(v, N3, "pf", 0) >= 1.3,
              g(v, R12, "mdd") >= -MDD_CAP and g(v, N3, "mdd") >= -MDD_CAP,
              sum((x or 0) > 0 for x in a8) >= 6 and w0 is not None and w00 is not None and w0 >= -5 and w00 >= -5,
              g(v, R12, "n", 0) >= 30 and g(v, N3, "n", 0) >= 30,
              ov is not None and ov["pct"] < 20]
        qr, qn = quality(name, "R12"), quality(name, "N3")
        lab = "稳健型" if qr.get("label") == "稳健型" and qn.get("label") == "稳健型" else "彩票型"
        perday = {tag: (round(g(v, p, "avg") / g(v, p, "days"), 3) if g(v, p, "days", 0) > 0 else None) for tag, p in (("R12", R12), ("N3", N3))}
        print(f"{name}: 8段 " + " ".join(f"{p.split()[0]} {'无' if x is None else f'{x:+.2f}'}" for p, x in zip(P8, a8))
              + f" | 正 {sum((x or 0) > 0 for x in a8)}/8")
        print("  n " + " ".join(f"{p.split()[0]} {int(g(v, p, 'n', 0))}" for p in P8 + [N3]))
        for tag, p, qq in (("R12", R12, qr), ("N3", N3, qn)):
            m = v.get(p, {})
            print(f"  {tag}: n={m.get('n')} win={m.get('win')} avg={m.get('avg')} pf={m.get('pf')} mdd={m.get('mdd')} days={m.get('days')} "
                  f"每持有日={perday[tag]} bench={m.get('bench')} ex={m.get('excess')} p05={m.get('p05')} | {qq}")
        print(f"  重叠 {ov}")
        print(f"  判定 {''.join('✓' if x else '✗' for x in ok)} → {'达标/采纳' if all(ok) else '否决'} | 标签 {lab}")
        summary[name] = {"ok": ok, "pass": all(ok), "label": lab, "perday": perday, "q_R12": qr, "q_N3": qn, "overlap": ov,
                         "avg8": dict(zip([p.split()[0] for p in P8], a8)),
                         "R12": v.get(R12), "N3": v.get(N3)}
    pair = {}
    ks = list(keysN3)
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            inter = len(keysN3[a] & keysN3[b])
            pair[f"{a}~{b}"] = {"inter": inter, "pct_of_min": round(100 * inter / max(1, min(len(keysN3[a]), len(keysN3[b]))), 1)}
    summary["pairwise_N3"] = pair
    print("pairwise N3", pair)
    json.dump(summary, open(os.path.join(D, "eval_summary.json"), "w"), indent=1, ensure_ascii=False)
