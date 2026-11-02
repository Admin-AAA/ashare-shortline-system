"""[长线] 财报/分红类事件实盘候选：CLD_h60 / DINC_h60 / UDP_h60（持60日，次日开盘买）。

研究（口径完全沿用，预注册、未调参）：
  - CLD_h60  流动负债占比同比下降  scripts/midterm/lt_research88.py（§240）
  - DINC_h60 分红增加（派现同比≥30%）scripts/midterm/lt_research48.py（§158）
  - UDP_h60  每股未分配利润同比高增   scripts/midterm/lt_research94.py（§252）
  - NCS_h60  净负债转为净现金         scripts/midterm/lt_research98.py（§261）
  - CG4_h60  营收与扣非净利连续≥4期双增 scripts/midterm/lt_research102.py（§269）
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
# NCS（§261）：去年同期 netdebt>0 且 interestdebt>0；本期 netdebt≤0（净现金）；本期 profit_dedt>0；
# 排序分 =（去年 netdebt − 本期 netdebt）/ 去年 interestdebt，封顶 3.0
NCS_CAP = 3.0
# CG4（§269）：双增 = or_yoy≥5 且 dt_netprofit_yoy≥5（累计同比，%）；streak = 以本期为终点、报告期逐期相连（跳期即断）且每期双增的期数；
# 入选 streak≥4；排序分 = min(streak,12) + min(dt_netprofit_yoy,100)/1000
CG_MIN_YOY, CG_STREAK, CG_STREAK_CAP, CG_NPY_CAP = 5.0, 4, 12, 100.0
# DINC（§158）：预案每股税前派现>0，较去年同报告期（优先实施记录，否则最早预案）提高≥30%（去年同期>0），不封顶
DINC_INC_TH = 0.30

FINA_FIELDS = ("ts_code,ann_date,end_date,currentdebt_to_debt,undist_profit_ps,netprofit_yoy,netdebt,interestdebt,profit_dedt,"
               "or_yoy,dt_netprofit_yoy,op_yoy,q_op_qoq,q_ocf_to_sales,ocf_to_shortdebt,netprofit_margin,q_sales_yoy,fcff_ps,fcfe_ps,ocf_yoy,ocfps,cfps_yoy,saleexp_to_gr,finaexp_of_gr,adminexp_of_gr,turn_days,fa_turn,tangibleasset_to_debt,eqt_to_debt,ca_to_assets")
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
    for k in ("currentdebt_to_debt", "undist_profit_ps", "netprofit_yoy", "netdebt", "interestdebt", "profit_dedt",
              "or_yoy", "dt_netprofit_yoy", "op_yoy", "q_op_qoq", "q_ocf_to_sales", "ocf_to_shortdebt", "netprofit_margin", "q_sales_yoy", "fcff_ps", "fcfe_ps", "ocf_yoy", "ocfps", "cfps_yoy", "saleexp_to_gr", "finaexp_of_gr", "adminexp_of_gr", "turn_days", "fa_turn", "tangibleasset_to_debt", "eqt_to_debt", "ca_to_assets"):
        if k in f.columns:
            f[k] = pd.to_numeric(f[k], errors="coerce")
    f = f[(f["ann_date"].str.len() == 8) & (f["end_date"].str.len() == 8)]
    _fina_cache[period] = (time.time(), f)
    if len(_fina_cache) > 24:
        _fina_cache.pop(next(iter(_fina_cache)))
    return f


def _first_ann(f: pd.DataFrame, metric) -> pd.DataFrame:
    """研究口径：先丢掉指标为空的行（metric 可为单列名或多列元组），再每个 (股票, 报告期) 取最早公告。"""
    g = f.dropna(subset=[metric] if isinstance(metric, str) else list(metric))
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


# §361/§801 ADMIN（管理费用率同比下降；规则同 §201 / lt_research69）
ADMIN_CUR_LO, ADMIN_CUR_HI = 0.5, 40.0
ADMIN_PREV_LO, ADMIN_PREV_HI = 0.5, 60.0
ADMIN_DELTA_MIN, ADMIN_DELTA_CAP = 1.0, 10.0


# §357 SEXP_h20（销售费用率同比下降；规则同 §198 / lt_research68，未调参）
SEXP_CUR_LO, SEXP_CUR_HI = 0.2, 40.0
SEXP_PREV_LO, SEXP_PREV_HI = 0.2, 60.0
SEXP_DELTA_MIN, SEXP_DELTA_CAP = 0.5, 10.0  # §426：门槛 1.0→0.5


def sexp_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """SEXP_h20：销售费用/营业总收入 同比下降 ≥0.5 百分点（§426：原≥1.0）。"""
    def passes(cur: float, prev: float):
        delta = prev - cur
        ok = (SEXP_CUR_LO <= cur <= SEXP_CUR_HI and SEXP_PREV_LO <= prev <= SEXP_PREV_HI and delta >= SEXP_DELTA_MIN)
        return ok, min(delta, SEXP_DELTA_CAP), {"delta": delta}
    return _fina_events(db, as_of, "saleexp_to_gr", passes)


def sexpvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§525/§536/§550/§694/§718 SEXPVOL：销售费用率下降≥0.35 ∩ 放量≥1.8×amt20（§718 硬抬；原≥1.5×）；排序原始降幅去封顶（§694；门槛仍 §550≥0.35）。"""
    from sqlalchemy import text, bindparam
    # §550：仅 vol 变体软扩降幅门槛；mid-sales-exp-drop 仍走 sexp_candidates(≥0.5)
    # §694：仅 vol 变体排序去封顶；mid-sales-exp-drop 仍封顶10
    def passes(cur: float, prev: float):
        delta = prev - cur
        ok = (SEXP_CUR_LO <= cur <= SEXP_CUR_HI and SEXP_PREV_LO <= prev <= SEXP_PREV_HI and delta >= 0.35)
        return ok, delta, {"delta": delta}  # §694：不再 min(delta, SEXP_DELTA_CAP)
    base = _fina_events(db, as_of, "saleexp_to_gr", passes)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.8 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))

