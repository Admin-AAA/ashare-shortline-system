"""市场级波动率（QVIX）数据源 —— 无 token，直连 akshare（非东财主机，实测稳定）。

背景
----
2026-11-15 从 ``xiaoshi-local-sources`` 公开取数包评估接入。该包的 55 个入口绝大多数
是普通 akshare 函数，与系统既有的东财/新浪/巨潮同源，不构成新源；QVIX 是其中少数
**系统此前完全没有的数据域**：期权隐含波动率指数（"中国版 VIX"），是市场级恐慌/情绪
刻度，与短线在研究的情绪/恐慌信号（RSI6-capitulation、lu_ld 情绪过滤、涨跌停家数）契合。

实测（本机 akshare 1.19.1，2026-11-15）：
* ``index_option_50etf_qvix``    2015-02-09 起，末行 2026-11-13 ✅
* ``index_option_300etf_qvix``   2849 行，末行 2026-11-13 close=17.17 ✅
* ``index_option_300index_qvix`` 同族 ✅
数据落在 ``options.qvix`` 上游（非 push2his.eastmoney.com），与东财分钟那种反复重置的
主机不同，连接稳定。

落库
----
写 ``index_daily``（``IndexDaily``），用专属 ``ts_code`` 前缀 ``QVIX*``，与真实指数
（000300.SH / 000905.SH）唯一键不冲突；基准服务只按具体 ts_code 查询，不会被这些行干扰。
QVIX 无成交量，``volume`` 记 0；``pct_chg`` 由相邻收盘价计算（首日 0）。
上游对缺失日填 0（300ETF/300指数期权 2019-12-23 上市前整段为 0）：``close<=0``
的行在 ``fetch_qvix`` 即丢弃，不入库；QVIX50 自 2015-02-09 全段有效。上游偶有同日
重复行（盘中快照+收盘修正），按日期去重保留最后一条有效行，避免撞唯一键。

⚠️ 只提供数据，不自动进选股/评分。作为因子用于实盘需按 §282 预注册 + 生产同构回测
达标后再接，这里不做隐式接线。默认也不挂常驻定时任务（见 scripts/backfill_qvix.py）。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

DATASET = "market.qvix"
PROVIDER = "akshare_qvix"

# ts_code -> (akshare 函数名, 人类可读名)。ts_code 用 QVIX 前缀，避免与真实指数撞唯一键。
QVIX_SERIES: dict[str, tuple[str, str]] = {
    "QVIX50.SH": ("index_option_50etf_qvix", "50ETF 期权波动率"),
    "QVIX300.SH": ("index_option_300etf_qvix", "300ETF 期权波动率"),
    "QVIX300IDX.SH": ("index_option_300index_qvix", "沪深300指数期权波动率"),
}


def _ak():
    import akshare  # lazy: heavy import, optional dependency

    return akshare


def _f(value: Any) -> float:
    try:
        f = float(value)
        return f if f == f else 0.0  # NaN -> 0.0
    except (TypeError, ValueError):
        return 0.0


def _iso_date(value: Any) -> str:
    """QVIX 的 date 列是 datetime.date / 'YYYY-MM-DD...' 字符串；统一成 YYYY-MM-DD。"""
    if hasattr(value, "isoformat"):
        return str(value.isoformat())[:10]
    return str(value or "")[:10]


def fetch_qvix(ts_code: str) -> list[dict[str, Any]]:
    """取一条 QVIX 序列，升序返回 index_daily 口径的行（未入库）。

    经网关 ``gw_call`` 走一遍：拿到源总开关（data_source_config）、跨进程熔断、限速与
    dataset_requests/dataset_latency 指标，与其它数据源同一套可观测性。
    """
    spec = QVIX_SERIES.get(ts_code)
    if spec is None:
        raise KeyError(f"unknown QVIX ts_code {ts_code!r}; expected one of {sorted(QVIX_SERIES)}")
    fn_name = spec[0]

    from app.services.datasource.wrap import gw_call

    df = gw_call(DATASET, PROVIDER, lambda: getattr(_ak(), fn_name)())
    if df is None or getattr(df, "empty", True):
        return []
    # 上游两类脏数据（实测 2026-11-15）：
    # 1) 缺失日填 0：300ETF/300指数期权 2019-12-23 才上市，之前整段为 0，之后也有
    #    零星 0 值（QVIX300 2849 行中 1188 行 close=0）。零值不是真实波动率，丢弃。
    # 2) 同日重复行：QVIX50 的 2026-11-02 出现两行（盘中快照 + 收盘修正），直接入库
    #    会撞 uq_index_daily_code_date。按日期去重，保留最后一条有效行（收盘修正）。
    by_day: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        day = _iso_date(r.get("date"))
        if len(day) != 10:
            continue
        close = _f(r.get("close"))
        if close <= 0:
            continue
        by_day[day] = {
            "ts_code": ts_code, "trade_date": day,
            "open": _f(r.get("open")), "high": _f(r.get("high")), "low": _f(r.get("low")),
            "close": close, "pct_chg": 0.0, "volume": 0.0,
        }
    rows = sorted(by_day.values(), key=lambda x: x["trade_date"])
    prev_close: float | None = None
    for row in rows:
        if prev_close:
            row["pct_chg"] = round((row["close"] / prev_close - 1.0) * 100.0, 4)
        prev_close = row["close"]
    return rows


def ensure_qvix_daily(
    db: Any,
    ts_codes: Sequence[str] | None = None,
    *,
    since: str | None = None,
) -> dict[str, int]:
    """把 QVIX 序列增量写入 ``index_daily``（已存在的 (ts_code, trade_date) 跳过）。

    Parameters
    ----------
    db : Session
    ts_codes : 只补这些序列；默认全部 QVIX_SERIES。
    since : 'YYYY-MM-DD'，只写该日期（含）之后的行，用于日常增量。

    Returns {ts_code: written_count}
    """
    from sqlalchemy import select

    from app.models.tables import IndexDaily

    targets = list(ts_codes or QVIX_SERIES.keys())
    counts: dict[str, int] = {}
    for ts_code in targets:
        try:
            rows = fetch_qvix(ts_code)
        except Exception as exc:  # noqa: BLE001 - 单条序列失败不影响其它；不静默当成功
            logger.warning("ensure_qvix_daily: %s fetch failed: %r", ts_code, exc)
            counts[ts_code] = 0
            continue
        if since:
            rows = [r for r in rows if r["trade_date"] >= since]
        written = 0
        for r in rows:
            existing = db.execute(
                select(IndexDaily).where(
                    IndexDaily.ts_code == r["ts_code"], IndexDaily.trade_date == r["trade_date"]
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(IndexDaily(**r))
            written += 1
        db.commit()
        counts[ts_code] = written
    return counts


def latest_qvix(db: Any, ts_code: str = "QVIX300.SH") -> Mapping[str, Any] | None:
    """读一条 QVIX 序列的最新值（研究/展示用；返回 {trade_date, close, pct_chg}）。"""
    from sqlalchemy import select

    from app.models.tables import IndexDaily

    row = db.execute(
        select(IndexDaily).where(IndexDaily.ts_code == ts_code).order_by(IndexDaily.trade_date.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {"ts_code": ts_code, "trade_date": row.trade_date, "close": row.close, "pct_chg": row.pct_chg}
