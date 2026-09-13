"""回填 daily_kbars 的特质风险列（ivol20 / ivol_dn20；L-093 / L-109）。

口径与 app.services.risk_features.compute_ivol20 / compute_ivol_dn20 完全一致。按月分块、
逐块提交，每块向前多取 120 自然日做滚动 warmup（warmup 行只用于计算、不写回）。

用法：
  python backfill_ivol20.py --start 2024-06-01                      # 回填 ivol20（默认）
  python backfill_ivol20.py --start 2024-06-01 --metric ivol_dn20   # 回填 ivol_dn20
  python backfill_ivol20.py --start 2024-06-01 --missing            # 只补 NULL（日常增量）
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
import pandas as pd
from sqlalchemy import text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402
from app.services.risk_features import (  # noqa: E402
    build_ivol_panel, build_ivol_dn20_panel, BENCHMARK_TS_CODE,
)

# metric -> (目标列, 面板构造函数)
_METRICS = {
    "ivol20": ("ivol20", build_ivol_panel),
    "ivol_dn20": ("ivol_dn20", build_ivol_dn20_panel),
}


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


def fill_chunk(s, c0: str, c1: str, only_missing: bool, metric: str = "ivol20") -> int:
    col, builder = _METRICS[metric]
    if only_missing:
        n_missing = s.execute(text(
            f"SELECT COUNT(*) FROM daily_kbars WHERE trade_date BETWEEN :a AND :b AND {col} IS NULL"),
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
    panel = builder(kbars, idx)
    panel = panel[(panel.trade_date >= c0) & (panel.trade_date <= c1)].dropna(subset=[col])
    if only_missing:
        panel = panel[panel[col].notna()]
    if panel.empty:
        return 0
    panel[col] = panel[col].astype(float).round(6)
    conn = s.connection()
    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _ivol_tmp"))
    conn.execute(text(f"CREATE TEMPORARY TABLE _ivol_tmp (code VARCHAR(6), trade_date VARCHAR(10), {col} DOUBLE, "
                      "PRIMARY KEY(code, trade_date))"))
    panel.to_sql("_ivol_tmp", con=conn, if_exists="append", index=False, chunksize=20000)
    res = conn.execute(text(
        f"UPDATE daily_kbars k JOIN _ivol_tmp t ON k.code=t.code AND k.trade_date=t.trade_date "
        f"SET k.{col}=t.{col}"))
    conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _ivol_tmp"))
    s.commit()
    return int(res.rowcount or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--end", default="2099-01-01")
    ap.add_argument("--metric", default="ivol20", choices=sorted(_METRICS))
    ap.add_argument("--missing", action="store_true")
    args = ap.parse_args()
    col = _METRICS[args.metric][0]
    with SessionLocal() as s:
        last_dt = s.execute(text("SELECT MAX(trade_date) FROM daily_kbars")).scalar()
    end = min(pd.Timestamp(args.end), pd.Timestamp(last_dt)).strftime("%Y-%m-%d")
    chunks = month_starts(args.start, end)
    print(f"{len(chunks)} monthly chunks {args.start}..{end} metric={args.metric} "
          f"missing_only={args.missing}", flush=True)
    total = 0
    with SessionLocal() as s:
        for i, (c0, c1) in enumerate(chunks, 1):
            n = fill_chunk(s, c0, c1, args.missing, args.metric)
            total += n
            print(f"[{i}/{len(chunks)}] {c0}..{c1} {col} updated={n:,} cumulative={total:,}", flush=True)
    print(f"DONE metric={args.metric} total updated={total:,}")


if __name__ == "__main__":
    main()
