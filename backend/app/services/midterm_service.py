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
        "name": "小市值周度分批（小盘趋势过滤）",
        "horizon": "mid",
        "summary": "每个周五收盘后，选全A（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）总市值最小的10只，次日开盘买入、各持有20个交易日。仅当小盘指数（最小400只等权）站上20日均线时开新仓，避开 2024 年初那类微盘踩踏。",
        "guard": True,
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


def small_cap_guard(db, as_of: str, window: int = 20) -> dict[str, Any]:
    """小盘趋势过滤：总市值最小400只（pe>0、非北交所）等权日收益累乘成指数，指数 > MA20 才开新仓。
    成分按当日（或之前最近一日）daily_basic 排名，与 research7.py 口径一致。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT :n"), {"d": as_of, "n": window * 2})][::-1]
    if len(dates) < window * 2 or dates[-1] != as_of:
        return {"on": False, "reason": "insufficient_dates"}
    level, levels = 1.0, []
    for d in dates:
        bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": d}).scalar()
        r = db.execute(text("""
            SELECT AVG(LEAST(GREATEST(k.pct_chg,-20),20)) FROM daily_kbars k JOIN (
              SELECT code FROM daily_basic WHERE trade_date=:bd AND pe_ttm>0 AND LEFT(code,1) NOT IN ('4','8','9')
              ORDER BY total_mv ASC LIMIT 400) m ON m.code=k.code WHERE k.trade_date=:d"""), {"bd": bd, "d": d}).scalar()
        level *= 1 + float(r or 0) / 100
        levels.append(level)
    ma = sum(levels[-window:]) / window
    return {"on": levels[-1] > ma, "index": round(levels[-1], 4), "ma20": round(ma, 4)}


def _held_codes(db, sid: str, as_of: str) -> set[str]:
    """之前批次中截至 as_of 仍在持有期（买入后不足 HOLD_DAYS 个交易日）的代码。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<:d "
        "ORDER BY trade_date DESC LIMIT :n"), {"d": as_of, "n": HOLD_DAYS + 1})]
    if not dates or not OUT_DIR.exists():
        return set()
    cutoff = dates[-1]
    held: set[str] = set()
    for f in OUT_DIR.glob(f"*_{sid}.json"):
        sd = f.name.split("_", 1)[0]
        if cutoff <= sd < as_of:
            held |= {p["code"] for p in json.loads(f.read_text())["picks"]}
    return held


def generate_picks(db, as_of: str, force: bool = False) -> dict[str, Any]:
    """盘后调用：若是信号日，生成并落盘各中线策略的选股。非致命。"""
    d = _dt.date.fromisoformat(as_of)
    result: dict[str, Any] = {}
    for sid, meta in STRATEGIES.items():
        if not force and not is_signal_day(sid, d):
            continue
        guard = small_cap_guard(db, as_of) if meta.get("guard") else {"on": True}
        if not guard["on"]:
            result[sid] = {"skipped": "small_cap_below_ma20", **guard}
            continue
        picks = _universe(db, as_of, meta["top_n"])
        held = _held_codes(db, sid, as_of)
        picks = [p for p in picks if p["code"] not in held]  # 已持有不重复买、也不顺延补位（与回测一致）
        if not picks and held:
            result[sid] = {"skipped": "all_held"}
            continue
        if not picks:
            result[sid] = "no_data"
            continue
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{as_of}_{sid}.json").write_text(json.dumps(
            {"strategy_id": sid, "signal_date": as_of, "picks": picks}, ensure_ascii=False, indent=1))
        result[sid] = len(picks)
    return result


def _realize(db, code: str, signal_date: str) -> dict[str, Any]:
    """次日开盘买入（一字/开盘涨停买不进 -> skipped）；第20个交易日开盘卖出，跌停封死顺延；0.3% 成本。"""
    rows = db.execute(text(
        "SELECT k.trade_date, k.open, k.high, k.close, k.pct_chg, l.up_limit, l.down_limit FROM daily_kbars k "
        "LEFT JOIN stk_limits l ON l.code=k.code AND l.trade_date=k.trade_date "
        "WHERE k.code=:c AND k.trade_date>:d AND k.close>0 ORDER BY k.trade_date LIMIT :n"),
        {"c": code, "d": signal_date, "n": HOLD_DAYS + 11}).all()
    if not rows or not rows[0].open:
        return {"status": "pending"}
    e = rows[0]
    if e.up_limit and e.open >= e.up_limit - 0.005:
        return {"status": "skipped", "entry_date": e.trade_date, "reason": "开盘涨停买不进"}
    tri = 1.0
    for i, r in enumerate(rows):
        if i > 0:
            tri *= 1 + (r.pct_chg or 0) / 100
        if i >= HOLD_DAYS and not (r.down_limit and r.high <= r.down_limit + 0.005):
            ret = (r.open / r.close * tri) / (e.open / e.close) - 1 - 0.003
            return {"status": "closed", "entry_date": e.trade_date, "exit_date": r.trade_date,
                    "ret": round(ret * 100, 2), "days": i}
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
        data["picks"] = [p for p in data["picks"]]
        data["strategy_name"] = STRATEGIES.get(data["strategy_id"], {}).get("name", data["strategy_id"])
        data["avg_ret"] = round(sum(closed) / len(closed), 2) if closed else None
        data["status"] = ("closed" if all(p.get("status") in ("closed", "skipped") for p in data["picks"])
                          else "pending" if all(p.get("status") == "pending" for p in data["picks"]) else "holding")
        batches.append(data)
        if len(batches) >= limit_batches:
            break
    return batches


def strategies_overview() -> list[dict[str, Any]]:
    metrics = json.loads(METRICS_FILE.read_text()) if METRICS_FILE.exists() else {}
    return [{"id": sid, **{k: v for k, v in meta.items() if k not in ("top_n", "guard")}, "hold_days": HOLD_DAYS,
             "top_n": meta["top_n"], "metrics": metrics.get(sid, {})} for sid, meta in STRATEGIES.items()]
