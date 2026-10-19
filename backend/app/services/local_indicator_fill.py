"""本地日K计算 daily_kbars 里没人写的指标列，只补 NULL（不覆盖已有值），缺值写 NULL 不写 0。

2026-10-19 审计：
- bias_10 / max_dd60：日更主路径（按日批量 daily+daily_basic+stk_factor+cyq_perf）不含本地派生列，
  scripts/backfill_indicators.py 只写 bias_5/bias_20/max_dd20，且只在 ma5 缺失时才触发；
  ma 由 fill_moving_averages 补齐后它再也不触发 → bias_10/max_dd60 只有 09-08 一次性补过；
  bias_5/bias_20/max_dd20 在 2026-02~05 也整段为空。
- atr/bbi/ema_12/ema_50/ma_volume_5/10/mtm/obv/psy/trix/vr/wr_6/wr_10/dpo/brar_ar/brar_br/asi/asit：
  原计划来自 stk_factor_pro，从未接入，任何代码都没写过。
口径：未复权 OHLCV（与 ma5..ma250 / indicator_computer 一致），窗口不足 → NULL；通达信公式。
profit_efficiency 无定义、无写方，保持 NULL（见报告）。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

FILL_COLUMNS = (
    "bias_5", "bias_10", "bias_20", "max_dd20", "max_dd60",
    "atr", "bbi", "ema_12", "ema_50", "ma_volume_5", "ma_volume_10", "mtm", "obv", "psy",
    "trix", "vr", "wr_6", "wr_10", "dpo", "brar_ar", "brar_br", "asi", "asit",
)


def _max_dd(close: np.ndarray, w: int) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if len(close) < w:
        return out
    win = np.lib.stride_tricks.sliding_window_view(close, w)
    peak = np.maximum.accumulate(win, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (win - peak) / peak * 100, np.nan)
    out[w - 1:] = np.nanmin(dd, axis=1)
    return out


def _tdx_sma(s: pd.Series, n: int, m: int = 1) -> pd.Series:
    return s.ewm(alpha=m / n, adjust=False).mean()


def compute_one(g: pd.DataFrame) -> pd.DataFrame:
    """g: 单只股票按日期升序，列 open/high/low/close/volume。"""
    o, h, low, c, v = (g[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    pc = c.shift(1)
    r = pd.DataFrame(index=g.index)

    def mean(s, n):
        return s.rolling(n, min_periods=n).mean()

    def ssum(s, n):
        return s.rolling(n, min_periods=n).sum()

    for n in (5, 10, 20):
        m = mean(c, n)
        r[f"bias_{n}"] = ((c - m) / m.where(m != 0) * 100)
    r["max_dd20"] = _max_dd(c.to_numpy(), 20)
    r["max_dd60"] = _max_dd(c.to_numpy(), 60)
    tr = pd.concat([h - low, (h - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    tr[pc.isna()] = np.nan
    r["atr"] = mean(tr, 14)
    r["bbi"] = (mean(c, 3) + mean(c, 6) + mean(c, 12) + mean(c, 24)) / 4
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    cnt = pd.Series(np.arange(1, len(c) + 1), index=c.index)
    r["ema_12"] = ema12.where(cnt >= 12)
    r["ema_50"] = ema50.where(cnt >= 50)
    r["ma_volume_5"] = mean(v, 5)
    r["ma_volume_10"] = mean(v, 10)
    r["mtm"] = c - c.shift(12)
    sign = np.sign(c - pc).fillna(0)
    r["obv"] = (sign * v).cumsum()  # 自本地最早一根日K起累计
    r["psy"] = ssum((c > pc).astype(float).where(pc.notna()), 12) / 12 * 100
    e1 = c.ewm(span=12, adjust=False).mean()
    e3 = e1.ewm(span=12, adjust=False).mean().ewm(span=12, adjust=False).mean()
    r["trix"] = ((e3 - e3.shift(1)) / e3.shift(1) * 100).where(cnt >= 36)
    up_v = v.where(c > pc, 0.0)
    dn_v = v.where(c < pc, 0.0)
    eq_v = v.where(c == pc, 0.0)
    num = ssum(up_v, 26) * 2 + ssum(eq_v, 26)
    den = ssum(dn_v, 26) * 2 + ssum(eq_v, 26)
    r["vr"] = (num / den.where(den != 0) * 100).where(pc.notna())
    for n in (6, 10):
        hh = h.rolling(n, min_periods=n).max()
        ll = low.rolling(n, min_periods=n).min()
        rng = (hh - ll)
        r[f"wr_{n}"] = (hh - c) / rng.where(rng != 0) * 100
    r["dpo"] = c - mean(c, 20).shift(11)
    ar_den = ssum(o - low, 26)
    r["brar_ar"] = ssum(h - o, 26) / ar_den.where(ar_den != 0) * 100
    br_up = (h - pc).clip(lower=0)
    br_dn = (pc - low).clip(lower=0)
    br_den = ssum(br_dn, 26)
    r["brar_br"] = ssum(br_up, 26) / br_den.where(br_den != 0) * 100
    # ASI（通达信 M1=26, M2=10）
    lc, lo_, ho = pc, o.shift(1), None
    aa = (h - lc).abs()
    bb = (low - lc).abs()
    cc = (h - low.shift(1)).abs()
    dd = (lc - lo_).abs()
    rr = np.where((aa > bb) & (aa > cc), aa + bb / 2 + dd / 4,
                  np.where((bb > cc) & (bb > aa), bb + aa / 2 + dd / 4, cc + dd / 4))
    rr = pd.Series(rr, index=c.index)
    x = (c - lc + (c - o) / 2 + lc - lo_)
    si = 16 * x / rr.where(rr != 0) * pd.concat([aa, bb], axis=1).max(axis=1)
    asi = ssum(si, 26)
    r["asi"] = asi
    r["asit"] = mean(asi, 10)
    del ho
    return r.replace([np.inf, -np.inf], np.nan).round(4)


def fill_local_indicators(db, *, since: str, end: str | None = None, batch_codes: int = 200,
                          columns: tuple[str, ...] = FILL_COLUMNS) -> dict[str, Any]:
    """对 [since, end] 的行补 NULL 列；按 batch_codes 分批控内存。"""
    t0 = time.perf_counter()
    end = end or "9999-12-31"
    # 全历史加载：obv 是自上市累计量，按窗口加载会随起点漂移；单只约 1–2k 行，按批控内存
    warm = "0000-00-00"
    codes = [r[0] for r in db.execute(text(
        f"SELECT DISTINCT code FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e"),
        {"s": since, "e": end})]
    load = text(
        "SELECT code, trade_date, open, high, low, close, volume FROM daily_kbars "
        "WHERE code IN :codes AND trade_date >= :w AND trade_date <= :e ORDER BY code, trade_date"
    ).bindparams(bindparam("codes", expanding=True))
    cols = list(columns)
    changed = 0
    for i in range(0, len(codes), batch_codes):
        chunk = codes[i:i + batch_codes]
        df = pd.DataFrame(db.execute(load, {"codes": chunk, "w": warm, "e": end}).all(),
                          columns=["code", "trade_date", "open", "high", "low", "close", "volume"])
        if df.empty:
            continue
        df = df[(df["close"] > 0)]
        parts = []
        for code, g in df.groupby("code", sort=False):
            g = g.reset_index(drop=True)
            res = compute_one(g)
            res["code"] = code
            res["trade_date"] = g["trade_date"].astype(str)
            parts.append(res[res["trade_date"] >= since])
        if not parts:
            continue
        out = pd.concat(parts, ignore_index=True)[["code", "trade_date", *cols]]
        out = out.astype(object).where(pd.notna(out), None)
        conn = db.connection()
        conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _lind_tmp"))
        conn.execute(text("CREATE TEMPORARY TABLE _lind_tmp (code VARCHAR(6), trade_date VARCHAR(10), "
                          + ", ".join(f"`{k}` DOUBLE" for k in cols) + ", PRIMARY KEY(code, trade_date))"))
        pd.DataFrame(out).to_sql("_lind_tmp", con=conn, if_exists="append", index=False, chunksize=20000)
        res = conn.execute(text(
            "UPDATE daily_kbars k JOIN _lind_tmp t ON k.code=t.code AND k.trade_date=t.trade_date SET "
            + ", ".join(f"k.`{k}` = COALESCE(k.`{k}`, t.`{k}`)" for k in cols)
            + ", k.updated_at = UTC_TIMESTAMP() WHERE "
            + " OR ".join(f"(k.`{k}` IS NULL AND t.`{k}` IS NOT NULL)" for k in cols)))
        conn.execute(text("DROP TEMPORARY TABLE IF EXISTS _lind_tmp"))
        db.commit()
        changed += int(res.rowcount or 0)
        del df, out, parts
    return {"since": since, "end": end, "codes": len(codes), "rows_changed": changed,
            "seconds": round(time.perf_counter() - t0, 1)}
