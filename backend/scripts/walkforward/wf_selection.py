"""Walk-forward（季度滚动起点）评估：分数线/上限、元标签重排、元标签扩票。"""
import json, math, sys, itertools
from collections import defaultdict
import numpy as np
sys.path.insert(0, "scripts/walkforward")
from harness import load, metrics, decide
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer

days = sorted(load("output/research/stage5/panel6/entry_panel_WX.json")["days"], key=lambda d: d["signal_date"])
E = json.load(open(".cache/wf/eligible.json"))
D = {d["signal_date"]: d for d in days}
dates = [d["signal_date"] for d in days]
FOLDS = [("2025Q3","2025-07-01","2025-10-01"),("2025Q4","2025-10-01","2026-01-01"),
         ("2026Q1","2026-01-01","2026-04-01"),("2026Q2","2026-04-01","2026-07-01"),
         ("2026Q3*","2026-07-01","2026-10-10")]
PURGE = 6

# 特征：feature_snapshot 中的数值字段 + 卡级字段
NUM = None
def feats(card, day):
    fs = card.get("feature_snapshot") or {}
    global NUM
    if NUM is None:
        keys = set()
        for dd in days[-60:]:
            for c in dd["cards"]:
                for k, v in (c.get("feature_snapshot") or {}).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool): keys.add(k)
                    if isinstance(v, bool): keys.add(k)
        NUM = sorted(keys)
    row = [float(fs.get(k)) if isinstance(fs.get(k), (int, float)) else np.nan for k in NUM]
    row += [float(card.get("final_score") or 0), float(card.get("consensus_score") or 0),
            float(card.get("ivol20_pct") or np.nan) if card.get("ivol20_pct") is not None else np.nan,
            len([s for s in card.get("strategy_signals") or [] if not s.get("hard_veto")])]
    return row

CARD = {}
for d in days:
    for c in d["cards"]:
        CARD[(d["signal_date"], str(c["code"]))] = c

def fit(train_dates):
    X, y = [], []
    for dt in train_dates:
        d = D[dt]; nets = d.get("net_by_code") or {}
        for c in d["cards"]:
            n = nets.get(str(c["code"]))
            if n is None or (isinstance(n, float) and math.isnan(n)): continue
            X.append(feats(c, d)); y.append(1 if n > 0 else 0)
    X = np.array(X, dtype=float)
    m = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), LogisticRegression(C=0.05, max_iter=500))
    m.fit(X, y)
    return m

def prob(m, dt, codes):
    if not codes: return {}
    X = np.array([feats(CARD[(dt, c)], D[dt]) for c in codes], dtype=float)
    return dict(zip(codes, m.predict_proba(X)[:, 1]))

def picks_for(rule, dt, model):
    kind = rule[0]
    if kind == "line":
        _, line, n = rule
        return E[dt]["elig"][str(line)][:n]
    if kind == "rerank":
        _, line, n, lam = rule
        el = E[dt]["elig"][str(line)]
        p = prob(model, dt, el)
        rank = {c: i for i, c in enumerate(el)}
        return sorted(el, key=lambda c: rank[c] - lam * 10 * p[c])[:n]
    if kind == "expand":
        _, low, thr, n = rule
        base = E[dt]["elig"]["72"][:6]
        extra = [c for c in E[dt]["elig"][str(low)] if c not in base]
        p = prob(model, dt, extra)
        add = [c for c in extra if p[c] >= thr]
        return (base + add)[:n]
    if kind == "filter":
        _, thr = rule
        base = E[dt]["elig"]["72"][:6]
        p = prob(model, dt, base)
        return [c for c in base if p[c] >= thr]

def rows(rule, ds, model):
    out = []
    for dt in ds:
        nets = D[dt].get("net_by_code") or {}
        for c in picks_for(rule, dt, model):
            out.append((dt, nets.get(c)))
    return out

