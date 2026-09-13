"""按指定代码集合回填 5min 分钟K（复用 backfill_minute_5m_baostock 的取数/校验/幂等）。

用于只回填某个研究窗口实际候选股（如第三窗 run3 全候选 774 只），避免全市场 --resume 扫描。
代码清单来源：--pairs-pkl（DataFrame 需含 code 列）；窗口 --start/--end。
按股幂等：该股在窗口内每个预期交易日都已有 48 根标准 5min 则跳过，可随时中断续跑。
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
import pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.db.session import SessionLocal
from scripts.backfill_minute_5m_baostock import (
    _fetch_rows, _validated_rows, _expected_trade_dates, _upsert_rows,
    _completed_codes_for_date, _A_SHARE_CODE_RE)
import logging
logging.basicConfig(level=logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs-pkl", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--freq", default="5min")
    args = ap.parse_args()
    pairs = pd.read_pickle(args.pairs_pkl)
    codes = sorted(str(c) for c in pairs.code.unique() if _A_SHARE_CODE_RE.fullmatch(str(c)))
    print(f"target codes={len(codes)} window={args.start}~{args.end}", flush=True)
    db = SessionLocal(); t0 = time.time(); stored = 0; failed = []; skipped = 0
    try:
        # 预取每日已完整代码集合，用于按股快速判断是否跳过
        all_dates = sorted({str(x[0]) for x in db.execute(
            __import__("sqlalchemy").text(
                "SELECT DISTINCT trade_date FROM daily_kbars WHERE trade_date BETWEEN :a AND :b ORDER BY 1"),
            {"a": args.start, "b": args.end}).all()})
        complete_by_day = {d: _completed_codes_for_date(db, args.freq, d) for d in all_dates}
        for i, code in enumerate(codes, 1):
            exp = _expected_trade_dates(db, code, args.start, args.end)
            if exp and all(code in complete_by_day.get(d, ()) for d in exp):
                skipped += 1; continue
            try:
                rows = _fetch_rows(code, args.freq, args.start, args.end)
                rows = _validated_rows(rows, code=code, freq=args.freq, expected_dates=exp)
                n = _upsert_rows(db, rows); db.commit(); stored += n
            except Exception as exc:  # noqa: BLE001
                db.rollback(); failed.append(code); print(f"{code} FAIL {str(exc)[:120]}", flush=True)
            if i % 25 == 0 or i == len(codes):
                el = time.time() - t0; rate = i / el; eta = (len(codes) - i) / rate
                print(f"{i}/{len(codes)} stored={stored} skip={skipped} fail={len(failed)} "
                      f"{el:.0f}s ETA{eta/60:.1f}m", flush=True)
        print(f"DONE codes={len(codes)} stored={stored} skip={skipped} fail={len(failed)} "
              f"failed_sample={failed[:15]} time={(time.time()-t0)/60:.1f}m", flush=True)
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
