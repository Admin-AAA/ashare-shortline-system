"""[长线] 财报/分红类事件实盘候选：CLD_h60 / DINC_h60 / UDP_h60（持60日，次日开盘买）。

研究（口径完全沿用，预注册、未调参）：
  - CLD_h60  流动负债占比同比下降  scripts/midterm/lt_research88.py（§240）
  - DINC_h60 分红增加（派现同比≥30%）scripts/midterm/lt_research48.py（§158）
  - UDP_h60  每股未分配利润同比高增   scripts/midterm/lt_research94.py（§252）
事件时点：研究把公告日映射到「公告日起第一个交易日」当信号日；实盘 as_of 收盘后扫描公告日在 (上一交易日, as_of] 的首次公告
（含周末/节假日期间的公告）。每个 (股票, 报告期) 只认第一次公告。股池：非ST、非北交所/B股(4/8/9 开头)、上市>250交易日、
20日均成交额>5000万、当日有成交（midterm_service._liq_ok）。排序分同研究：封顶后降序，平手按代码升序（研究里 score−code×1e-10 与此等价）。
数据：
  - 财报：Tushare fina_indicator_vip(period=…) 经网关，整期一次调用，进程内缓存（历史回放同一期只拉一次）。
  - 分红：本地 data/midterm/dividend/*.parquet（到本地缓存截止日）+ 截止日之后的公告日经网关 dividend(ann_date=…)；
    候选个股的历史分红记录本地优先，缓存截止日之后的信号日再经网关 dividend(ts_code=…) 补全。
实盘推荐不设仓位上限（k_slots/max_new 仅是回测口径，回测指标按 20 仓位、每日最多新进 5 只计算）。
数据拉取失败返回 None（上层记 no_data），不会静默出空票。"""
from __future__ import annotations

import datetime as _dt
import glob
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DIV_DIR = ROOT / "data" / "midterm" / "dividend"

# CLD（§240）：本期 currentdebt_to_debt∈[30,95]、去年同期∈[20,100]（百分数），降幅=去年−本期≥5.0 个百分点，降幅封顶 40
CLD_CUR_LO, CLD_CUR_HI, CLD_PREV_LO, CLD_PREV_HI = 30.0, 95.0, 20.0, 100.0
CLD_DELTA_MIN, CLD_DELTA_CAP = 5.0, 40.0
# UDP（§252）：去年同期 undist_profit_ps>0、本期≥0.5 元/股、增速=本期/去年−1≥30%，增速封顶 2.0
UDP_PREV_MIN, UDP_FLOOR, UDP_G, UDP_CAP = 0.0, 0.5, 0.30, 2.0
# DINC（§158）：预案每股税前派现>0，较去年同报告期（优先实施记录，否则最早预案）提高≥30%（去年同期>0），不封顶
DINC_INC_TH = 0.30

FINA_FIELDS = "ts_code,ann_date,end_date,currentdebt_to_debt,undist_profit_ps,netprofit_yoy"
_FINA_TTL = 1800.0
_fina_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_div_local: dict[str, Any] = {}
_div_code_cache: dict[str, pd.DataFrame | None] = {}
_div_day_cache: dict[str, pd.DataFrame | None] = {}