RULES = [("line", 72, 6)]
RULES += [("line", l, n) for l in (66, 68, 70, 72, 74, 76) for n in (6, 8, 10) if (l, n) != (72, 6)]
RULES += [("rerank", l, n, lam) for l in (68, 70, 72) for n in (6, 8) for lam in (0.5, 1.0, 3.0)]
RULES += [("expand", low, thr, n) for low in (64, 66, 68, 70) for thr in (0.5, 0.55, 0.6) for n in (8, 10)]
RULES += [("filter", thr) for thr in (0.4, 0.45)]
BASE = ("line", 72, 6)

def boot_ci(base_rows, new_rows, ds, B=2000, seed=7):
    def daily(rs):
        m = defaultdict(float)
        for d, n in rs:
            if n is not None and not math.isnan(n): m[d] += n
        return np.array([m[d] for d in ds])
    diff = daily(new_rows) - daily(base_rows)
    rng = np.random.default_rng(seed)
    bs = [diff[rng.integers(0, len(diff), len(diff))].sum() for _ in range(B)]
    return float(diff.sum()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))

report = {"folds": []}
for name, lo, hi in FOLDS:
    tr = [d for d in dates if d < lo]
    tr_fit = tr[:-PURGE]
    te = [d for d in dates if lo <= d < hi]
    model = fit(tr_fit)
    bm = metrics(rows(BASE, tr, model))
    best, bestm = BASE, bm
    for r in RULES[1:]:
        m = metrics(rows(r, tr, model))
        if not m: continue
        # 选择准则：票数不少于基线 95%，胜率与平均都不低于基线，平均最高
        if m["n"] >= 0.95 * bm["n"] and m["wr"] >= bm["wr"] and m["avg"] > bestm["avg"] and m["wr"] >= bestm["wr"] - 0.005:
            best, bestm = r, m
    tb = rows(BASE, te, model); tn = rows(best, te, model)
    mb, mn = metrics(tb), metrics(tn)
    ci = boot_ci(tb, tn, te)
    rec = {"fold": name, "chosen": best, "train_base": bm, "train_chosen": bestm,
           "test_base": mb, "test_chosen": mn, "delta_sum_ci": ci,
           "test_days": len(te), "decision": str(decide(mb, mn).decision) if mb and mn and best != BASE else "BASE"}
    report["folds"].append(rec)
    f = lambda m: f"n={m['n']} wr={m['wr']*100:.1f} avg={m['avg']:+.2f} sum={m['sum']:+.1f} mdd={m['mdd']:+.1f}" if m else "n=0"
    print(name, "chosen", best, "| test base", f(mb), "| test new", f(mn), "| Δsum CI", [round(x,1) for x in ci], rec["decision"], flush=True)

# 固定规则逐折一致性（不经选择）：每条规则在每一折的 Δwr/Δavg/Δn
cons = {}
for r in RULES[1:]:
    per = []
    for name, lo, hi in FOLDS:
        tr = [d for d in dates if d < lo][:-PURGE]; te = [d for d in dates if lo <= d < hi]
        model = MODELS.get(name) if False else None
        if r[0] in ("rerank", "expand", "filter"):
            model = fit(tr)
        mb, mn = metrics(rows(BASE, te, model)), metrics(rows(r, te, model))
        if not mb or not mn: per.append(None); continue
        per.append((mn["wr"] - mb["wr"], mn["avg"] - mb["avg"], mn["n"] - mb["n"], mn["sum"] - mb["sum"]))
    good = sum(1 for p in per if p and p[0] > 0 and p[1] > 0 and p[2] >= 0)
    cons[str(r)] = {"per_fold": per, "folds_better_wr_avg_n": good}
report["consistency"] = cons
top = sorted(cons.items(), key=lambda kv: -kv[1]["folds_better_wr_avg_n"])[:12]
for k, v in top:
    print("CONS", k, v["folds_better_wr_avg_n"], [None if p is None else tuple(round(x, 3) for x in p) for p in v["per_fold"]])
json.dump(report, open("output/research/holdout/wf_round1.json", "w"), ensure_ascii=False, indent=1, default=str)
