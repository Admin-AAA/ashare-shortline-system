"""teajoin 第二轮探测：重试第一轮 capacity busy/超时的接口、字段完整度、与本地库对账、延迟与稳定性。严格限速，密钥只从 Settings 读。

  PYTHONPATH=. .venv/bin/python scripts/probe_teajoin_deep.py [--out ../docs/teajoin_probe_deep.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from scripts.probe_teajoin import call


def rows_of(res: dict):
    j = res.get("json") or {}
    d = j.get("data") or {}
    return d.get("fields") or [], d.get("items") or [], j


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/teajoin_probe_deep.json")
    ap.add_argument("--sleep", type=float, default=1.5)
    a = ap.parse_args()
    st = get_settings()
    token, url = st.tushare_teajoin_token.strip(), st.tushare_teajoin_url.strip()
    out: dict = {"probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def go(api, params, tries=2):
        for i in range(tries):
            r = call(url, token, api, params, "")
            f, it, j = rows_of(r)
            busy = "busy" in str((j or {}).get("msg", "")) or "error" in r
            if not busy or i == tries - 1:
                time.sleep(a.sleep)
                return r, f, it, j
            time.sleep(8)  # capacity busy：退避后只重试一次

    # 1) 重试第一轮失败项
    retry = [("daily", {"trade_date": "20200102"}), ("adj_factor", {"trade_date": "20261023"}),
             ("index_daily", {"ts_code": "000905.SH", "start_date": "20261016", "end_date": "20261023"}),
             ("ths_index", {}), ("stk_auction_o", {"trade_date": "20230103"}), ("stk_auction_o", {"trade_date": "20250102"}),
             ("stk_auction", {"trade_date": "20250102"}), ("stk_auction", {"trade_date": "20230103"}), ("stk_auction", {"trade_date": "20200102"}),
             ("stk_auction_c", {"trade_date": "20250102"}), ("limit_step", {"trade_date": "20261023"}),
             ("block_trade", {"trade_date": "20261023"}), ("repurchase", {"ann_date": "20261023"}),
             ("dc_index", {"trade_date": "20261023", "idx_type": "概念板块"}), ("stk_factor_pro", {"ts_code": "000001.SZ", "start_date": "20261016", "end_date": "20261023"}),
             ("kpl_list", {"trade_date": "20230103", "tag": "涨停"}), ("kpl_list", {"trade_date": "20250102", "tag": "涨停"}),
             ("etf_mins", {"ts_code": "510300.SH", "freq": "1min", "start_date": "2024-01-03 09:30:00", "end_date": "2024-01-03 09:35:00"}),
             ("stk_mins", {"ts_code": "000001.SZ", "freq": "5min", "start_date": "2026-10-23 09:30:00", "end_date": "2026-10-23 10:00:00"})]
    out["retry"] = []
    for api, params in retry:
        r, f, it, j = go(api, params)
        rec = {"api": api, "params": params, "ms": r.get("ms"), "rows": len(it), "fields": f,
               "msg": str((j or {}).get("msg") or r.get("error") or "")[:100].replace(token, "***")}
        out["retry"].append(rec)
        print(api, params.get("trade_date") or params.get("ann_date") or "", rec["rows"], rec["ms"], rec["msg"], flush=True)

    # 2) 涨停榜字段完整度（按 limit 类型、fd_amount/first_time/open_times 非空率）
    out["limit_list_d_quality"] = []
    for d in ("20261023", "20260105", "20250102", "20230103", "20200102"):
        r, f, it, j = go("limit_list_d", {"trade_date": d})
        q = {"trade_date": d, "rows": len(it)}
        if it:
            col = {c: [row[i] for row in it] for i, c in enumerate(f)}
            q["by_limit"] = {k: col["limit"].count(k) for k in set(col["limit"])}
            for c in ("fd_amount", "first_time", "last_time", "open_times", "limit_times", "up_stat"):
                q[c + "_nonnull"] = round(sum(v is not None for v in col[c]) / len(it), 3)
        out["limit_list_d_quality"].append(q)
        print("limit_list_d", q, flush=True)
    for lt in ("U", "Z", "D"):
        r, f, it, j = go("limit_list_d", {"trade_date": "20261023", "limit_type": lt})
        out.setdefault("limit_list_d_by_type", {})[lt] = len(it)
        print("limit_type", lt, len(it), flush=True)

    # 3) 与本地库对账（最近交易日 daily 收盘价、cyq_perf 的 cost_50）
    from app.db.session import SessionLocal

    r, f, it, j = go("daily", {"trade_date": "20261023"})
    t = {row[f.index("ts_code")][:6]: row[f.index("close")] for row in it}
    r2, f2, it2, j2 = go("cyq_perf", {"trade_date": "20261023"})
    c = {row[f2.index("ts_code")][:6]: row[f2.index("cost_50pct")] for row in it2}
    with SessionLocal() as db:
        rows = db.execute(text("SELECT code, close, chip_cost_50 FROM daily_kbars WHERE trade_date='2026-10-23'")).all()
    n = m = cn = cm = 0
    for code, close, c50 in rows:
        if code in t and close is not None:
            n += 1
            m += abs(float(close) - float(t[code])) < 0.0051
        if code in c and c50 is not None:
            cn += 1
            cm += abs(float(c50) - float(c[code])) < 0.0051
    out["reconcile_20261023"] = {"daily_close": {"compared": n, "match": m, "ratio": round(m / n, 4) if n else None},
                                 "cyq_cost_50": {"compared": cn, "match": cm, "ratio": round(cm / cn, 4) if cn else None},
                                 "teajoin_daily_rows": len(it), "local_rows": len(rows)}
    print("reconcile", out["reconcile_20261023"], flush=True)

    # 4) 延迟与稳定性：12 次小请求（间隔 1.5s）
    lat, fail = [], 0
    for _ in range(12):
        r = call(url, token, "trade_cal", {"exchange": "SSE", "start_date": "20261026", "end_date": "20261027"}, "", timeout=40)
        ok = "json" in r and (r["json"].get("code") in (0, "0"))
        lat.append(r["ms"])
        fail += 0 if ok else 1
        time.sleep(a.sleep)
    out["stability_small_calls"] = {"n": 12, "fail": fail, "ms_min": min(lat), "ms_median": statistics.median(lat), "ms_max": max(lat)}
    print("stability", out["stability_small_calls"], flush=True)
    p = Path(a.out)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", p)


if __name__ == "__main__":
    main()
