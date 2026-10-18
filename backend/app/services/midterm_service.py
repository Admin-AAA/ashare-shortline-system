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

from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parents[2] / "output" / "midterm" / "picks"
METRICS_FILE = Path(__file__).resolve().parent / "midterm_backtest_metrics.json"
EVENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm" / "events"
HOLD_DAYS = 20

STRATEGIES: dict[str, dict[str, Any]] = {
    "mid-small-cap-weekly": {
        "name": "小市值周度分批（小盘趋势过滤）",
        "horizon": "mid",
        "summary": "每个周五收盘后，选全A（剔除ST、北交所、亏损股、上市不足一年、20日均成交额<3000万）总市值最小的10只，次日开盘买入、各持有20个交易日。仅当小盘指数（最小400只等权）站上20日均线时开新仓，避开 2024 年初那类微盘踩踏。",
        "guard": True,
        "top_n": 10,
        "hold_days": 20,
        "schedule": "weekly",
    },
    # §91 事件型（scripts/midterm/lt_research12.py 预注册 6 版中达标的 2 版；同市值五分位对照算超额；mid 版 §100 加金额≥0.5%）
    "mid-repurchase-plan": {
        # §100 预注册 RP_r05（scripts/midterm/lt_research18.py，未调参）：在 §91 R_plan_h20 基础上只保留金额上限≥总市值0.5%的预案
        "name": "回购预案事件（金额≥市值0.5%，持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的股票回购「预案」公告（同一股票365天内只取第一次），只保留回购金额上限≥总市值0.5%的（剔除象征性小额回购），剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按回购金额上限占总市值比例从高到低，次日开盘买入，各持有20个交易日。最多同时持有20只、每天最多新开5只。",
        "kind": "repurchase",
        "min_ratio": 0.005,
        "k_slots": 20,
        "max_new": 5,
        "hold_days": 20,
        "schedule": "daily",
    },
    # §95 新权重复评（scripts/midterm/lt_research15.py EA_h20，即 §93 G_h20 原条件，未调参）
    "mid-earnings-accel": {
        "name": "财报业绩加速（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今首次披露的定期报告（年报/季报，同一报告期只取第一次公告）：扣非净利同比≥50%、营收同比≥20%、扣非净利为正、且扣非同比高于上一期报告（加速）。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按扣非同比（封顶300%）从高到低，次日开盘买入，各持有20个交易日。最多同时持有20只、每天最多新开5只。",
        "kind": "earnings_accel",
        "k_slots": 20,
        "max_new": 5,
        "hold_days": 20,
        "schedule": "daily",
    },
    # §103 业绩预告正向漂移（scripts/midterm/lt_research20.py F_any_h20，预注册 3 版中采纳的一版，未调参；实盘候选 app/services/midterm_forecast.py）
    "mid-forecast-beat": {
        "name": "业绩预告首次预喜（持20日）",
        "horizon": "mid",
        "summary": "每个交易日盘后，扫描上一交易日收盘后至今新发布的业绩预告，同一报告期只认公司第一次预告（修正公告不算）：类型为预增/扭亏/略增、预告净利同比增幅下限>0、预告净利润下限>0。剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按预告增幅下限（封顶300%）从高到低，次日开盘买入，各持有20个交易日。最多同时持有20只、每天最多新开5只。信号集中在1/4/7/10月预告季。",
        "kind": "forecast_beat",
        "k_slots": 20,
        "max_new": 5,
        "hold_days": 20,
        "schedule": "daily",
    },
    "long-repurchase-big": {
        "name": "大额回购预案（持60日）",
        "horizon": "long",
        "summary": "每个交易日盘后，扫描新发布的股票回购「预案」公告（同一股票365天内只取第一次），只保留回购金额上限≥总市值1%的，剔除ST、北交所、上市不足250个交易日、20日均成交额<5000万；按金额占市值比例从高到低，次日开盘买入，各持有60个交易日。最多同时持有20只、每天最多新开5只。",
        "kind": "repurchase",
        "min_ratio": 0.01,
        "k_slots": 20,
        "max_new": 5,
        "hold_days": 60,
        "schedule": "daily",
    },
    # §98 防御型异象（scripts/midterm/lt_research16.py LTV_h60，预注册 3 版中唯一达标，未调参）
    "long-lowturn-lowvol": {
        "name": "低换手+低波动月度组合（持60日）",
        "horizon": "long",
        "summary": "每月最后一个交易日收盘后，在全A（剔除北交所）总市值排名第200–1500的股票里，剔除ST、上市不足250个交易日、股价<3元；按近60日平均换手率排名 + 近60日日收益波动率排名（都越低越好）相加，取得分最低的10只，次日开盘等权买入、各持有60个交易日。仍在持有期内的不重复买、也不顺延补位（与回测一致）。",
        "kind": "lowturn_lowvol",
        "top_n": 10,
        "hold_days": 60,
        "schedule": "month_end",
    },
    # §102 红利低波（scripts/midterm/lt_research19.py DVLV_h60，预注册 3 版中唯一达标，未调参）
    "long-dividend-lowvol": {
        "name": "红利低波月度组合（股息率+低波动，持60日）",
        "horizon": "long",
        "summary": "每月最后一个交易日收盘后，在全A（剔除北交所）总市值前1500名里，剔除ST、上市不足250个交易日、股价<3元，只看近12个月股息率（dv_ttm，取当周或上周最近一期周度快照）>0 的股票；按股息率排名（越高越好）+ 近60日日收益波动率排名（越低越好）相加，取得分最低的10只，次日开盘等权买入、各持有60个交易日。仍在持有期内的不重复买、也不顺延补位（与回测一致）。",
        "kind": "dividend_lowvol",
        "top_n": 10,
        "hold_days": 60,
        "schedule": "month_end",
    },
}


