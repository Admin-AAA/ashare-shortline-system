"""
Tushare 稳定数据源客户端。

使用方式：
    from backend.app.services.tushare_client import get_pro
    pro = get_pro()
    df = pro.daily(ts_code="000001.SZ", limit=5)

自建服务器：http://101.35.233.113:8020/
不限速，提供完整 EOD 数据。
"""

from __future__ import annotations

import functools
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

_TOKEN = "f8b05f8ee54f468220f2569afe56b4c027f1d388d164899339d394ad"
_CUSTOM_URL = "http://101.35.233.113:8020/"

# ═════════════════════════════════════════════════════════════════════════════
# 客户端 — requests.Session 直连，杜绝 tushare 库的 TCP 悬挂问题
# ═════════════════════════════════════════════════════════════════════════════


class _TushareSession:
    """Tushare pro_api 兼容接口，底层用 requests.Session 池化连接。"""

    def __init__(self, token: str, timeout: tuple[float, float] = (5, 10)):
        self._token = token
        self._url = _CUSTOM_URL
        self._session = requests.Session()
        self._timeout = timeout

    def close(self) -> None:
        self._session.close()

    def __getattr__(self, api_name: str):
        if api_name.startswith("_"):
            raise AttributeError(api_name)

        def caller(**params: Any) -> pd.DataFrame:
            r = self._session.post(
                f"{self._url}{api_name}",
                json={
                    "api_name": api_name,
                    "token": self._token,
                    "params": params,
                    "fields": "",
                },
                timeout=self._timeout,
            )
            r.raise_for_status()
            d = r.json()
            if d.get("code") != 0:
                msg = d.get("msg", "Unknown error")
                raise RuntimeError(f"Tushare {api_name}: {msg}")
            data = d.get("data")
            if not data or "items" not in data or not data["items"]:
                return pd.DataFrame()
            return pd.DataFrame(data["items"], columns=data["fields"])

        return caller


@functools.lru_cache(maxsize=1)
def get_pro() -> _TushareSession:
    """获取 Tushare 客户端实例（单例缓存，requests.Session 直连）。"""
    return _TushareSession(_TOKEN, timeout=(5, 10))


def clear_pro_cache() -> None:
    """清除缓存的客户端实例。"""
    get_pro.cache_clear()


def ts_code(code: str) -> str:
    """纯数字代码 → Tushare ts_code 格式。"""
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