# §359 FINA_h20（财务费用率同比下降；规则同 §203 / lt_research70，未调参）
FINA_CUR_LO, FINA_CUR_HI = -2.0, 15.0
FINA_PREV_LO, FINA_PREV_HI = -2.0, 25.0
FINA_DELTA_MIN, FINA_DELTA_CAP = 0.5, 8.0  # CAP 仅历史对照；§396 起排序不再封顶


def fina_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """FINA_h20：财务费用/营业总收入 同比下降 ≥0.5 百分点。§396：排序用原始降幅（不封顶）。"""
    def passes(cur: float, prev: float):
        delta = prev - cur
        ok = (FINA_CUR_LO <= cur <= FINA_CUR_HI and FINA_PREV_LO <= prev <= FINA_PREV_HI and delta >= FINA_DELTA_MIN)
        return ok, delta, {"delta": delta}  # §396：不再 min(delta, FINA_DELTA_CAP)
    return _fina_events(db, as_of, "finaexp_of_gr", passes)




def finavol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§527 FINAVOL_h20：财务费用率下降 ∩ 信号日成交额≥1.5×amt20；排序原始 dq 去封顶（与 §396 / 研究 finavol_s527 一致）。"""
    from sqlalchemy import text, bindparam
    base = fina_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))

def ncs_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """NCS_h60：去年同期净负债>0（有息负债>0）→ 本期净负债≤0（净现金），且本期扣非净利>0。
    研究口径（lt_research98）：fina 先丢 netdebt/profit_dedt 为空的行，再每个 (股票, 报告期) 取最早公告；去年同期取同一张表。"""
    from app.services import midterm_service as ms
    w = _window(db, as_of)
    if w is None:
        return None
    dates, start, end = w
    key = ("netdebt", "profit_dedt")
    out: dict[str, dict[str, Any]] = {}
    for period in ms._quarter_ends(as_of, 4):
        cur_all = _fina_period(period)
        if cur_all is None:
            return None
        if not set(key) <= set(cur_all.columns):
            continue
        cur = _first_ann(cur_all, key)
        today = cur[(cur["ann_date"] >= start) & (cur["ann_date"] <= end)]
        today = today[today["ts_code"].map(_tradable_code)]
        if today.empty:
            continue
        prev_all = _fina_period(_prev_period(period))
        if prev_all is None:
            return None
        if not set(key) <= set(prev_all.columns) or "interestdebt" not in prev_all.columns:
            continue
        prev = _first_ann(prev_all, key).set_index("ts_code")
        for _, r in today.iterrows():
            ts = r["ts_code"]
            if ts not in prev.index:
                continue
            ndp, idp = prev.at[ts, "netdebt"], prev.at[ts, "interestdebt"]
            nd, pdd = float(r["netdebt"]), float(r["profit_dedt"])
            if pd.isna(ndp) or pd.isna(idp) or not (ndp > 0 and idp > 0 and nd <= 0 and pdd > 0):
                continue
            score = min((float(ndp) - nd) / float(idp), NCS_CAP)
            code = str(ts)[:6]
            idc = r.get("interestdebt")
            npy = r.get("netprofit_yoy")
            item = {"code": code, "ann_date": str(r["ann_date"]), "end_date": period,
                    "netdebt": nd, "netdebt_prev": float(ndp), "interestdebt_prev": float(idp),
                    "interestdebt": None if idc is None or pd.isna(idc) else float(idc),
                    "profit_dedt": pdd, "netprofit_yoy": None if npy is None or pd.isna(npy) else float(npy),
                    "score": score}
            if code not in out or item["score"] > out[code]["score"]:
                out[code] = item
    return _attach(db, as_of, dates, out)


def _shift_period(period: str, k: int) -> str:
    """报告期往前推 k 个季度（YYYYMMDD，季末）。"""
    ends = ["0331", "0630", "0930", "1231"]
    idx = int(period[:4]) * 4 + ends.index(period[4:]) - k
    return f"{idx // 4}{ends[idx % 4]}"


def _cg_ok(row) -> bool:
    o, n = row.get("or_yoy"), row.get("dt_netprofit_yoy")
    return o is not None and n is not None and not pd.isna(o) and not pd.isna(n) and o >= CG_MIN_YOY and n >= CG_MIN_YOY


def cg_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """CG4_h60：营收与扣非净利同比连续≥4个报告期双增（各≥5%），本期为窗口内首次披露。
    研究口径（lt_research102）：每个 (股票, 报告期) 取最早公告（不先丢空值；空值=不双增），报告期逐期相连，跳期即断。"""
    from app.services import midterm_service as ms
    w = _window(db, as_of)
    if w is None:
        return None
    dates, start, end = w
    out: dict[str, dict[str, Any]] = {}
    hist: dict[str, pd.DataFrame | None] = {}

    def table(per: str):
        if per not in hist:
            f = _fina_period(per)
            if f is None:
                hist[per] = None
            elif not {"or_yoy", "dt_netprofit_yoy"} <= set(f.columns):
                hist[per] = pd.DataFrame(columns=["ts_code"]).set_index("ts_code")
            else:
                hist[per] = _first_ann(f, "end_date").set_index("ts_code")
        return hist[per]

    for period in ms._quarter_ends(as_of, 4):
        cur = table(period)
        if cur is None:
            return None
        if cur.empty:
            continue
        today = cur[(cur["ann_date"] >= start) & (cur["ann_date"] <= end)]
        today = today[today.index.map(_tradable_code)]
        if today.empty:
            continue
        for ts, r in today.iterrows():
            if not _cg_ok(r):
                continue
            streak = 1
            for k in range(1, CG_STREAK_CAP):
                t = table(_shift_period(period, k))
                if t is None:
                    return None
                if ts not in t.index or not _cg_ok(t.loc[ts]):
                    break
                streak += 1
            if streak < CG_STREAK:
                continue
            npy = float(r["dt_netprofit_yoy"])
            score = min(streak, CG_STREAK_CAP) + min(npy, CG_NPY_CAP) / 1000.0
            code = str(ts)[:6]
            item = {"code": code, "ann_date": str(r["ann_date"]), "end_date": period, "streak": streak,
                    "or_yoy": float(r["or_yoy"]), "dt_netprofit_yoy": npy, "score": score}
            if code not in out or item["score"] > out[code]["score"]:
                out[code] = item
    return _attach(db, as_of, dates, out)


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
                logger.warning("midterm_fina_events: 读取分红缓存 %s 失败，已跳过（DINC 覆盖可能缺该文件）", f, exc_info=True)
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


# §599 TDVOL_h20（营业周期缩短∩放量；规则同 §184 / lt_research61，未调参）
TD_IMPROVE, TD_LO, TD_HI, TD_PREV_HI, TD_CAP = 20.0, 30.0, 400.0, 800.0, 200.0



# §615 FATVOL_h20（固定资产周转扩张∩放量；规则同 §205 / lt_research71，未调参）
FAT_RISE_MIN, FAT_FLOOR, FAT_CAP = 0.10, 0.5, 2.0


def fat_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """FAT_h20：fa_turn 同比升幅≥0.10 且本期≥0.5；排序升幅封顶2.0。"""
    def passes(cur: float, prev: float):
        dq = cur - prev
        ok = (cur >= FAT_FLOOR and dq >= FAT_RISE_MIN)
        return ok, min(dq, FAT_CAP), {"dq": dq}
    return _fina_events(db, as_of, "fa_turn", passes)


def fatvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§615 FATVOL_h20：固定资产周转扩张 ∩ 信号日成交额≥1.5×amt20；排序升幅封顶2.0。"""
    from sqlalchemy import text, bindparam
    base = fat_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