def _hold(strategy_id: str) -> int:
    return int(STRATEGIES.get(strategy_id, {}).get("hold_days", HOLD_DAYS))


def is_signal_day(strategy_id: str, d: _dt.date) -> bool:
    schedule = STRATEGIES[strategy_id]["schedule"]
    if schedule == "daily":
        return True
    if schedule == "month_end":  # 无交易日历时的近似：下一个工作日已是下个月（generate_picks 用 trade_calendar 精确判断）
        nxt = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
        return d.weekday() < 5 and nxt.month != d.month
    if d.weekday() != 4:
        return False
    return schedule == "weekly" or d.day <= 7


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
    """之前批次中截至 as_of 仍在持有期（买入后不足该策略 hold_days 个交易日）的代码。"""
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<:d "
        "ORDER BY trade_date DESC LIMIT :n"), {"d": as_of, "n": _hold(sid) + 1})]
    if not dates or not OUT_DIR.exists():
        return set()
    cutoff = dates[-1]
    held: set[str] = set()
    for f in OUT_DIR.glob(f"*_{sid}.json"):
        sd = f.name.split("_", 1)[0]
        if cutoff <= sd < as_of:
            held |= {p["code"] for p in json.loads(f.read_text())["picks"]}
    return held


def _fetch_repurchase_window(start: str, end: str):
    from app.services.tushare_client import get_pro
    try:
        return get_pro().repurchase(start_date=start, end_date=end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("repurchase window %s-%s failed: %s", start, end, exc)
        return None


def _store_repurchase_events(new) -> None:
    """把新拉到的公告并入 data/midterm/events/repurchase_<year>.parquet，供以后"365天内首次"判断。非致命。"""
    import pandas as pd
    try:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        new = new.copy()
        new["ann_date"] = new["ann_date"].astype(str).str.strip()
        for year, part in new.groupby(new["ann_date"].str.slice(0, 4)):
            f = EVENTS_DIR / f"repurchase_{year}.parquet"
            merged = pd.concat([pd.read_parquet(f), part]) if f.exists() else part
            merged = merged.drop_duplicates(["ts_code", "ann_date", "proc", "amount"])
            for col in ("vol", "amount", "high_limit", "low_limit"):
                if col in merged:
                    merged[col] = pd.to_numeric(merged[col], errors="coerce")
            merged.to_parquet(f, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("store repurchase events failed: %s", exc)


def repurchase_plan_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的新回购预案（公告日在 (上一交易日, as_of] 内，周末/盘后公告顺延到下一交易日），
    同一股票 365 天内只取首次；流动性口径与 lt_research.py liq 一致。按 回购金额上限/总市值 降序。
    数据拉取失败返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    start = (_dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    end = as_of.replace("-", "")
    new = _fetch_repurchase_window(start, end)
    if new is None:
        return None
    if len(new):
        _store_repurchase_events(new)
    files = sorted(EVENTS_DIR.glob("repurchase_*.parquet"))
    hist = pd.concat([pd.read_parquet(f) for f in files] + ([new] if len(new) else []))
    hist["ann_date"] = hist["ann_date"].astype(str).str.strip()
    plans = hist[hist["proc"].astype(str).str.contains("预案")].drop_duplicates(["ts_code", "ann_date"])
    today = plans[(plans["ann_date"] >= start) & (plans["ann_date"] <= end)]
    if today.empty:
        return []
    cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=365)).strftime("%Y%m%d")
    earlier = set(plans[(plans["ann_date"] < start) & (plans["ann_date"] >= cutoff)]["ts_code"].str.slice(0, 6))
    amt: dict[str, float] = {}
    ann: dict[str, str] = {}
    for _, r in today.iterrows():
        code = str(r["ts_code"])[:6]
        if code in earlier or code[0] in "489":
            continue
        a = pd.to_numeric(r.get("amount"), errors="coerce")
        amt[code] = max(amt.get(code, 0.0), 0.0 if pd.isna(a) else float(a))
        ann[code] = str(r["ann_date"])
    if not amt:
        return []
    codes = sorted(amt)
    q = text("""
        SELECT b.code, b.total_mv, a.amt20 FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20 FROM daily_kbars
              WHERE trade_date>:d20x AND trade_date<=:d AND code IN :codes GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250 AND code IN :codes) old ON old.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d AND close>0 AND code IN :codes) t ON t.code=b.code
        WHERE b.trade_date=(SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d) AND b.code IN :codes
    """).bindparams(bindparam("codes", expanding=True))
    rows = db.execute(q, {"d20x": dates[20], "d": as_of, "d250": dates[250], "codes": codes}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": codes}).all())
    out = []
    for r in rows:
        if r["code"] in st or not r["amt20"] or float(r["amt20"]) <= 5e7 or not r["total_mv"]:
            continue
        mv = float(r["total_mv"])
        out.append({"code": r["code"], "name": names.get(r["code"], ""), "ann_date": ann[r["code"]],
                    "repurchase_amount": amt[r["code"]], "total_mv": mv,
                    "ratio": round(amt[r["code"]] / (mv * 1e4), 5), "amt20": float(r["amt20"])})
    return sorted(out, key=lambda x: (-x["ratio"], x["code"]))


def _quarter_ends(as_of: str, n: int = 3) -> list[str]:
    d = _dt.date.fromisoformat(as_of)
    out, y, q = [], d.year, (d.month - 1) // 3  # q = number of quarters fully ended this year
    while len(out) < n:
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}{['0331', '0630', '0930', '1231'][q - 1]}")
        q -= 1
    return out


def _fetch_fina_period(period: str):
    from app.services.tushare_client import get_pro
    try:
        return get_pro().fina_indicator_vip(period=period, fields="ts_code,ann_date,end_date,dt_netprofit_yoy,or_yoy,profit_dedt")
    except Exception as exc:  # noqa: BLE001
        logger.warning("fina_indicator_vip %s failed: %s", period, exc)
        return None


def _liq_ok(db, as_of: str, dates: list[str], codes: list[str]) -> dict[str, dict[str, Any]]:
    """lt_research liq 口径：非ST、上市>250交易日、20日均成交额>5000万、当日有成交。"""
    q = text("""
        SELECT b.code, b.total_mv, a.amt20 FROM daily_basic b
        JOIN (SELECT code, AVG(amount) amt20 FROM daily_kbars
              WHERE trade_date>:d20x AND trade_date<=:d AND code IN :codes GROUP BY code) a ON a.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d250 AND code IN :codes) old ON old.code=b.code
        JOIN (SELECT code FROM daily_kbars WHERE trade_date=:d AND close>0 AND code IN :codes) t ON t.code=b.code
        WHERE b.trade_date=(SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d) AND b.code IN :codes
    """).bindparams(bindparam("codes", expanding=True))
    rows = db.execute(q, {"d20x": dates[20], "d": as_of, "d250": dates[250], "codes": codes}).mappings().all()
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    return {r["code"]: dict(r) for r in rows if r["code"] not in st and r["amt20"] and float(r["amt20"]) > 5e7}


def earnings_accel_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """as_of 收盘后可见的首次定期报告公告（公告日在 (上一交易日, as_of]），条件同 lt_research14 G。数据失败返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    start = (_dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    end = as_of.replace("-", "")
    periods = _quarter_ends(as_of, 3)  # 最近两个报告期 + 上一期（算加速）
    frames = {}
    for per in periods:
        f = _fetch_fina_period(per)
        if f is None:
            return None
        f = f.copy()
        f["ann_date"] = f["ann_date"].astype(str).str.strip()
        frames[per] = f.sort_values("ann_date").drop_duplicates("ts_code", keep="first")
    out: dict[str, dict[str, Any]] = {}
    for cur, prev in ((periods[0], periods[1]), (periods[1], periods[2])):
        f = frames[cur]
        today = f[(f["ann_date"] >= start) & (f["ann_date"] <= end)]
        if today.empty:
            continue
        pv = dict(zip(frames[prev]["ts_code"], pd.to_numeric(frames[prev]["dt_netprofit_yoy"], errors="coerce")))
        for _, r in today.iterrows():
            code = str(r["ts_code"])[:6]
            if code[0] in "489":
                continue
            dtp = pd.to_numeric(r["dt_netprofit_yoy"], errors="coerce")
            orv = pd.to_numeric(r["or_yoy"], errors="coerce")
            pdd = pd.to_numeric(r["profit_dedt"], errors="coerce")
            prev_dtp = pv.get(r["ts_code"])
            if any(pd.isna(x) for x in (dtp, orv, pdd)) or prev_dtp is None or pd.isna(prev_dtp):
                continue
            if dtp >= 50 and orv >= 20 and pdd > 0 and dtp > prev_dtp:
                out[code] = {"code": code, "ann_date": str(r["ann_date"]), "end_date": cur, "dt_netprofit_yoy": float(dtp),
                             "or_yoy": float(orv), "prev_dt_netprofit_yoy": float(prev_dtp), "score": min(float(dtp), 300.0)}
    if not out:
        return []
    ok = _liq_ok(db, as_of, dates, sorted(out))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**out[c], "name": names.get(c, ""), "total_mv": float(ok[c]["total_mv"] or 0), "amt20": float(ok[c]["amt20"])} for c in ok]
    return sorted(res, key=lambda x: (-x["score"], x["code"]))


def _is_month_end(db, as_of: str) -> bool:
    """as_of 是否为当月最后一个交易日（trade_calendar SSE；日历缺失时退回工作日近似）。"""
    nxt = db.execute(text(
        "SELECT MIN(cal_date) FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>:d"),
        {"d": as_of.replace("-", "")}).scalar()
    if not nxt:
        d = _dt.date.fromisoformat(as_of)
        nd = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
        return nd.month != d.month
    return str(nxt)[4:6] != as_of[5:7]


def lowturn_lowvol_candidates(db, as_of: str, top_n: int = 10) -> list[dict[str, Any]] | None:
    """§98 lt_research16 LTV 口径：非北交所按折算总市值从大到小排名第 200–1500、非ST、上市≥250交易日、收盘≥3；
    近60日均换手率秩 + 近60日日收益标准差秩（都低好，平均秩；同分按波动率低者优先）之和最低的 top_n 只。数据不足返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 300"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    bd = db.execute(text("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=:d"), {"d": as_of}).scalar()
    if not bd:
        return None
    mv = pd.DataFrame(db.execute(text("""
        SELECT b.code, b.total_mv, kb.close AS close_bd, kt.close AS close_t
        FROM daily_basic b
        JOIN daily_kbars kt ON kt.code=b.code AND kt.trade_date=:d
        LEFT JOIN daily_kbars kb ON kb.code=b.code AND kb.trade_date=:bd
        WHERE b.trade_date=:bd AND b.total_mv>0 AND kt.close>0 AND LEFT(b.code,1) NOT IN ('4','8','9')
    """), {"bd": bd, "d": as_of}).mappings().all())
    if mv.empty:
        return None
    mv["close_bd"] = pd.to_numeric(mv["close_bd"], errors="coerce")
    ratio = (mv["close_t"].astype(float) / mv["close_bd"]).where(mv["close_bd"] > 0, 1.0)
    mv["mvd"] = mv["total_mv"].astype(float) * ratio  # = 总股本 × 当日收盘（研究口径）
    mv["rk_mv"] = mv["mvd"].rank(ascending=False, method="first")
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    mv = mv[(mv["rk_mv"] >= 200) & (mv["rk_mv"] <= 1500) & (mv["close_t"].astype(float) >= 3) & ~mv["code"].isin(st)]
    if mv.empty:
        return []
    k = pd.DataFrame(db.execute(text(
        "SELECT code, trade_date, turnover_rate, pct_chg FROM daily_kbars "
        "WHERE trade_date>=:d0 AND trade_date<=:d AND close>0 AND code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"d0": dates[-1], "d": as_of, "codes": mv["code"].tolist()}).mappings().all())
    if k.empty:
        return None
    k = k.sort_values(["code", "trade_date"])
    age = k.groupby("code").size()
    k = k[k["code"].isin(age[age >= 251].index)].groupby("code").tail(60)
    g = k.groupby("code").agg(n=("pct_chg", "size"), turn60=("turnover_rate", "mean"),
                              vol60=("pct_chg", lambda x: (x.astype(float) / 100).clip(-0.35, 0.35).std(ddof=1)))
    g = g[(g["n"] >= 60) & g["turn60"].notna() & g["vol60"].notna()].reset_index()
    if g.empty:
        return []
    g["score"] = (g["turn60"].rank(method="average") + g["vol60"].rank(method="average")
                  + g["vol60"].rank(method="first") * 1e-6)
    g = g.sort_values(["score", "code"]).head(int(top_n)).merge(mv[["code", "mvd", "rk_mv"]], on="code")
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": g["code"].tolist()}).all())
    return [{"code": r.code, "name": names.get(r.code, ""), "total_mv": round(float(r.mvd), 1), "mv_rank": int(r.rk_mv),
             "turn60": round(float(r.turn60), 4), "vol60": round(float(r.vol60) * 100, 3), "score": round(float(r.score), 1)}
            for r in g.sort_values("score").itertuples()]


BASIC_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm"


def _weekly_snapshot_date(db, as_of: str, dates: list[str]) -> str | None:
    """§102 研究口径的周度 daily_basic 快照日：as_of 若是当周（ISO 周）最后一个交易日则取 as_of，否则取上一周最后一个交易日。
    dates = daily_kbars 交易日（降序，dates[0]==as_of）。"""
    d = _dt.date.fromisoformat(as_of)
    nxt = db.execute(text(
        "SELECT MIN(cal_date) FROM trade_calendar WHERE exchange='SSE' AND is_open=1 AND cal_date>:d"),
        {"d": as_of.replace("-", "")}).scalar()
    if nxt:
        nd = _dt.datetime.strptime(str(nxt).replace("-", "")[:8], "%Y%m%d").date()
    else:
        nd = d + _dt.timedelta(days=3 if d.weekday() == 4 else 1)
    wk = d.isocalendar()[:2]
    if nd.isocalendar()[:2] != wk:
        return as_of
    for x in dates:
        if _dt.date.fromisoformat(x).isocalendar()[:2] != wk:
            return x
    return None


def _weekly_basic(snap: str):
    """周度 daily_basic 快照（ts_code,total_share,dv_ttm）：先读研究用 data/midterm/basic_<year>.parquet，
    没有该日再经统一数据网关（tushare_client.get_pro 的 provider router，只读）拉 daily_basic。失败返回 None。"""
    import pandas as pd
    key = snap.replace("-", "")
    f = BASIC_DIR / f"basic_{key[:4]}.parquet"
    if f.exists():
        try:
            b = pd.read_parquet(f, columns=["ts_code", "trade_date", "total_share", "dv_ttm"])
            b = b[b["trade_date"].astype(str) == key]
            if not b.empty:
                return b
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly basic parquet %s read failed: %s", f.name, exc)
    try:
        from app.services.tushare_client import get_pro
        b = get_pro().daily_basic(trade_date=key, fields="ts_code,trade_date,total_share,dv_ttm")
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily_basic %s via gateway failed: %s", key, exc)
        return None
    return b if b is not None and not b.empty else None


def dividend_lowvol_candidates(db, as_of: str, top_n: int = 10) -> list[dict[str, Any]] | None:
    """§102 lt_research19 DVLV_h60 口径：非北交所按 周度 total_share × 当日收盘 从大到小排名第 1–1500、非ST、上市≥250交易日、收盘≥3；
    dv_ttm（周度快照：当周最后交易日=as_of 时取 as_of，否则上周最后交易日）>0 的子集内，dv_ttm 秩（高好，平均秩）+ 近60日日收益标准差秩
    （低好，平均秩；同分按波动率低者、再按代码）之和最低的 top_n 只。数据不足返回 None。"""
    import pandas as pd
    dates = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 300"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    snap = _weekly_snapshot_date(db, as_of, dates)
    b = _weekly_basic(snap) if snap else None
    if b is None:
        return None
    b = pd.DataFrame({"code": b["ts_code"].astype(str).str[:6],
                      "total_share": pd.to_numeric(b["total_share"], errors="coerce"),
                      "dv_ttm": pd.to_numeric(b["dv_ttm"], errors="coerce")})
    b = b[~b["code"].str[0].isin(["4", "8", "9"]) & b["total_share"].notna()].drop_duplicates("code")
    px = pd.DataFrame(db.execute(text(
        "SELECT code, close FROM daily_kbars WHERE trade_date=:d AND close>0"), {"d": as_of}).mappings().all())
    if px.empty:
        return None
    mv = b.merge(px, on="code")
    mv["close"] = mv["close"].astype(float)
    mv["mvd"] = mv["total_share"] * mv["close"]  # 万股 × 元 = 万元（研究口径）
    mv["rk_mv"] = mv["mvd"].rank(ascending=False, method="first")
    st = {r[0] for r in db.execute(text(
        "SELECT code FROM stock_metadata_history WHERE name LIKE '%ST%' AND effective_from<=:d "
        "AND (effective_to IS NULL OR effective_to>:d)"), {"d": as_of})}
    mv = mv[(mv["rk_mv"] <= 1500) & (mv["close"] >= 3) & ~mv["code"].isin(st)]
    if mv.empty:
        return []
    k = pd.DataFrame(db.execute(text(
        "SELECT code, trade_date, pct_chg FROM daily_kbars "
        "WHERE trade_date>=:d0 AND trade_date<=:d AND close>0 AND code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"d0": dates[-1], "d": as_of, "codes": mv["code"].tolist()}).mappings().all())
    if k.empty:
        return None
    k = k.sort_values(["code", "trade_date"])
    age = k.groupby("code").size()
    k = k[k["code"].isin(age[age >= 251].index)].groupby("code").tail(60)
    g = k.groupby("code").agg(n=("pct_chg", "size"),
                              vol60=("pct_chg", lambda x: (x.astype(float) / 100).clip(-0.35, 0.35).std(ddof=1)))
    g = g[(g["n"] >= 60) & g["vol60"].notna()].reset_index().merge(mv[["code", "mvd", "rk_mv", "dv_ttm"]], on="code")
    g = g[g["dv_ttm"] > 0].sort_values("code").reset_index(drop=True)
    if g.empty:
        return []
    g["score"] = ((-g["dv_ttm"]).rank(method="average") + g["vol60"].rank(method="average")
                  + g["vol60"].rank(method="first") * 1e-6)
    g = g.sort_values(["score", "code"]).head(int(top_n))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": g["code"].tolist()}).all())
    return [{"code": r.code, "name": names.get(r.code, ""), "total_mv": round(float(r.mvd), 1), "mv_rank": int(r.rk_mv),
             "dv_ttm": round(float(r.dv_ttm), 2), "vol60": round(float(r.vol60) * 100, 3), "score": round(float(r.score), 1),
             "basic_date": snap}
            for r in g.itertuples()]


def generate_picks(db, as_of: str, force: bool = False) -> dict[str, Any]:
    """盘后调用：若是信号日，生成并落盘各中线策略的选股。非致命。"""
    d = _dt.date.fromisoformat(as_of)
    result: dict[str, Any] = {}
    rp_cands: list[dict[str, Any]] | None | str = "unset"
    for sid, meta in STRATEGIES.items():
        if meta.get("schedule") == "month_end":
            if not force and not _is_month_end(db, as_of):
                continue
        elif not force and not is_signal_day(sid, d):
            continue
        if meta.get("kind") in ("lowturn_lowvol", "dividend_lowvol"):
            cand_fn = lowturn_lowvol_candidates if meta["kind"] == "lowturn_lowvol" else dividend_lowvol_candidates
            cands = cand_fn(db, as_of, int(meta["top_n"]))
            if cands is None:
                result[sid] = "no_data"
                continue
            held = _held_codes(db, sid, as_of)
            picks = [p for p in cands if p["code"] not in held]  # 已持有不重复买、也不顺延补位（与回测一致）
            if not picks:
                result[sid] = {"skipped": "all_held"} if cands else 0
                continue
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{as_of}_{sid}.json").write_text(json.dumps(
                {"strategy_id": sid, "signal_date": as_of, "picks": picks}, ensure_ascii=False, indent=1))
            result[sid] = len(picks)
            continue
        if meta.get("kind") == "earnings_accel":
            held = _held_codes(db, sid, as_of)
            free = int(meta["k_slots"]) - len(held)
            if free <= 0:
                result[sid] = {"skipped": "slots_full", "held": len(held)}
                continue
            ea = earnings_accel_candidates(db, as_of)
            if ea is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in ea if p["code"] not in held][:min(int(meta["max_new"]), free)]
            if not picks:
                result[sid] = 0
                continue
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{as_of}_{sid}.json").write_text(json.dumps(
                {"strategy_id": sid, "signal_date": as_of, "picks": picks}, ensure_ascii=False, indent=1))
            result[sid] = len(picks)
            continue
        if meta.get("kind") == "forecast_beat":
            from app.services.midterm_forecast import forecast_beat_candidates
            held = _held_codes(db, sid, as_of)
            free = int(meta["k_slots"]) - len(held)
            if free <= 0:
                result[sid] = {"skipped": "slots_full", "held": len(held)}
                continue
            fb = forecast_beat_candidates(db, as_of)
            if fb is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in fb if p["code"] not in held][:min(int(meta["max_new"]), free)]
            if not picks:
                result[sid] = 0
                continue
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{as_of}_{sid}.json").write_text(json.dumps(
                {"strategy_id": sid, "signal_date": as_of, "picks": picks}, ensure_ascii=False, indent=1))
            result[sid] = len(picks)
            continue
        if meta.get("kind") == "repurchase":
            held = _held_codes(db, sid, as_of)
            free = int(meta["k_slots"]) - len(held)
            if free <= 0:
                result[sid] = {"skipped": "slots_full", "held": len(held)}
                continue
            if rp_cands == "unset":
                rp_cands = repurchase_plan_candidates(db, as_of)
            if rp_cands is None:
                result[sid] = "no_data"
                continue
            picks = [p for p in rp_cands if p["ratio"] >= meta["min_ratio"] and p["code"] not in held]
            picks = picks[:min(int(meta["max_new"]), free)]
            if not picks:
                result[sid] = 0
                continue
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{as_of}_{sid}.json").write_text(json.dumps(
                {"strategy_id": sid, "signal_date": as_of, "picks": picks}, ensure_ascii=False, indent=1))
            result[sid] = len(picks)
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