# ═════════════════════════════════════════════════════════════════════════════
# K线 + 技术指标 + 筹码（一次拉取，合并返回）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_daily_kbars(
    code: str,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """
    获取一只股票的完整日K数据（OHLCV + 技术指标 + 筹码分布）。

    合并 daily + daily_basic + stk_factor + cyq_perf 四个接口。
    """
    pro = get_pro()
    tsc = ts_code(code)
    sd = start_date or (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")

    # 1. daily — OHLCV
    daily = pro.daily(ts_code=tsc, start_date=sd, end_date=ed)
    if daily is None or daily.empty:
        return []
    daily = daily.sort_values("trade_date")
    if start_date is None:
        daily = daily.tail(days)
    daily_rows = {str(r["trade_date"]): r for _, r in daily.iterrows()}

    # 2. stk_factor — MACD/KDJ/RSI/BOLL/CCI + adj_factor
    factor_map: dict[str, dict] = {}
    try:
        factor = pro.stk_factor(ts_code=tsc, start_date=sd, end_date=ed)
        if factor is not None and not factor.empty:
            for _, r in factor.iterrows():
                factor_map[str(r["trade_date"])] = dict(r)
    except Exception:
        pass

    # 3. daily_basic — turnover_rate, pe_ttm, total_mv
    basic_map: dict[str, dict] = {}
    try:
        basic = pro.daily_basic(ts_code=tsc, start_date=sd, end_date=ed)
        if basic is not None and not basic.empty:
            for _, r in basic.iterrows():
                basic_map[str(r["trade_date"])] = dict(r)
    except Exception:
        pass

    # 4. cyq_perf — 筹码分布
    chip_map: dict[str, dict] = {}
    try:
        chips = pro.cyq_perf(ts_code=tsc, start_date=sd, end_date=ed)
        if chips is not None and not chips.empty:
            for _, r in chips.iterrows():
                chip_map[str(r["trade_date"])] = dict(r)
    except Exception:
        pass

    # 5. stk_factor_pro — 扩展因子（ASI/ATR/BBI/DPO/OBV/PSY/VR/WR 等）
    factor_pro_map: dict[str, dict] = {}
    try:
        fp = pro.stk_factor_pro(ts_code=tsc, start_date=sd, end_date=ed)
        if fp is not None and not fp.empty:
            for _, r in fp.iterrows():
                factor_pro_map[str(r["trade_date"])] = dict(r)
    except Exception:
        pass

    bars: list[dict[str, Any]] = []
    for td, row in daily_rows.items():
        fac = factor_map.get(td, {})
        bas = basic_map.get(td, {})
        chp = chip_map.get(td, {})
        fpr = factor_pro_map.get(td, {})

        bar: dict[str, Any] = {
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "volume": _f(row.get("vol")),
            "amount": _f(row.get("amount")) * 1000,  # 千元 → 元
            "pct_chg": _f(row.get("pct_chg")),
            "turnover_rate": _f(bas.get("turnover_rate")) or _f(fac.get("turnover_rate")),
            "pe_ttm": _f(bas.get("pe_ttm")),
            "total_mv_yi": _f(bas.get("total_mv")) / 1e8 if bas.get("total_mv") is not None else 0.0,
            # 技术指标 (from stk_factor)
            "macd_dif": _f(fac.get("macd_dif")),
            "macd_dea": _f(fac.get("macd_dea")),
            "macd": _f(fac.get("macd")),
            "kdj_k": _f(fac.get("kdj_k")),
            "kdj_d": _f(fac.get("kdj_d")),
            "kdj_j": _f(fac.get("kdj_j")),
            "rsi_6": _f(fac.get("rsi_6")),
            "rsi_12": _f(fac.get("rsi_12")),
            "rsi_24": _f(fac.get("rsi_24")),
            "boll_upper": _f(fac.get("boll_upper")),
            "boll_mid": _f(fac.get("boll_mid")),
            "boll_lower": _f(fac.get("boll_lower")),
            "cci": _f(fac.get("cci")),
            # 扩展因子 (from stk_factor_pro)
            "adj_factor": _f(fpr.get("adj_factor")),
            "asi": _f(fpr.get("asi_bfq")),
            "asit": _f(fpr.get("asit_bfq")),
            "atr": _f(fpr.get("atr_bfq")),
            "bbi": _f(fpr.get("bbi_bfq")),
            "brar_ar": _f(fpr.get("brar_ar_bfq")),
            "brar_br": _f(fpr.get("brar_br_bfq")),
            "dpo": _f(fpr.get("dpo_bfq")),
            "ema_12": _f(fpr.get("ema_12_bfq")),
            "ema_50": _f(fpr.get("ema_50_bfq")),
            "ma_volume_5": _f(fpr.get("ma_volume_5_bfq")),
            "ma_volume_10": _f(fpr.get("ma_volume_10_bfq")),
            "mtm": _f(fpr.get("mtm_bfq")),
            "obv": _f(fpr.get("obv_bfq")),
            "psy": _f(fpr.get("psy_bfq")),
            "trix": _f(fpr.get("trix_bfq")),
            "vr": _f(fpr.get("vr_bfq")),
            "wr_6": _f(fpr.get("wr_6_bfq")),
            "wr_10": _f(fpr.get("wr_10_bfq")),
            # 筹码分布 (from cyq_perf)
            "chip_cost_15": _f(chp.get("cost_15pct")),
            "chip_cost_50": _f(chp.get("cost_50pct")),
            "chip_cost_85": _f(chp.get("cost_85pct")),
            "chip_weight_avg": _f(chp.get("weight_avg")),
            "chip_winner_rate": _f(chp.get("winner_rate")),
            # 来源标记
            "source": "tushare",
        }
        bars.append(bar)
    return bars


# ═════════════════════════════════════════════════════════════════════════════
# 股票基础信息
# ═════════════════════════════════════════════════════════════════════════════


def fetch_stock_list() -> list[dict[str, Any]]:
    """获取全 A 股列表（含行业、地区、上市日期）。"""
    pro = get_pro()
    df = pro.stock_basic(exchange="", list_status="L", fields="symbol,name,industry,area,market,list_date")
    if df is None or df.empty:
        return []
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "code": str(row.get("symbol", "")).strip(),
            "name": str(row.get("name", "")).strip(),
            "industry": str(row.get("industry", "")).strip(),
            "area": str(row.get("area", "")).strip(),
            "market": str(row.get("market", "")).strip(),
            "list_date": _fmt_date(str(row.get("list_date", ""))),
        })
    return stocks


