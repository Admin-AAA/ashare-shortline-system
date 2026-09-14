"""集合竞价因子的 v4 × 三窗 canonical 复核（履行 L-078 规则②）。

为什么要跑：L-078 测集合竞价时**只有部分覆盖**（冻结约 43 日 / 前瞻约 81 日），
而它自己的规则②写明："任何按交易日回填的辅助表，因子斜率必须在窗口覆盖补齐后
复核；覆盖不全会制造跨窗假阳性"（同一教训里 rzmre 的斜率从 −0.61 变 −0.009）。
另外 L-078 用的是 **v2 策略 + 两个窗口**，既不是当前生产 v4，也从未包含第三窗。

本脚本因此做三件事：
1. 报每个窗口"选中卡里有多少张真的带竞价数据"（决定规则有没有咬口）；
2. 用 v4 生产选择 + 真实递补，测竞价换手/竞价跳空/竞价量比/竞价额 的
   上/下尾部否决（bottom/top 5%、10%）；
3. 给配对日 Δcohort、bootstrap CI、逐月同号、被砍/递补的净收益，并用
   `evaluate_challenger` 判闸门（L-010）。

口径：竞价数据是信号日 T 早盘 09:25 的量，T 收盘决策时已可得 → 无未来函数；
分位按**全市场当日截面**排名（与 L-078 的 build_pct 同构）。缺竞价数据的卡
保守保留（不罚），与 L-078 一致。

只读，不改生产代码。
用法：cd backend && .venv/bin/python scripts/research_auction_three_window_v4.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

from sqlalchemy import create_engine, text  # noqa: E402

from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.production_policy import (  # noqa: E402
    load_production_selection_policy,
    evaluate_selection_policy,
    SelectionPolicyContext,
)
from app.services.backtest.challenger_decision import (  # noqa: E402
    PerformanceSnapshot,
    evaluate_challenger,
)
from app.services.backtest.policy_experiment import (  # noqa: E402
    ScoredCandidateSnapshot,
    _thaw_json,
    _simulate_card,
)
from scripts.research_canonical_two_window import (  # noqa: E402
    _bootstrap_ci,
    build_forward_canonical,
    build_market_snapshots,
)
from scripts.research_third_window import load_run3  # noqa: E402

OUT = B / "output" / "research"
SCORE_LINE, OUTPUT_COUNT = 72.0, 10
THRS = (0.05, 0.10)
FIELDS = (
    ("auc_turn", "竞价换手", "turnover_rate"),
    ("auc_vr", "竞价量比", "volume_ratio"),
    ("auc_amt", "竞价额", "amount"),
)


def _db_url() -> str:
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("no db url")


def _pct_map(sql: str, params: dict | None = None) -> dict:
    """按 (trade_date, code) 给全市场当日截面分位。"""
    eng = create_engine(_db_url())
    with eng.connect() as c:
        df = pd.read_sql(text(sql), c, params=params or {})
    if df.empty:
        return {}
    df["pct"] = df.groupby("trade_date")["v"].rank(pct=True)
    return {(str(r.trade_date), str(r.code)): float(r.pct) for r in df.itertuples()}


def _maps() -> dict:
    out = {}
    for key, _label, col in FIELDS:
        if col in ("turnover_rate", "volume_ratio", "amount"):
            out[key] = _pct_map(f"SELECT code,trade_date,{col} v FROM auction_data WHERE {col} IS NOT NULL")
    out["auc_gap"] = _pct_map("SELECT code,trade_date,price/pre_close-1 v FROM auction_data WHERE pre_close>0")
    return out


def _metrics(rows, dates) -> dict:
    if not rows:
        return {"n": 0, "avg": 0.0, "win": 0.0, "pf": float("nan"), "g": pd.Series(dtype=float)}
    d = pd.DataFrame(rows)
    gp, gl = d.net[d.net > 0].sum(), abs(d.net[d.net < 0].sum())
    return {
        "n": len(d), "avg": float(d.net.mean()), "win": float((d.net > 0).mean()),
        "pf": float(gp / gl) if gl > 1e-9 else float("inf"),
        "sum": float(d.net.sum()),
        "g": d.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0),
    }


def _arm(picks_fn, tox, dates) -> tuple:
    """两臂：全卡 vs 剔除 tox 卡（真实递补）。"""
    base, veto, rem, pro = [], [], [], []
    for dt, cards, netmap, day in picks_fn:
        ms = dict(getattr(day, "market_snapshot", {}) or {})
        keep = [c for c in cards if not tox(dt, c)]
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(
            cards, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms), _POLICY).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy(
            keep, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms), _POLICY).recommendations}
        cm = {str(c.get("code")): c for c in cards}

        def net(code):
            if netmap is not None:
                return netmap.get(code)
            tr = (_simulate_card(day, cm[code], _EP) or {}).get("trade")
            return float(tr.net_return_pct) if (tr is not None and tr.executed) else None

        for code in s0:
            v = net(code)
            if v is not None:
                base.append({"dt": dt, "net": v})
        for code in s1:
            v = net(code)
            if v is not None:
                veto.append({"dt": dt, "net": v})
        for code in s0 - s1:
            v = net(code)
            if v is not None:
                rem.append(v)
        for code in s1 - s0:
            v = net(code)
            if v is not None:
                pro.append(v)
    return base, veto, np.array(rem), np.array(pro)


def _report(label, dates, base, veto, rem, pro) -> dict:
    mb, mv = _metrics(base, dates), _metrics(veto, dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    dd = j.v - j.b
    lo, hi = _bootstrap_ci(dd.values, draws=5000)
    month = dd.groupby(dd.index.str[:7]).mean()
    dec = evaluate_challenger(
        PerformanceSnapshot(mb["avg"], mb["sum"], mb["win"] * 100, mb["pf"], 0.0),
        PerformanceSnapshot(mv["avg"], mv["sum"], mv["win"] * 100, mv["pf"], 0.0),
    )
    print(f"  [{label}] base n{mb['n']} 均{mb['avg']:+.3f} 胜{mb['win']:.3f} PF{mb['pf']:.2f} → "
          f"veto n{mv['n']} 均{mv['avg']:+.3f} 胜{mv['win']:.3f} PF{mv['pf']:.2f}")
    print(f"      cohort {mb['g'].mean():+.4f}→{mv['g'].mean():+.4f} "
          f"(Δ{mv['g'].mean()-mb['g'].mean():+.4f}) CI[{lo:+.4f},{hi:+.4f}] "
          f"逐月正{(np.sign(month) > 0).mean():.2f} 被砍n{len(rem)}"
          f"均{rem.mean() if len(rem) else float('nan'):+.3f} 递补n{len(pro)}"
          f"均{pro.mean() if len(pro) else float('nan'):+.3f} 闸门{dec.decision}")
    return {"base": {k: mb[k] for k in ("n", "avg", "win", "pf", "sum")},
            "veto": {k: mv[k] for k in ("n", "avg", "win", "pf", "sum")},
            "d_cohort": float(mv["g"].mean() - mb["g"].mean()), "ci": [lo, hi],
            "removed_n": int(len(rem)),
            "removed_avg": float(rem.mean()) if len(rem) else None,
            "promoted_n": int(len(pro)),
            "promoted_avg": float(pro.mean()) if len(pro) else None,
            "gate": dec.decision}


_POLICY = None
_EP = None


def main() -> None:
    global _POLICY, _EP
    _POLICY = load_production_selection_policy(version=4)
    _EP = ExecutionPolicy()
    pmaps = _maps()
    print(f"竞价分位图：{ {k: len(v) for k, v in pmaps.items()} }", flush=True)

    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    w1 = [(d.signal_date, _thaw_json(d.cards), None, d) for d in snap.days]
    fw = build_forward_canonical()
    snaps2 = build_market_snapshots(sorted(fw.dt.unique()))
    have2 = [d for d in sorted(fw.dt.unique()) if d in snaps2]
    fw = fw[fw.dt.isin(have2)]
    w2 = []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        w2.append((d, cards, {str(r.code): float(r.net) for r in gp.itertuples()},
                   type("S", (), {"market_snapshot": dict(snaps2[d])})()))
    r3 = load_run3()
    snaps3 = build_market_snapshots(sorted(r3.dt.unique()))
    have3 = [d for d in sorted(r3.dt.unique()) if d in snaps3]
    r3 = r3[r3.dt.isin(have3)]
    w3 = []
    for d, gp in r3.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        w3.append((d, cards, {str(r.code): float(r.net) for r in gp.itertuples()},
                   type("S", (), {"market_snapshot": dict(snaps3[d])})()))
    windows = (("W1", [d for d, *_ in w1], w1), ("W2", have2, w2), ("W3", have3, w3))

    res = {"policy": "production_selection.v4", "coverage": {}, "variants": {}}
    # 咬口诊断：选中卡里有多少张带竞价数据
    for w, dates, groups in windows:
        sel = 0
        hit = 0
        for dt, cards, netmap, day in groups:
            ms = dict(getattr(day, "market_snapshot", {}) or {})
            for c in evaluate_selection_policy(
                cards, SelectionPolicyContext(SCORE_LINE, OUTPUT_COUNT, ms), _POLICY
            ).recommendations:
                sel += 1
                if (dt, str(c.get("code"))) in pmaps["auc_turn"]:
                    hit += 1
        res["coverage"][w] = {"selected": sel, "with_auction": hit,
                              "pct": round(hit / max(1, sel) * 100, 1)}
        print(f"[{w}] 选中 {sel} 张，其中 {hit} 张有竞价数据（{hit/max(1,sel)*100:.1f}%）", flush=True)

    field_labels = [(k, lbl) for k, lbl, _col in FIELDS] + [("auc_gap", "竞价跳空")]
    for key, label in field_labels:
        pmap = pmaps.get(key) or {}
        for thr in THRS:
            for side in ("bottom", "top"):
                def tox(dt, c, t=thr, s=side, m=pmap):
                    v = m.get((dt, str(c.get("code"))))
                    if v is None:
                        return False
                    return v < t if s == "bottom" else v > 1 - t
                name = f"{label}|{side}{int(thr*100)}%"
                print(f"\n===== {name} =====", flush=True)
                per = {}
                for w, dates, groups in windows:
                    b, v, r, p = _arm(groups, tox, dates)
                    per[w] = _report(w, dates, b, v, r, p)
                res["variants"][name] = per

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "auction_three_window_v4.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