# ------------------------------------------------------------------ 公共
def _window(db, as_of: str) -> tuple[list[str], str, str] | None:
    """(近251个交易日降序, 窗口起 YYYYMMDD, 窗口止 YYYYMMDD)；日K没到 as_of 返回 None。"""
    from sqlalchemy import text as _t
    dates = [r[0] for r in db.execute(_t(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001' AND trade_date<=:d "
        "ORDER BY trade_date DESC LIMIT 251"), {"d": as_of})]
    if len(dates) < 251 or dates[0] != as_of:
        return None
    start = (_dt.date.fromisoformat(dates[1]) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    return dates, start, as_of.replace("-", "")


def _attach(db, as_of: str, dates: list[str], cands: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """liq 过滤 + 补名称/市值/成交额；排序：score 降序、代码升序（同研究 score − code×1e-10）。"""
    from app.services import midterm_service as ms
    if not cands:
        return []
    ok = ms._liq_ok(db, as_of, dates, sorted(cands))
    names = dict(db.execute(text("SELECT code,name FROM stocks WHERE code IN :codes").bindparams(
        bindparam("codes", expanding=True)), {"codes": sorted(ok) or ["-"]}).all())
    res = [{**cands[c], "name": names.get(c, ""), "total_mv": float(ok[c]["total_mv"] or 0),
            "amt20": float(ok[c]["amt20"])} for c in ok]
    return sorted(res, key=lambda x: (-x["score"], x["code"]))


def _tradable_code(ts_code: str) -> bool:
    return bool(ts_code) and ts_code[:1] not in "489"


# ------------------------------------------------------------------ 财报（CLD / UDP）
def _fina_period(period: str) -> pd.DataFrame | None:
    """某报告期全市场财务指标（每个 (股票, 报告期) 只留第一次公告）。网关失败返回 None。"""
    hit = _fina_cache.get(period)
    if hit and time.time() - hit[0] < _FINA_TTL:
        return hit[1]
    from app.services import midterm_service as ms
    raw = ms._pro_fetch_with_wait("fina_indicator_vip", period, period=period, fields=FINA_FIELDS)
    if raw is None:
        return None
    return _fina_store(period, raw)


def _fina_store(period: str, raw: pd.DataFrame) -> pd.DataFrame:
    f = raw.copy()
    for k in ("ts_code", "ann_date", "end_date"):
        f[k] = f[k].astype(str).str.strip()
    for k in ("currentdebt_to_debt", "undist_profit_ps", "netprofit_yoy"):
        f[k] = pd.to_numeric(f[k], errors="coerce")
    f = f[(f["ann_date"].str.len() == 8) & (f["end_date"].str.len() == 8)]
    _fina_cache[period] = (time.time(), f)
    if len(_fina_cache) > 24:
        _fina_cache.pop(next(iter(_fina_cache)))
    return f


def _first_ann(f: pd.DataFrame, metric: str) -> pd.DataFrame:
    """研究口径：先丢掉指标为空的行，再每个 (股票, 报告期) 取最早公告。"""
    g = f.dropna(subset=[metric])
    return g.sort_values(["ann_date", "ts_code"], kind="mergesort").drop_duplicates(["ts_code", "end_date"], keep="first")


def _prev_period(period: str) -> str:
    return f"{int(period[:4]) - 1}{period[4:]}"


def _fina_events(db, as_of: str, metric: str, passes) -> list[dict[str, Any]] | None:
    """扫描窗口内首次披露的财报：passes(cur, prev) -> (ok, score, extra)。返回候选（含 liq 过滤）；数据失败 None。"""
    from app.services import midterm_service as ms
    w = _window(db, as_of)
    if w is None:
        return None
    dates, start, end = w
    out: dict[str, dict[str, Any]] = {}
    for period in ms._quarter_ends(as_of, 4):
        cur_all = _fina_period(period)
        if cur_all is None:
            return None
        cur = _first_ann(cur_all, metric)
        today = cur[(cur["ann_date"] >= start) & (cur["ann_date"] <= end)]
        today = today[today["ts_code"].map(_tradable_code)]
        if today.empty:
            continue
        prev_all = _fina_period(_prev_period(period))
        if prev_all is None:
            return None
        prev = _first_ann(prev_all, metric).set_index("ts_code")[metric]
        for _, r in today.iterrows():
            pv = prev.get(r["ts_code"])
            if pv is None or pd.isna(pv):
                continue
            ok, score, extra = passes(float(r[metric]), float(pv))
            if not ok:
                continue
            code = str(r["ts_code"])[:6]
            npy = r["netprofit_yoy"]
            item = {"code": code, "ann_date": str(r["ann_date"]), "end_date": period,
                    "cur": float(r[metric]), "prev": float(pv),
                    "netprofit_yoy": None if pd.isna(npy) else float(npy),
                    "score": score, **extra}
            if code not in out or item["score"] > out[code]["score"]:  # 同日多事件取最大（研究 group_by max）
                out[code] = item
    return _attach(db, as_of, dates, out)


def cld_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """CLD_h60：流动负债/负债合计 同比下降≥5 个百分点。"""
    def passes(cur: float, prev: float):
        delta = prev - cur
        ok = (CLD_CUR_LO <= cur <= CLD_CUR_HI and CLD_PREV_LO <= prev <= CLD_PREV_HI and delta >= CLD_DELTA_MIN)
        return ok, min(delta, CLD_DELTA_CAP), {"delta": delta}
    return _fina_events(db, as_of, "currentdebt_to_debt", passes)


def udp_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """UDP_h60：每股未分配利润同比增速≥30%（去年同期>0、本期≥0.5 元/股）。"""
    def passes(cur: float, prev: float):
        if not prev > UDP_PREV_MIN:
            return False, 0.0, {}
        g = cur / prev - 1.0
        return (g >= UDP_G and cur >= UDP_FLOOR), min(g, UDP_CAP), {"growth": g}
    return _fina_events(db, as_of, "undist_profit_ps", passes)


# ------------------------------------------------------------------ 分红（DINC）
def _local_div() -> tuple[pd.DataFrame, str]:
    """本地分红库（缓存到进程）与其完整覆盖的最后公告日（缓存文件最新修改日前一天，YYYYMMDD）。"""
    if "df" not in _div_local:
        files = sorted(glob.glob(str(DIV_DIR / "*.parquet")))
        cols = ["ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax"]
        parts = []
        for f in files:
            try:
                parts.append(pd.read_parquet(f, columns=cols))
            except Exception:  # noqa: BLE001
                continue
        df = _clean_div(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame(columns=cols)
        mt = max((Path(f).stat().st_mtime for f in files), default=0.0)
        cutoff = (_dt.datetime.fromtimestamp(mt).date() - _dt.timedelta(days=1)).strftime("%Y%m%d") if mt else "00000000"
        _div_local["df"], _div_local["cutoff"] = df, cutoff
    return _div_local["df"], _div_local["cutoff"]


def _clean_div(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["cash_div_tax"] = pd.to_numeric(d["cash_div_tax"], errors="coerce").fillna(0.0)
    for k in ("ts_code", "ann_date", "end_date"):
        d[k] = d[k].astype(str).str.strip()
    return d[d["end_date"].str.len() == 8]


def _div_day(day: str) -> pd.DataFrame | None:
    """某公告日（YYYYMMDD）全市场分红记录（网关）。失败 None。"""
    if day in _div_day_cache:
        return _div_day_cache[day]
    from app.services import midterm_service as ms
    raw = ms._pro_fetch_with_wait("dividend", day, ann_date=day,
                                  fields="ts_code,end_date,ann_date,div_proc,cash_div_tax")
    out = None if raw is None else _clean_div(raw)
    if out is not None:
        _div_day_cache[day] = out
    return out


def _div_code(ts_code: str) -> pd.DataFrame | None:
    if ts_code in _div_code_cache:
        return _div_code_cache[ts_code]
    from app.services import midterm_service as ms
    raw = ms._pro_fetch_with_wait("dividend", ts_code, ts_code=ts_code,
                                  fields="ts_code,end_date,ann_date,div_proc,cash_div_tax")
    out = None if raw is None else _clean_div(raw)
    if out is not None:
        _div_code_cache[ts_code] = out
    return out


def _plan_first(d: pd.DataFrame) -> pd.DataFrame:
    plan = d[d["div_proc"].isin(["预案", "预披露"]) & (d["ann_date"].str.len() == 8)]
    return plan.sort_values(["ann_date", "ts_code"], kind="mergesort").drop_duplicates(["ts_code", "end_date"], keep="first")


def dinc_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """DINC_h60：分红预案每股派现较去年同报告期提高≥30%。"""
    w = _window(db, as_of)
    if w is None:
        return None
    dates, start, end = w
    local, cutoff = _local_div()
    # 1) 窗口内公告：本地覆盖到的日子用本地，之后的日子逐日走网关
    parts = [local[(local["ann_date"] >= start) & (local["ann_date"] <= min(end, cutoff))]]
    d0 = max(_dt.datetime.strptime(start, "%Y%m%d").date(), _dt.datetime.strptime(cutoff, "%Y%m%d").date() + _dt.timedelta(days=1))
    d1 = _dt.datetime.strptime(end, "%Y%m%d").date()
    while d0 <= d1:
        day = _div_day(d0.strftime("%Y%m%d"))
        if day is None:
            return None
        parts.append(day)
        d0 += _dt.timedelta(days=1)
    win = pd.concat(parts, ignore_index=True)
    cur = _plan_first(win)
    cur = cur[(cur["cash_div_tax"] > 0) & cur["ts_code"].map(_tradable_code)]
    cur = cur[(cur["ann_date"] >= start) & (cur["ann_date"] <= end)]
    if cur.empty:
        return []
    # 2) 先过 liq 缩小候选，再逐只核对「首次预案」与去年同期基准
    from app.services import midterm_service as ms
    liq = ms._liq_ok(db, as_of, dates, sorted({t[:6] for t in cur["ts_code"]}))
    cur = cur[cur["ts_code"].str[:6].isin(liq)]
    out: dict[str, dict[str, Any]] = {}
    for _, r in cur.iterrows():
        tc = r["ts_code"]
        hist = local[local["ts_code"] == tc]
        if end > cutoff:  # 本地缓存之后的信号日：补网关个股历史（含缓存之后的更早预案/实施）
            gw = _div_code(tc)
            if gw is None:
                return None
            hist = pd.concat([hist, gw], ignore_index=True)
        hist = hist.drop_duplicates(["ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax"])
        first = _plan_first(hist[hist["end_date"] == r["end_date"]])
        if first.empty or first.iloc[0]["ann_date"] != r["ann_date"]:
            continue  # 之前已有过该报告期预案 -> 不是首次
        pe = _prev_period(r["end_date"])
        ph = hist[hist["end_date"] == pe]
        impl = ph[ph["div_proc"] == "实施"].sort_values("ann_date", kind="mergesort")
        pf = _plan_first(ph)
        if len(impl):
            prev_dps = float(impl.iloc[0]["cash_div_tax"])
        elif len(pf):
            prev_dps = float(pf.iloc[0]["cash_div_tax"])
        else:
            continue
        if not prev_dps > 0:
            continue
        inc = float(r["cash_div_tax"]) / prev_dps - 1.0
        if inc < DINC_INC_TH:
            continue
        code = tc[:6]
        item = {"code": code, "ann_date": r["ann_date"], "end_date": r["end_date"],
                "cash_div_tax": float(r["cash_div_tax"]), "prev_dps": prev_dps, "inc": inc, "score": inc}
        if code not in out or inc > out[code]["score"]:
            out[code] = item
    return _attach(db, as_of, dates, out)