def fetch_stock_industry_map() -> dict[str, str]:
    """获取 code → industry 映射。"""
    pro = get_pro()
    df = pro.stock_basic(exchange="", list_status="L", fields="symbol,industry")
    if df is None or df.empty:
        return {}
    result: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row.get("symbol", "")).strip()
        ind = str(row.get("industry", "")).strip()
        if code and ind:
            result[code] = ind
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 资金流向
# ═════════════════════════════════════════════════════════════════════════════


def fetch_moneyflow(code: str, days: int = 10) -> dict[str, Any] | None:
    """获取个股资金流向（含累计数据）。"""
    pro = get_pro()
    tsc = ts_code(code)
    df = pro.moneyflow(ts_code=tsc, limit=days)
    if df is None or df.empty:
        return None

    df = df.sort_values("trade_date", ascending=False)
    latest = df.iloc[0]

    cum_3d = cum_5d = cum_10d = 0.0
    for i in range(min(len(df), 10)):
        row = df.iloc[i]
        net = float(row.get("net_mf_amount") or 0)
        if i < 3:
            cum_3d += net
        if i < 5:
            cum_5d += net
        cum_10d += net

    return {
        "code": code,
        "trade_date": str(latest.get("trade_date", "")),
        "main_net_inflow_yi": round(float(latest.get("net_mf_amount") or 0) / 1e8, 4),
        "super_large_net_inflow_yi": round(
            float(latest.get("buy_elg_amount") or 0) / 1e8
            - float(latest.get("sell_elg_amount") or 0) / 1e8,
            4,
        ),
        "large_net_inflow_yi": round(
            float(latest.get("buy_lg_amount") or 0) / 1e8
            - float(latest.get("sell_lg_amount") or 0) / 1e8,
            4,
        ),
        "medium_net_inflow_yi": round(
            float(latest.get("buy_md_amount") or 0) / 1e8
            - float(latest.get("sell_md_amount") or 0) / 1e8,
            4,
        ),
        "small_net_inflow_yi": round(
            float(latest.get("buy_sm_amount") or 0) / 1e8
            - float(latest.get("sell_sm_amount") or 0) / 1e8,
            4,
        ),
        "main_net_inflow_ratio": round(float(latest.get("net_mf_vol") or 0), 2),
        "cumulative_inflow_3d_yi": round(cum_3d / 1e8, 4),
        "cumulative_inflow_5d_yi": round(cum_5d / 1e8, 4),
        "cumulative_inflow_10d_yi": round(cum_10d / 1e8, 4),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 涨停板
# ═════════════════════════════════════════════════════════════════════════════


def fetch_limit_up_board(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取涨停板榜单。trade_date 格式 YYYYMMDD，默认最新交易日。"""
    pro = get_pro()
    td = trade_date or date.today().strftime("%Y%m%d")
    result: list[dict[str, Any]] = []
    for limit_type in ("U", "D"):
        try:
            df = pro.limit_list_d(trade_date=td, limit_type=limit_type)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                if len(code) != 6:
                    continue
                result.append({
                    "code": code,
                    "name": str(row.get("name", "")),
                    "trade_date": _fmt_date(str(row.get("trade_date", td))),
                    "pct_chg": _f(row.get("pct_chg")),
                    "limit_times": int(row.get("limit_times") or 0),
                    "limit_type": str(row.get("limit", "")),
                    "first_time": str(row.get("first_time", "")),
                    "last_time": str(row.get("last_time", "")),
                    "open_times": int(row.get("open_times") or 0),
                    "up_stat": str(row.get("up_stat", "")),
                    "limit_amount_yi": _f(row.get("limit_amount")) / 1e8 if row.get("limit_amount") is not None else 0.0,
                    "turnover_ratio": _f(row.get("turnover_ratio")),
                    "fd_amount": _f(row.get("fd_amount")) / 1e8 if row.get("fd_amount") is not None else 0.0,
                    "industry": str(row.get("industry", "")),
                })
        except Exception:
            pass
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 龙虎榜
# ═════════════════════════════════════════════════════════════════════════════


def fetch_dragon_tiger(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取龙虎榜数据。"""
    pro = get_pro()
    td = trade_date or date.today().strftime("%Y%m%d")
    try:
        df = pro.top_list(trade_date=td)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "name": str(row.get("name", "")),
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "reason": str(row.get("reason", "")),
            "buy_amount_yi": round(_f(row.get("l_buy")) / 1e8, 4) if row.get("l_buy") is not None else None,
            "sell_amount_yi": round(_f(row.get("l_sell")) / 1e8, 4) if row.get("l_sell") is not None else None,
            "net_amount_yi": round(_f(row.get("net_amount")) / 1e8, 4) if row.get("net_amount") is not None else None,
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 概念板块
# ═════════════════════════════════════════════════════════════════════════════


def fetch_concept_sectors() -> list[dict[str, Any]]:
    """获取同花顺概念板块列表（含成分股数量）。"""
    pro = get_pro()
    try:
        df = pro.ths_index()
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "sector_code": str(row.get("ts_code", "")),
            "sector_name": str(row.get("name", "")),
            "member_count": int(_f(row.get("count"))),
            "exchange": str(row.get("exchange", "")),
            "list_date": _fmt_date(str(row.get("list_date", ""))),
            "type": str(row.get("type", "")),
        })
    return result


