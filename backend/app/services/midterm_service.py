"""[中长线] 中线策略：选股、持仓跟踪与回测画像。

与短线 replay 完全独立：信号在周五收盘后生成，次日开盘买入，固定持有 20 个交易日后开盘卖出。
收益用 pct_chg 复权（daily_kbars 是不复权价）。回测脚本：scripts/midterm/research4.py。
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parents[2] / "output" / "midterm" / "picks"
METRICS_FILE = Path(__file__).resolve().parent / "midterm_backtest_metrics.json"
HOLD_DAYS = 20

STRATEGIES: dict[str, dict[str, Any]] = {
    "mid-small-cap-weekly": {
        "name": "小市值周度分批",
        "horizon": "mid",
        "summary": "每个周五收盘后，选全A（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）总市值最小的10只，次日开盘买入、各持有20个交易日；四批滚动持仓，分散建仓时点。",
        "top_n": 10,
        "schedule": "weekly",
    },
}


def is_signal_day(strategy_id: str, d: _dt.date) -> bool:
    if d.weekday() != 4:
        return False
    return STRATEGIES[strategy_id]["schedule"] == "weekly" or d.day <= 7


def _universe(db, as_of: str, limit: int) -> list[dict[str, Any]]:
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return []
    d20, d250 = dates[19], dates[250]
    basic_date = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    rows = db.execute(text(f"""
        SELECT b.code, b.total_mv, b.pe_ttm, a.amt20
        FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20, COUNT(*) n FROM daily_kbars
              WHERE trade_date> :d20x AND trade_date<=:d GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250) old ON old.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d AND close>0) today ON today.code=b.code
        WHERE b.trade_date=:bd AND b.pe_ttm>0 AND a.amt20>3e7
          AND LEFT(b.code,1) NOT IN ('4','8','9')
        ORDER BY b.total_mv ASC LIMIT {int(limit) * 3}
    """), {"d20x": dates[20], "d": as_of, "d250": d250, "bd": basic_date}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    names = dict(db.execute(text("SELECT code,name FROM stocks")).all())
    out = []
    for r in rows:
        if r["code"] in st:
            continue
        out.append({"code": r["code"], "name": names.get(r["code"], ""), "total_mv": float(r["total_mv"]),
                    "pe_ttm": float(r["pe_ttm"]), "amt20": float(r["amt20"])})
        if len(out) >= limit:
            break
    return out


def generate_picks(db, as_of: str, force: bool = False) -> dict[str, Any]:
    """盘后调用：若是信号日，生成并落盘各中线策略的选股。非致命。"""
    d = _dt.date.fromisoformat(as_of)
    result: dict[str, Any] = {}
    for sid, meta in STRATEGIES.items():
        if not force and not is_signal_day(sid, d):
            continue
        picks = _universe(db, as_of, meta["top_n"])
        if not picks:
            result[sid] = "no_data"
            continue
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{as_of}_{sid}.json").write_text(json.dumps(
            {"strategy_id": sid, "signal_date": as_of, "picks": picks}, ensure_ascii=False, indent=1))
        result[sid] = len(picks)
    return result


def _realize(db, code: str, signal_date: str) -> dict[str, Any]:
    rows = db.execute(text(
        "SELECT trade_date, open, close, pct_chg FROM daily_kbars WHERE code=:c AND trade_date>:d "
        "AND close>0 ORDER BY trade_date LIMIT :n"), {"c": code, "d": signal_date, "n": HOLD_DAYS + 1}).all()
    if not rows:
        return {"status": "pending"}
    e = rows[0]
    if not e.open:
        return {"status": "pending"}
    tri = 1.0
    for i, r in enumerate(rows):
        if i > 0:
            tri *= 1 + (r.pct_chg or 0) / 100
        if i == HOLD_DAYS:
            ret = (r.open / r.close * tri) / (e.open / e.close) - 1 - 0.003
            return {"status": "closed", "entry_date": e.trade_date, "exit_date": r.trade_date,
                    "ret": round(ret * 100, 2), "days": HOLD_DAYS}
    last = rows[-1]
    ret = tri / (e.open / e.close) - 1
    return {"status": "holding", "entry_date": e.trade_date, "mark_date": last.trade_date,
            "ret": round(ret * 100, 2), "days": len(rows) - 1}


def list_picks(db, strategy_id: str | None = None, limit_batches: int = 30) -> list[dict[str, Any]]:
    if not OUT_DIR.exists():
        return []
    files = sorted(OUT_DIR.glob("*.json"), reverse=True)
    batches = []
    for f in files:
        data = json.loads(f.read_text())
        if strategy_id and data["strategy_id"] != strategy_id:
            continue
        for p in data["picks"]:
            p.update(_realize(db, p["code"], data["signal_date"]))
        closed = [p["ret"] for p in data["picks"] if p.get("status") in ("closed", "holding")]
        data["strategy_name"] = STRATEGIES.get(data["strategy_id"], {}).get("name", data["strategy_id"])
        data["avg_ret"] = round(sum(closed) / len(closed), 2) if closed else None
        data["status"] = ("closed" if all(p.get("status") == "closed" for p in data["picks"])
                          else "pending" if all(p.get("status") == "pending" for p in data["picks"]) else "holding")
        batches.append(data)
        if len(batches) >= limit_batches:
            break
    return batches


def strategies_overview() -> list[dict[str, Any]]:
    metrics = json.loads(METRICS_FILE.read_text()) if METRICS_FILE.exists() else {}
    return [{"id": sid, **{k: v for k, v in meta.items() if k != "top_n"}, "hold_days": HOLD_DAYS,
             "top_n": meta["top_n"], "metrics": metrics.get(sid, {})} for sid, meta in STRATEGIES.items()]
