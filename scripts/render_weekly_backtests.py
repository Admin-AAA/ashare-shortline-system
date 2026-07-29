#!/usr/bin/env python3
"""分周批量跑 champion 回测，避开回测引擎长窗口慢/超时的瓶颈。

策略 ID：daily-stock-analysis-skill（与 LIVE 主力策略对齐）
窗口：2026-02-02 ~ 2026-07-15（最近约 5 个多月，按周分块，每块5信号日）
输出：每个块的 metrics 写到 /tmp/bt_weeks/<start>_<end>.json，最后汇总。
"""
import json, os, sys, time, urllib.request
from datetime import date, timedelta
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.db.session import engine  # noqa: E402  复用 .env 配置的 engine，不硬编码凭据

STRATEGY = "daily-stock-analysis-skill"
SCORE_LINE = 72.0
HORIZON = 5


def chunks(days: list[str], block_size: int = 5) -> list[tuple[str, str, list[str]]]:
    out = []
    i = 0
    while i < len(days):
        s = days[i]
        e_i = min(i + block_size - 1, len(days) - 1)
        e = days[e_i]
        sig = days[i:e_i + 1]
        out.append((s, e, sig))
        i += block_size
    return out


def post_backtest(payload: dict) -> dict:
    req = urllib.request.Request(
        "http://127.0.0.1:8765/api/runs/backtest",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main() -> int:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kbars "
            "WHERE trade_date >= '2026-02-02' AND trade_date <= '2026-07-15' ORDER BY trade_date"
        )).fetchall()
    days = [str(r[0]) for r in rows]
    blks = chunks(days, 5)
    print(f"{len(days)} 交易日, {len(blks)} 个块 (每块信号期约5天)")

    summary = []
    t0 = time.time()
    for idx, (s, end, sig) in enumerate(blks, 1):
        out_path = f"/tmp/bt_weeks/{s}_{end}.json"
        try:
            r = post_backtest({
                "strategy_id": STRATEGY,
                "start_date": s,
                "end_date": end,
                "score_line": SCORE_LINE,
                "horizon_days": HORIZON,
            })
            with open(out_path, "w") as f:
                json.dump(r, f)
            metrics = (r.get("result") or {}).get("metrics") or {}
            summary.append({"block": f"{s}_{end}", "status": r.get("status"),
                            "n": metrics.get("sample_count"),
                            "wr": metrics.get("win_rate_pct"),
                            "avg_net": metrics.get("average_net_return_pct"),
                            "pl": metrics.get("profit_loss_ratio"),
                            "dd": metrics.get("max_drawdown_pct"),
                            "conf": metrics.get("confidence")})
            print(f"[{idx}/{len(blks)}] {s}~{end}: status={r.get('status')} n={metrics.get('sample_count')} WR={metrics.get('win_rate_pct')} avg_net={metrics.get('average_net_return_pct')} PL={metrics.get('profit_loss_ratio')} conf={metrics.get('confidence')}")
        except Exception as exc:
            print(f"[{idx}/{len(blks)}] {s}~{end}: FAIL {str(exc)[:120]}")
            summary.append({"block": f"{s}_{end}", "status": "FAIL", "err": str(exc)[:120]})
    with open("/tmp/bt_weeks/_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n总耗时 {time.time()-t0:.0f}s, 结果汇总 /tmp/bt_weeks/_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
