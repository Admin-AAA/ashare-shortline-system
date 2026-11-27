"""用上交所 ETF 期权日行情按 CBOE VIX 方法计算 QVIX（optbbs 上游停更后的替代来源）。

背景：QVIX 原上游 1.optbbs.com/d/csv/d/k.csv（akshare index_option_*_qvix 读的就是它）
2026-11-19 07:00 之后不再更新，最后一行 2026-11-19 全为空，分钟文件也停在当天 9:30。
optbbs 的 QVIX 本身就是按 CBOE VIX 方法用 50ETF/300ETF 期权实时报价计算的，这里用
teajoin opt_daily（经数据网关 get_pro()）的结算价，按同一方法在日频上重算。

方法（CBOE VIX 白皮书）：
* 合约：标的期权全部在市合约（含分红调整后的非标准合约，报价都是每份标的的价格）；
* 期限：剩余自然日 >= 1 的最近两个到期月，按 30 天线性插值方差；
* 远期：|C-P| 最小的行权价 K*，F = K* + e^{RT}(C-P)；K0 = 不超过 F 的最大行权价；
* 方差：σ² = 2/T·Σ ΔK/K²·e^{RT}·Q(K) − (F/K0−1)²/T，K0 处取认购认沽均价；
* 无风险利率：SHIBOR 1 个月。

与 optbbs 原值的对照（output/qvix_probe/，2025-09-15 至 2026-11-18）：QVIX50 近半年
平均绝对误差 0.36 点、相关系数 0.965、五日涨幅相关系数 0.87；QVIX300 误差 0.53 点、
相关系数 0.92。到期周临近到期日时 optbbs 原值会出现盘中尖峰（如 2026-03-23 的 42.16），
日频结算价算不出这种尖峰，这是两者最大的差异来源。
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

# QVIX ts_code -> 上交所期权标的代码（opt_basic.opt_code）
QVIX_UNDERLYING: dict[str, str] = {
    "QVIX50.SH": "OP510050.SH",
    "QVIX300.SH": "OP510300.SH",
}
TARGET_DAYS = 30
MIN_DAYS = 1


def _ymd(value: Any) -> date:
    s = str(value).replace("-", "")[:8]
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def term_variance(quotes: Sequence[Mapping[str, Any]], t: float, r: float) -> float:
    """单个到期月的方差 σ²。quotes 每项含 exercise_price、call_put（C/P）、price。"""
    by_strike: dict[float, dict[str, float]] = {}
    for q in quotes:
        price = float(q["price"])
        if price > 0:
            by_strike.setdefault(float(q["exercise_price"]), {})[str(q["call_put"])] = price
    paired = {k: v for k, v in by_strike.items() if "C" in v and "P" in v}
    if not paired:
        raise ValueError("no strike has both call and put prices")
    k_star = min(paired, key=lambda k: abs(paired[k]["C"] - paired[k]["P"]))
    growth = math.exp(r * t)
    forward = k_star + growth * (paired[k_star]["C"] - paired[k_star]["P"])
    strikes = sorted(by_strike)
    below = [k for k in strikes if k <= forward]
    k0 = max(below) if below else strikes[0]
    otm: dict[float, float] = {}
    for k in strikes:
        side = by_strike[k]
        if k < k0 and "P" in side:
            otm[k] = side["P"]
        elif k > k0 and "C" in side:
            otm[k] = side["C"]
        elif k == k0:
            otm[k] = (side["C"] + side["P"]) / 2.0 if "C" in side and "P" in side else next(iter(side.values()))
    ks = sorted(otm)
    if len(ks) < 2:
        raise ValueError(f"only {len(ks)} out-of-money strikes")
    total = 0.0
    for i, k in enumerate(ks):
        if i == 0:
            dk = ks[1] - ks[0]
        elif i == len(ks) - 1:
            dk = ks[-1] - ks[-2]
        else:
            dk = (ks[i + 1] - ks[i - 1]) / 2.0
        total += dk / (k * k) * growth * otm[k]
    return 2.0 / t * total - (forward / k0 - 1.0) ** 2 / t


def vix_from_chain(quotes: Sequence[Mapping[str, Any]], trade_date: str, r: float) -> float:
    """一个标的当日全部期权报价 -> 30 天波动率指数（百分数点）。

    quotes 每项含 maturity_date、exercise_price、call_put、price。
    """
    d0 = _ymd(trade_date)
    by_expiry: dict[str, list[Mapping[str, Any]]] = {}
    for q in quotes:
        by_expiry.setdefault(str(q["maturity_date"]).replace("-", "")[:8], []).append(q)
    days = {e: (_ymd(e) - d0).days for e in by_expiry}
    terms = sorted((e for e in days if days[e] >= MIN_DAYS), key=days.get)[:2]
    if len(terms) < 2:
        raise ValueError(f"{trade_date}: need two expiries with >= {MIN_DAYS} days, got {terms}")
    n1, n2 = days[terms[0]], days[terms[1]]
    t1, t2 = n1 / 365.0, n2 / 365.0
    s1 = term_variance(by_expiry[terms[0]], t1, r)
    if n1 >= TARGET_DAYS:
        return 100.0 * math.sqrt(s1)
    s2 = term_variance(by_expiry[terms[1]], t2, r)
    var = (t1 * s1 * (n2 - TARGET_DAYS) / (n2 - n1) + t2 * s2 * (TARGET_DAYS - n1) / (n2 - n1)) * 365.0 / TARGET_DAYS
    return 100.0 * math.sqrt(var)


def compute_qvix_rows(ts_codes: Sequence[str], trade_dates: Sequence[str]) -> list[dict[str, Any]]:
    """经数据网关取 opt_basic / opt_daily / shibor，计算指定交易日的 QVIX，返回 index_daily 口径的行。

    trade_dates 为 YYYY-MM-DD。当日 opt_daily 为空（上游还没出数）时该日不返回，由下次日更补上。
    open/high/low 与 close 相同（日频只算得出收盘值），pct_chg 由调用方按相邻收盘重算。
    """
    from app.services.tushare_client import get_pro

    if not trade_dates:
        return []
    pro = get_pro()
    basic = pro.opt_basic(exchange="SSE")
    codes = {QVIX_UNDERLYING[c] for c in ts_codes}
    basic = basic[basic["opt_code"].isin(codes)][
        ["ts_code", "opt_code", "call_put", "exercise_price", "maturity_date"]]
    start = min(trade_dates).replace("-", "")
    end = max(trade_dates).replace("-", "")
    shibor_df = pro.shibor(start_date=f"{int(start[:4]) - 1}{start[4:]}", end_date=end)
    shibor = {str(row["date"]): float(row["1m"]) / 100.0 for _, row in shibor_df.iterrows()}
    rows: list[dict[str, Any]] = []
    for day in sorted(trade_dates):
        ymd = day.replace("-", "")
        daily = pro.opt_daily(trade_date=ymd, exchange="SSE")
        if daily is None or daily.empty:
            continue
        r = shibor[max(k for k in shibor if k <= ymd)]
        merged = daily[["ts_code", "settle"]].merge(basic, on="ts_code")
        for ts_code in ts_codes:
            chain = merged[merged["opt_code"] == QVIX_UNDERLYING[ts_code]]
            quotes = [
                {"maturity_date": m, "exercise_price": k, "call_put": cp, "price": p}
                for m, k, cp, p in zip(chain["maturity_date"], chain["exercise_price"],
                                       chain["call_put"], chain["settle"], strict=True)
                if p == p  # 去掉 NaN
            ]
            close = round(vix_from_chain(quotes, ymd, r), 2)
            rows.append({
                "ts_code": ts_code, "trade_date": day, "open": close, "high": close, "low": close,
                "close": close, "pct_chg": 0.0, "volume": 0.0,
            })
    return rows