def fetch_concept_members(sector_code: str, trade_date: str = "") -> list[dict[str, Any]]:
    """获取指定概念板块的成分股列表。"""
    pro = get_pro()
    try:
        df = pro.ths_member(ts_code=sector_code)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if not trade_date:
        from datetime import date as _date, timedelta
        td = _date.today()
        if td.weekday() == 5:
            td = td - timedelta(days=1)
        elif td.weekday() == 6:
            td = td - timedelta(days=2)
        trade_date = td.isoformat()
    result = []
    for _, row in df.iterrows():
        code = str(row.get("con_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "sector_code": sector_code,
            "code": code,
            "name": str(row.get("con_name", "")),
            "trade_date": trade_date,
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 新增能力：融资融券、港股通、股东人数、涨跌停价、停复牌
# ═════════════════════════════════════════════════════════════════════════════


def fetch_margin(code: str, days: int = 5) -> list[dict[str, Any]]:
    """获取个股融资融券余额。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.margin(ts_code=tsc, limit=days)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": str(row.get("trade_date", "")),
            "rzye": _f(row.get("rzye")) / 1e8,  # 融资余额(亿)
            "rqye": _f(row.get("rqye")) / 1e8,  # 融券余额(亿)
            "rzmre": _f(row.get("rzmre")) / 1e8,  # 融资买入额(亿)
            "rzche": _f(row.get("rzche")) / 1e8,  # 融资偿还额(亿)
            "rzrqye": _f(row.get("rzrqye")) / 1e8,  # 融资融券余额(亿)
        })
    return result


def fetch_hk_hold(code: str) -> dict[str, Any] | None:
    """获取港股通持股（最新一条）。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.hk_hold(ts_code=tsc, limit=1)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "code": code,
        "trade_date": str(row.get("trade_date", "")),
        "vol": _f(row.get("vol")),  # 持股数量(股)
        "ratio": _f(row.get("ratio")),  # 持股比例(%)
    }


def fetch_holder_number(code: str, limit: int = 5) -> list[dict[str, Any]]:
    """获取股东人数变化。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.stk_holdernumber(ts_code=tsc, limit=limit)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "end_date": _fmt_date(str(row.get("end_date", ""))),
            "holder_num": int(row.get("holder_num") or 0),
        })
    return result


def fetch_stk_limit(code: str, days: int = 5) -> list[dict[str, Any]]:
    """获取每日涨跌停价格。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.stk_limit(ts_code=tsc, limit=days)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": str(row.get("trade_date", "")),
            "up_limit": _f(row.get("up_limit")),
            "down_limit": _f(row.get("down_limit")),
        })
    return result


