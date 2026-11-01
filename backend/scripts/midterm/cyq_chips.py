"""§286/§287 共用：换手率衰减筹码分布（邢不行第 102 期《投资不识筹码峰,炒遍A股也枉然?》的算法：每天成交按换手率 τ
等比例"卖掉"所有旧筹码、在当日均价处新增 τ 份筹码）。预注册 docs/strategy-research.md §286/§287，结果出来前写定。
  τ_t = amount_t / (circ_mv_t × 1e4)（成交额/流通市值，circ_mv 单位万元；clip 到 [0, 0.99]，缺失→不算）。
  当日均价 p_t = (amount/(volume×100)) × ac/close（复权到 ac 口径；不在 [low,high] 内或无效时用 ac）。
  t 日筹码 = 近 L=250 个交易日每天 i 的权重 w_i = τ_i · ∏_{j=i+1..t}(1−τ_j)，归一化。
  WR（获利盘比例）= Σ w_i·[p_i ≤ ac_t]；CR70（70% 筹码集中度）= (q85−q15)/(q85+q15)，q 为加权分位。
  载入窗口内不足 250 行的日期记为空（不出信号）。"""
import numpy as np
import polars as pl
c = pl.col
L = 250


def _wq(p, w, q):
    o = np.argsort(p, axis=1); ps = np.take_along_axis(p, o, 1); ws = np.take_along_axis(w, o, 1)
    cw = np.cumsum(ws, axis=1)
    return [ps[np.arange(len(p)), np.minimum((cw < x).sum(1), p.shape[1] - 1)] for x in q]


def add_chips(df):
    df = df.sort("code", "trade_date")
    adj = c("ac") / c("close")
    vw = c("amount") / (c("volume") * 100)
    ok = (vw >= c("low") * 0.999) & (vw <= c("high") * 1.001) & (c("volume") > 0)
    df = df.with_columns(
        pl.when(ok).then(vw * adj).otherwise(c("ac")).alias("_p"),
        (c("amount") / (c("circ_mv").cast(pl.Float64, strict=False) * 1e4)).clip(0, 0.99).alias("_tau"))
    wr_all, cr_all = [], []
    for code, part in df.select("code", "_p", "_tau", "ac").partition_by("code", maintain_order=True, as_dict=True).items():
        p = part["_p"].to_numpy().astype(float); tau = part["_tau"].to_numpy().astype(float); ac = part["ac"].to_numpy().astype(float)
        n = len(p); wr = np.full(n, np.nan); cr = np.full(n, np.nan)
        if n >= L:
            bad = ~np.isfinite(tau); tau2 = np.where(bad, 0.0, tau)
            S = np.concatenate([[0.0], np.cumsum(np.log1p(-tau2))])  # S[k] = Σ_{j<k} log(1−τ_j)
            t_idx = np.arange(L - 1, n)
            I = t_idx[:, None] - np.arange(L - 1, -1, -1)[None, :]  # rows t, cols i = t-L+1..t
            w = tau2[I] * np.exp(S[t_idx + 1][:, None] - S[I + 1])
            pp = p[I]
            sw = w.sum(1); valid = (sw > 0) & ~bad[t_idx] & np.isfinite(pp).all(1)
            w = w / np.where(sw > 0, sw, 1)[:, None]
            wr[t_idx] = (w * (pp <= ac[t_idx][:, None])).sum(1)
            q15, q85 = _wq(np.where(np.isfinite(pp), pp, 0), w, (0.15, 0.85))
            cr[t_idx] = (q85 - q15) / (q85 + q15)
            wr[t_idx[~valid]] = np.nan; cr[t_idx[~valid]] = np.nan
        wr_all.append(wr); cr_all.append(cr)
    return df.with_columns(pl.Series("wr", np.concatenate(wr_all)), pl.Series("cr70", np.concatenate(cr_all))
                           ).with_columns(c("wr").fill_nan(None), c("cr70").fill_nan(None)).drop("_p", "_tau")