def td_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """TD_h20：营业周期同比缩短≥20 日（本期∈[30,400]、去年∈[30,800]）；排序缩短天数封顶200。"""
    def passes(cur: float, prev: float):
        improve = prev - cur
        ok = (TD_LO <= cur <= TD_HI and TD_LO <= prev <= TD_PREV_HI and improve >= TD_IMPROVE)
        return ok, min(improve, TD_CAP), {"improve": improve}
    return _fina_events(db, as_of, "turn_days", passes)


def tdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§599 TDVOL_h20：营业周期缩短 ∩ 信号日成交额≥1.5×amt20；排序缩短天数封顶200。"""
    from sqlalchemy import text, bindparam
    base = td_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))



# §661 FATTDVOL_h20（FAT ∩ TD ∩ 放量；排序 FAT 升幅）
def fattdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§661/§662：FAT升幅≥0.12 ∩ TD缩短 ∩ 放量≥1.5×amt20；排序 FAT 升幅（§662 硬抬）。"""
    fat = fatvol_candidates(db, as_of)
    td = td_candidates(db, as_of)
    if fat is None or td is None:
        return None
    td_by = {p["code"]: p for p in td}
    out = []
    for p0 in fat:
        dq = p0.get("dq", p0.get("score"))
        try:
            dq_f = float(dq) if dq is not None else None
        except (TypeError, ValueError):
            dq_f = None
        if dq_f is None or dq_f < 0.12:
            continue
        e = td_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["td_improve"] = e.get("improve")
        q["td_cur"] = e.get("cur")
        q["td_prev"] = e.get("prev")
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))



