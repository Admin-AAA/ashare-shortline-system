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

import tushare as ts

_TOKEN = "f8b05f8ee54f468220f2569afe56b4c027f1d388d164899339d394ad"
_CUSTOM_URL = "http://101.35.233.113:8020/"

# ═════════════════════════════════════════════════════════════════════════════
# 客户端
# ═════════════════════════════════════════════════════════════════════════════


@functools.lru_cache(maxsize=1)
def get_pro() -> Any:
    """获取 Tushare pro_api 实例（单例缓存）。"""
    pro = ts.pro_api(_TOKEN)
    pro._DataApi__http_url = _CUSTOM_URL
    return pro


def clear_pro_cache() -> None:
    """清除缓存的 pro_api 实例。"""
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

    bars: list[dict[str, Any]] = []
    for td, row in daily_rows.items():
        fac = factor_map.get(td, {})
        bas = basic_map.get(td, {})
        chp = chip_map.get(td, {})

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


def fetch_concept_members(sector_code: str) -> list[dict[str, Any]]:
    """获取指定概念板块的成分股列表。"""
    pro = get_pro()
    try:
        df = pro.ths_member(ts_code=sector_code)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("con_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        result.append({
            "sector_code": sector_code,
            "code": code,
            "name": str(row.get("con_name", "")),
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