def fetch_suspend(code: str) -> dict[str, Any] | None:
    """获取最新停复牌信息。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.suspend(ts_code=tsc, limit=1)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "code": code,
        "suspend_date": _fmt_date(str(row.get("suspend_date", ""))),
        "resume_date": _fmt_date(str(row.get("resume_date", ""))),
        "suspend_reason": str(row.get("suspend_reason", "")),
    }


def fetch_top_inst(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取龙虎榜机构席位交易明细（top_inst 接口）。"""
    pro = get_pro()
    td = trade_date or date.today().strftime("%Y%m%d")
    try:
        df = pro.top_inst(trade_date=td)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "institution": str(row.get("exalter", "")),
            "side": str(row.get("side", "")),
            "buy_amount_yi": round(_f(row.get("buy")) / 1e8, 4) if row.get("buy") is not None else 0.0,
            "buy_rate": _f(row.get("buy_rate")),
            "sell_amount_yi": round(_f(row.get("sell")) / 1e8, 4) if row.get("sell") is not None else 0.0,
            "sell_rate": _f(row.get("sell_rate")),
            "net_buy_yi": round(_f(row.get("net_buy")) / 1e8, 4) if row.get("net_buy") is not None else 0.0,
            "reason": str(row.get("reason", "")),
        })
    return result


def fetch_latest_trade_date() -> str:
    """获取最新交易日。"""
    pro = get_pro()
    today = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today, is_open="1")
        if cal is not None and not cal.empty:
            return str(cal.sort_values("cal_date").iloc[-1]["cal_date"])
    except Exception:
        pass
    return today


# ═════════════════════════════════════════════════════════════════════════════
# 集合竞价
# ═════════════════════════════════════════════════════════════════════════════