# §663 ETDFATVOL_h20（ETD ∩ FAT ∩ 放量；排序 ETD 升幅）
def etdfatvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§663/§750：股东权益/负债改善 ∩ 固定资产周转扩张 ∩ 放量≥1.8×amt20（§750 硬抬；原≥1.5×）；排序 ETD 升幅。"""
    etd = etdvol_candidates(db, as_of)
    fat = fat_candidates(db, as_of)
    if etd is None or fat is None:
        return None
    fat_by = {p["code"]: p for p in fat}
    out = []
    for p0 in etd:
        e = fat_by.get(p0["code"])
        if e is None:
            continue
        # §750：在 etdvol(≥1.5×) 基础上再硬抬到 ≥1.8×（不改动单腿 etdvol）
        amount = p0.get("amount")
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
            amount_f = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amt20_f = None
            amount_f = None
        if amount_f is None or amt20_f is None or amt20_f <= 0 or amount_f < 1.8 * amt20_f:
            continue
        q = dict(p0)
        q["fat_dq"] = e.get("dq")
        q["fat_cur"] = e.get("cur")
        q["fat_prev"] = e.get("prev")
        q["vol_ratio"] = round(amount_f / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


# §619 TADVOL_h20（有形资产/负债改善∩放量；规则同 §234 / lt_research85，未调参）
TAD_CUR_LO, TAD_CUR_HI, TAD_PREV_LO, TAD_PREV_HI = 0.3, 5.0, 0.2, 8.0
TAD_DELTA_MIN, TAD_DELTA_CAP = 0.25, 2.0


def tad_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """TAD_h20：tangibleasset_to_debt 本期∈[0.3,5]、去年∈[0.2,8]、升幅≥0.25（§620 硬抬）；排序升幅封顶2.0。"""
    def passes(cur: float, prev: float):
        delta = cur - prev
        ok = (TAD_CUR_LO <= cur <= TAD_CUR_HI and TAD_PREV_LO <= prev <= TAD_PREV_HI and delta >= TAD_DELTA_MIN)
        return ok, min(delta, TAD_DELTA_CAP), {"delta": delta}
    return _fina_events(db, as_of, "tangibleasset_to_debt", passes)


def tadvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§619/§620 TADVOL_h20：有形资产/负债改善（升幅≥0.25）∩ 信号日成交额≥1.5×amt20；排序升幅封顶2.0。"""
    from sqlalchemy import text, bindparam
    base = tad_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


# §621 ETDVOL_h20（股东权益/负债改善∩放量；规则同 §236 / lt_research86，未调参）
ETD_CUR_LO, ETD_CUR_HI, ETD_PREV_LO, ETD_PREV_HI = 0.3, 5.0, 0.2, 8.0
ETD_DELTA_MIN, ETD_DELTA_CAP = 0.20, 2.0


