"""[中线] §114 大宗交易溢价成交后漂移（mid-block-trade-premium）实盘候选。

研究：scripts/midterm/lt_research25.py BT_prem_h20（预注册 3 版中采纳的一版，未调参）。
数据：Tushare block_trade（经统一数据网关 app.services.tushare_client.get_pro，只读，按 trade_date）。
口径：同一股票 T 日全部大宗成交汇总：amt = Σamount（万元），vwp = Σamount/Σvol（元/股，成交量加权大宗价）；
premium = vwp / close_T − 1（daily_kbars 不复权收盘）；流通市值 fmv = 最近一期 daily_basic.circ_mv（万元，≤T）
× close_T / 该快照日收盘；ratio = amt / fmv。入选：premium ≥ 0 且 ratio ≥ 0.5%，非北交所、非ST、上市>250交易日、
20日均成交额>5000万（midterm_service._liq_ok）。排序 = ratio 降序，平手按代码升序。T 收盘后出信号、T+1 开盘买、持 20 日。
时点：Tushare 大宗交易约 T 日 21:00 才发布 → 17:35 盘后流水线通常拿不到当日数据（返回 None = no_data，不落盘）；
需要在 21:00 之后、T+1 开盘前调用 midterm_service.generate_block_trade_picks(db, T) 出票。"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

FIELDS = "ts_code,trade_date,price,vol,amount,buyer,seller"
PAGE = 1000
MIN_PREMIUM = 0.0
MIN_RATIO = 0.005


def fetch_block_trades(trade_date: str) -> list[dict[str, Any]] | None:
    """trade_date (YYYY-MM-DD) 的全市场大宗交易明细；失败返回 None，空表返回 []。"""
    from app.services.tushare_client import get_pro
    td = trade_date.replace("-", "")
    rows: list[dict[str, Any]] = []
    off, last_head = 0, None
    try:
        p = get_pro()
        while True:
            kw: dict[str, Any] = {"trade_date": td, "fields": FIELDS}
            if off:
                kw.update(offset=off, limit=PAGE)
            df = p.block_trade(**kw)
            n = 0 if df is None else len(df)
            if n:
                page = df.to_dict("records")
                if last_head is not None and page[:3] == last_head:
                    raise RuntimeError("offset ignored")
                last_head = page[:3]
                rows.extend(page)
            if n < PAGE or off > 20000:
                break
            off += PAGE
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("block_trade %s failed: %s", td, exc)
        return None
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """明细 -> {code: {amt 万元, vol 万股, n}}（剔除北交所/非 6 位代码/非正量额）。"""
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        ts = str(r.get("ts_code") or "")
        code = ts[:6]
        if len(code) != 6 or not code.isdigit() or code[0] in "489" or ts.endswith(".BJ"):
            continue
        try:
            vol, amt = float(r.get("vol")), float(r.get("amount"))
        except (TypeError, ValueError):
            continue
        if not (vol > 0 and amt > 0):
            continue
        e = out.setdefault(code, {"amt": 0.0, "vol": 0.0, "n": 0})
        e["amt"] += amt
        e["vol"] += vol
        e["n"] += 1
    return out


def _close_and_fmv(db, as_of: str, codes: list[str]) -> dict[str, tuple[float, float]]:
    """{code: (close_T, fmv 万元)}：最近 daily_basic.circ_mv（≤as_of）按 close_T/快照日收盘折算。"""
    bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    if not bd:
        return {}
    q = text("""
        SELECT t.code, t.close AS c_t, k.close AS c_b, b.circ_mv FROM daily_kbars t
        JOIN daily_basic b ON b.code=t.code AND b.trade_date=:bd
        JOIN daily_kbars k ON k.code=t.code AND k.trade_date=:bd
        WHERE t.trade_date=:d AND t.close>0 AND t.code IN :codes
    """).bindparams(bindparam("codes", expanding=True))
    out = {}
    for code, c_t, c_b, cmv in db.execute(q, {"bd": bd, "d": as_of, "codes": codes}).all():
        if c_t and c_b and cmv:
            out[code] = (float(c_t), float(cmv) * float(c_t) / float(c_b))
    return out


def events(agg: dict[str, dict[str, float]], px: dict[str, tuple[float, float]]) -> dict[str, dict[str, Any]]:
    """汇总 + 收盘/流通市值 -> 达标事件 {code: {...}}（premium ≥ 0 且 ratio ≥ 0.5%）。"""
    out = {}
    for code, a in agg.items():
        if code not in px:
            continue
        close, fmv = px[code]
        vwp = a["amt"] / a["vol"]
        prem = vwp / close - 1
        ratio = a["amt"] / fmv
        if prem >= MIN_PREMIUM and ratio >= MIN_RATIO:
            out[code] = {"bt_amt_wan": round(a["amt"], 2), "vwp": round(vwp, 4), "close": close,
                         "premium": round(prem, 4), "ratio": round(ratio, 6), "n_trades": int(a["n"])}
    return out


def block_trade_candidates(db, as_of: str, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的大宗溢价成交信号（lt_research25 BT_prem_h20）。数据未发布/拉取失败返回 None。"""
    from app.services.midterm_service import _liq_ok
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    if rows is None:
        rows = fetch_block_trades(as_of)
    if not rows:  # 交易日全市场至少几十笔大宗；空 = 还没发布（约 21:00），不当作"无信号"
        return None
    agg = aggregate(rows)
    if not agg:
        return []
    ev = events(agg, _close_and_fmv(db, as_of, sorted(agg)))
    if not ev:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(ev))
    if not ok:
        return []
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok)}).all())
    res = [{"code": c, "name": names.get(c, ""), **ev[c], "score": ev[c]["ratio"],
            "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])} for c in ok]
    return sorted(res, key=lambda x: (-x["ratio"], x["code"]))