def _realize(db, code: str, signal_date: str, hold: int = HOLD_DAYS) -> dict[str, Any]:
    """次日开盘买入（一字/开盘涨停买不进 -> skipped）；第 hold 个交易日开盘卖出，跌停封死顺延；0.3% 成本。"""
    rows = db.execute(text(
        "SELECT k.trade_date, k.open, k.high, k.close, k.pct_chg, l.up_limit, l.down_limit FROM daily_kbars k "
        "LEFT JOIN stk_limits l ON l.code=k.code AND l.trade_date=k.trade_date "
        "WHERE k.code=:c AND k.trade_date>:d AND k.close>0 ORDER BY k.trade_date LIMIT :n"),
        {"c": code, "d": signal_date, "n": hold + 11}).all()
    if not rows or not rows[0].open:
        return {"status": "pending"}
    e = rows[0]
    if e.up_limit and e.open >= e.up_limit - 0.005:
        return {"status": "skipped", "entry_date": e.trade_date, "reason": "开盘涨停买不进"}
    tri = 1.0
    for i, r in enumerate(rows):
        if i > 0:
            tri *= 1 + (r.pct_chg or 0) / 100
        if i >= hold and not (r.down_limit and r.high <= r.down_limit + 0.005):
            ret = (r.open / r.close * tri) / (e.open / e.close) - 1 - 0.003
            return {"status": "closed", "entry_date": e.trade_date, "exit_date": r.trade_date,
                    "ret": round(ret * 100, 2), "days": i}
    last = rows[-1]
    ret = tri / (e.open / e.close) - 1
    return {"status": "holding", "entry_date": e.trade_date, "mark_date": last.trade_date,
            "ret": round(ret * 100, 2), "days": len(rows) - 1}


def list_picks(db, strategy_id: str | None = None, limit_batches: int = 30,
               horizon: str | None = None) -> list[dict[str, Any]]:
    if not OUT_DIR.exists():
        return []
    files = sorted(OUT_DIR.glob("*.json"), reverse=True)
    batches = []
    for f in files:
        data = json.loads(f.read_text())
        if strategy_id and data["strategy_id"] != strategy_id:
            continue
        if horizon and STRATEGIES.get(data["strategy_id"], {}).get("horizon", "mid") != horizon:
            continue
        for p in data["picks"]:
            p.update(_realize(db, p["code"], data["signal_date"], _hold(data["strategy_id"])))
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


def strategies_overview(horizon: str | None = None) -> list[dict[str, Any]]:
    metrics = json.loads(METRICS_FILE.read_text()) if METRICS_FILE.exists() else {}
    return [{"id": sid, **{k: v for k, v in meta.items() if k not in ("top_n", "guard", "hold_days")},
             "hold_days": _hold(sid), "top_n": meta.get("top_n", meta.get("max_new")), "metrics": metrics.get(sid, {})}
            for sid, meta in STRATEGIES.items() if horizon is None or meta.get("horizon") == horizon]
