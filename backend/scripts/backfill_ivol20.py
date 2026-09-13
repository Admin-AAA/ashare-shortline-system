"""回填 daily_kbars.ivol20（个股20日特质波动，对中证500残差；L-093）。

口径与 app.services.risk_features.compute_ivol20 完全一致。按月分块、逐块提交，
每块向前多取 90 自然日做滚动 warmup（warmup 行只用于计算、不写回）。

用法：
  python backfill_ivol20.py --start 2024-06-01                 # 回填该日起（已填的会重算覆盖）
  python backfill_ivol20.py --start 2024-06-01 --missing       # 只补 NULL（日常增量，按月跳过已满月）
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
import pandas as pd
from sqlalchemy import text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402
from app.services.risk_features import build_ivol_panel, BENCHMARK_TS_CODE  # noqa: E402


def month_starts(start: str, end: str) -> list[tuple[str, str]]:
    s = pd.Timestamp(start); e = pd.Timestamp(end)
    out = []
    cur = pd.Timestamp(s.year, s.month, 1)
    while cur <= e:
        nxt = (cur + pd.offsets.MonthBegin(1))
        c0 = max(cur, s).strftime("%Y-%m-%d")
        c1 = min(nxt - pd.Timedelta(days=1), e).strftime("%Y-%m-%d")
        out.append((c0, c1))
        cur = nxt
    return out


def fill_chunk(s, c0: str, c1: str, only_missing: bool) -> int:
    if only_missing:
        n_missing = s.execute(text(
            "SELECT COUNT(*) FROM daily_kbars WHERE trade_date BETWEEN :a AND :b AND ivol20 IS NULL"),
            {"a": c0, "b": c1}).scalar()
        if not n_missing:
            return 0
    warm = (pd.Timestamp(c0) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    kbars = pd.DataFrame(s.execute(text(
        """SELECT code, trade_date, pct_chg FROM daily_kbars
           WHERE trade_date >= :w AND trade_date <= :b
             AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'
           ORDER BY code, trade_date"""), {"w": warm, "b": c1}).mappings().all())
    if kbars.empty:
        return 0
    idx = pd.DataFrame(s.execute(text(
        "SELECT trade_date, pct_chg FROM index_daily WHERE ts_code=:c AND trade_date>=:w AND trade_date<=:b "
        "ORDER BY trade_date"), {"c": BENCHMARK_TS_CODE, "w": warm, "b": c1}).mappings().all())
    panel = build_ivol_panel(kbars, idx)
    panel = panel[(panel.trade_date >= c0) & (panel.trade_date <= c1)].dropna(subset=["ivol20"])
    if only_missing:
        panel = panel[panel.ivol20.notna()]
    if panel.empty:
        return 0
    panel["ivol20"] = panel.ivol20.astype(float).round(6)
    conn = s.connection()
    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _ivol_tmp"))
    conn.execute(text("CREATE TEMPORARY TABLE _ivol_tmp (code VARCHAR(6), trade_date VARCHAR(10), ivol20 DOUBLE, "
                      "PRIMARY KEY(code, trade_date))"))
    panel.to_sql("_ivol_tmp", con=conn, if_exists="append", index=False, chunksize=20000)
    res = conn.execute(text(
        "UPDATE daily_kbars k JOIN _ivol_tmp t ON k.code=t.code AND k.trade_date=t.trade_date "
        "SET k.ivol20=t.ivol20"))
    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _ivol_tmp"))
    s.commit()
    return int(res.rowcount or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--end", default="2099-01-01")
    ap.add_argument("--missing", action="store_true")
    args = ap.parse_args()
    with SessionLocal() as s:
        last_dt = s.execute(text("SELECT MAX(trade_date) FROM daily_kbars")).scalar()
    end = min(pd.Timestamp(args.end), pd.Timestamp(last_dt)).strftime("%Y-%m-%d")
    chunks = month_starts(args.start, end)
    print(f"{len(chunks)} monthly chunks {args.start}..{end} missing_only={args.missing}", flush=True)
    total = 0
    with SessionLocal() as s:
        for i, (c0, c1) in enumerate(chunks, 1):
            n = fill_chunk(s, c0, c1, args.missing)
            total += n
            print(f"[{i}/{len(chunks)}] {c0}..{c1} updated={n:,} cumulative={total:,}", flush=True)
    print(f"DONE total updated={total:,}")


if __name__ == "__main__":
    main()
