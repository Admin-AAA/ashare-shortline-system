"""用已有重放 staging 按修正后口径（卖出日 ≤ cutoff）重算近12个月滚动体检，写对照文件，不重跑重放。

只读：staging 选股文件 + 库里日K（_load_batch 结算）。不改 rolling_r12_latest.json。
用法：.venv/bin/python scripts/recount_rolling_r12_cutoff.py [--month 2026-11] [--out PATH]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import midterm_service as ms  # noqa: E402
from app.services import rolling_r12 as rr  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-11")
    ap.add_argument("--root", default=str(rr.OUT_ROOT))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = Path(a.root)
    old = json.loads((root / "rolling_r12_latest.json").read_text(encoding="utf-8"))
    man = json.loads((rr.month_dir(root, a.month) / "manifest.json").read_text(encoding="utf-8"))
    assert old["cutoff"] == man["cutoff"], (old["cutoff"], man["cutoff"])
    cutoff, ws = man["cutoff"], man["window_start"]
    live = set(ms.STRATEGIES)
    # 结果文件里已退役的策略也一起算，方便对照；只在本进程内临时并入
    merged = {**{s: ms.RETIRED_STRATEGIES[s] for s in old["midlong"] if s not in live and s in ms.RETIRED_STRATEGIES},
              **ms.STRATEGIES}
    orig = ms.STRATEGIES
    ms.STRATEGIES = merged
    try:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            new = rr.midlong_window_stats(db, rr.month_dir(root, a.month) / "picks", ws, cutoff)
            # 参考：同期指数涨跌（截止日收盘 → 库里最新收盘），帮助判断「最近已卖出」偏弱是不是全市场一起跌
            from sqlalchemy import text

            idx = {}
            for code in ("000300.SH", "000905.SH", "000852.SH"):
                r = db.execute(text(
                    "SELECT (SELECT close FROM index_daily WHERE ts_code=:c AND trade_date<=:d ORDER BY trade_date DESC LIMIT 1),"
                    " (SELECT close FROM index_daily WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 1),"
                    " (SELECT MAX(trade_date) FROM index_daily WHERE ts_code=:c)"), {"c": code, "d": cutoff}).one()
                if r[0] and r[1]:
                    idx[code] = {"to": str(r[2])[:10], "pct": round((float(r[1]) / float(r[0]) - 1) * 100, 2)}
        finally:
            db.close()
    finally:
        ms.STRATEGIES = orig

    rows = {}
    for sid, o in old["midlong"].items():
        n = new.get(sid) or {}
        late = n.get("sold_after_cutoff") or {"n": 0, "avg": None}
        change = ("由正变负" if o["verdict"] == "positive" and n.get("verdict") == "negative"
                  else "由负变正" if o["verdict"] == "negative" and n.get("verdict") == "positive"
                  else "不变" if o["verdict"] == n.get("verdict") else f"{o['verdict']}→{n.get('verdict')}")
        rows[sid] = {
            "name": n.get("name"), "hold_days": n.get("hold_days"), "active_now": sid in live,
            "before": {k: o.get(k) for k in ("n", "avg", "open", "verdict")},
            "after": {k: n.get(k) for k in ("n", "avg", "median", "win_rate", "pf", "trim5_avg", "open", "verdict")},
            "recent_sold": late, "verdict_change": change,
        }
    judged = [r["after"]["verdict"] for s, r in rows.items() if r["active_now"]
              and r["after"]["verdict"] in ("positive", "negative")]
    short = {s: {k: v.get(k) for k in ("n", "avg", "verdict")} for s, v in old["short"]["strategies"].items()}
    doc = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": "rolling_r12_latest.json（generated_at %s）+ staging %s/picks，按今日日K结算" % (old["generated_at"], a.month),
        "cutoff": cutoff, "window": {"signal_start": ws, "signal_end": cutoff},
        "method": ("修后：只算信号日在窗口内、且卖出日 ≤ 截止日的笔（扣 0.3%）；截止日后才卖出的算持有中（计入 open），"
                   "其收益列在 recent_sold「最近已卖出」只作预警，不进正负判定。修前取自原结果文件。"
                   "短线段口径不变，原样照抄。"),
        "midlong": rows,
        "short_unchanged": short,
        "index_since_cutoff": idx,
        "active_midlong_positive_ratio_after": round(judged.count("positive") / len(judged), 3) if judged else None,
        "flips": {s: r["verdict_change"] for s, r in rows.items() if r["verdict_change"] in ("由正变负", "由负变正")},
    }
    out = Path(a.out) if a.out else root / f"rolling_r12_recount_{_dt.date.today():%Y%m%d}.json"
    if out.resolve() == (root / "rolling_r12_latest.json").resolve():
        raise SystemExit("不覆盖 rolling_r12_latest.json")
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    for sid, r in rows.items():
        b, f, l = r["before"], r["after"], r["recent_sold"]
        print(f"{'*' if r['active_now'] else ' '} {sid:42s} {b['n']:>4} {str(b['avg']):>7} {b['verdict']:>19} | "
              f"{f['n']:>4} {str(f['avg']):>7} {f['verdict']:>19} | 最近卖出 {l['n']:>3} {str(l['avg']):>7} | {r['verdict_change']}")


if __name__ == "__main__":
    main()
