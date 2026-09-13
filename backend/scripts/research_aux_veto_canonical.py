"""龙虎榜上榜 / 两融融资买入 的生产同构双窗口 canonical 否决终审（只读）。

两个候选 VETO（T 收盘已知，买 T+1 开盘）：
  lhb_event  : 信号日登上龙虎榜(dragon_tiger) → 上榜后 fade，否决该候选
  rzmre_hi   : 当日融资买入额 rzmre 处全市场日截面 topX%（杠杆拥挤）→ 否决

两臂=evaluate_selection_policy(全卡) vs (排序前剔除 tox 卡让下一名递补)，与生产同构；
冻结走 _simulate_card 真实因果出场，前瞻 join canonical 净收益；配对日差 bootstrap5000、
逐月同号、被砍正期望票机会成本。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import (
    build_forward_canonical, build_market_snapshots, _bootstrap_ci)

OUT = B / "output" / "research"


def db_url():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError


def build_lookups():
    eng = create_engine(db_url())
    with eng.connect() as c:
        lhb = pd.read_sql(text("SELECT code,trade_date,buy_amount_yi,sell_amount_yi FROM dragon_tiger"), c)
        lhb_set = {(r.trade_date, str(r.code)) for r in lhb.itertuples()}
        lhb_bs = {(r.trade_date, str(r.code)): (float(r.buy_amount_yi or 0), float(r.sell_amount_yi or 0))
                  for r in lhb.itertuples()}
        mg = pd.read_sql(text("SELECT code,trade_date,rzmre FROM margin_detail WHERE rzmre IS NOT NULL"), c)
    mg["pct"] = mg.groupby("trade_date").rzmre.rank(pct=True)
    rzpct = {(r.trade_date, str(r.code)): float(r.pct) for r in mg.itertuples()}
    return lhb_set, lhb_bs, rzpct


def metrics(rows, all_dates):
    if not rows:
        return {"n": 0, "avg": 0, "win": 0, "pf": float("nan"), "g": pd.Series(dtype=float)}
    df = pd.DataFrame(rows)
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    g = df.groupby("dt").net.mean().reindex(sorted(all_dates)).fillna(0.0)
    return {"n": len(df), "avg": df.net.mean(), "win": (df.net > 0).mean(),
            "pf": gp / gl if gl > 1e-9 else float("inf"), "g": g}


def frozen_run(tox, pol, ep):
    snap = ScoredCandidateSnapshot.from_payload(
        __import__("json").loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    dates = sorted(d.signal_date for d in snap.days)
    base, veto, rem, pro = [], [], [], []
    for day in snap.days:
        dt = day.signal_date; cards = _thaw_json(day.cards); cm = {str(c.get("code")): c for c in cards}
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy([c for c in cards if not tox(dt, c)], SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations}
        def net(code):
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr and tr.executed) else None
        for code in s0:
            v = net(code);  base.append({"dt": dt, "code": code, "net": v}) if v is not None else None
        for code in s1:
            v = net(code);  veto.append({"dt": dt, "code": code, "net": v}) if v is not None else None
        for code in s0 - s1:
            v = net(code);  rem.append(v) if v is not None else None
        for code in s1 - s0:
            v = net(code);  pro.append(v) if v is not None else None
    return dates, base, veto, np.array(rem), np.array(pro)


def forward_run(tox, pol):
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates); have = [d for d in dates if d in snaps]
    fw = fw[fw.dt.isin(have)]
    base, veto, rem, pro = [], [], [], []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry),
                  "signal_price": 10.0} for r in gp.itertuples()]
        net = {str(r.code): float(r.net) for r in gp.itertuples()}
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(snaps[d])), pol).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy([c for c in cards if not tox(d, c)], SelectionPolicyContext(72., 10, dict(snaps[d])), pol).recommendations}
        for code in s0:
            if code in net: base.append({"dt": d, "code": code, "net": net[code]})
        for code in s1:
            if code in net: veto.append({"dt": d, "code": code, "net": net[code]})
        for code in s0 - s1:
            if code in net: rem.append(net[code])
        for code in s1 - s0:
            if code in net: pro.append(net[code])
    return have, base, veto, np.array(rem), np.array(pro)


def report(nm, dates, base, veto, rem, pro):
    mb, mv = metrics(base, dates), metrics(veto, dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    dd = j.v - j.b; lo, hi = _bootstrap_ci(dd.values, draws=5000)
    samepos = dd.groupby(dd.index.str[:7]).mean().pipe(lambda s: (np.sign(s) > 0).mean())
    npos = int((rem > 0).sum()) if len(rem) else 0
    print(f"  [{nm}] 逐笔 base n{mb['n']} 均{mb['avg']:+.3f} 胜{mb['win']:.3f} PF{mb['pf']:.2f} → "
          f"veto n{mv['n']} 均{mv['avg']:+.3f} 胜{mv['win']:.3f} PF{mv['pf']:.2f}")
    print(f"      日cohort {mb['g'].mean():+.4f}→{mv['g'].mean():+.4f}(Δ{mv['g'].mean()-mb['g'].mean():+.4f}) "
          f"配对CI[{lo:+.4f},{hi:+.4f}] 逐月正{samepos:.2f} | 被砍n{len(rem)}均{rem.mean() if len(rem) else float('nan'):+.3f}/正期望{npos}张 "
          f"递补n{len(pro)}均{pro.mean() if len(pro) else float('nan'):+.3f}")


def main():
    lhb_set, lhb_bs, rzpct = build_lookups()
    print("lookups: lhb", len(lhb_set), "rzmre pct", len(rzpct))
    pol = load_production_selection_policy(version=2); ep = ExecutionPolicy()

    print("\n########## 候选1: 龙虎榜上榜否决（一刀切） ##########")
    tox = lambda dt, c: (dt, str(c.get("code"))) in lhb_set
    d, b, v, r, p = frozen_run(tox, pol, ep); report("冻结", d, b, v, r, p)
    d, b, v, r, p = forward_run(tox, pol); report("前瞻", d, b, v, r, p)

    for cut, tag in [(1.0, "买卖比<1(净卖出)"), (0.7, "买卖比<0.7(重度净卖出)")]:
        print(f"\n########## 候选1b: 仅否决弱势龙虎榜[{tag}] ##########")
        def tox(dt, c, cut=cut):
            bs = lhb_bs.get((dt, str(c.get("code"))))
            return bs is not None and bs[1] > 0 and bs[0] / bs[1] < cut
        d, b, v, r, p = frozen_run(tox, pol, ep); report("冻结", d, b, v, r, p)
        d, b, v, r, p = forward_run(tox, pol); report("前瞻", d, b, v, r, p)

    for thr in (0.95, 0.90):
        print(f"\n########## 候选2: 融资买入 rzmre 日截面 top{int(round((1-thr)*100))}% 否决 (pct>{thr}) ##########")
        tox = lambda dt, c, t=thr: (v := rzpct.get((dt, str(c.get("code"))))) is not None and v > t
        d, b, v, r, p = frozen_run(tox, pol, ep); report("冻结", d, b, v, r, p)
        d, b, v, r, p = forward_run(tox, pol); report("前瞻", d, b, v, r, p)


if __name__ == "__main__":
    main()
