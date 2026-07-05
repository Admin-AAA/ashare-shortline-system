"""
Tushare 稳定数据源客户端。

使用方式：
    from backend.app.services.tushare_client import get_pro
    pro = get_pro()
    df = pro.daily(ts_code="000001.SZ", limit=5)

代理服务器：https://fastapic.stockai888.top/
兼容 Tushare pro_api HTTP 协议（POST JSON: api_name/token/params/fields）。
"""

from __future__ import annotations

import functools
import json
import threading
import time
from collections import deque
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

_TOKEN = "eqyrJ71eIKjLoM12OeDLQOwRMUTuETs2ItnXAJitOhjTE8sLIjm8UuihObjXEs3zOFDKUI1PMrTmMp1yO9Tjl19"
_CUSTOM_URL = "https://fastapic.stockai888.top/"

# ═════════════════════════════════════════════════════════════════════════════
# 全局令牌桶限速器（代理服务器每分钟100次上限）
# ═════════════════════════════════════════════════════════════════════════════

_RATE_LIMIT_LOCK = threading.Lock()
_RATE_TIMESTAMPS: deque[float] = deque()
_RATE_LIMIT_PER_MINUTE = 90  # 留 10 次 buffer，避免边界 429


def _rate_limit_wait() -> None:
    """令牌桶：确保最近 60s 内请求数不超过 _RATE_LIMIT_PER_MINUTE。"""
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        # 清理 60s 前的时间戳
        while _RATE_TIMESTAMPS and _RATE_TIMESTAMPS[0] < now - 60:
            _RATE_TIMESTAMPS.popleft()
        if len(_RATE_TIMESTAMPS) >= _RATE_LIMIT_PER_MINUTE:
            # 需要等到最老的时间戳过期
            wait = _RATE_TIMESTAMPS[0] + 60 - now + 0.1
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
                while _RATE_TIMESTAMPS and _RATE_TIMESTAMPS[0] < now - 60:
                    _RATE_TIMESTAMPS.popleft()
        _RATE_TIMESTAMPS.append(now)


# ═════════════════════════════════════════════════════════════════════════════
# 客户端 — requests.Session 直连，杜绝 tushare 库的 TCP 悬挂问题
# ═════════════════════════════════════════════════════════════════════════════


class _TushareSession:
    """Tushare pro_api 兼容接口，底层用 requests.Session 池化连接。"""

    def __init__(self, token: str, timeout: tuple[float, float] = (5, 120)):
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
            _rate_limit_wait()
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
    pro = _TushareSession(_TOKEN, timeout=(5, 120))
    pro._DataApi__http_url = _CUSTOM_URL
    return pro


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


def _normalize_trade_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]


def _compact_trade_date(value: Any) -> str:
    return _normalize_trade_date(value).replace("-", "")


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


