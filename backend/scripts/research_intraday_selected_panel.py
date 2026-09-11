"""构建【v2 选中候选】的信号日分时特征面板（只读，缓存）。

口径：冻结窗用原生 15min；前瞻窗用 5min 三根合一根重采样为 15min 等价序列
（15min bar 标在区间末：09:45=09:30-09:45；5min 09:35/09:40/09:45 三合一与之对齐）。
所有特征只用到信号日收盘前（含收盘）信息，选股时点可得。gap 用 daily_kbars 前收。
输出 output/research/intraday_selected_panel.pkl：一行一笔 v2 选中交易，含 net/score/
base/sent/窗口标记与 12 个分时特征。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def _engine():
    env = (B / ".env").read_text()
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in env.splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    return create_engine(url)


def _bars_to_15(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """df: 单 code 单日，已按时间排序，列 o/h/l/c/v/a；返回 15min 等价 bar。"""
    df = df[["o", "h", "l", "c", "v", "a"]].reset_index(drop=True)
    if freq == "15min":
        return df
    # 5min -> 15min：每 3 根一桶
    df["bucket"] = df.index // 3
    g = df.groupby("bucket")
    out = pd.DataFrame({
        "o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(),
        "v": g.v.sum(), "a": g.a.sum()}).reset_index(drop=True)
    return out


def _fetch_bars(eng, pairs: pd.DataFrame, freq: str) -> dict:
    """pairs: columns dt,code。返回 {(dt,code): 15min 等价 bar DataFrame}。"""
    result = {}
    dates = sorted(pairs.dt.unique())
    # 按月分块，避免超大 IN 查询
    months = sorted({str(d)[:7] for d in dates})
    for m in months:
        md = [d for d in dates if str(d).startswith(m)]
        codes = sorted(pairs[pairs.dt.isin(md)].code.unique().tolist())
        d0, d1 = min(md), max(md)
        sql = text(f"""SELECT trade_date, code, trade_time, open, high, low, close, volume, amount
                       FROM minute_kbars WHERE freq=:f AND trade_date BETWEEN :d0 AND :d1
                       AND code IN :codes ORDER BY code, trade_time""")
        df = pd.read_sql(sql, eng, params={"f": freq, "d0": d0, "d1": d1,
                                          "codes": tuple(codes)}, )
        df.columns = ["dt", "code", "tt", "o", "h", "l", "c", "v", "a"]
        df["dt"] = df.dt.astype(str)
        for (dt, code), sub in df.groupby(["dt", "code"], sort=False):
            result[(dt, str(code))] = _bars_to_15(
                sub.drop(columns=["dt", "code", "tt"]), freq)
    return result


def _features(bars: pd.DataFrame, preclose: float | None) -> dict:
    o = bars.o.values.astype(float); h = bars.h.values.astype(float)
    l = bars.l.values.astype(float); c = bars.c.values.astype(float)
    v = bars.v.values.astype(float); a = bars.a.values.astype(float)
    n = len(c)
    if n < 8: return {}
    day_open, day_close, day_high, day_low = o[0], c[-1], h.max(), l.min()
    tot_v, tot_a = v.sum(), a.sum()
    vwap = tot_a / tot_v if tot_v > 0 else day_close
    # 上午：前 8 根（09:45..11:30），下午：其余（13:15..15:00）
    am = slice(0, 8); pm = slice(8, n)
    am_close = c[7]
    pm_open = o[8] if n > 8 else am_close
    bar_ret = np.diff(c) / c[:-1]
    # 收盘路径最大回撤
    run_max = np.maximum.accumulate(c); mdd = ((l - run_max) / run_max).min() * 100
    above = v[(c > vwap)].sum() / tot_v if tot_v > 0 else np.nan
    # bar 收益-量相关
    if len(bar_ret) >= 5 and v[1:].std() > 0 and bar_ret.std() > 0:
        vp = np.corrcoef(bar_ret, v[1:])[0, 1]
    else:
        vp = np.nan
    f = {
        "gap_pct": (day_open / preclose - 1) * 100 if preclose else np.nan,
        "am_ret": (am_close / day_open - 1) * 100,
        "pm_ret": (day_close / pm_open - 1) * 100,
        "am_minus_pm": (am_close / day_open - 1) * 100 - (day_close / pm_open - 1) * 100,
        "amplitude": (day_high - day_low) / vwap * 100,
        "rvol": bar_ret.std() * 100,
        "intraday_mdd": mdd,
        "am_volshare": v[am].sum() / tot_v if tot_v else np.nan,
        "vwap_above_volshare": above,
        "close_off_high": (day_close - day_high) / day_high * 100,
        "last30_ret": (c[-1] / c[-3] - 1) * 100 if n >= 3 else np.nan,
        "vp_corr": vp,
    }
    return f


def _preclose_map(eng, pairs):
    dates = sorted(pairs.dt.unique()); codes = sorted(pairs.code.unique())
    d0, d1 = min(dates), max(dates)
    # daily_kbars 前收：用同表前一交易日 close 对齐；直接取 open 与 lag close
    sql = text("""SELECT trade_date, code, `open` open_, `close` close_
                  FROM daily_kbars WHERE trade_date BETWEEN :d0 AND :d1 AND code IN :codes
                  ORDER BY code, trade_date""")
    df = pd.read_sql(sql, eng, params={"d0": d0, "d1": d1, "codes": tuple(codes)})
    df["dt"] = df.trade_date.astype(str); df["code"] = df.code.astype(str)
    df = df.sort_values(["code", "dt"])
    df["preclose"] = df.groupby("code").close_.shift(1)
    return {(r.dt, r.code): r.preclose for r in df.itertuples()}


def build():
    eng = _engine()
    frames = []
    for win, path, freq in [
        ("frozen", OUT / "canon_frozen_v2_selected.pkl", "15min"),
        ("forward", OUT / "canon_forward_v2_selected.pkl", "5min")]:
        sel = pd.read_pickle(path)
        sel["dt"] = sel.dt.astype(str); sel["code"] = sel.code.astype(str)
        bars = _fetch_bars(eng, sel[["dt", "code"]], freq)
        pre = _preclose_map(eng, sel[["dt", "code"]])
        rows = []
        miss = 0
        for r in sel.itertuples():
            key = (r.dt, r.code)
            b = bars.get(key)
            if b is None or len(b) < 8:
                miss += 1; continue
            f = _features(b, pre.get(key))
            f.update({"win": win, "dt": r.dt, "code": r.code, "net": float(r.net),
                      "score": float(r.score), "key": r.key,
                      "split": getattr(r, "split", "forward")})
            rows.append(f)
        out = pd.DataFrame(rows)
        print(f"{win}: selected={len(sel)} matched={len(out)} miss={miss} "
              f"({len(out)/len(sel)*100:.1f}%) freq={freq}")
        frames.append(out)
    panel = pd.concat(frames, ignore_index=True)
    panel.to_pickle(OUT / "intraday_selected_panel.pkl")
    print("saved", panel.shape, "-> intraday_selected_panel.pkl")
    return panel


if __name__ == "__main__":
    build()