def etd_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """ETD_h20：eqt_to_debt 本期∈[0.3,5]、去年∈[0.2,8]、升幅≥0.20；排序升幅封顶2.0。"""
    def passes(cur: float, prev: float):
        delta = cur - prev
        ok = (ETD_CUR_LO <= cur <= ETD_CUR_HI and ETD_PREV_LO <= prev <= ETD_PREV_HI and delta >= ETD_DELTA_MIN)
        return ok, min(delta, ETD_DELTA_CAP), {"delta": delta}
    return _fina_events(db, as_of, "eqt_to_debt", passes)


def etdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§621 ETDVOL_h20：股东权益/负债改善 ∩ 信号日成交额≥1.5×amt20；排序升幅封顶2.0。"""
    from sqlalchemy import text, bindparam
    base = etd_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))



# §656 TADETDVOL_h20（TAD≥0.25 ∩ ETD≥0.20 ∩ 放量；排序 TAD 升幅）
def tadetdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§656：有形资产/负债改善(≥0.25) ∩ 股东权益/负债改善(≥0.20) ∩ 放量≥1.5×amt20；排序 TAD 升幅。"""
    tad = tadvol_candidates(db, as_of)
    etd = etd_candidates(db, as_of)
    if tad is None or etd is None:
        return None
    etd_by = {p["code"]: p for p in etd}
    out = []
    for p0 in tad:
        e = etd_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["etd_delta"] = e.get("delta")
        q["etd_cur"] = e.get("cur")
        q["etd_prev"] = e.get("prev")
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


# §623 CASVOL_h20（流动资产占比上升∩放量；规则同 §242 / lt_research89，未调参）
CAS_CUR_LO, CAS_CUR_HI, CAS_PREV_LO, CAS_PREV_HI = 20.0, 95.0, 10.0, 100.0
CAS_DELTA_MIN, CAS_DELTA_CAP = 3.0, 25.0


def cas_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """CAS_h20：ca_to_assets 本期∈[20,95]、去年∈[10,100]、升幅≥3.0pp；排序升幅封顶25。"""
    def passes(cur: float, prev: float):
        delta = cur - prev
        ok = (CAS_CUR_LO <= cur <= CAS_CUR_HI and CAS_PREV_LO <= prev <= CAS_PREV_HI and delta >= CAS_DELTA_MIN)
        return ok, min(delta, CAS_DELTA_CAP), {"delta": delta}
    return _fina_events(db, as_of, "ca_to_assets", passes)


def casvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§623 CASVOL_h20：流动资产占比上升 ∩ 信号日成交额≥1.5×amt20；排序升幅封顶25。"""
    from sqlalchemy import text, bindparam
    base = cas_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))





def dincvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§535 DINCVOL_h20：分红增加 ∩ 信号日成交额≥1.5×amt20；排序增幅（与研究 dincvol_s535 一致）。"""
    from sqlalchemy import text, bindparam
    base = dinc_candidates(db, as_of)
    if base is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))


# §735 NCSTDVOL_h20（NCS ∩ TD ∩ 放量；排序 NCS score）
def ncstdvol_candidates(db, as_of: str) -> list[dict[str, Any]] | None:
    """§735：净负债→净现金 ∩ TD缩短≥20 ∩ 放量≥1.5×amt20；排序 NCS score（封顶3.0）。"""
    from sqlalchemy import text, bindparam
    base = ncs_candidates(db, as_of)
    td = td_candidates(db, as_of)
    if base is None or td is None:
        return None
    if not base:
        return []
    codes = [p["code"] for p in base]
    rows = db.execute(text(
        "SELECT code, amount FROM daily_kbars WHERE trade_date=:d AND code IN :codes"
    ).bindparams(bindparam("codes", expanding=True)), {"d": as_of, "codes": codes}).mappings().all()
    amt_by = {r["code"]: float(r["amount"]) for r in rows if r["amount"] is not None}
    td_by = {p["code"]: p for p in td}
    out = []
    for p0 in base:
        amount = amt_by.get(p0["code"])
        amt20 = p0.get("amt20")
        try:
            amt20_f = float(amt20) if amt20 is not None else None
        except (TypeError, ValueError):
            amt20_f = None
        if amount is None or amt20_f is None or amt20_f <= 0 or amount < 1.5 * amt20_f:
            continue
        e = td_by.get(p0["code"])
        if e is None:
            continue
        q = dict(p0)
        q["amount"] = amount
        q["vol_ratio"] = round(amount / amt20_f, 4)
        q["td_improve"] = e.get("improve")
        q["td_cur"] = e.get("cur")
        q["td_prev"] = e.get("prev")
        out.append(q)
    return sorted(out, key=lambda x: (-x["score"], x["code"]))