def fetch_basic_daily_kbars(
    code: str,
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """获取一只股票的基础日K，用于交互式快速补齐本地池历史缺口。"""
    pro = get_pro()
    tsc = ts_code(code)
    sd = start_date or (date.today() - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")

    daily = pro.daily(ts_code=tsc, start_date=sd, end_date=ed)
    if daily is None or daily.empty:
        return []
    daily = daily.sort_values("trade_date")
    if start_date is None:
        daily = daily.tail(days)

    bars: list[dict[str, Any]] = []
    for _, row in daily.iterrows():
        bars.append(
            {
                "code": code,
                "trade_date": _fmt_date(str(row.get("trade_date") or "")),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("vol")),
                "amount": _f(row.get("amount")) * 1000,
                "pct_chg": _f(row.get("pct_chg")),
                "turnover_rate": 0.0,
                "source": "tushare",
            }
        )
    return bars


def fetch_daily_kbars_for_trade_date(trade_date: str) -> list[dict[str, Any]]:
    """按单个交易日批量获取全市场日K，用于数据缺口修复。"""
    target = _compact_trade_date(trade_date)
    if not target:
        return []

    pro = get_pro()
    daily = pro.daily(trade_date=target)
    if daily is None or daily.empty:
        return []

    basic_map: dict[str, dict[str, Any]] = {}
    try:
        basic = pro.daily_basic(
            trade_date=target,
            fields="ts_code,trade_date,turnover_rate,pe_ttm,total_mv",
        )
        if basic is not None and not basic.empty:
            for _, row in basic.iterrows():
                basic_map[str(row.get("ts_code") or "")] = dict(row)
    except Exception:
        pass

    factor_map: dict[str, dict[str, Any]] = {}
    try:
        factor = pro.stk_factor(trade_date=target)
        if factor is not None and not factor.empty:
            for _, row in factor.iterrows():
                factor_map[str(row.get("ts_code") or "")] = dict(row)
    except Exception:
        pass

    bars: list[dict[str, Any]] = []
    for _, row in daily.iterrows():
        raw_ts_code = str(row.get("ts_code") or "")
        code = raw_ts_code.split(".")[0].strip()
        if not code:
            continue
        fac = factor_map.get(raw_ts_code, {})
        bas = basic_map.get(raw_ts_code, {})
        bars.append(
            {
                "code": code,
                "trade_date": _fmt_date(str(row.get("trade_date") or target)),
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "volume": _f(row.get("vol")),
                "amount": _f(row.get("amount")) * 1000,
                "pct_chg": _f(row.get("pct_chg")),
                "turnover_rate": _f(bas.get("turnover_rate")) or _f(fac.get("turnover_rate")),
                "pe_ttm": _f(bas.get("pe_ttm")),
                "total_mv_yi": _f(bas.get("total_mv")) / 1e8 if bas.get("total_mv") is not None else 0.0,
                "ma5": _f(fac.get("ma5")),
                "ma10": _f(fac.get("ma10")),
                "ma20": _f(fac.get("ma20")),
                "ma60": _f(fac.get("ma60")),
                "ma250": _f(fac.get("ma250")),
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
                "adj_factor": _f(fac.get("adj_factor")),
                "source": "tushare",
            }
        )
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


def fetch_moneyflow(code: str, days: int = 10, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取个股资金流向（含累计数据）。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    if target:
        start = (date.fromisoformat(_normalize_trade_date(target)) - timedelta(days=max(days * 3, 30))).strftime("%Y%m%d")
        try:
            df = pro.moneyflow(ts_code=tsc, start_date=start, end_date=target)
        except Exception:
            df = pro.moneyflow(ts_code=tsc, trade_date=target)
    else:
        df = pro.moneyflow(ts_code=tsc, limit=days)
    if df is None or df.empty:
        return None

    df = df.sort_values("trade_date", ascending=False)
    if target:
        df = df[df["trade_date"].astype(str) <= target]
        if df.empty or str(df.iloc[0].get("trade_date", "")) != target:
            return None
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
        "trade_date": _normalize_trade_date(latest.get("trade_date", target)),
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
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    result: list[dict[str, Any]] = []
    errors: list[str] = []
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
                    "limit_type": limit_type,  # 循环已按 U/D 调 API，直接用循环变量（row.get("limit") 字段名不可靠）
                    "first_time": str(row.get("first_time", "")),
                    "last_time": str(row.get("last_time", "")),
                    "open_times": int(row.get("open_times") or 0),
                    "up_stat": str(row.get("up_stat", "")),
                    "limit_amount_yi": _f(row.get("limit_amount")) / 1e8 if row.get("limit_amount") is not None else 0.0,
                    "turnover_ratio": _f(row.get("turnover_ratio")),
                    "fd_amount": _f(row.get("fd_amount")) / 1e8 if row.get("fd_amount") is not None else 0.0,
                    "industry": str(row.get("industry", "")),
                })
        except Exception as exc:
            errors.append(f"{limit_type}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(f"limit_list_d({td}) failed: {'; '.join(errors)}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 龙虎榜
# ═════════════════════════════════════════════════════════════════════════════


def fetch_dragon_tiger(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取龙虎榜数据。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
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


def fetch_margin(code: str, days: int = 5, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取个股融资融券余额。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            start = (date.fromisoformat(_normalize_trade_date(target)) - timedelta(days=max(days * 3, 30))).strftime("%Y%m%d")
            df = pro.margin(ts_code=tsc, start_date=start, end_date=target)
        else:
            df = pro.margin(ts_code=tsc, limit=days)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if target:
        df = df[df["trade_date"].astype(str) <= target]
        if df.empty:
            return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": _normalize_trade_date(row.get("trade_date", target)),
            "rzye": _f(row.get("rzye")) / 1e8,  # 融资余额(亿)
            "rqye": _f(row.get("rqye")) / 1e8,  # 融券余额(亿)
            "rzmre": _f(row.get("rzmre")) / 1e8,  # 融资买入额(亿)
            "rzche": _f(row.get("rzche")) / 1e8,  # 融资偿还额(亿)
            "rzrqye": _f(row.get("rzrqye")) / 1e8,  # 融资融券余额(亿)
        })
    return result


def fetch_hk_hold(code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取港股通持股（最新一条）。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            try:
                df = pro.hk_hold(ts_code=tsc, trade_date=target)
            except Exception:
                df = pro.hk_hold(ts_code=tsc, limit=1)
        else:
            df = pro.hk_hold(ts_code=tsc, limit=1)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if target:
        df = df[df["trade_date"].astype(str) <= target]
        if df.empty:
            return None
        df = df.sort_values("trade_date", ascending=False)
    row = df.iloc[0]
    return {
        "code": code,
        "trade_date": _normalize_trade_date(row.get("trade_date", target)),
        "vol": _f(row.get("vol")),  # 持股数量(股)
        "ratio": _f(row.get("ratio")),  # 持股比例(%)
    }


def fetch_holder_number(code: str, limit: int = 5, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取股东人数变化。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            df = pro.stk_holdernumber(ts_code=tsc, end_date=target, limit=limit)
        else:
            df = pro.stk_holdernumber(ts_code=tsc, limit=limit)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if target:
        df = df[df["end_date"].astype(str) <= target]
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "end_date": _fmt_date(str(row.get("end_date", ""))),
            "holder_num": int(row.get("holder_num") or 0),
        })
    return result


def fetch_stk_limit(code: str, days: int = 5, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取每日涨跌停价格。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        if target:
            start = (date.fromisoformat(_normalize_trade_date(target)) - timedelta(days=max(days * 3, 30))).strftime("%Y%m%d")
            df = pro.stk_limit(ts_code=tsc, start_date=start, end_date=target)
        else:
            df = pro.stk_limit(ts_code=tsc, limit=days)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if target:
        df = df[df["trade_date"].astype(str) <= target]
    result = []
    for _, row in df.iterrows():
        result.append({
            "code": code,
            "trade_date": _normalize_trade_date(row.get("trade_date", target)),
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
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
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
# 指数日线（用于回测基准对比）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_index_daily(
    ts_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """获取指数日线行情。

    Parameters
    ----------
    ts_code : str, optional
        Tushare 指数代码（如 "000300.SH"）。默认沪深300。
    start_date, end_date : str, optional
        YYYYMMDD 格式。未指定则查最近 60 个自然日。

    Returns
    -------
    list[dict] 每行含 ts_code, trade_date, open, high, low, close, pct_chg, volume
    """
    pro = get_pro()
    target = ts_code or "000300.SH"
    sd = start_date or (date.today() - timedelta(days=90)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")
    try:
        df = pro.index_daily(ts_code=target, start_date=sd, end_date=ed)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        trade_date_raw = str(row.get("trade_date") or "")
        if len(trade_date_raw) == 8:
            trade_date = f"{trade_date_raw[:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:]}"
        else:
            trade_date = trade_date_raw
        result.append({
            "ts_code": str(row.get("ts_code") or target),
            "trade_date": trade_date,
            "close": _f(row.get("close")),
            "pct_chg": _f(row.get("pct_chg")),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "volume": _f(row.get("volume")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 集合竞价
# ═════════════════════════════════════════════════════════════════════════════


def fetch_auction(code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    """获取个股集合竞价数据。"""
    pro = get_pro()
    tsc = ts_code(code)
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
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


def fetch_factor_pro(code: str, days: int = 90, trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取个股量化因子（stk_factor_pro）。"""
    pro = get_pro()
    tsc = ts_code(code)
    target = _normalize_trade_date(trade_date)
    end_day = date.fromisoformat(target) if target else date.today()
    sd = (end_day - timedelta(days=days * 3)).strftime("%Y%m%d")
    ed = end_day.strftime("%Y%m%d")
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
# 分钟K线（Tushare stk_mins）
# ═════════════════════════════════════════════════════════════════════════════


_TUSHARE_STK_MINS_PERMISSION_DENIED_REASON: str | None = None


def fetch_minute_kbars(
    code: str,
    trade_date: str | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """获取个股 1 分钟 K 线。仅使用 Tushare stk_mins。"""
    target_trade_date = _normalize_trade_date(trade_date)
    if target_trade_date:
        rows = _fetch_tushare_minute_kbars(
            code,
            start_time=f"{target_trade_date} 09:00:00",
            end_time=f"{target_trade_date} 15:30:00",
            trace=trace,
        )
        return [row for row in rows if row["trade_date"] == target_trade_date]

    rows = fetch_recent_minute_kbars(code, trace=trace)
    latest_trade_date = max((row["trade_date"] for row in rows), default="")
    return [row for row in rows if row["trade_date"] == latest_trade_date]


def fetch_recent_minute_kbars(
    code: str,
    ndays: int = 5,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """获取个股近 N 个交易日的 1 分钟数据。仅使用 Tushare stk_mins。"""
    end_day = date.today()
    start_day = end_day - timedelta(days=max(10, int(ndays or 5) * 3))
    tushare_rows = _fetch_tushare_minute_kbars(
        code,
        start_time=f"{start_day.isoformat()} 09:00:00",
        end_time=f"{end_day.isoformat()} 15:30:00",
        trace=trace,
    )
    if tushare_rows:
        return _trim_recent_trade_dates(tushare_rows, ndays)
    return []


def fetch_recent_index_minute_kbars(
    index_code: str,
    ndays: int = 5,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """获取指数近 N 个交易日的 1 分钟数据。仅使用 Tushare stk_mins。"""
    end_day = date.today()
    start_day = end_day - timedelta(days=max(10, int(ndays or 5) * 3))
    tushare_rows = _fetch_tushare_minute_kbars(
        index_code,
        start_time=f"{start_day.isoformat()} 09:00:00",
        end_time=f"{end_day.isoformat()} 15:30:00",
        trace=trace,
        is_index=True,
    )
    if tushare_rows:
        return _trim_recent_trade_dates(tushare_rows, ndays)
    return []


def fetch_index_minute_kbars(
    index_code: str,
    start_date: str,
    end_date: str,
    trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按日期区间获取指数 1 分钟 K 线。仅使用 Tushare stk_mins。"""
    start_day = _normalize_trade_date(start_date)
    end_day = _normalize_trade_date(end_date)
    if not start_day or not end_day:
        return []
    rows = _fetch_tushare_minute_kbars(
        index_code,
        start_time=f"{start_day} 09:00:00",
        end_time=f"{end_day} 15:30:00",
        trace=trace,
        is_index=True,
    )
    return [
        row for row in rows
        if start_day <= row.get("trade_date", "") <= end_day
    ]


def _fetch_tushare_minute_kbars(
    code: str,
    *,
    start_time: str,
    end_time: str,
    trace: list[dict[str, Any]] | None = None,
    is_index: bool = False,
) -> list[dict[str, Any]]:
    global _TUSHARE_STK_MINS_PERMISSION_DENIED_REASON
    if _TUSHARE_STK_MINS_PERMISSION_DENIED_REASON:
        _record_minute_source_trace(
            trace,
            source="tushare_stk_mins",
            code=code,
            status="skipped",
            row_count=0,
            reason=_TUSHARE_STK_MINS_PERMISSION_DENIED_REASON,
        )
        return []
    try:
        df = get_pro().stk_mins(
            ts_code=_minute_ts_code(code, is_index=is_index),
            freq="1min",
            start_date=start_time,
            end_date=end_time,
        )
    except Exception as exc:
        reason = str(exc)
        status = "permission_denied" if _is_tushare_permission_error(reason) else "error"
        if status == "permission_denied":
            _TUSHARE_STK_MINS_PERMISSION_DENIED_REASON = reason
        _record_minute_source_trace(
            trace,
            source="tushare_stk_mins",
            code=code,
            status=status,
            row_count=0,
            reason=reason,
        )
        return []
    if df is None or df.empty:
        _record_minute_source_trace(
            trace,
            source="tushare_stk_mins",
            code=code,
            status="empty",
            row_count=0,
        )
        return []

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        trade_time = str(row.get("trade_time") or row.get("datetime") or row.get("date") or "").strip()
        trade_date = _normalize_trade_date(trade_time)
        if not trade_time or not trade_date:
            continue
        close_price = _f(row.get("close"))
        open_price = _f(row.get("open"), close_price)
        high_price = _f(row.get("high"), max(open_price, close_price))
        low_price = _f(row.get("low"), min(open_price, close_price))
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "trade_time": trade_time if len(trade_time) >= 19 else f"{trade_time}:00",
                "open": round(open_price, 3),
                "close": round(close_price, 3),
                "high": round(max(high_price, open_price, close_price), 3),
                "low": round(min(v for v in (low_price, open_price, close_price) if v > 0) if any(v > 0 for v in (low_price, open_price, close_price)) else close_price, 3),
                "volume": _f(row.get("vol") if "vol" in row else row.get("volume")),
                "amount": _f(row.get("amount")),
                "source": "tushare_stk_mins",
            }
        )
    rows = _attach_pre_close(rows)
    _record_minute_source_trace(
        trace,
        source="tushare_stk_mins",
        code=code,
        status="success" if rows else "empty",
        row_count=len(rows),
    )
    return rows


def _record_minute_source_trace(
    trace: list[dict[str, Any]] | None,
    *,
    source: str,
    code: str,
    status: str,
    row_count: int,
    reason: str | None = None,
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "source": source,
            "code": code,
            "status": status,
            "row_count": int(row_count or 0),
            "reason": (reason or "")[:160],
        }
    )


def _is_tushare_permission_error(reason: str) -> bool:
    return "没有该接口权限" in reason or "接口权限" in reason


def _minute_ts_code(code: str, *, is_index: bool = False) -> str:
    if "." in code:
        return code
    if is_index:
        return f"{code}.SZ" if code.startswith("399") else f"{code}.SH"
    return ts_code(code)


def _trade_dates(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("trade_date") or "") for row in rows if row.get("trade_date")})


def _trim_recent_trade_dates(rows: list[dict[str, Any]], ndays: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    recent_dates = _trade_dates(rows)[-max(1, int(ndays or 1)) :]
    recent_set = set(recent_dates)
    return [row for row in rows if str(row.get("trade_date") or "") in recent_set]


def _attach_pre_close(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda item: str(item["trade_time"]))
    grouped_dates = sorted({str(row["trade_date"]) for row in ordered})
    prev_close_map: dict[str, float] = {}
    for index, trade_date in enumerate(grouped_dates):
        if index == 0:
            first_row = next((row for row in ordered if row["trade_date"] == trade_date), None)
            prev_close_map[trade_date] = _f((first_row or {}).get("close"))
            continue
        previous_date = grouped_dates[index - 1]
        last_close = next(
            (row["close"] for row in reversed(ordered) if row["trade_date"] == previous_date),
            0.0,
        )
        prev_close_map[trade_date] = _f(last_close)
    return [
        {
            **row,
            "pre_close": round(prev_close_map.get(str(row["trade_date"]), _f(row.get("close"))), 3),
        }
        for row in ordered
    ]


# ═════════════════════════════════════════════════════════════════════════════
# 融资融券明细（全市场批量）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_margin_detail_batch(trade_date: str | None = None) -> list[dict[str, Any]]:
    """按交易日批量获取全市场融资融券明细（margin_detail 接口）。

    比逐股 margin 更高效，一次拉 4000+ 条，适合日常同步。
    """
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.margin_detail(trade_date=td)
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
            "rzye": _f(row.get("rzye")) / 1e8,         # 融资余额(亿)
            "rqye": _f(row.get("rqye")) / 1e8,         # 融券余额(亿)
            "rzmre": _f(row.get("rzmre")) / 1e8,        # 融资买入额(亿)
            "rzche": _f(row.get("rzche")) / 1e8,        # 融资偿还额(亿)
            "rzrqye": _f(row.get("rzrqye")) / 1e8,      # 融资融券余额(亿)
            "rqyl": _f(row.get("rqyl")),                 # 融券余量(股)
            "rqmcl": _f(row.get("rqmcl")),               # 融券卖出量(股)
            "rzrqye_md": _f(row.get("rzrqye")) / 1e8,   # 融资融券余额(亿,同字段)
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 大宗交易
# ═════════════════════════════════════════════════════════════════════════════


def fetch_block_trade(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取大宗交易数据。折价率=机构态度，大宗交易量=大资金动向。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.block_trade(trade_date=td)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        code = str(row.get("ts_code", "")).split(".")[0]
        if len(code) != 6:
            continue
        price = _f(row.get("price"))
        vol = _f(row.get("vol"))
        amount = _f(row.get("amount"))
        result.append({
            "code": code,
            "trade_date": _fmt_date(str(row.get("trade_date", td))),
            "price": price,
            "vol": vol,                     # 成交量(万股)
            "amount": amount,               # 成交额(万元)
            "amount_yi": amount / 1e4,       # 成交额(亿)
            "buyer": str(row.get("buyer", "")),
            "seller": str(row.get("seller", "")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 北向资金
# ═════════════════════════════════════════════════════════════════════════════


def fetch_hsgt_flow(trade_date: str | None = None) -> dict[str, Any] | None:
    """获取沪深港通资金流向（全市场汇总，1条/天）。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.moneyflow_hsgt(trade_date=td)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "trade_date": _fmt_date(str(row.get("trade_date", td))),
        "north_money": _f(row.get("north_money")) / 1e4,  # 北向资金(亿)
        "south_money": _f(row.get("south_money")) / 1e4,  # 南向资金(亿)
        "hgt": _f(row.get("hgt")),                        # 沪股通(万元)
        "sgt": _f(row.get("sgt")),                        # 深股通(万元)
        "ggt_ss": _f(row.get("ggt_ss")),                  # 港股通(沪)(万元)
        "ggt_sz": _f(row.get("ggt_sz")),                  # 港股通(深)(万元)
    }


def fetch_hsgt_top10(trade_date: str | None = None) -> list[dict[str, Any]]:
    """获取北向持股 TOP10。"""
    pro = get_pro()
    td = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.hsgt_top10(trade_date=td)
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
            "name": str(row.get("name", "")),
            "close": _f(row.get("close")),
            "change": _f(row.get("change")),
            "rank": int(_f(row.get("rank"))),
            "market_type": str(row.get("market_type", "")),
            "amount": _f(row.get("amount")),           # 持股数量(股)
            "net_amount": _f(row.get("net_amount")),   # 持股变化(股)
            "buy": _f(row.get("buy")),                 # 买入(股)
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 财务指标（季报）
# ═════════════════════════════════════════════════════════════════════════════


def fetch_fina_indicator(
    code: str,
    limit: int = 4,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """获取个股财务指标（fina_indicator 接口）。

    缺失字段保留为 None（不伪装成 0），由消费端区分"无数据"和"真实 0"。

    Parameters
    ----------
    code : str
        6 位股票代码。
    limit : int
        返回最近几期报告，默认 4（即最近一年）。
    trade_date : str, optional
        YYYY-MM-DD 或 YYYYMMDD。指定时只返回该日之前（含）的报告期。
    """
    pro = get_pro()
    tsc = ts_code(code)
    target = _compact_trade_date(trade_date)
    try:
        df = pro.fina_indicator(ts_code=tsc, limit=limit)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    if target:
        if "ann_date" in df.columns:
            df = df[df["ann_date"].astype(str) <= target]
        elif "end_date" in df.columns:
            df = df[df["end_date"].astype(str) <= target]
    result: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        end_raw = str(row.get("end_date", ""))
        ann_raw = str(row.get("ann_date", ""))
        result.append({
            "code": code,
            "end_date": end_raw,
            "ann_date": ann_raw,
            "roe": _safe_float(row.get("roe")),
            "grossprofit_margin": _safe_float(row.get("grossprofit_margin")),
            "netprofit_margin": _safe_float(row.get("netprofit_margin")),
            "netprofit_yoy": _safe_float(row.get("netprofit_yoy")),
            "or_yoy": _safe_float(row.get("or_yoy")),
            "eps": _safe_float(row.get("eps")),
            "bps": _safe_float(row.get("bps")),
        })
    return result


def fetch_fina_indicator_batch(
    trade_date: str | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """按公告日批量获取全市场财务指标。

    适合 backfill 场景：一次拉取某日之前公告的所有财报数据，返回
    最近 limit 期的指标。缺失字段保留为 None。
    """
    pro = get_pro()
    target = _compact_trade_date(trade_date) or date.today().strftime("%Y%m%d")
    try:
        df = pro.fina_indicator(ann_date=target)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    result: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        ts = str(row.get("ts_code", ""))
        code = ts.split(".")[0]
        if len(code) != 6:
            continue
        end_raw = str(row.get("end_date", ""))
        ann_raw = str(row.get("ann_date", ""))
        result.append({
            "code": code,
            "end_date": end_raw,
            "ann_date": ann_raw,
            "roe": _safe_float(row.get("roe")),
            "grossprofit_margin": _safe_float(row.get("grossprofit_margin")),
            "netprofit_margin": _safe_float(row.get("netprofit_margin")),
            "netprofit_yoy": _safe_float(row.get("netprofit_yoy")),
            "or_yoy": _safe_float(row.get("or_yoy")),
            "eps": _safe_float(row.get("eps")),
            "bps": _safe_float(row.get("bps")),
        })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════


def _safe_float(val: Any) -> float | None:
    """安全转 float，NaN/空值保留为 None。"""
    import math
    if val is None or val == "":
        return None
    try:
        v = float(val)
        return None if math.isnan(v) else v
    except (ValueError, TypeError):
        return None


def _f(val: Any, default: float = 0.0) -> float:
    """安全转 float，NaN → 0.0。"""
    import math
    if val is None:
        return default
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (ValueError, TypeError):
        return default


def _fmt_date(val: str) -> str:
    """统一日期格式 → YYYY-MM-DD。"""
    v = val.strip().replace("-", "")
    if len(v) == 8:
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return val