def fetch_auction(code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取个股集合竞价数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    td = trade_date or date.today().strftime("%Y%m%d")
    try:
        df = pro.stk_auction(ts_code=tsc, trade_date=td)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "code": code,
        "trade_date": _fmt_date(str(row.get("trade_date", td))),
        "vol": _f(row.get("vol")),
        "price": _f(row.get("price")),
        "amount": _f(row.get("amount")),
        "pre_close": _f(row.get("pre_close")),
        "turnover_rate": _f(row.get("turnover_rate")),
        "volume_ratio": _f(row.get("volume_ratio")),
        "float_share": _f(row.get("float_share")),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 量化因子
# ═════════════════════════════════════════════════════════════════════════════


def fetch_factor_pro(code: str, days: int = 90) -> list[dict[str, Any]]:
    """获取个股量化因子（stk_factor_pro）。"""
    pro = get_pro()
    tsc = ts_code(code)
    sd = (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = date.today().strftime("%Y%m%d")
    try:
        df = pro.stk_factor_pro(ts_code=tsc, start_date=sd, end_date=ed)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    df = df.sort_values("trade_date")
    if days and len(df) > days:
        df = df.tail(days)
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", ""))),
            "adj_factor": _f(row.get("adj_factor")),
            "asi": _f(row.get("asi_bfq")),
            "asit": _f(row.get("asit_bfq")),
            "atr": _f(row.get("atr_bfq")),
            "bbi": _f(row.get("bbi_bfq")),
            "bias_6": _f(row.get("bias_6_bfq")),
            "bias_12": _f(row.get("bias_12_bfq")),
            "bias_24": _f(row.get("bias_24_bfq")),
            "brar_ar": _f(row.get("brar_ar_bfq")),
            "brar_br": _f(row.get("brar_br_bfq")),
            "dpo": _f(row.get("dpo_bfq")),
            "ema_12": _f(row.get("ema_12_bfq")),
            "ema_50": _f(row.get("ema_50_bfq")),
            "kdj_k": _f(row.get("kdj_k_bfq")),
            "kdj_d": _f(row.get("kdj_d_bfq")),
            "kdj_j": _f(row.get("kdj_j_bfq")),
            "ma_volume_5": _f(row.get("ma_volume_5_bfq")),
            "ma_volume_10": _f(row.get("ma_volume_10_bfq")),
            "macd_dif": _f(row.get("macd_dif_bfq")),
            "macd_dea": _f(row.get("macd_dea_bfq")),
            "macd": _f(row.get("macd_bfq")),
            "mtm": _f(row.get("mtm_bfq")),
            "obv": _f(row.get("obv_bfq")),
            "psy": _f(row.get("psy_bfq")),
            "rsi_6": _f(row.get("rsi_6_bfq")),
            "rsi_12": _f(row.get("rsi_12_bfq")),
            "rsi_24": _f(row.get("rsi_24_bfq")),
            "boll_upper": _f(row.get("boll_upper_bfq")),
            "boll_mid": _f(row.get("boll_mid_bfq")),
            "boll_lower": _f(row.get("boll_lower_bfq")),
            "trix": _f(row.get("trix_bfq")),
            "vr": _f(row.get("vr_bfq")),
            "wr_6": _f(row.get("wr_6_bfq")),
            "wr_10": _f(row.get("wr_10_bfq")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 增减持
# ═════════════════════════════════════════════════════════════════════════════


def fetch_holder_trade(code: str) -> list[dict[str, Any]]:
    """获取股东增减持数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.stk_holdertrade(ts_code=tsc)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "ann_date": _fmt_date(str(row.get("ann_date", ""))),
            "holder_name": str(row.get("holder_name", "")),
            "holder_type": str(row.get("holder_type", "")),
            "in_de": str(row.get("in_de", "")),
            "change_vol": _f(row.get("change_vol")),
            "change_ratio": _f(row.get("change_ratio")),
            "after_ratio": _f(row.get("after_ratio")),
            "avg_price": _f(row.get("avg_price")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 机构调研
# ═════════════════════════════════════════════════════════════════════════════


def fetch_survey(code: str) -> list[dict[str, Any]]:
    """获取机构调研记录。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.stk_surv(ts_code=tsc)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "name": str(row.get("name", "")),
            "surv_date": _fmt_date(str(row.get("surv_date", ""))),
            "fund_visitors": int(_f(row.get("fund_visitors"))),
            "rece_org": str(row.get("rece_org", "")),
            "org_type": str(row.get("org_type", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 回购
# ═════════════════════════════════════════════════════════════════════════════


def fetch_repurchase(code: str) -> list[dict[str, Any]]:
    """获取股票回购数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.repurchase(ts_code=tsc)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "ann_date": _fmt_date(str(row.get("ann_date", ""))),
            "end_date": _fmt_date(str(row.get("end_date", ""))),
            "vol": _f(row.get("vol")),
            "amount": _f(row.get("amount")),
            "high_limit": _f(row.get("high_limit")),
            "low_limit": _f(row.get("low_limit")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 质押统计
# ═════════════════════════════════════════════════════════════════════════════


def fetch_pledge_stat(code: str) -> dict[str, Any] | None:
    """获取股权质押统计。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.pledge_stat(ts_code=tsc)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    latest = df.sort_values("end_date").iloc[-1]
    return {
        "code": code,
        "end_date": _fmt_date(str(latest.get("end_date", ""))),
        "pledge_count": int(_f(latest.get("pledge_count"))),
        "unrest_pledge": _f(latest.get("unrest_pledge")),
        "rest_pledge": _f(latest.get("rest_pledge")),
        "pledge_ratio": _f(latest.get("pledge_ratio")),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 限售解禁
# ═════════════════════════════════════════════════════════════════════════════


def fetch_share_float(code: str) -> list[dict[str, Any]]:
    """获取限售股解禁数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    try:
        df = pro.share_float(ts_code=tsc)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "ann_date": _fmt_date(str(row.get("ann_date", ""))),
            "float_date": _fmt_date(str(row.get("float_date", ""))),
            "float_share": _f(row.get("float_share")),
            "float_ratio": _f(row.get("float_ratio")),
            "holder_name": str(row.get("holder_name", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 券商金股
# ═════════════════════════════════════════════════════════════════════════════


def fetch_broker_recommend(month: str | None = None) -> list[dict[str, Any]]:
    """获取券商金股推荐。"""
    pro = get_pro()
    m = month or date.today().strftime("%Y%m")
    try:
        df = pro.broker_recommend(month=m)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "code": code,
            "name": str(row.get("name", "")),
            "month": str(row.get("month", m)),
            "broker": str(row.get("broker", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 分钟K线（腾讯 ifzq API — Tushare stk_mins 需特殊权限）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_minute_kbars(code: str, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取个股1分钟K线数据（腾讯 ifzq.gtimg.cn）。"""
    import httpx

    market = "sh" if code.startswith(("6", "9")) else "sz"
    td = trade_date or date.today().strftime("%Y-%m-%d")

    url = f"https://ifzq.gtimg.cn/appstock/app/minute/query?_var=min_data&code={market}{code}"
    try:
        resp = httpx.get(url, timeout=10.0, headers={"Referer": "https://gu.qq.com/"})
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return []

    # 腾讯返回格式: min_data={...json...}
    if not text.startswith("min_data="):
        return []
    try:
        import json
        data = json.loads(text[len("min_data="):])
    except Exception:
        return []

    qt_key = f"{market}{code}"
    minute_data = data.get("data", {}).get(qt_key, {}).get("data", {}).get("data")
    if not minute_data:
        return []

    result = []
    for line in minute_data:
        parts = line.split()
        if len(parts) < 3:
            continue
        time_str = parts[0]  # HHMM
        price = float(parts[1])
        vol = float(parts[2])
        amount = float(parts[3]) if len(parts) >= 4 else 0.0
        # 格式化时间为 HH:MM:SS
        formatted_time = f"{time_str[:2]}:{time_str[2:]}:00"
        trade_time = f"{td} {formatted_time}"
        result.append({
            "code": code,
            "trade_date": td,
            "trade_time": trade_time,
            "open": price,
            "close": price,
            "high": price,
            "low": price,
            "volume": vol,
            "amount": amount,
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════


def _f(val: Any) -> float:
    """安全转 float，NaN → 0.0。"""
    import math
    if val is None:
        return 0.0
    try:
        v = float(val)
        return 0.0 if math.isnan(v) else v
    except (ValueError, TypeError):
        return 0.0


def _fmt_date(val: str) -> str:
    """统一日期格式 → YYYY-MM-DD。"""
    v = val.strip().replace("-", "")
    if len(v) == 8:
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return val
