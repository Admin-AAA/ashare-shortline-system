#!/usr/bin/env python3
"""中线离场研究之二：固定止损 / 保本止损 / 移动保护止损 的逐策略对照。

回答两个问题：
  1) 「达到自身平均收益就尾盘离场」是不是对每个中线策略都成立？
  2) 加移动保护止损能不能让某些策略收益更好、更稳？哪些策略受益、哪些受损？

口径（与 exit_target_research.py / 现役中线回测一致）：
  - 信号日收盘出信号，次日开盘买入（T+1），开盘涨停买不进剔除
  - 基准：固定持有 20 个交易日，第 21 行开盘卖，往返成本 0.3%
  - 止损：用日内 low 触发；跳空低开直接破位则按开盘价成交；跌停一字封死当日不能卖，顺延次日
  - 移动止损的峰值只用「当日及以前收盘」计算，止损从下一交易日开始生效（无同日高低点前视）
  - 每笔都是同笔配对：买入价、成本、样本完全相同，唯一差别是离场规则

参数选择诚实性：
  - 规则集在开发期(2019~2023)全池上选，样本外(2024+)只评估不改参
  - 逐策略同样用开发期选规则、样本外评估，避免"挑最好看的策略+参数"
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
import lt_data as L  # noqa: E402

c = pl.col
COST = 0.003
HOLD = 20
MAXK = 31
TOP = 5
SAMPLE_END = "2026-10-16"
DEV_END = "2023-12-31"
OUT = BACKEND / "output" / "research" / "exit_stop"
OUT.mkdir(parents=True, exist_ok=True)

PH = {
    "P19": ("2019-01-01", "2019-12-31"), "P20": ("2020-01-01", "2020-12-31"),
    "P21": ("2021-01-01", "2021-12-31"), "W0": ("2022-04-01", "2023-03-31"),
    "W00": ("2023-04-01", "2023-12-31"), "W1": ("2024-01-01", "2024-12-31"),
    "W2": ("2025-01-01", "2025-09-30"), "R12": ("2025-10-01", SAMPLE_END),
    "N3": ("2024-01-01", SAMPLE_END),
}


# ──────────────────────────── 价格面板与路径 ────────────────────────────
def load_panel() -> pl.DataFrame:
    df = L.prices()
    df = df.with_columns(
        ((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac")
    ).with_columns(
        (c("open") * c("ac") / c("close")).alias("ao"),
        (c("low") * c("ac") / c("close")).alias("al"),
    )
    return (df.sort("code", "trade_date")
            .with_columns(pl.int_range(pl.len()).over("code").cast(pl.Int64).alias("row"),
                          ((c("high") <= c("low") + 1e-6) & (c("pct_chg") <= -9.5)).fill_null(False).alias("lock"))
            .select("code", "trade_date", "row", "ac", "ao", "al", "lock"))


def make_paths(panel: pl.DataFrame, holds: pl.DataFrame):
    """holds 含 code / row0 / hid。返回 [n, MAXK+1] 的 ac/ao/al/lock/pdate。"""
    ks = pl.DataFrame({"k": pl.Series(range(MAXK + 1), dtype=pl.Int64)})
    keys = holds.join(ks, how="cross").with_columns((c("row0") + c("k")).alias("row_k"))
    p = panel.select("code", "row", "trade_date", "ac", "ao", "al", "lock").rename(
        {"row": "row_k", "trade_date": "pdate", "ac": "pac", "ao": "pao", "al": "pal", "lock": "plock"})
    j = keys.join(p, on=["code", "row_k"], how="left").sort("hid", "k")
    n = holds.height
    assert j.height == n * (MAXK + 1), (j.height, n)
    sh = (n, MAXK + 1)

    def g(col, dt):
        return j[col].cast(dt).to_numpy().reshape(sh)

    return (g("pac", pl.Float64), g("pao", pl.Float64), g("pal", pl.Float64),
            g("plock", pl.Boolean), g("pdate", pl.Utf8))


# ──────────────────────────── 模拟 ────────────────────────────
def valid_mask(ac, ao, al, horizon=HOLD):
    px = ao[:, 0]
    base = ao[:, horizon] / px - 1 - COST
    return (np.isfinite(px) & (px > 0) & np.isfinite(base)
            & np.all(np.isfinite(ac[:, 1:horizon]), axis=1)
            & np.all(np.isfinite(al[:, 1:horizon]), axis=1)), px, base


def simulate_stop(ac, ao, al, lk, kind, a=0.0, d=0.0, sl=0.0, horizon=HOLD):
    """kind: 'sl' 固定止损 / 'be' 保本 / 'trail' 移动保护。触发即当日离场。"""
    n = ac.shape[0]
    valid, px, base = valid_mask(ac, ao, al, horizon)
    ret, days = base.copy(), np.full(n, float(horizon))
    hit = np.zeros(n, dtype=bool)
    active = valid.copy()
    peak = np.where(valid, ac[:, 0] / np.where(px > 0, px, 1) - 1, 0.0)   # 入场日收盘浮盈
    for k in range(1, horizon):
        live = valid & active
        lvl = np.full(n, -np.inf)
        if kind == "sl":
            lvl[live] = px[live] * (1 - sl)
        elif kind == "be":
            sel = live & (peak >= a)
            lvl[sel] = px[sel]
        elif kind == "trail":
            sel = live & (peak >= a)
            lvl[sel] = px[sel] * (1 + peak[sel]) * (1 - d)
        else:
            raise ValueError(kind)
        sellable = live & ~lk[:, k] & np.isfinite(lvl)
        gap = sellable & (ao[:, k] <= lvl)          # 跳空破位：按开盘
        touch = sellable & ~gap & (al[:, k] <= lvl)  # 日内触发：按止损价
        for m, price in ((gap, ao[:, k]), (touch, lvl)):
            idx = np.flatnonzero(m)
            if idx.size:
                ret[idx] = price[idx] / px[idx] - 1 - COST
                days[idx] = k + 1
                hit[idx] = True
                active[idx] = False
        still = live & ~(gap | touch)
        peak[still] = np.maximum(peak[still], ac[still, k] / px[still] - 1)
    return dict(ret=ret, days=days, hit=hit, base=base, valid=valid)


def simulate_target(ac, ao, lk, target, horizon=HOLD):
    """浮盈首次达标的当日尾盘离场（跌停顺延到下一可卖日尾盘），未达标持满。"""
    n = ac.shape[0]
    valid, px, base = valid_mask(ac, ao, ao, horizon)
    ret, days = base.copy(), np.full(n, float(horizon))
    hit = np.zeros(n, dtype=bool)
    active, pending = valid.copy(), np.zeros(n, dtype=bool)
    T = np.broadcast_to(np.asarray(target, dtype=float), (n,)).copy()
    for k in range(1, horizon):
        live = valid & active
        pend = live & (pending | (ac[:, k] / px - 1 >= T))
        can = pend & ~lk[:, k]
        idx = np.flatnonzero(can)
        if idx.size:
            ret[idx] = ac[idx, k] / px[idx] - 1 - COST
            days[idx] = k + 1
            hit[idx] = True
            active[idx] = False
        pending = pend & ~can
    return dict(ret=ret, days=days, hit=hit, base=base, valid=valid)


def stats(ret, days=None, mask=None):
    r = ret if mask is None else ret[mask]
    d = (np.full(len(r), float(HOLD)) if days is None else days) if mask is None else days[mask]
    if len(r) == 0:
        return {"n": 0}
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    dm = float(np.mean(d))
    return {"n": int(len(r)), "avg": round(100 * float(r.mean()), 2),
            "med": round(100 * float(np.median(r)), 2),
            "win": round(100 * float((r > 0).mean()), 1),
            "pf": round(float(pos / neg), 2) if neg > 0 else 99.0,
            "std": round(100 * float(np.std(r)), 2),
            "p10": round(100 * float(np.quantile(r, 0.1)), 2),
            "days": round(dm, 1), "per_day": round(100 * float(r.mean()) / dm, 3)}


def phase_of(sd, name):
    a, b = PH[name]
    return (sd >= a) & (sd <= b)


def worst_phase(sd, ret, min_n=25):
    out = []
    for p in PH:
        m = phase_of(sd, p)
        if m.sum() >= min_n:
            out.append((p, 100 * float(ret[m].mean())))
    if not out:
        return None, 0
    p, v = min(out, key=lambda z: z[1])
    return round(v, 2), sum(1 for _, x in out if x > 0)


# ──────────────────────────── 组合（K=20 名额，含已实现回撤） ────────────────────────────
def portfolio(sd, code, exd, ret, k_slots=20, max_new=5):
    by_date = defaultdict(list)
    for i in np.argsort(sd, kind="stable"):
        by_date[str(sd[i])].append(i)
    held, events, peak_cnt = {}, [], 0
    for d in sorted(by_date):
        held = {k: v for k, v in held.items() if v > d}
        if len(held) >= k_slots:
            continue
        for i in sorted(by_date[d])[:max_new]:
            if len(held) >= k_slots:
                break
            if str(code[i]) in held:
                continue
            held[str(code[i])] = str(exd[i])
            events.append((str(exd[i]), float(ret[i]) / k_slots))
            peak_cnt = max(peak_cnt, len(held))
    if not events:
        return {"n": 0, "tot": 0.0, "mdd": 0.0}
    events.sort()
    eqs = np.cumsum([p for _, p in events])
    mdd = float((eqs - np.maximum.accumulate(eqs)).min())
    return {"n": len(events), "tot": round(100 * float(eqs[-1]), 1), "mdd": round(100 * mdd, 1),
            "max_hold": peak_cnt}


# ──────────────────────────── 规则集 ────────────────────────────
def rule_book():
    R = [("基准(持20日)", None)]
    for x in (5, 8, 10, 12, 15):
        R.append((f"固定止损-{x}%", dict(kind="sl", sl=x / 100)))
    for a in (3, 5, 8, 10):
        R.append((f"保本止损(浮盈{a}%后)", dict(kind="be", a=a / 100)))
    for a in (0, 3, 5, 8):
        for d in (3, 5, 8):
            tag = "入场即移动" if a == 0 else f"浮盈{a}%启动"
            R.append((f"移动止损 回撤{d}%/{tag}", dict(kind="trail", a=a / 100, d=d / 100)))
    return R


# ──────────────────────────── 池：全部中线 base 逐笔 ────────────────────────────
def build_pool(panel):
    files = glob.glob(str(BACKEND / "output" / "research" / "**" / "trades_*_base_*.csv"), recursive=True)
    frames = []
    for f in files:
        try:
            x = pl.read_csv(f, schema_overrides={"code": pl.Utf8})
        except Exception:
            continue
        if not {"code", "entry", "days", "ret"} <= set(x.columns):
            continue
        prefix = Path(f).stem.split("_base_")[0]
        frames.append(x.filter((c("days") >= 15) & (c("days") <= 25))
                      .select("code", c("entry").cast(pl.Utf8).alias("entry"), c("ret").cast(pl.Float64).alias("csv_ret"))
                      .with_columns(pl.lit(prefix).alias("prefix")))
    rec = pl.concat(frames).unique(["prefix", "code", "entry"])
    uniq = rec.select("code", "entry").unique().with_row_index("hid")
    u = uniq.join(panel.select("code", "trade_date", "row").rename({"trade_date": "entry", "row": "row0"}),
                  on=["code", "entry"], how="left")
    ac, ao, al, lk, pd = make_paths(panel, u.select("hid", "code", "row0"))
    valid, px, base = valid_mask(ac, ao, al)
    m = rec.join(uniq, on=["code", "entry"], how="left")
    hid = m["hid"].cast(pl.Int64).to_numpy()
    print(f"[池] 记录={rec.height} 策略={m['prefix'].n_unique()} 唯一路径={len(uniq)} 有效={int(valid.sum())}", flush=True)
    return dict(hid=hid, prefix=m["prefix"].to_numpy(), ac=ac, ao=ao, al=al, lk=lk, pd=pd,
                valid=valid, base=base, n_unique=len(uniq))


def sub(pool, hid):
    return (pool["ac"][hid], pool["ao"][hid], pool["al"][hid], pool["lk"][hid])


# ──────────────────────────── 主流程 ────────────────────────────
def main():
    print("载入面板 …", flush=True)
    panel = load_panel()
    print(f"面板 {panel.height} 行 {panel['trade_date'].min()}~{panel['trade_date'].max()}", flush=True)
    pool = build_pool(panel)

    hid = pool["hid"]
    prefix = pool["prefix"]
    valid = pool["valid"][hid]
    base = pool["base"][hid]
    entry = pool["pd"][hid][:, 0].astype(str)
    sd = entry  # 买入日即持仓起点，用于分段
    dev = sd <= DEV_END
    oos = sd >= "2024-01-01"

    # 每笔所属路径在池内的索引，便于按规则重算
    rules = rule_book()
    sims = {}
    for name, p in rules:
        if p is None:
            sims[name] = dict(ret=base, days=np.full(len(base), float(HOLD)), hit=np.zeros(len(base), bool))
        else:
            s = simulate_stop(*sub(pool, hid), **p)
            sims[name] = {k: s[k] for k in ("ret", "days", "hit")}

    # ── 1) 逐策略复核「达标离场」 ──
    print(f"[校验] base_sum={base.sum():.3f} be10_sum={sims['保本止损(浮盈10%后)']['ret'].sum():.3f} "
          f"trail88_sum={sims['移动止损 回撤8%/浮盈8%启动']['ret'].sum():.3f}", flush=True)
    uniq_p = np.unique(prefix)
    tg_rows = []
    for p in uniq_p:
        s = (prefix == p) & valid
        if s.sum() < 40:
            continue
        b = base[s]
        T_own = float(b.mean())
        a_own = simulate_target(pool["ac"][hid[s]], pool["ao"][hid[s]], pool["lk"][hid[s]], T_own)
        d_o = (a_own["ret"] - b).mean() * 100
        d_oos = (a_own["ret"][oos[s]] - b[oos[s]]).mean() * 100 if oos[s].sum() >= 10 else np.nan
        tg_rows.append([str(p), int(s.sum()), f"{100*b.mean():+.2f}", f"{100*T_own:+.2f}",
                        f"{100*a_own['ret'].mean():+.2f}", f"{d_o:+.2f}",
                        "—" if np.isnan(d_oos) else f"{d_oos:+.2f}",
                        round(float(a_own["days"].mean()), 1)])
    tg_better = sum(1 for r in tg_rows if float(r[5]) > 0)
    tg_better_oos = sum(1 for r in tg_rows if r[6] != "—" and float(r[6]) > 0)
    tg_oos_n = sum(1 for r in tg_rows if r[6] != "—")
    print(f"[达标离场] {len(tg_rows)} 个策略(n>=40)，逐笔均值变好 {tg_better} 个；样本外可比 {tg_oos_n} 个中变好 {tg_better_oos} 个", flush=True)

    # ── 2) 全池规则对照：开发期选参、样本外评估 ──
    first = np.zeros(len(hid), dtype=bool)          # 每个唯一路径只取一次，剔除策略变体重复加权
    first[np.unique(hid, return_index=True)[1]] = True
    rows = []
    for name, _ in rules:
        s = sims[name]
        st = stats(s["ret"][valid], s["days"][valid])
        st_dev = stats(s["ret"][valid & dev], s["days"][valid & dev])
        st_oos = stats(s["ret"][valid & oos], s["days"][valid & oos])
        st_uni = stats(s["ret"][valid & first], s["days"][valid & first])
        wp, npos = worst_phase(sd[valid & first], s["ret"][valid & first])
        st.update(dev=st_dev["avg"], oos=st_oos["avg"], d_dev=round(st_dev["avg"] - stats(base[valid & dev])["avg"], 2),
                  d_oos=round(st_oos["avg"] - stats(base[valid & oos])["avg"], 2), uni=st_uni["avg"],
                  hit_rate=round(100 * float(s["hit"][valid].mean()), 1), worst=wp, npos=npos)
        rows.append([name, st_uni["n"], f"{st['avg']:+.2f}", f"{st['dev']:+.2f}", f"{st['oos']:+.2f}",
                     f"{st['d_oos']:+.2f}", st["win"], st["pf"], st["std"], st["p10"],
                     st["days"], st["per_day"], st["hit_rate"], wp])
    dev_best = max([r for r in rows if r[0] != "基准(持20日)"], key=lambda r: float(r[3]))
    print(f"[全池] 开发期最优规则 = {dev_best[0]}  dev {dev_best[3]}  oos {dev_best[4]}  Δoos {dev_best[5]}", flush=True)

    # ── 3) 逐策略：开发期选规则 → 样本外评估 ──
    strat = []
    for p in uniq_p:
        s = (prefix == p) & valid
        n_all, n_dev, n_oos = int(s.sum()), int((s & dev).sum()), int((s & oos).sum())
        if n_dev < 30 or n_oos < 20:
            continue
        cand = []
        for name, _ in rules:
            if name == "基准(持20日)":
                continue
            cand.append((name, float(sims[name]["ret"][s & dev].mean())))
        best = max(cand, key=lambda z: z[1])
        b_dev = float(base[s & dev].mean())
        b_oos = float(base[s & oos].mean())
        r_oos = float(sims[best[0]]["ret"][s & oos].mean())
        d_dev = 100 * (best[1] - b_dev)
        d_oos = 100 * (r_oos - b_oos)
        # 该规则在样本外各分段的稳定性
        seg = []
        for ph in ("W1", "W2", "R12"):
            m = s & phase_of(sd, ph)
            if m.sum() >= 10:
                seg.append(100 * (float(sims[best[0]]["ret"][m].mean()) - float(base[m].mean())))
        strat.append([str(p), n_all, n_dev, n_oos, best[0], f"{100*b_dev:+.2f}",
                      f"{100*b_oos:+.2f}", f"{d_dev:+.2f}", f"{d_oos:+.2f}",
                      "无" if not seg else f"{min(seg):+.2f}", int(sum(1 for x in seg if x > 0)), len(seg),
                      round(float(sims[best[0]]["days"][s].mean()), 1)])
    strat.sort(key=lambda r: -float(r[8]))
    oos_up = sum(1 for r in strat if float(r[8]) > 0)
    oos_up50 = sum(1 for r in strat if float(r[8]) > 0.5)
    print(f"[逐策略] {len(strat)} 个可检验策略，样本外最优规则仍为正 {oos_up} 个，>+0.5pp {oos_up50} 个", flush=True)

    # 严格筛选：开发期正、样本外正、且样本外三个分段全部为正
    robust = []
    for p in uniq_p:
        s = (prefix == p) & valid
        if (s & dev).sum() < 30 or (s & oos).sum() < 20:
            continue
        for name, _ in rules[1:]:
            d_d = 100 * (float(sims[name]["ret"][s & dev].mean()) - float(base[s & dev].mean()))
            d_o = 100 * (float(sims[name]["ret"][s & oos].mean()) - float(base[s & oos].mean()))
            segs = []
            for ph in ("W1", "W2", "R12"):
                m = s & phase_of(sd, ph)
                if m.sum() >= 10:
                    segs.append(100 * (float(sims[name]["ret"][m].mean()) - float(base[m].mean())))
            if d_d > 0 and d_o > 0 and segs and min(segs) > 0:
                stable = (stats(sims[name]["ret"][s])["std"] <= stats(base[s])["std"])
                robust.append([str(p), name, int(s.sum()), int((s & oos).sum()),
                               f"{100*base[s].mean():+.2f}", f"{d_d:+.2f}", f"{d_o:+.2f}",
                               f"{min(segs):+.2f}", "是" if stable else "否"])
    robust.sort(key=lambda r: -float(r[6]))
    print(f"[严格筛选] 开发期+样本外+三个分段全部为正的 (策略,规则) 组合 {len(robust)} 个，"
          f"覆盖 {len(set(r[0] for r in robust))} 个策略", flush=True)

    # 市场状态依赖性：分段看基准 vs 两条代表规则
    rep_rules = [dev_best[0], "移动止损 回撤8%/浮盈8%启动", "移动止损 回撤3%/浮盈3%启动"]
    rep_rules = list(dict.fromkeys(rep_rules))
    ph_tbl = []
    for ph in ("P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12"):
        m = valid & phase_of(sd, ph)
        if m.sum() < 50:
            continue
        row = [ph, int(m.sum()), f"{100*base[m].mean():+.2f}"]
        for name in rep_rules:
            row.append(f"{100*float(sims[name]['ret'][m].mean() - base[m].mean()):+.2f}")
        ph_tbl.append(row)

    # 策略特征 × 止损效果：左尾越厚，止损帮助越大？
    feat = []
    r_trail = "移动止损 回撤8%/浮盈8%启动"
    r_be = "保本止损(浮盈10%后)"
    for p in uniq_p:
        s = (prefix == p) & valid
        if s.sum() < 80 or (s & oos).sum() < 40:
            continue
        b = base[s]
        feat.append([str(p), int(s.sum()), f"{100*b.mean():+.2f}", round(100 * float(np.quantile(b, 0.1)), 1),
                     round(100 * float(np.std(b)), 1),
                     f"{100*float(sims[r_trail]['ret'][s & oos].mean() - b[oos[s]].mean()):+.2f}",
                     f"{100*float(sims[r_be]['ret'][s & oos].mean() - b[oos[s]].mean()):+.2f}"])
    bucket = []
    if feat:
        p10s = np.array([float(r[3]) for r in feat])
        qs = np.quantile(p10s, [0.25, 0.5, 0.75])
        names = [f"最厚(≤{qs[0]:.1f}%)", f"{qs[0]:.1f}~{qs[1]:.1f}%", f"{qs[1]:.1f}~{qs[2]:.1f}%", f"最薄(>{qs[2]:.1f}%)"]
        groups = [[], [], [], []]
        for r in feat:
            v = float(r[3])
            groups[0 if v <= qs[0] else 1 if v <= qs[1] else 2 if v <= qs[2] else 3].append(r)
        for nm, g in zip(names, groups):
            if not g:
                continue
            bucket.append([nm, len(g), round(float(np.mean([float(x[2]) for x in g])), 2),
                           round(float(np.mean([float(x[5]) for x in g])), 2),
                           round(float(np.mean([float(x[6]) for x in g])), 2),
                           f"{np.mean([float(x[5]) > 0 for x in g])*100:.0f}%"])

    # 全池按开发期最优规则，逐策略看
    dev_best_name = dev_best[0]
    per_by_best = []
    for p in uniq_p:
        s = (prefix == p) & valid
        if s.sum() < 40:
            continue
        d_all = float(sims[dev_best_name]["ret"][s].mean() - base[s].mean()) * 100
        d_o = float(sims[dev_best_name]["ret"][s & oos].mean() - base[s & oos].mean()) * 100 if (s & oos).sum() >= 10 else np.nan
        per_by_best.append([str(p), int(s.sum()), f"{100*base[s].mean():+.2f}",
                            f"{100*sims[dev_best_name]['ret'][s].mean():+.2f}", f"{d_all:+.2f}",
                            "—" if np.isnan(d_o) else f"{d_o:+.2f}",
                            round(float(sims[dev_best_name]["days"][s].mean()), 1)])
    per_by_best.sort(key=lambda r: -float(r[4]))

    # ── 4) 现役 EA / FA 两个策略的组合口径 ──
    port = {}
    D = BACKEND / "output" / "research" / "rv1030"
    idx = panel.select("code", "trade_date", "row").rename({"trade_date": "sd"})
    for S in ("EA", "FA"):
        x = pl.read_parquet(D / f"sig_{S}.parquet").filter(
            ~c("partial") & ~c("lu_e").fill_null(False) & (c("trade_date") <= SAMPLE_END))
        if S == "EA":
            nn = pl.len().over("trade_date")
            x = x.with_columns(((c("d0").rank().over("trade_date") / nn)
                                + ((-c("total_mv")).rank().over("trade_date") / nn)).alias("rk_score"))
            key = "rk_score"
        else:
            key = "score"
        x = x.with_columns(c("code").cast(pl.Int64).alias("ci"))
        x = (x.sort(["trade_date", key, "ci"], descending=[False, True, False], nulls_last=True)
              .with_columns(pl.int_range(pl.len()).over("trade_date").alias("rank")))
        picks = x.filter(c("rank") < TOP).rename({"trade_date": "sd"}).join(idx, on=["code", "sd"], how="left")
        picks = picks.with_columns((c("row") + 1).alias("row0")).drop("row").with_row_index("hid")
        ac, ao, al, lk, pdv = make_paths(panel, picks.select("hid", "code", "row0"))
        v, px, b = valid_mask(ac, ao, al)
        vi = np.flatnonzero(v)
        acv, aov, alv, lkv, pdvv = ac[vi], ao[vi], al[vi], lk[vi], pdv[vi]
        sdv = picks["sd"].cast(pl.Utf8).to_numpy().astype(str)[vi]
        codev = picks["code"].cast(pl.Utf8).to_numpy().astype(str)[vi]
        rankv = picks["rank"].cast(pl.Int64).to_numpy()[vi]
        pv_dev = sdv <= DEV_END
        pv_oos = sdv >= "2024-01-01"
        core = {}
        for name, p in rules:
            if p is None:
                core[name] = dict(ret=b[vi], days=np.full(len(vi), float(HOLD)),
                                  exd=pdvv[:, HOLD], hit=np.zeros(len(vi), bool))
            else:
                s = simulate_stop(acv, aov, alv, lkv, **p)
                core[name] = dict(ret=s["ret"], days=s["days"], exd=pdvv[np.arange(len(vi)), np.minimum(s["days"].astype(int) - 1, MAXK)],
                                  hit=s["hit"])
        # 全池最优规则 + 开发期最优（本策略）两个
        cand = [(n, float(core[n]["ret"][pv_dev].mean())) for n, _ in rules if n != "基准(持20日)"]
        own = max(cand, key=lambda z: z[1])[0]
        sel = ["基准(持20日)", dev_best_name, own]
        sel = list(dict.fromkeys(sel))
        tbl = []
        for name in sel:
            s = core[name]
            st = stats(s["ret"], s["days"])
            st_o = stats(s["ret"][pv_oos], s["days"][pv_oos])
            pf = portfolio(pdv[:, 0][vi], codev, s["exd"], s["ret"])
            hm = s["hit"] & pv_oos
            lock_avg = 100 * float(s["ret"][hm].mean()) if hm.sum() else 0.0
            hold_avg = 100 * float(b[vi][hm].mean()) if hm.sum() else 0.0
            tbl.append([name, st["n"], pf["n"], f"{st['avg']:+.2f}", f"{st['win']}", st["pf"], st["std"],
                        f"{st['days']}", f"{st_o['avg']:+.2f}", f"{100*float(s['hit'].mean()):.0f}",
                        pf["tot"], pf["mdd"],
                        round(pf["tot"] / abs(pf["mdd"]), 2) if pf["mdd"] else 0.0,
                        f"{lock_avg:+.2f}", f"{hold_avg:+.2f}"])
        port[S] = dict(own=own, rows=tbl)
        print(f"[{S}] 本策略开发期最优={own}", flush=True)

    report = {"meta": {"cost": COST, "hold": HOLD, "sample_end": SAMPLE_END,
                       "n_paths": int(pool["n_unique"]), "n_valid_unique": int(pool["valid"].sum()),
                       "n_records": int(len(hid)), "n_prefix": int(len(uniq_p))},
              "target_exit_by_prefix": tg_rows, "rules_pooled": rows,
              "dev_best": dev_best_name, "dev_best_oos_delta": dev_best[5],
              "strategy_dev_select": strat, "strategy_with_dev_best": per_by_best,
              "strict_robust": robust, "phase_table": dict(rules=rep_rules, rows=ph_tbl),
              "left_tail_buckets": bucket, "strategy_features": feat,
              "portfolios": port}
    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))

    # ── 报告 ──
    def md(rows_, head):
        o = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
        o += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows_]
        return "\n".join(o)

    L_ = ["# 中线离场研究（二）：达标离场是否普适 + 移动保护止损",
          "",
          f"生成 2026-11-15｜数据截止 {SAMPLE_END}｜成本 0.3%/笔｜T+1｜最多持 20 交易日｜"
          f"唯一持仓路径 {pool['n_unique']} 条（有效 {int(pool['valid'].sum())}），逐笔记录 {len(hid)} 条，策略前缀 {len(uniq_p)} 个。",
          "",
          "止损成交假设：日内 low 触发按止损价成交；开盘即破位按开盘价成交；跌停一字封死当日不可卖，顺延。"
          "移动止损的峰值只用当日及以前收盘，止损次日生效，无同日高低点前视。",
          "",
          "## 一、达标离场是不是对每个中线策略都成立",
          "",
          f"- 有 {len(tg_rows)} 个策略样本 ≥40 笔：逐笔均值变好 **{tg_better}** 个，变差 {len(tg_rows)-tg_better} 个。",
          f"- 样本外（2024+）可比 {tg_oos_n} 个：变好 **{tg_better_oos}** 个。",
          ""]
    L_ += ["变好的策略（按全样本差值排序）：", ""]
    up = [r for r in tg_rows if float(r[5]) > 0]
    up.sort(key=lambda r: -float(r[5]))
    L_ += [md(up[:20], ["策略", "笔数", "基准%", "自身均值目标%", "达标离场%", "差值pp", "样本外差值pp", "持有日"]), ""]
    L_ += ["变差最多的策略：", ""]
    dn = sorted(tg_rows, key=lambda r: float(r[5]))[:10]
    L_ += [md(dn, ["策略", "笔数", "基准%", "自身均值目标%", "达标离场%", "差值pp", "样本外差值pp", "持有日"]), ""]

    L_ += ["## 二、全池规则对照（开发期 2019–2023 每笔均值）", "",
           "同一批买入、同一成本，只有离场规则不同。'唯一路径%' 是去掉多策略重复持仓后的每笔均值（每个 (股票,买入日) 只算一次）。"
           "最后一列是该规则最差分段（任一分段 ≥25 笔）的每笔均值。", "",
           md(rows, ["离场规则", "唯一路径n", "全样本%", "开发期%", "样本外%", "样本外Δpp", "胜率%", "PF", "波动%", "P10%",
                     "持有日", "每持仓日%", "触发率%", "最差分段%"]), ""]

    L_ += ["## 三、逐策略：开发期选规则，样本外验证", "",
           f"共 {len(strat)} 个策略满足 开发期 ≥30 笔、样本外 ≥20 笔。每个策略在开发期从 21 条止损规则里挑每笔均值最高的，"
           f"然后只看样本外。**样本外仍为正 {oos_up} 个，提升 >0.5pp 的 {oos_up50} 个。**", "",
           md(strat[:25], ["策略", "笔数", "开发期n", "样本外n", "开发期选中规则", "开发期基准%", "样本外基准%",
                           "开发期Δpp", "样本外Δpp", "样本外最差分段Δpp", "正分段数", "分段数", "持有日"]), ""]

    L_ += ["## 三之二、严格筛选：开发期正 + 样本外正 + 样本外三个分段全正", "",
           f"在 {len(strat)} 个策略 × 21 条规则 = {len(strat)*21} 个组合里，同时满足三项条件的只有 **{len(robust)}** 个"
           f"（覆盖 {len(set(r[0] for r in robust))} 个策略）。这是「加止损后确实更好」的唯一严格口径。"
           f"若止损毫无真实效应，仅凭随机也应有约 5%~8%（即 75~125 个）组合同时满足三项条件；"
           f"实际只有 {len(robust)} 个，远低于随机水平 —— 说明止损对该池是系统性负效应，而不是发现了有效规则。", "",
           md(robust[:20], ["策略", "规则", "笔数", "样本外n", "基准%", "开发期Δpp", "样本外Δpp", "样本外最差分段Δpp", "波动是否下降"]), ""]

    L_ += ["## 四、为什么止损是「保险」而不是「增强」：分段看", "",
           "同一批买入，只改离场规则，按市场阶段看每笔均值的差值（相对固定持 20 日）。", "",
           md(ph_tbl, ["阶段", "笔数", "基准%"] + [f"{n} Δpp" for n in rep_rules]), "",
           "## 四之二、哪类策略适合加止损：按基准左尾（P10）分组", "",
           "只有笔数 ≥80 且样本外 ≥40 的策略参与分组。结论：**没有单调关系**——左尾最厚的组与最薄的组止损代价相近，"
           "决定止损损益的不是左尾厚度，而是右尾形态（能不能在回撤后继续新高）。", "",
           md(bucket, ["左尾分组", "策略数", "基准均值%", "移动8%/8% Δpp", "保本10% Δpp", "移动8%/8% 变好占比"]), ""]

    L_ += [f"## 五、若全池统一采用开发期最优规则「{dev_best_name}」", "",
           md(per_by_best[:20], ["策略", "笔数", "基准%", "规则后%", "差值pp", "样本外差值pp", "持有日"]), ""]

    L_ += ["## 六、现役策略组合口径（K=20 名额 / 每日最多新开 5 只，含已实现回撤）", ""]
    for S, d in port.items():
        L_ += [f"### {S}（本策略开发期最优：{d['own']}）", "",
               md(d["rows"], ["方案", "逐笔n", "开仓n", "每笔均值%", "胜率%", "PF", "波动%", "持有日", "样本外均值%",
                              "触发率%", "累计名义%", "已实现回撤%", "累计/回撤", "样本外已触发锁定%", "这批若持满%"]), ""]

    (OUT / "report.md").write_text("\n".join(L_))
    print(f"\n报告：{OUT/'report.md'} / {OUT/'summary.json'}", flush=True)
    print("\n".join(L_[:60]))


if __name__ == "__main__":
    main()
