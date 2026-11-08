"""[短线研究 §1030–§1039] RSI 恐慌信号的出场规则续研。

预注册 docs/strategy-research.md §1030–§1039（2026-11-07 12:05 CST 写于跑任何收益之前）。
信号与 §1013–§1017 完全相同；本轮只换出场：生产执行合约（止盈 6%、移动止损 2%/0.8%/0.3、
止损 4.5%）+ 显式止盈 3%/5% + 更短持有期。

执行：app.engine.simulation.simulate + execution_policy_for_next_open_entry()
（T+1 开盘买、一字涨停买不进、跌停锁死顺延、盘中止盈/止损 STOP_LOSS 优先、到期 horizon_close）。
费用/滑点：佣金 0.01%×2 + 印花税 0.05% + 滑点 11.5bp×2 = 往返 0.30%。

用法：cd backend/scripts/midterm
  LT_START=.. LT_END=.. PYTHONPATH=<backend>:.:../shortline_research \
  ../../.venv/bin/python ../shortline_research/mrsi_exit_s1030.py "<phase>" out.json
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import replace

import numpy as np
import polars as pl

from app.engine.backtest import BarInput, RecommendationInput
from app.engine.simulation import simulate
from app.services.entry_policy import execution_policy_for_next_open_entry
from lt_research6_ctrl import cohort_bench
from realistic import metrics_real
from mrsi_s1013 import PHASES, prep

c = pl.col
BASE = execution_policy_for_next_open_entry()

CONFIG_SETS = {
    # 第一轮 §1030–§1039：信号/出场组合
    "a": (
        ("MRSI2_DIP_PROD_h2", "S_MRSI2_DIP", "sc_dip", 2, 5, 20, "PROD"),
        ("MRSI2_DIP_PROD_h3", "S_MRSI2_DIP", "sc_dip", 3, 5, 20, "PROD"),
        ("MRSI2_DIP_PROD_h5", "S_MRSI2_DIP", "sc_dip", 5, 5, 20, "PROD"),
        ("MRSI2_DIP_TP3_h3", "S_MRSI2_DIP", "sc_dip", 3, 5, 20, "TP3"),
        ("MRSI2_DIP_TP5_h5", "S_MRSI2_DIP", "sc_dip", 5, 5, 20, "TP5"),
        ("MRSI2_DIP_TRAIL_h5", "S_MRSI2_DIP", "sc_dip", 5, 5, 20, "TRAIL"),
        ("MRSI6_BROAD_PROD_h5", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "PROD"),
        ("MRSI14_BROAD_PROD_h5", "S_MRSI14_BROAD", "sc_amt", 5, 10, 20, "PROD"),
        ("MRSI6_DIP_PROD_h5", "S_MRSI6_BROAD", "sc_dip", 5, 5, 20, "PROD"),
        ("MRSI14_DIP_PROD_h5", "S_MRSI14_BROAD", "sc_dip", 5, 5, 20, "PROD"),
        ("ALL_DIP_PROD_h2", "S_ALL_DIP", "sc_dip", 2, 5, 100, "PROD"),
        ("ALL_DIP_PROD_h3", "S_ALL_DIP", "sc_dip", 3, 5, 100, "PROD"),
        ("ALL_DIP_PROD_h5", "S_ALL_DIP", "sc_dip", 5, 5, 100, "PROD"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
    # 第二轮 §1040–§1045：只收紧 RSI6 宽基的出场
    "b": (
        ("MRSI6_BROAD_TP3_h5", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "TP3"),
        ("MRSI6_BROAD_TP5_h5", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "TP5"),
        ("MRSI6_BROAD_TRAIL_h5", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "TRAIL"),
        ("MRSI6_BROAD_STOP3_h5", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "STOP3"),
        ("MRSI6_BROAD_PROD_h3", "S_MRSI6_BROAD", "sc_amt", 3, 10, 20, "PROD"),
        ("MRSI6_BROAD_PROD_h10", "S_MRSI6_BROAD", "sc_amt", 10, 10, 20, "PROD"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
    ),
    # 第三轮 §1046–§1048：短持有 + 止盈的组合
    "c": (
        ("MRSI6_BROAD_TP3_h3", "S_MRSI6_BROAD", "sc_amt", 3, 10, 20, "TP3"),
        ("MRSI6_BROAD_TP3_h2", "S_MRSI6_BROAD", "sc_amt", 2, 10, 20, "TP3"),
        ("MRSI6_BROAD_TP5_h3", "S_MRSI6_BROAD", "sc_amt", 3, 10, 20, "TP5"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
    ),
    # 第四轮 §1049：按等权指数/MA60 切换出场
    "d": (
        ("MRSI6_BROAD_REGIME", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "REGIME"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
    # 第五轮 §1050：市场 RSI6 归一化出场
    "e": (
        ("MRSI6_BROAD_MKT_EXIT", "S_MRSI6_BROAD", "sc_amt", 5, 10, 20, "MKT_EXIT"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
    # 第六轮 §1051：跌停家数占优（capitulation）过滤
    "f": (
        ("MRSI6_BROAD_CAP_TP3_h5", "S_MRSI6_CAP", "sc_amt", 5, 10, 20, "TP3"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
    # 第七轮 §1052：capitulation 过滤 + 生产默认出场（便于直接接生产）
    "g": (
        ("MRSI6_BROAD_CAP_PROD_h5", "S_MRSI6_CAP", "sc_amt", 5, 10, 20, "PROD"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
    # 第八轮 §1053：capitulation 过滤 + TP5（生产结算允许 +5% 下限）
    "h": (
        ("MRSI6_BROAD_CAP_TP5_h5", "S_MRSI6_CAP", "sc_amt", 5, 10, 20, "TP5"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
    # 第九轮 §1054：用当日成交额（而非 amt20）排序，验证实盘近似口径
    "i": (
        ("MRSI6_BROAD_CAP_TP5_H5_AMTTODAY", "S_MRSI6_CAP", "sc_amt_today", 5, 10, 20, "TP5"),
        ("ALL_BROAD_TP3_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "TP3"),
        ("ALL_BROAD_PROD_h5", "S_ALL_BROAD", "sc_amt", 5, 10, 100, "PROD"),
    ),
}
CONFIGS = CONFIG_SETS[os.environ.get("S1030_SET", "a")]


def build_policy(horizon: int, mode: str):
    p = replace(BASE, horizon_days=int(horizon))
    p = replace(p, slippage=replace(p.slippage, entry_bps=11.5, exit_bps=11.5))
    p = replace(
        p,
        stop_loss=replace(p.stop_loss, enabled=True, default_loss_pct=4.5, maximum_loss_pct=4.7),
    )
    if mode == "PROD":
        return p
    if mode == "STOP3":
        return replace(
            p,
            stop_loss=replace(p.stop_loss, enabled=True, default_loss_pct=3.0, maximum_loss_pct=4.7),
        )
    if mode == "TP3":
        return replace(
            p,
            take_profit=replace(p.take_profit, enabled=True, default_gain_pct=3.0, maximum_gain_pct=15.0),
            trailing=replace(p.trailing, enabled=False),
        )
    if mode == "TP5":
        return replace(
            p,
            take_profit=replace(p.take_profit, enabled=True, default_gain_pct=5.0, maximum_gain_pct=15.0),
            trailing=replace(p.trailing, enabled=False),
        )
    if mode == "TRAIL":
        return replace(
            p,
            take_profit=replace(p.take_profit, enabled=False),
            trailing=replace(
                p.trailing,
                enabled=True,
                activation_gain_pct=2.0,
                callback_pct=1.0,
                minimum_lock_gain_pct=0.3,
                profit_lock_ratio=0.5,
            ),
        )
    raise ValueError(mode)


def market_ma60_ratio(df: pl.DataFrame) -> dict[str, float]:
    """信号日等权指数 / MA60；只用于 §1049 的趋势切换。"""
    md = df.select("trade_date", "mkt_ret").unique().drop_nulls().sort("trade_date")
    if md.height < 60:
        return {}
    idx = (1.0 + md["mkt_ret"].to_numpy() / 100.0).cumprod()
    ma = np.convolve(idx, np.ones(60) / 60.0, mode="valid")
    dates = md["trade_date"].to_list()
    return {d: float(idx[i] / ma[i - 59]) for i, d in enumerate(dates) if i >= 59 and ma[i - 59] > 0}


def _codes_map(df: pl.DataFrame) -> dict:
    out = {}
    for (code,), part in df.partition_by("code", as_dict=True, maintain_order=True).items():
        # 用复权价回测，避免除权除息造成假跳空；比例与原始 OHLC 一致。
        kf = part["ac"].to_numpy() / part["close"].to_numpy()
        out[code] = {
            "trade_date": part["trade_date"].to_numpy(),
            "open": part["ao"].to_numpy(),
            "high": part["high"].to_numpy() * kf,
            "low": part["low"].to_numpy() * kf,
            "close": part["ac"].to_numpy(),
            "volume": part["volume"].to_numpy(),
        }
    return out


def run_sim(df, sig_col, score_col, policy, s, e, k_slots, max_new, horizon, policy_by_date=None, market_exit=None):
    sub = df.filter((c("trade_date") >= s) & (c("trade_date") <= e)).sort("code", "trade_date")
    dates = sorted(sub["trade_date"].unique().to_list())
    codes = _codes_map(sub)
    idx = {code: {d: i for i, d in enumerate(a["trade_date"])} for code, a in codes.items()}

    by_date = defaultdict(list)
    x = (
        sub.filter(c(sig_col))
        .select("code", "trade_date", score_col)
        .sort(["trade_date", score_col], descending=[False, True])
    )
    for code, d, _score in x.iter_rows():
        by_date[d].append(code)

    held: dict[str, str] = {}
    rows = []
    for d in dates:
        held = {code: x for code, x in held.items() if x > d}
        if len(held) >= k_slots:
            continue
        new_count = 0
        for code in by_date.get(d, []):
            if new_count >= max_new or len(held) >= k_slots:
                break
            if code in held:
                continue
            a = codes.get(code)
            if a is None:
                continue
            i0 = idx[code].get(d)
            if i0 is None:
                continue
            e0 = i0 + 1
            n = len(a["trade_date"])
            if e0 >= n:
                continue
            xd = market_exit.get(str(d)) if market_exit else None
            bars = []
            for j in range(e0, min(n, e0 + max(horizon, 2) + 8)):
                if xd and str(a["trade_date"][j]) > xd:
                    break
                o = float(a["open"][j])
                h = float(a["high"][j])
                l = float(a["low"][j])
                cl = float(a["close"][j])
                if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(l) and np.isfinite(cl)):
                    break
                if o <= 0 or h <= 0 or l <= 0 or cl <= 0:
                    break
                h = max(h, o, cl)
                l = min(l, o, cl)
                vraw = a["volume"][j]
                v = float(vraw) if vraw is not None and np.isfinite(vraw) else 0.0
                bars.append(
                    BarInput(
                        code=code,
                        trade_date=str(a["trade_date"][j]),
                        open=o,
                        high=h,
                        low=l,
                        close=cl,
                        volume=v,
                        tradable=bool(v > 0),
                    )
                )
            if len(bars) < 2:
                continue
            try:
                pol = policy_by_date(d) if policy_by_date else policy
                if market_exit and pol is not None:
                    pol = replace(pol, horizon_days=len(bars))
                res = simulate(
                    RecommendationInput(code=code, signal_date=str(d), signal_price=float(a["close"][i0])),
                    tuple(bars),
                    pol,
                )  # a["close"] 已是复权收盘 ac
            except Exception as exc:  # noqa: BLE001 - 单票异常不阻断整日
                print("SIM_SKIP", code, d, type(exc).__name__, exc, flush=True)
                continue
            tr = res.trade
            if not tr.executed or not tr.exit_date:
                continue
            rows.append(
                {
                    "code": code,
                    "entry": tr.entry_date,
                    "exit": tr.exit_date,
                    # 统一成与 realistic.run_real 同口径的小数收益（net_return_pct 是百分数）
                    "ret": float(tr.net_return_pct) / 100.0,
                    "days": int(tr.holding_days),
                    "exit_reason": tr.exit_reason or "",
                    "entry_px": float(tr.entry_price or 0.0),
                    "exit_px": float(tr.exit_price or 0.0),
                }
            )
            held[code] = tr.exit_date
            new_count += 1
    schema = {
        "code": pl.Utf8,
        "entry": pl.Utf8,
        "exit": pl.Utf8,
        "ret": pl.Float64,
        "days": pl.Int64,
        "exit_reason": pl.Utf8,
        "entry_px": pl.Float64,
        "exit_px": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def daily_curve(df: pl.DataFrame, t: pl.DataFrame, k_slots: int) -> tuple[list[str], np.ndarray]:
    if t.height == 0:
        return [], np.array([1.0])
    px = {
        code: dict(zip(part["trade_date"].to_list(), part["ac"].to_list()))
        for (code,), part in df.select("code", "trade_date", "ac").partition_by("code", as_dict=True, maintain_order=True).items()
    }
    all_dates = sorted(df["trade_date"].unique().to_list())
    date_idx = {d: i for i, d in enumerate(all_dates)}
    pnl = defaultdict(float)
    for row in t.iter_rows(named=True):
        code, entry, exit_ = row["code"], row["entry"], row["exit"]
        if code not in px or entry not in date_idx or exit_ not in date_idx:
            continue
        i0, i1 = date_idx[entry], date_idx[exit_]
        prev = row["entry_px"]
        for j in range(i0, i1 + 1):
            d = all_dates[j]
            if j == i1:
                mark = row["exit_px"]
            else:
                mark = px[code].get(d)
            if mark is None or prev <= 0:
                continue
            pnl[d] += (mark / prev - 1.0) / k_slots
            prev = mark
    ds = [d for d in all_dates if d in pnl]
    if not ds:
        return [], np.array([1.0])
    eq = 1.0 + np.cumsum([pnl[d] for d in ds])
    return ds, eq


def metrics(t: pl.DataFrame, df: pl.DataFrame, k_slots: int) -> dict:
    if t.height == 0:
        return {"n": 0}
    r = t["ret"].to_numpy()
    win = float((r > 0).mean())
    w = r[r > 0].sum()
    l = -r[r < 0].sum()
    ds, eq = daily_curve(df, t, k_slots)
    base = metrics_real(t.select("ret", "days"), (ds, eq))
    base["win"] = round(100 * win, 1)
    base["pf"] = round(float(w / l), 2) if l > 0 else 99.0
    base["streak"] = float(_streak(t))
    base["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
    try:
        b = cohort_bench(df, t.select("code", "entry", "exit"))
        base["bench"] = round(100 * float(b["bench"].mean()), 2)
        base["excess"] = round(base["avg"] - base["bench"], 2)
        base["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
    except Exception:  # noqa: BLE001
        base["bench"] = None
        base["excess"] = None
        base["ex_win"] = None
    mix = t.group_by("exit_reason").len().sort("len", descending=True)
    base["exit_mix"] = {str(k): int(v) for k, v in zip(mix["exit_reason"].to_list(), mix["len"].to_list())}
    return {k: (round(float(v), 4) if isinstance(v, (int, float)) and v is not None else v) for k, v in base.items()}


def _streak(t: pl.DataFrame) -> int:
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def main() -> None:
    phase_arg, out = sys.argv[1], sys.argv[2]
    phases = [p for p in phase_arg.split(",") if p in PHASES]
    if not phases:
        raise SystemExit(f"unknown phase: {phase_arg}")

    df = prep()
    print("panel", df.shape, flush=True)
    keep = [
        "code", "trade_date", "open", "high", "low", "close", "ac", "volume", "liq", "total_mv", "ao",
        "mkt_ret", "rsi6", "S_MRSI2_DIP", "S_MRSI6_BROAD", "S_MRSI6_CAP", "S_MRSI14_BROAD", "S_ALL_BROAD", "S_ALL_DIP",
        "sc_amt", "sc_amt_today", "sc_dip", "sc_strong",
    ]
    keep = [x for x in dict.fromkeys(keep) if x in df.columns]
    df = df.select(keep)
    regime = market_ma60_ratio(df)
    # 市场 RSI6 归一化日：信号日之后第一个 rsi6>50 的交易日；最多看 5 日。
    md = df.select("trade_date", "rsi6").unique().drop_nulls().sort("trade_date")
    mdates = md["trade_date"].to_list()
    mrsi6 = dict(zip(mdates, md["rsi6"].to_list()))
    market_exit: dict[str, str] = {}
    for i, d in enumerate(mdates):
        xd = mdates[min(i + 5, len(mdates) - 1)]
        for j in range(i + 1, min(i + 6, len(mdates))):
            if float(mrsi6.get(mdates[j], 0.0)) > 50.0:
                xd = mdates[j]
                break
        market_exit[d] = xd

    od = os.path.dirname(out) or "."
    os.makedirs(od, exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}

    for ph in phases:
        s, e = PHASES[ph]
        for name, sig, score, horizon, max_new, k_slots, mode in CONFIGS:
            if mode == "REGIME":
                weak = build_policy(3, "PROD")
                strong = build_policy(5, "TP3")
                policy = None
                policy_by_date = lambda d, w=weak, st=strong: w if regime.get(d, 1.0) < 1.0 else st
                horizon = max(horizon, 5)
                me = None
            elif mode == "MKT_EXIT":
                policy = build_policy(5, "TP3")
                policy_by_date = None
                me = market_exit
            else:
                policy = build_policy(horizon, mode)
                policy_by_date = None
                me = None
            t = run_sim(df, sig, score, policy, s, e, k_slots, max_new, horizon,
                        policy_by_date=policy_by_date, market_exit=me)
            m = metrics(t, df, k_slots)
            info = {
                "n": int(t.height),
                "sigdays": int(df.filter(c(sig) & (c("trade_date") >= s) & (c("trade_date") <= e))["trade_date"].n_unique()),
                "avg_days": round(float(t["days"].mean()), 2) if t.height else 0.0,
            }
            res.setdefault(name, {})[ph] = m
            res.setdefault("_info", {}).setdefault(name, {})[ph] = info
            if t.height:
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            print(name, ph, m, info, flush=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done ->", out, flush=True)


if __name__ == "__main__":
    main()
