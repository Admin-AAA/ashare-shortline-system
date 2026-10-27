"""本地兜底：daily_kbars 的 KDJ / BOLL / CCI / vol_ratio_5 / vol_ratio_20，只填 NULL（vol_ratio 另把 0 视作缺失）。

背景（2026-10-25 审计 P0-3）：quantdata 额度耗尽（quota_exhausted）后 promax 回退只给 stk_factor 的部分列，
10-20~10-23 的 kdj_* / boll_* / cci 整列 NULL，08-24~28、09-02、09-08 也有缺口；vol_ratio_5/20 在 06~09 月大面积为 0/NULL。
这些指标都能从已有 OHLCV 精确复算，所以不再依赖上游。

口径（用库里有数据的日子逐只核对过，60 只随机股、2024-06 起约 2.6 万行）：
- KDJ(9,3,3)：RSV=(C-L9)/(H9-L9)*100，K=SMA(RSV,3,1)，D=SMA(K,3,1)，J=3K-2D；价格用前复权（adj_factor/最新 adj_factor）。
  与源数据逐行最大误差 0.0005（源数据四舍五入到 3 位小数）。
- CCI(14)：TP=(H+L+C)/3，CCI=(TP-MA(TP,14))/(0.015*AVEDEV(TP,14))，同样前复权。逐行最大误差 0.0005。
- BOLL(20,2)：mid=MA20(C)，std 总体标准差(ddof=0)，upper/lower=mid±2std。价格按「当天为基准」的前复权
  （窗口内每日价 × adj_t / adj_当天）。无除权股逐行误差 0.0005；窗口内有除权的股票与源数据偶有差异（源数据本身
  对除权前窗口的口径不稳定，中位误差仍是 0.0005）。
- vol_ratio_5 / vol_ratio_20 = 当日成交量 / 含当日的近 5 / 20 日均量（未复权），与源数据中位误差 2.6e-5。
窗口不足 → NULL（不写 0）。
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

KDJ_COLS = ("kdj_k", "kdj_d", "kdj_j")
BOLL_COLS = ("boll_upper", "boll_mid", "boll_lower")
FILL_COLS = (*KDJ_COLS, *BOLL_COLS, "cci", "vol_ratio_5", "vol_ratio_20")
ZERO_IS_MISSING = ("vol_ratio_5", "vol_ratio_20")
WARMUP_DAYS = 450  # KDJ 的 SMA(.,3,1) 递推误差按 (2/3)^n 衰减，约 60 根后可忽略；留足余量


def _sma_tdx(s: pd.Series, n: int, m: int = 1) -> pd.Series:
    return s.ewm(alpha=m / n, adjust=False).mean()


def compute_kdj_boll_cci(g: pd.DataFrame) -> pd.DataFrame:
    """g：单只股票按日期升序，列 high/low/close/volume/adj_factor（adj_factor 缺失按 1）。返回同索引的 FILL_COLS。"""
    adj = g["adj_factor"].astype(float).where(lambda s: s > 0).ffill().bfill().fillna(1.0)
    c_raw, h_raw, l_raw = (g[k].astype(float) for k in ("close", "high", "low"))
    v = g["volume"].astype(float)
    f = adj / adj.iloc[-1]  # 前复权（以最新一根为基准）
    c, h, low = c_raw * f, h_raw * f, l_raw * f
    out = pd.DataFrame(index=g.index)

    hh = h.rolling(9, min_periods=1).max()
    ll = low.rolling(9, min_periods=1).min()
    rng = hh - ll
    rsv = ((c - ll) / rng.where(rng != 0) * 100).fillna(50.0)
    k = _sma_tdx(rsv, 3)
    d = _sma_tdx(k, 3)
    out["kdj_k"], out["kdj_d"], out["kdj_j"] = k, d, 3 * k - 2 * d

    tp = (h + low + c) / 3
    ma = tp.rolling(14, min_periods=14).mean()
    dev = tp.rolling(14, min_periods=14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    out["cci"] = (tp - ma) / (0.015 * dev.where(dev != 0))

    # BOLL：以「当天」为基准的前复权。x = close*adj；anchored mean = mean(x)/adj_t，std = std(x)/adj_t
    x = c_raw * adj
    mid = x.rolling(20, min_periods=20).mean() / adj
    sd = x.rolling(20, min_periods=20).std(ddof=0) / adj
    out["boll_mid"], out["boll_upper"], out["boll_lower"] = mid, mid + 2 * sd, mid - 2 * sd

    for n in (5, 20):
        ma_v = v.rolling(n, min_periods=n).mean()
        out[f"vol_ratio_{n}"] = v / ma_v.where(ma_v > 0)
    # 前 59 根 KDJ 递推尚未收敛 → 仍写出（与源数据一致），但上市首日 RSV 无意义不影响 NULL 语义
    return out.replace([np.inf, -np.inf], np.nan).round(4)


def fill_kdj_boll_cci(db, *, since: str, end: str | None = None, batch_codes: int = 250,
                      columns: tuple[str, ...] = FILL_COLS) -> dict[str, Any]:
    """对 [since, end] 内这些列为 NULL（vol_ratio 另含 0）的行补值。绝不覆盖已有非空值。分批控内存。"""
    t0 = time.perf_counter()
    end = end or "9999-12-31"
    cols = list(columns)
    miss = " OR ".join(
        f"(`{c}` IS NULL OR `{c}` = 0)" if c in ZERO_IS_MISSING else f"`{c}` IS NULL" for c in cols)
    codes = [r[0] for r in db.execute(text(
        f"SELECT DISTINCT code FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e AND ({miss})"),
        {"s": since, "e": end})]
    warm = (_dt.date.fromisoformat(since[:10]) - _dt.timedelta(days=WARMUP_DAYS)).isoformat()
    load = text(
        "SELECT code, trade_date, high, low, close, volume, adj_factor FROM daily_kbars "
        "WHERE code IN :codes AND trade_date >= :w ORDER BY code, trade_date"
    ).bindparams(bindparam("codes", expanding=True))
    changed = 0
    per_col: dict[str, int] = {c: 0 for c in cols}
    for i in range(0, len(codes), batch_codes):
        chunk = codes[i:i + batch_codes]
        df = pd.DataFrame(db.execute(load, {"codes": chunk, "w": warm}).all(),
                          columns=["code", "trade_date", "high", "low", "close", "volume", "adj_factor"])
        if df.empty:
            continue
        df["trade_date"] = df["trade_date"].astype(str)
        df = df[df["close"].astype(float) > 0]
        parts = []
        for code, g in df.groupby("code", sort=False):
            g = g.reset_index(drop=True)
            if len(g) < 2:
                continue
            res = compute_kdj_boll_cci(g)[cols]
            res.insert(0, "trade_date", g["trade_date"])
            res.insert(0, "code", code)
            parts.append(res[(res["trade_date"] >= since) & (res["trade_date"] <= end)])
        if not parts:
            continue
        out = pd.concat(parts, ignore_index=True)
        out = out.astype(object).where(pd.notna(out), None)
        conn = db.connection()
        conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _kbc_tmp"))
        conn.execute(text("CREATE TEMPORARY TABLE _kbc_tmp (code VARCHAR(6), trade_date VARCHAR(10), "
                          + ", ".join(f"`{k}` DOUBLE" for k in cols) + ", PRIMARY KEY(code, trade_date))"))
        out.to_sql("_kbc_tmp", con=conn, if_exists="append", index=False, chunksize=20000)
        sets = ", ".join(
            f"k.`{c}` = IF(k.`{c}` IS NULL" + (f" OR k.`{c}` = 0" if c in ZERO_IS_MISSING else "") + f", t.`{c}`, k.`{c}`)"
            for c in cols)
        where = " OR ".join(
            f"((k.`{c}` IS NULL" + (f" OR k.`{c}` = 0" if c in ZERO_IS_MISSING else "") + f") AND t.`{c}` IS NOT NULL)"
            for c in cols)
        res = conn.execute(text(
            f"UPDATE daily_kbars k JOIN _kbc_tmp t ON k.code=t.code AND k.trade_date=t.trade_date SET {sets}, "
            f"k.updated_at = UTC_TIMESTAMP() WHERE {where}"))
        conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _kbc_tmp"))
        db.commit()
        changed += int(res.rowcount or 0)
        del df, out, parts
    return {"since": since, "end": end, "codes": len(codes), "rows_changed": changed,
            "seconds": round(time.perf_counter() - t0, 1)}
