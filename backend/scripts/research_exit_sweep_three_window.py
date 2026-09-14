"""出场参数的三窗系统搜索（performance-conscious：选择与 bar 只算一次）。

背景：第 8 轮发现出场侧是三窗唯一能把累计收益翻正的杠杆（关 trailing →
W1/W2/W3 累计 +614/+116/+22），但它压低胜率且在 W1/W2 过不了 evaluate_challenger。
本脚本把出场空间的网格一次跑完，回答两个问题：
  1. 收益最优（sum/avg）的出场配置是什么；
  2. 有没有配置能在**三窗同时**过 evaluate_challenger（≥1 收益项改善、
     ≤1 辅助项退化、≥1 辅助项改善）。

性能设计（对应用户的性能要求）：
- 选择只做一次（`_selected`）；出场不改选择，所以候选集全程复用。
- 每天只构造一次 `ScoredCandidateDay`（bar 从 daily_kbars 取一次），
  所有配置共用，避免 N 倍重复取数。
- 逐配置只跑 `_simulate_card`（纯计算、无 IO）；结果增量写 JSONL，可续跑/可中断。

只读，不改生产代码。
用法：
  cd backend && .venv/bin/python scripts/research_exit_sweep_three_window.py --stage base
  cd backend && .venv/bin/python scripts/research_exit_sweep_three_window.py --stage refine
"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.services.backtest.challenger_decision import (  # noqa: E402
    PerformanceSnapshot,
    evaluate_challenger,
)
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    _thaw_json,
    _simulate_card,
)
from app.engine.production_policy import load_production_selection_policy  # noqa: E402
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    _main_industries_from_db,
    _attach_main_industries,
)
from scripts.research_exit_policy_canonical import (  # noqa: E402
    _bars_for,
    _calendar,
    _closes_for,
    _selected,
)
import scripts.research_exit_policy_canonical as exit_harness  # noqa: E402

OUT = B / "output" / "research"
BAR_HORIZON = 9


def _grid(stage: str) -> list:
    base = ExecutionPolicy()
    if stage == "gap":
        # v3 最优点附近的第二轴扫描。
        #
        # ⚠️ 刻意**不扫** `gap_risk.stop_gap_loss_cap_pct`：它是**成交假设**而非交易规则
        # （execution_core: `price = max(bar_open, entry×(1-cap/100))`——把 −20% 的跳空
        # 按 −8% 记账）。降低它会让回测用更乐观的成交价刷出更高收益，没有任何真实机制，
        # 属于"改假设出数字"，禁止用于发布。此处固定为生产值 15%。
        #
        # 只扫两个**真规则**旋钮：`max_entry_gap_up_pct`（跳空超过阈值就不入场）
        # 与 `trailing.minimum_lock_gain_pct`（利润地板的最小锁利），外加同 bar 优先级。
        # v3 = act1.5 / cb0.8 / lock0.5 / TP off / SL5.5。
        cfgs = []
        for mge, minlock, prio in itertools.product(
            (4.0, 5.0, 6.0, 8.0, 10.0), (0.0, 0.3, 0.8, 1.5, 2.5),
            (base.intraday_exit_priority, __import__(
                "app.engine.execution_policy", fromlist=["IntradayExitPriority"]
            ).IntradayExitPriority.TAKE_PROFIT),
        ):
            ep = dataclasses.replace(
                base,
                take_profit=dataclasses.replace(base.take_profit, enabled=False),
                stop_loss=dataclasses.replace(base.stop_loss, default_loss_pct=5.5,
                                              maximum_loss_pct=5.7),
                trailing=dataclasses.replace(base.trailing, activation_gain_pct=1.5,
                                             minimum_lock_gain_pct=minlock),
                gap_risk=dataclasses.replace(base.gap_risk, stop_gap_loss_cap_pct=15.0,  # 固定生产值：不扫成交假设
                                             max_entry_gap_up_pct=mge),
                intraday_exit_priority=prio,
            )
            cfgs.append((f"mge{mge:g}_ml{minlock:g}_prio{prio.value[:4]}_cap15", ep))
        return [("baseline", base)] + cfgs
    cfgs = [("baseline", base)]
    tp_opts = [6.0, 8.0, None]
    sl_opts = [4.5, 5.5]
    if stage == "base":
        tr_opts = [
            (False, 2.0, 0.8, 0.5),   # 关 trailing
            (True, 2.0, 0.8, 0.5),    # 基线参数
            (True, 3.0, 0.8, 0.5),
            (True, 2.0, 2.0, 0.5),
            (True, 2.0, 0.8, 0.7),
        ]
    else:
        tr_opts = [
            (on, act, cb, lock)
            for on, act, cb, lock in itertools.product(
                (False, True), (1.5, 2.0, 3.0, 4.0), (0.8, 1.5, 2.5), (0.3, 0.5, 0.7))
            if not (not on and (act, cb, lock) != (2.0, 0.8, 0.5))
        ]
    for (on, act, cb, lock), tp, sl in itertools.product(tr_opts, tp_opts, sl_opts):
        tp_pol = base.take_profit if tp is not None else dataclasses.replace(base.take_profit, enabled=False)
        if tp is not None:
            tp_pol = dataclasses.replace(base.take_profit, default_gain_pct=tp)
        ep = dataclasses.replace(
            base,
            trailing=dataclasses.replace(base.trailing, enabled=on, mode=base.trailing.mode,
                                         activation_gain_pct=act, callback_pct=cb,
                                         profit_lock_ratio=lock),
            take_profit=tp_pol,
            # 校验要求 default_loss_pct <= maximum_loss_pct，放宽止损时必须同时抬上限
            stop_loss=dataclasses.replace(
                base.stop_loss, default_loss_pct=sl,
                maximum_loss_pct=max(base.stop_loss.maximum_loss_pct, sl + 0.2)),
        )
        name = f"tr{'on' if on else 'off'}_act{act}_cb{cb}_lk{lock}_tp{tp}_sl{sl}"
        cfgs.append((name, ep))
    # 去重（同名同参）
    seen, out = set(), []
    for n, ep in cfgs:
        k = repr(ep)
        if k in seen:
            continue
        seen.add(k)
        out.append((n, ep))
    return out


def _metrics(rows, dates) -> dict:
    if not rows:
        return {"n": 0, "win": 0.0, "avg": 0.0, "pf": float("nan"), "sum": 0.0,
                "cohort": 0.0, "mdd": 0.0, "g": pd.Series(dtype=float)}
    df = pd.DataFrame(rows)
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    g = df.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0)
    cum = g.cumsum()
    return {"n": len(df), "win": float((df.net > 0).mean()), "avg": float(df.net.mean()),
            "pf": float(gp / gl) if gl > 1e-9 else float("inf"),
            "sum": float(df.net.sum()), "cohort": float(g.mean()),
            "mdd": float((cum - cum.cummax()).min()) if len(cum) else 0.0, "g": g}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("base", "refine", "gap"), default="base")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else OUT / f"exit_sweep_{args.stage}.jsonl"

    exit_harness._POLICY = load_production_selection_policy(version=4)
    cal = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    mi = _main_industries_from_db(all_dates)
    for _w, _d, g in windows:
        _attach_main_industries(g, mi)
    with SessionLocal() as s:
        iv20 = load_ivol20_pct_map(s, all_dates)
        ivdn = load_ivol_dn20_pct_map(s, all_dates)
    picks = {w: _selected(g, iv20, ivdn) for w, _d, g in windows}

    # 每日 bar 只取一次；构造一次 ScoredCandidateDay 供所有配置复用
    days = {}
    bars, closes = {}, {}
    for w, _d, _g in windows:
        codes = sorted({c for _dt, c in picks[w]})
        need = {d for dt in {x[0] for x in picks[w]} for d in cal[idx[dt] + 1: idx[dt] + 1 + BAR_HORIZON]}
        bars.update(_bars_for(codes, need))
        closes.update(_closes_for(codes, {d for d, _c in picks[w]}))
    for w, dates, groups in windows:
        per_day = {}
        for dt, _cards, _nm, day in groups:
            sel = [(c, picks[w][(dt, c)]) for (d, c) in picks[w] if d == dt]
            if not sel:
                continue
            built = {code: tuple(bars[(code, x)] for x in cal[idx[dt] + 1: idx[dt] + 1 + BAR_HORIZON]
                                 if (code, x) in bars) for code, _card in sel}
            built = {k: v for k, v in built.items() if v}
            cards = []
            for code, card in sel:
                cc = dict(card)
                if not cc.get("recommend_price"):
                    cc["recommend_price"] = closes.get((code, dt))
                cards.append(cc)
            per_day[dt] = ScoredCandidateDay(
                signal_date=dt,
                market_snapshot=_thaw_json(getattr(day, "market_snapshot", {}) or {}),
                cards=tuple(cards), outcome_bars=built)
        days[w] = per_day
    print(f"[setup] picks W1={len(picks['W1'])} W2={len(picks['W2'])} W3={len(picks['W3'])}；"
          f"bars={len(bars):,}；days W1={len(days['W1'])} W2={len(days['W2'])} W3={len(days['W3'])}",
          flush=True)

    cfgs = _grid(args.stage)
    print(f"[sweep] stage={args.stage} 配置数={len(cfgs)} → {out_path}", flush=True)
    base_metrics, base_g = {}, {}
    t0 = time.time()
    with out_path.open("w", encoding="utf-8") as fh:
        for ci, (name, ep) in enumerate(cfgs, 1):
            rec = {"name": name, "windows": {}}
            for w, dates, _groups in windows:
                rows = []
                for dt, day in days[w].items():
                    for card in day.cards:
                        tr = (_simulate_card(day, card, ep) or {}).get("trade")
                        if tr is not None and tr.executed:
                            rows.append({"dt": dt, "net": float(tr.net_return_pct)})
                m = _metrics(rows, dates)
                g = m.pop("g")
                rec["windows"][w] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
                if name == "baseline":
                    base_metrics[w] = m
                    base_g[w] = g
                elif w in base_metrics:
                    b = base_metrics[w]
                    dec = evaluate_challenger(
                        PerformanceSnapshot(b["avg"], b["sum"], b["win"] * 100, b["pf"], b["mdd"]),
                        PerformanceSnapshot(m["avg"], m["sum"], m["win"] * 100, m["pf"], m["mdd"]),
                    )
                    rec["windows"][w]["gate"] = dec.decision
                    rec["windows"][w]["d_avg"] = round(m["avg"] - b["avg"], 4)
                    rec["windows"][w]["d_sum"] = round(m["sum"] - b["sum"], 2)
                    rec["windows"][w]["d_win_pp"] = round((m["win"] - b["win"]) * 100, 2)
            # baseline 的行不进结果文件（只做基准）
            if name != "baseline":
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            if ci % 10 == 0 or ci == len(cfgs):
                print(f"  {ci}/{len(cfgs)} {name} {(time.time()-t0)/ci:.1f}s/cfg", flush=True)
    print(f"[sweep] 完成，用时 {(time.time()-t0)/60:.1f}min，结果 {out_path}", flush=True)


if __name__ == "__main__":
    main()
