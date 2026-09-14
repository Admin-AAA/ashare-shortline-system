"""出场策略的 canonical 三窗测量（W2/W3 不再受"缓存净收益"限制）。

背景：此前的三窗收益口径里，W2 前瞻与 W3 第三窗的净收益来自一次固定回放的
`backtest_trades` 缓存，**出场参数无法改动**——所以任何出场侧候选都只有 W1 可测，
重演了 L-112 说的"静默只在单窗生效"。

本轮把出场侧解锁：
1. 选择阶段沿用真实 `evaluate_selection_policy`（v4 artifact + 预附 ivol 分位）。
2. 被选中的 (signal_date, code) 用 `daily_kbars` 重建 T+1 起的 9 根日 K
   （`BarInput`），再交给 `_simulate_card` 真实因果出场模拟。
3. **正确性前置**：W1 的冻结快照自带 outcome bars 与真实净收益，先断言
   「用库重建的 bar → 与冻结快照逐笔净收益完全一致」，通过后才解释 W2/W3。
   （已实测 12 日 164 笔 max|Δ|=0.0；本脚本内每次运行都会重新断言。）

出场变体用 `dataclasses.replace` 改 `ExecutionPolicy` 的嵌套策略，不改生产代码。
闸门用 `evaluate_challenger()`（L-010 唯一发布判定）。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_exit_policy_canonical.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.backtest.contracts import BarInput  # noqa: E402
from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy,
    evaluate_selection_policy,
    SelectionPolicyContext,
)
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateDay,
    _simulate_card,
)
from app.services.backtest.challenger_decision import (  # noqa: E402
    PerformanceSnapshot,
    evaluate_challenger,
)
from app.services.risk_features import (  # noqa: E402
    load_ivol20_pct_map,
    load_ivol_dn20_pct_map,
    attach_ivol20_pct,
    attach_ivol_dn20_pct,
)
from scripts.research_canonical_two_window import _bootstrap_ci  # noqa: E402
from scripts.research_riskveto_canonical import frozen_groups, canon_groups  # noqa: E402
from scripts.research_off_main_theme_penalty import (  # noqa: E402
    EXPECTED_BASELINE,
    OUTPUT_COUNT,
    SCORE_LINE,
)

OUT = B / "output" / "research"
BAR_HORIZON = 9  # 与冻结快照一致的 T+1 起 bar 数
_POLICY = None


def _variants():
    base = ExecutionPolicy()
    tp, tr = base.take_profit, base.trailing
    sl = base.stop_loss
    return {
        "baseline": base,
        "tp_off(持有到期)": dataclasses.replace(base, take_profit=dataclasses.replace(tp, enabled=False)),
        "tp_off+trail_off(纯持有)": dataclasses.replace(
            base,
            take_profit=dataclasses.replace(tp, enabled=False),
            trailing=dataclasses.replace(tr, enabled=False),
        ),
        "trail_off(只留止盈止损)": dataclasses.replace(base, trailing=dataclasses.replace(tr, enabled=False)),
        "tp_10": dataclasses.replace(base, take_profit=dataclasses.replace(tp, default_gain_pct=10.0)),
        "tp_15": dataclasses.replace(base, take_profit=dataclasses.replace(tp, default_gain_pct=15.0)),
        "trail_act_4": dataclasses.replace(base, trailing=dataclasses.replace(tr, activation_gain_pct=4.0)),
        "stop_6.0": dataclasses.replace(
            base, stop_loss=dataclasses.replace(sl, default_loss_pct=6.0, maximum_loss_pct=6.2)
        ),
        # trail_off 是第一组让三窗 sum_net 全正的配置；下面测"提高胜率"的搭配
        # （更小止盈 / 更快砍亏）能否在不破坏三窗为正的前提下把 WR/PF 拉向目标。
        "trail_off+tp_5": dataclasses.replace(
            base,
            trailing=dataclasses.replace(tr, enabled=False),
            take_profit=dataclasses.replace(tp, default_gain_pct=5.0),
        ),
        "trail_off+tp_4": dataclasses.replace(
            base,
            trailing=dataclasses.replace(tr, enabled=False),
            take_profit=dataclasses.replace(tp, default_gain_pct=4.0),
        ),
        "trail_off+tp_5+stop_3.5": dataclasses.replace(
            base,
            trailing=dataclasses.replace(tr, enabled=False),
            take_profit=dataclasses.replace(tp, default_gain_pct=5.0),
            stop_loss=dataclasses.replace(sl, default_loss_pct=3.5),
        ),
        "trail_off+tp_6+stop_6": dataclasses.replace(
            base,
            trailing=dataclasses.replace(tr, enabled=False),
            stop_loss=dataclasses.replace(sl, default_loss_pct=6.0, maximum_loss_pct=6.2),
        ),
    }


def _calendar() -> list:
    with SessionLocal() as s:
        return [r[0] for r in s.execute(
            text("select distinct trade_date from daily_kbars order by trade_date")).all()]


def _bars_for(codes, dates):
    if not codes or not dates:
        return {}
    with SessionLocal() as s:
        rows = s.execute(
            text(
                "select code, trade_date, open, high, low, close, volume from daily_kbars "
                "where code in :cs and trade_date in :ds"
            ),
            {"cs": tuple(codes), "ds": tuple(sorted(dates))},
        ).all()
    out = {}
    for code, d, o, h, lo, c, v in rows:
        out[(str(code), d)] = BarInput(
            code=str(code), trade_date=d,
            open=float(o), high=float(h), low=float(lo), close=float(c),
            volume=float(v) if v is not None else None, tradable=True,
        )
    return out


def _closes_for(codes, dates):
    if not codes or not dates:
        return {}
    with SessionLocal() as s:
        rows = s.execute(
            text("select code, trade_date, close from daily_kbars where code in :cs and trade_date in :ds"),
            {"cs": tuple(codes), "ds": tuple(sorted(dates))},
        ).all()
    return {(str(c), d): float(v) for c, d, v in rows if v is not None}


def _selected(groups, iv20map, ivdnmap):
    """基线选择结果：{(dt, code): card}。出场不改选择，故只算一次。"""
    picks = {}
    for dt, cards, _netmap, dayobj in groups:
        ms = dict(getattr(dayobj, "market_snapshot", {}) or {}) if dayobj is not None else {}
        cc = [dict(c) for c in cards]
        attach_ivol20_pct(cc, dt, iv20map)
        attach_ivol_dn20_pct(cc, dt, ivdnmap)
        ctx = SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms)
        for c in evaluate_selection_policy(cc, ctx, _POLICY).recommendations:
            picks[(dt, str(c.get("code")))] = dict(c)
    return picks


def _sim_day(signal_date, day, picked_cards, ep, bars=None, closes=None):
    """picked_cards: [(code, card)]；bars=None → 用 day 自带的 outcome_bars。"""
    picked_cards = [
        (code, {**card, "recommend_price": card.get("recommend_price") or (closes or {}).get((code, signal_date))})
        if not card.get("recommend_price") else (code, card)
        for code, card in picked_cards
    ]
    use_day = day
    if bars is not None:
        built = {}
        for code, _card in picked_cards:
            bs = tuple(bars[(code, d)] for d in bars_dates[signal_date] if (code, d) in bars)
            if bs:
                built[code] = bs
        use_day = ScoredCandidateDay(
            signal_date=signal_date,
            market_snapshot=dict(day.market_snapshot),
            cards=tuple(c for _code, c in picked_cards),
            outcome_bars=built,
        )
    rows = []
    for code, card in picked_cards:
        tr = (_simulate_card(use_day, card, ep) or {}).get("trade")
        if tr is not None and tr.executed:
            rows.append({"dt": signal_date, "code": code, "net": float(tr.net_return_pct)})
    return rows


def _metrics(rows, dates):
    if not rows:
        return {"n": 0, "win": 0.0, "avg": 0.0, "pf": float("nan"), "sum_net": 0.0,
                "cohort": 0.0, "mdd": 0.0, "g": pd.Series(dtype=float)}
    df = pd.DataFrame(rows)
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    g = df.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0)
    cum = g.cumsum()
    return {
        "n": len(df), "win": float((df.net > 0).mean()), "avg": float(df.net.mean()),
        "pf": float(gp / gl) if gl > 1e-9 else float("inf"),
        "sum_net": float(df.net.sum()), "cohort": float(g.mean()),
        "mdd": float((cum - cum.cummax()).min()) if len(cum) else 0.0, "g": g,
    }


bars_dates = {}


def main():
    global _POLICY
    _POLICY = load_production_selection_policy(version=4)
    cal = _calendar()
    idx = {d: i for i, d in enumerate(cal)}
    f_dates, f_groups = frozen_groups()
    w2_dates, w2_groups = canon_groups("forward")
    w3_dates, w3_groups = canon_groups("third")
    windows = (("W1", f_dates, f_groups), ("W2", w2_dates, w2_groups), ("W3", w3_dates, w3_groups))
    all_dates = sorted({d for _w, ds, _g in windows for d in ds})
    with SessionLocal() as s:
        iv20map = load_ivol20_pct_map(s, all_dates)
        ivdnmap = load_ivol_dn20_pct_map(s, all_dates)
    for w, dates, groups in windows:
        for d in dates:
            bars_dates[d] = cal[idx[d] + 1: idx[d] + 1 + BAR_HORIZON]

    picks = {w: _selected(g, iv20map, ivdnmap) for w, _d, g in windows}
    codes_needed = {w: sorted({c for _d, c in picks[w]}) for w, _d, _g in windows}
    bars, closes = {}, {}
    for w, _d, _g in windows:
        need_dates = {d for dt in {x[0] for x in picks[w]} for d in bars_dates[dt]}
        bars.update(_bars_for(codes_needed[w], need_dates))
        closes.update(_closes_for(codes_needed[w], {x[0] for x in picks[w]}))
    print(f"picks W1={len(picks['W1'])} W2={len(picks['W2'])} W3={len(picks['W3'])}；bars={len(bars):,}", flush=True)

    # W2/W3 的权威基线来自缓存回放的 netmap；重建基线只用于算配对 Δ。
    cached = {}
    for w, dates, groups in windows:
        rows = [{"dt": dt, "code": str(c), "net": float(v)}
                for dt, _cards, netmap, _day in groups if netmap
                for c, v in netmap.items() if v is not None and (dt, str(c)) in picks[w]]
        cached[w] = _metrics(rows, dates)
    print("权威基线(缓存回放)：" + " ".join(
        f"{w} n{cached[w]['n']} 均{cached[w]['avg']:+.4f} 胜{cached[w]['win']*100:.1f}% 汇总{cached[w]['sum_net']:+.2f}"
        for w in ("W2", "W3")), flush=True)

    # 一致性前置（L-112）：W1 用库重建的 bar 必须与冻结快照逐笔净收益完全一致。
    ep0 = ExecutionPolicy()
    checked = 0
    for dt, _cards, _nm, day in f_groups:
        sel = [(c, picks["W1"][(dt, c)]) for (d, c) in picks["W1"] if d == dt]
        sel = [(c, card) for c, card in sel]
        if not sel:
            continue
        a = _sim_day(dt, day, sel, ep0)
        b = _sim_day(dt, day, sel, ep0, bars=bars)
        am = {r["code"]: r["net"] for r in a}
        bm = {r["code"]: r["net"] for r in b}
        if set(am) != set(bm):
            raise SystemExit(f"[W1 一致性失败] {dt} 选中集合不一致 frozen={len(am)} rebuilt={len(bm)}")
        for code, v in am.items():
            if abs(v - bm[code]) > 1e-9:
                raise SystemExit(f"[W1 一致性失败] {dt} {code} frozen={v} rebuilt={bm[code]}")
        checked += len(am)
    print(f"W1 一致性校验通过：{checked} 笔库重建净收益 == 冻结快照净收益", flush=True)

    variants = _variants()
    res = {"policy": "production_selection.v4", "score_line": SCORE_LINE,
           "evidence_scope": "diagnostic path A; DB-rebuilt daily bars; not live_forward",
           "bar_rebuild": "T+1 起 9 根日K from daily_kbars；W1 冻结快照仅用于一致性校验",
           "windows": {}}
    by_variant = {}
    for name, ep in variants.items():
        print(f"\n===== 出场变体：{name} =====", flush=True)
        rec = {}
        for w, dates, groups in windows:
            rows = []
            for dt, cards, _nm, day in groups:
                sel = [(c, picks[w][(dt, c)]) for (d, c) in picks[w] if d == dt]
                if not sel:
                    continue
                # W1 用冻结快照自带的 outcome bars（权威）；W2/W3 用库重建 bars。
                rows += _sim_day(dt, day, sel, ep, bars=None if w == "W1" else bars, closes=closes)
            m = _metrics(rows, dates)
            rec[w] = m
            print(f"  {w} n{m['n']} 胜{m['win']*100:.1f}% 均{m['avg']:+.4f} PF{m['pf']:.3f} "
                  f"sum{m['sum_net']:+.2f} cohort{m['cohort']:+.4f} MDD{m['mdd']:+.2f}", flush=True)
        by_variant[name] = rec
    base = by_variant["baseline"]
    for name, rec in by_variant.items():
        if name == "baseline":
            continue
        gate = {}
        for w in ("W1", "W2", "W3"):
            b, c = base[w], rec[w]
            idxs = b["g"].index.union(c["g"].index)
            dd = (c["g"].reindex(idxs).fillna(0.0) - b["g"].reindex(idxs).fillna(0.0)).values
            lo, hi = _bootstrap_ci(dd, draws=5000)
            month = pd.Series(dd, index=idxs).groupby(lambda x: str(x)[:7]).mean()
            gate[w] = {
                "d_cohort": float(np.mean(dd)), "ci": [lo, hi],
                "month_same_pos": float((month > 0).mean()) if len(month) else float("nan"),
                "decision": evaluate_challenger(
                    PerformanceSnapshot(b["avg"], b["sum_net"], b["win"] * 100, b["pf"], b["mdd"]),
                    PerformanceSnapshot(c["avg"], c["sum_net"], c["win"] * 100, c["pf"], c["mdd"]),
                ).decision,
            }
        print(f"\n[{name}] " + " | ".join(
            f"{w} Δcohort{gate[w]['d_cohort']:+.4f} CI[{gate[w]['ci'][0]:+.3f},{gate[w]['ci'][1]:+.3f}] "
            f"闸门{gate[w]['decision']}" for w in ("W1", "W2", "W3")), flush=True)
        res["windows"][name] = {
            "metric": {w: {k: rec[w][k] for k in ("n", "win", "avg", "pf", "sum_net", "cohort", "mdd")}
                       for w in ("W1", "W2", "W3")},
            "vs_baseline": gate,
        }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "exit_policy_canonical.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(f"\nsaved {path}", flush=True)


if __name__ == "__main__":
    main()
