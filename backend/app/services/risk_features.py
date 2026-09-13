"""横截面风险特征：个股特质波动 ivol20 及其当日全市场分位（L-093）。

定义（严格 T 收盘可得、无前视）：
- 个股日收益 r_t = pct_chg/100；市场收益 m_t = 中证500(000905.SH) pct_chg/100，按交易日对齐。
- 20 个交易日滚动：beta_t = Cov(r,m)_20 / Var(m)_20；残差 e_t = r_t - beta_t * m_t；
  ivol20_t = Std(e)_{20}（min_periods=16）。
- 当日全市场横截面分位 pct = rank(ivol20)/N ∈ (0,1]，供强牛 regime 风险否决使用。

为什么用特质波动而非总波动：总波动混入市场 beta（L-080 证明 beta/指数趋势是会翻号的
regime 量）；剔除市场后的个股特质风险在 v2 选中池三窗口（冻结/前瞻/run3）同向为负，
是 L-061~L-083 未构造过的衍生因子（非 daily_kbars 存量列）。详见 docs/lessons L-093。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

IVOL_WINDOW = 20
IVOL_MIN_PERIODS = 16
BENCHMARK_TS_CODE = "000905.SH"


def compute_ivol20(
    returns: pd.Series,
    market_returns: pd.Series,
    window: int = IVOL_WINDOW,
    min_periods: int = IVOL_MIN_PERIODS,
) -> pd.Series:
    """对齐索引后逐点滚动回归，返回残差标准差序列（与 returns 同索引）。

    returns / market_returns 需已按相同交易日索引对齐（调用方负责 merge/ffill 口径）。
    """
    r = pd.to_numeric(returns, errors="coerce").astype(float)
    m = pd.to_numeric(market_returns, errors="coerce").astype(float)
    cov = r.rolling(window, min_periods=min_periods).cov(m)
    mvar = m.rolling(window, min_periods=min_periods).var()
    beta = cov / mvar.where(mvar > 0)
    resid = r - beta * m
    return resid.rolling(window, min_periods=min_periods).std()


def ivol_pct_series(values: pd.Series) -> pd.Series:
    """单日全市场横截面分位（pct rank），缺失不参与排名。"""
    return values.rank(pct=True, method="average")


def build_ivol_panel(
    kbars: pd.DataFrame,
    index_rows: pd.DataFrame,
    *,
    code_col: str = "code",
    date_col: str = "trade_date",
) -> pd.DataFrame:
    """给一张含 code/trade_date/pct_chg 的日线面板计算 ivol20（逐股滚动，内存安全）。

    index_rows 需含 trade_date 与 pct_chg（中证500）。返回 [code, trade_date, ivol20]。
    """
    idx = index_rows[[date_col, "pct_chg"]].copy()
    idx["mkt"] = pd.to_numeric(idx["pct_chg"], errors="coerce").astype(float) / 100.0
    df = kbars[[code_col, date_col, "pct_chg"]].copy()
    df["r"] = pd.to_numeric(df["pct_chg"], errors="coerce").astype(float) / 100.0
    df = df.merge(idx[[date_col, "mkt"]], on=date_col, how="left")
    df = df.sort_values([code_col, date_col])
    parts = []
    for code, blk in df.groupby(code_col, sort=False):
        iv = compute_ivol20(blk["r"], blk["mkt"])
        parts.append(pd.DataFrame({
            code_col: code,
            date_col: blk[date_col].values,
            "ivol20": iv.astype(float).values,
        }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=[code_col, date_col, "ivol20"])


def attach_ivol20_pct(
    cards: list[dict[str, Any]],
    trade_date: str,
    pct_map: Mapping[tuple[str, str], float],
    *,
    field: str = "ivol20_pct",
) -> None:
    """就地为卡片附加当日全市场 ivol20 分位；查不到（如停牌/新股/北交所未参与排名）留空。"""
    for card in cards or []:
        code = str(card.get("code") or "")
        if not code:
            continue
        pct = pct_map.get((str(trade_date), code))
        if pct is not None and not (isinstance(pct, float) and np.isnan(pct)):
            card[field] = float(pct)


def load_ivol20_pct_map(session, trade_dates: Iterable[str]) -> dict[tuple[str, str], float]:
    """从 daily_kbars.ivol20 读取给定交易日的全市场特质波动并按日横截面排名。

    返回 {(trade_date, code): pct∈(0,1]}。与生产可交易宇宙一致：剔北交所（8/4/920 开头）。
    结果进程内可缓存；ivol20 为 T 收盘写入的历史列，只读、无前视。
    """
    from sqlalchemy import text as _text

    dates = sorted({str(d) for d in trade_dates})
    if not dates:
        return {}
    stmt = _text(
        "SELECT trade_date, code, ivol20 FROM daily_kbars "
        "WHERE trade_date BETWEEN :d0 AND :d1 AND ivol20 IS NOT NULL AND ivol20 > 0 "
        "AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '920%'"
    )
    rows = session.execute(stmt, {"d0": dates[0], "d1": dates[-1]}).mappings().all()
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df["pct"] = df.groupby("trade_date")["ivol20"].rank(pct=True, method="average")
    return {(str(r.trade_date), str(r.code)): float(r.pct) for r in df.itertuples()}


__all__ = [
    "IVOL_WINDOW",
    "IVOL_MIN_PERIODS",
    "BENCHMARK_TS_CODE",
    "compute_ivol20",
    "ivol_pct_series",
    "build_ivol_panel",
    "attach_ivol20_pct",
]
