"""集合竞价因子的生产同构双窗口 canonical 终审（只读）。

全市场若"高竞价换手/高竞价跳空→未来5日更好"成立（正斜率），可落地形态=否决选中池中
处【日截面低位】的卡（低竞价参与/低开），让下一名递补。缺竞价数据的卡保守保留（不罚）。
两臂=evaluate_selection_policy(全卡) vs (剔除 tox 卡)，冻结 _simulate_card 因果、
前瞻 join canonical 净；配对日差 bootstrap5000、逐月同号、被砍正期望机会成本。
"""
from __future__ import annotations
import json, sys
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


def db_url():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError


def build_pct(field):
    eng = create_engine(db_url())
    with eng.connect() as c:
        df = pd.read_sql(text(f"SELECT code,trade_date,{field} v FROM auction_data WHERE {field} IS NOT NULL"), c)
    df["pct"] = df.groupby("trade_date").v.rank(pct=True)
    return {(r.trade_date, str(r.code)): float(r.pct) for r in df.itertuples()}


def metrics(rows, dates):
    if not rows:
        return {"n": 0, "avg": 0, "win": 0, "pf": float("nan"), "g": pd.Series(dtype=float)}
    d = pd.DataFrame(rows); gp, gl = d.net[d.net > 0].sum(), abs(d.net[d.net < 0].sum())
    return {"n": len(d), "avg": d.net.mean(), "win": (d.net > 0).mean(),
            "pf": gp / gl if gl > 1e-9 else float("inf"),
            "g": d.groupby("dt").net.mean().reindex(sorted(dates)).fillna(0.0)}


def frozen(tox, pol, ep):
    snap = ScoredCandidateSnapshot.from_payload(json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    dates = sorted(x.signal_date for x in snap.days); base, veto, rem, pro = [], [], [], []
    for day in snap.days:
        dt = day.signal_date; cards = _thaw_json(day.cards); cm = {str(c.get("code")): c for c in cards}
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy([c for c in cards if not tox(dt, c)], SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations}
        def nt(code):
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr and tr.executed) else None
        for code in s0:
            v = nt(code);  base.append({"dt": dt, "net": v}) if v is not None else None
        for code in s1:
            v = nt(code);  veto.append({"dt": dt, "net": v}) if v is not None else None
        for code in s0 - s1:
            v = nt(code);  rem.append(v) if v is not None else None
        for code in s1 - s0:
            v = nt(code);  pro.append(v) if v is not None else None
    return dates, base, veto, np.array(rem), np.array(pro)


def forward(tox, pol):
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]; fw = fw[fw.dt.isin(have)]
    base, veto, rem, pro = [], [], [], []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        net = {str(r.code): float(r.net) for r in gp.itertuples()}
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(snaps[d])), pol).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy([c for c in cards if not tox(d, c)], SelectionPolicyContext(72., 10, dict(snaps[d])), pol).recommendations}
        for code in s0:
            if code in net: base.append({"dt": d, "net": net[code]})
        for code in s1:
            if code in net: veto.append({"dt": d, "net": net[code]})
        for code in s0 - s1:
            if code in net: rem.append(net[code])
        for code in s1 - s0:
            if code in net: pro.append(net[code])
    return have, base, veto, np.array(rem), np.array(pro)


def report(nm, dates, base, veto, rem, pro):
    mb, mv = metrics(base, dates), metrics(veto, dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0); dd = j.v - j.b
    lo, hi = _bootstrap_ci(dd.values, draws=5000); sp = dd.groupby(dd.index.str[:7]).mean().pipe(lambda s: (np.sign(s) > 0).mean())
    print(f"  [{nm}] base n{mb['n']}均{mb['avg']:+.3f}胜{mb['win']:.3f}PF{mb['pf']:.2f} → veto n{mv['n']}均{mv['avg']:+.3f}胜{mv['win']:.3f}PF{mv['pf']:.2f}")
    print(f"      cohort {mb['g'].mean():+.4f}→{mv['g'].mean():+.4f}(Δ{mv['g'].mean()-mb['g'].mean():+.4f}) CI[{lo:+.4f},{hi:+.4f}] 逐月正{sp:.2f} "
          f"被砍n{len(rem)}均{rem.mean() if len(rem) else float('nan'):+.3f}/正期望{int((rem>0).sum()) if len(rem) else 0} 递补n{len(pro)}均{pro.mean() if len(pro) else float('nan'):+.3f}")


def main():
    pol = load_production_selection_policy(version=2); ep = ExecutionPolicy()
    for field, label in [("turnover_rate", "竞价换手"), ("price/pre_close", "竞价跳空")]:
        if field == "price/pre_close":
            eng = create_engine(db_url())
            with eng.connect() as c:
                au = pd.read_sql(text("SELECT code,trade_date,price,pre_close FROM auction_data WHERE pre_close>0"), c)
            au["v"] = au.price / au.pre_close - 1
            au["pct"] = au.groupby("trade_date").v.rank(pct=True)
            pmap = {(r.trade_date, str(r.code)): float(r.pct) for r in au.itertuples()}
        else:
            pmap = build_pct(field)
        for thr in (0.10, 0.05):
            print(f"\n##### {label}: 否决选中池中处日截面 bottom{int(thr*100)}% (pct<{thr}) #####")
            tox = lambda dt, c, t=thr: (v := pmap.get((dt, str(c.get("code"))))) is not None and v < t
            d, b, v, r, p = frozen(tox, pol, ep); report("冻结", d, b, v, r, p)
            d, b, v, r, p = forward(tox, pol); report("前瞻", d, b, v, r, p)


if __name__ == "__main__":
    main()
