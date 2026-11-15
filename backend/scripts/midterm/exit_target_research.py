#!/usr/bin/env python3
"""中线离场研究：固定持 20 个交易日 vs「浮盈达到策略平均收益的当日尾盘离场」。

只读本地 parquet / 逐笔 CSV，不改任何线上配置。用法：
    .venv/bin/python backend/scripts/midterm/exit_target_research.py

口径（与现役中线回测一致）：
  - sd 收盘出信号，e = 面板下一行(=sd+1 交易日) 开盘价买入，开盘涨停买不进剔除
  - 基准：持有 20 个交易日，第 21 行(e+20) 开盘卖出，往返成本 0.3%
  - 方案：持仓第 2~20 日的任一尾盘，浮盈 >= 目标 T 则当日收盘卖出（T+1 约束）
          未达标则仍按基准到期卖出 —— 因此每笔都是同笔配对，差异只来自离场时点
  - 另做「收盘判定、次日开盘卖」变体（对应生产引擎 exit_fn 的可执行口径）
  - 跌停一字封死当日不能卖，顺延到下一可卖日尾盘

阈值 T 三种取法：
  - T_dev : 开发期(2019~2023)每笔平均收益，样本外(2024+)评估，无前视
  - T_walk: 信号日之前所有已完结交易的平均收益（严格因果，可实现）
  - T_full: 全样本平均收益（含前视，仅作上界参考 / 用户直觉口径）
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
SAMPLE_END = "2026-10-16"          # 与项目 R12 / N3 窗口一致
DEV_END = "2023-12-31"
OOS_START = "2024-01-01"
OUT = BACKEND / "output" / "research" / "exit_target"
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
    ).with_columns((c("open") * c("ac") / c("close")).alias("ao"))
    return (df.sort("code", "trade_date")
            .with_columns(pl.int_range(pl.len()).over("code").cast(pl.Int64).alias("row"),
                          ((c("high") <= c("low") + 1e-6) & (c("pct_chg") <= -9.5)).fill_null(False).alias("lock"))
            .select("code", "trade_date", "row", "ac", "ao", "lock"))


def make_paths(panel: pl.DataFrame, holds: pl.DataFrame):
    """holds 需含 code / row0(=买入日行号) / hid(0..n-1)。返回 [n, MAXK+1] 的 ac/ao/lock/pdate。"""
    ks = pl.DataFrame({"k": pl.Series(range(MAXK + 1), dtype=pl.Int64)})
    keys = holds.join(ks, how="cross").with_columns((c("row0") + c("k")).alias("row_k"))
    p = panel.select("code", "row", "trade_date", "ac", "ao", "lock").rename(
        {"row": "row_k", "trade_date": "pdate", "ac": "pac", "ao": "pao", "lock": "plock"})
    j = keys.join(p, on=["code", "row_k"], how="left").sort("hid", "k")
    n = holds.height
    assert j.height == n * (MAXK + 1), (j.height, n)
    sh = (n, MAXK + 1)
    ac = j["pac"].cast(pl.Float64).to_numpy().reshape(sh)
    ao = j["pao"].cast(pl.Float64).to_numpy().reshape(sh)
    lk = j["plock"].fill_null(False).cast(pl.Boolean).to_numpy().reshape(sh)
    pd = j["pdate"].cast(pl.Utf8).to_numpy().reshape(sh)
    return ac, ao, lk, pd


# ──────────────────────────── 模拟与指标 ────────────────────────────
def valid_mask(ac, ao, horizon=HOLD):
    px = ao[:, 0]
    base = ao[:, horizon] / px - 1 - COST
    return (np.isfinite(px) & (px > 0) & np.isfinite(base)
            & np.all(np.isfinite(ac[:, 1:horizon]), axis=1)), px, base


def simulate(ac, ao, lk, target, mode="eod", horizon=HOLD, net_target=False):
    """浮盈首次达标的当日尾盘离场（eod）/ 次日开盘离场（nxo）。未达标走基准。"""
    n = ac.shape[0]
    valid, px, base = valid_mask(ac, ao, horizon)
    ret, days = base.copy(), np.full(n, float(horizon))
    hit = np.zeros(n, dtype=bool)
    T = np.broadcast_to(np.asarray(target, dtype=float), (n,)).copy()
    if net_target:                      # 「扣完成本后的净浮盈达标」口径
        T += COST
    for i in np.flatnonzero(valid):
        for k in range(1, horizon):     # k=1 是持仓第 2 日（T+1 才能卖）
            if ac[i, k] / px[i] - 1 >= T[i]:
                kk = k
                while kk < horizon and lk[i, kk]:
                    kk += 1
                if kk >= horizon:
                    break
                price = ac[i, kk] if mode == "eod" else ao[i, min(kk + 1, MAXK)]
                if not np.isfinite(price):
                    break
                ret[i] = price / px[i] - 1 - COST
                days[i] = kk + 1 if mode == "eod" else kk + 2
                hit[i] = True
                break
    return dict(ret=ret, days=days, hit=hit, base=base, valid=valid)


def stats(ret, days=None, mask=None):
    r = ret if mask is None else ret[mask]
    if days is None:
        d = np.full(len(r), float(HOLD))
    else:
        d = days if mask is None else days[mask]
    if len(r) == 0:
        return {"n": 0}
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    dm = float(np.mean(d))
    return {"n": int(len(r)), "avg": round(100 * float(r.mean()), 2),
            "med": round(100 * float(np.median(r)), 2),
            "win": round(100 * float((r > 0).mean()), 1),
            "pf": round(float(pos / neg), 2) if neg > 0 else 99.0,
            "days": round(dm, 1), "per_day": round(100 * float(r.mean()) / dm, 3)}


def phase_of(sd: np.ndarray, name: str) -> np.ndarray:
    a, b = PH[name]
    return (sd >= a) & (sd <= b)


# ──────────────────────────── 组合（K20/mn5）模拟 ────────────────────────────
def portfolio(sd, code, rank, exit_date, ret, k_slots=20, max_new=5):
    by_date = defaultdict(list)
    for i in np.argsort(sd, kind="stable"):
        by_date[str(sd[i])].append(i)
    held = {}          # code -> exit date
    total, n = 0.0, 0
    for d in sorted(by_date):
        held = {k: v for k, v in held.items() if v > d}
        if len(held) >= k_slots:
            continue
        for i in sorted(by_date[d], key=lambda z: rank[z])[:max_new]:
            if len(held) >= k_slots:
                break
            if str(code[i]) in held:
                continue
            held[str(code[i])] = str(exit_date[i])
            total += float(ret[i]) / k_slots
            n += 1
    return {"n": n, "tot": round(100 * total, 1)}


# ──────────────────────────── 主实验：现役 EA / FA 每日 top5 ────────────────────────────
def main_experiment(panel):
    idx = panel.select("code", "trade_date", "row").rename({"trade_date": "sd"})
    D = BACKEND / "output" / "research" / "rv1030"
    res = {}
    for S in ("EA", "FA"):
        x = pl.read_parquet(D / f"sig_{S}.parquet").filter(
            ~c("partial") & ~c("lu_e").fill_null(False) & (c("trade_date") <= SAMPLE_END))
        if S == "EA":      # 现役 EA_combo 排序：d0 降序百分位 + 市值升序百分位
            nn = pl.len().over("trade_date")
            x = x.with_columns(((c("d0").rank().over("trade_date") / nn)
                                + ((-c("total_mv")).rank().over("trade_date") / nn)).alias("rk_score"))
            key = "rk_score"
        else:              # 现役 FA：p_change_min 封顶 300 降序
            key = "score"
        x = x.with_columns(c("code").cast(pl.Int64).alias("ci"))
        x = (x.sort(["trade_date", key, "ci"], descending=[False, True, False], nulls_last=True)
              .with_columns(pl.int_range(pl.len()).over("trade_date").alias("rank")))
        picks = x.filter(c("rank") < TOP).rename({"trade_date": "sd"}).join(idx, on=["code", "sd"], how="left")
        picks = picks.with_columns((c("row") + 1).alias("row0")).drop("row").with_row_index("hid")
        ac, ao, lk, pd = make_paths(panel, picks.select("hid", "code", "row0"))
        valid, px, base = valid_mask(ac, ao)
        fwd = picks["fwd"].cast(pl.Float64).to_numpy()
        chk = np.abs(base[valid] - fwd[valid])
        print(f"[{S}] picks={picks.height} valid={valid.sum()} fwd校验 mean|diff|={np.nanmean(chk):.5f} max={np.nanmax(chk):.5f}", flush=True)
        res[S] = dict(picks=picks, ac=ac, ao=ao, lk=lk, pd=pd, base=base, valid=valid)
    return res


def walk_target(sd, base, exit_dates, fallback):
    """信号日之前已完结交易的平均收益（严格因果）。"""
    n = len(base)
    ed = np.array([str(x) for x in exit_dates])
    order = np.argsort(ed, kind="stable")
    ed_s, cum = ed[order], np.cumsum(base[order])
    k = np.searchsorted(ed_s, sd.astype(str), side="left")
    out = np.full(n, float(fallback))
    ok = k >= 50
    out[ok] = cum[k[ok] - 1] / k[ok]
    return out


# ──────────────────────────── 泛化检验：全部中线 base 逐笔 ────────────────────────────
def generalized(panel):
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
    print(f"[泛化] 逐笔记录={rec.height} 策略前缀={rec['prefix'].n_unique()} 唯一持仓={rec.select('code','entry').n_unique()}", flush=True)
    uniq = rec.select("code", "entry").unique().with_row_index("hid")
    u = uniq.join(panel.select("code", "trade_date", "row").rename({"trade_date": "entry", "row": "row0"}),
                  on=["code", "entry"], how="left")
    ac, ao, lk, pd = make_paths(panel, u.select("hid", "code", "row0"))
    valid, px, base = valid_mask(ac, ao)
    # 映射回每条记录
    m = rec.join(uniq, on=["code", "entry"], how="left")
    hid = m["hid"].cast(pl.Int64).to_numpy()
    prefix = m["prefix"].to_numpy()
    b = base[hid]
    v = valid[hid]
    # 每个策略用自己的（重建口径）历史平均收益作为目标
    uniq_p = np.unique(prefix)
    T = np.zeros(len(prefix))
    for p in uniq_p:
        sel = (prefix == p) & v
        mu = float(b[sel].mean()) if sel.sum() >= 10 else float(b[v].mean())
        T[prefix == p] = mu
    sim = simulate(ac[hid], ao[hid], lk[hid], T, mode="eod")
    sret, sdays, shit = sim["ret"], sim["days"], sim["hit"]
    ok = v
    by_prefix = []
    for p in uniq_p:
        s = (prefix == p) & ok
        if s.sum() < 10:
            continue
        by_prefix.append((str(p), int(s.sum()), float(b[s].mean()), float(sret[s].mean()), float(T[prefix == p][0])))
    worse = sum(1 for _, _, a0, a1, _ in by_prefix if a1 < a0)
    # 唯一路径池上的阈值扫描 / 固定持有期扫描（避免多策略重复权重）
    uv = np.flatnonzero(valid)
    scan = {}
    for t in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.15, 0.20):
        s = simulate(ac[uv], ao[uv], lk[uv], t, mode="eod")
        st = stats(s["ret"], s["days"])
        st["hit"] = round(100 * float(s["hit"].mean()), 1)
        scan[f"{int(t*100)}%"] = st
    horizons = {}
    for h in (3, 5, 10, 15, 20, 25, 30):
        vh, _, bh = valid_mask(ac[uv], ao[uv], h)
        horizons[f"h{h}"] = stats(bh[vh], np.full(vh.sum(), float(h)))
    return dict(n_records=int(rec.height), n_unique=int(len(uniq)), n_valid=int(ok.sum()),
                n_prefix=len(by_prefix), prefix_worse=worse,
                base=stats(b[ok]), eod=stats(sret[ok], sdays[ok]),
                hit_rate=round(100 * float(shit[ok].mean()), 1),
                by_prefix=by_prefix, scan=scan, horizons=horizons,
                ac=ac[uv], ao=ao[uv], lk=lk[uv])


# ──────────────────────────── 报告 ────────────────────────────
def md_table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def main():
    print("载入逐日价格面板 …", flush=True)
    panel = load_panel()
    print(f"面板 rows={panel.height} 日期 {panel['trade_date'].min()}~{panel['trade_date'].max()}", flush=True)

    exp = main_experiment(panel)
    gen = generalized(panel)
    report = {"main": {}, "generalized": {}, "meta": {
        "cost": COST, "hold_days": HOLD, "sample_end": SAMPLE_END,
        "note": "同笔配对；基准=第21行开盘卖；方案=浮盈达标当日尾盘收盘卖；均为20交易日上限"}}

    lines = ["# 中线「达到策略平均收益当天尾盘离场」对照回测", "",
             f"生成日期：2026-11-15；数据截止 {SAMPLE_END}；成本 0.3%/笔；T+1；跌停封死顺延。", "",
             "说明：每笔都是同笔配对，买入价、成本、样本完全相同，唯一差别是离场时点。基准 = 固定持 20 个交易日后开盘卖；"
             "方案 = 持仓第 2~20 日尾盘浮盈首次达到目标 T 时当日收盘卖，未达标仍按基准到期卖。", ""]

    for S, label in (("EA", "业绩加速 mid-earnings-accel (EA_combo)"), ("FA", "业绩预告预喜 mid-forecast-beat")):
        d = exp[S]
        picks, ac, ao, lk, pd = d["picks"], d["ac"], d["ao"], d["lk"], d["pd"]
        base, valid = d["base"], d["valid"]
        sd = picks["sd"].cast(pl.Utf8).to_numpy().astype(str)
        rank = picks["rank"].cast(pl.Int64).to_numpy()
        code = picks["code"].cast(pl.Utf8).to_numpy().astype(str)
        vi = np.flatnonzero(valid)
        ac_v, ao_v, lk_v, pd_v = ac[vi], ao[vi], lk[vi], pd[vi]
        sd_v, base_v = sd[vi], base[vi]
        dev = sd_v <= DEV_END
        T_dev = float(base_v[dev].mean())
        T_full = float(base_v.mean())
        T_walk = walk_target(sd_v, base_v, pd_v[:, HOLD], T_dev)
        print(f"[{S}] T_dev={100*T_dev:.2f}% T_full={100*T_full:.2f}% T_walk 均值={100*np.mean(T_walk):.2f}%", flush=True)

        variants = {
            "基准(持20日)": None,
            f"达标尾盘卖 T_dev={100*T_dev:.1f}%": simulate(ac_v, ao_v, lk_v, T_dev),
            "达标尾盘卖 T_walk(因果)": simulate(ac_v, ao_v, lk_v, T_walk),
            f"达标尾盘卖 T_full={100*T_full:.1f}%(含前视)": simulate(ac_v, ao_v, lk_v, T_full),
            "达标次日开盘卖 T_dev": simulate(ac_v, ao_v, lk_v, T_dev, mode="nxo"),
            "达标尾盘卖 T_dev(净浮盈口径)": simulate(ac_v, ao_v, lk_v, T_dev, net_target=True),
            "达标尾盘卖 T=+2%": simulate(ac_v, ao_v, lk_v, 0.02),
            "达标尾盘卖 T=+8%": simulate(ac_v, ao_v, lk_v, 0.08),
        }
        rows = []
        for name, s in variants.items():
            if s is None:
                st = stats(base_v, np.full(len(base_v), float(HOLD)))
                exd, r, hit = pd_v[:, HOLD], base_v, np.zeros(len(base_v), bool)
            else:
                r, days, hit = s["ret"], s["days"], s["hit"]
                st = stats(r, days)
                exd = np.where(hit, pd_v[np.arange(len(pd_v)), np.minimum(days.astype(int) - 1, MAXK)], pd_v[:, HOLD])
            st["hit"] = round(100 * float(hit.mean()), 1)
            # 样本外 N3 / R12
            oos = phase_of(sd_v, "N3")
            st["N3"] = round(100 * float(r[oos].mean()), 2)
            st["N3_base"] = round(100 * float(base_v[oos].mean()), 2)
            rows.append([name, st["n"], f"{st['avg']:+.2f}", f"{st['med']:+.2f}", st["win"], st["pf"],
                         st["days"], st["per_day"], st["hit"], f"{st['N3']:+.2f}"])
            # 组合口径（K20/mn5，按买入日开仓、1/20 名义）
            if name in ("基准(持20日)", f"达标尾盘卖 T_dev={100*T_dev:.1f}%", "达标尾盘卖 T_walk(因果)"):
                entry_date = pd_v[:, 0]
                st["portfolio"] = portfolio(entry_date, code[vi], rank[vi], exd, r)
                st["portfolio_phases"] = {}
                for p in ("W1", "W2", "R12", "N3"):
                    m = phase_of(sd_v, p)
                    if m.sum() >= 50:
                        st["portfolio_phases"][p] = portfolio(entry_date[m], code[vi][m], rank[vi][m], exd[m], r[m])
            report["main"].setdefault(S, {})[name] = st
        # 右尾诊断：达标卖出的那批「若继续持到 20 日」能赚多少
        sv = variants[f"达标尾盘卖 T_dev={100*T_dev:.1f}%"]
        hm = sv["hit"]
        diag = {"hit_n": int(hm.sum()),
                "hit_locked_avg": round(100 * float(sv["ret"][hm].mean()), 2),
                "hit_hold20_avg": round(100 * float(base_v[hm].mean()), 2),
                "miss_n": int((~hm).sum()),
                "miss_avg": round(100 * float(sv["ret"][~hm].mean()), 2),
                "miss_hold20_avg": round(100 * float(base_v[~hm].mean()), 2)}
        for q in (0.05, 0.10, 0.20):
            cut = np.quantile(base_v, 1 - q)
            diag[f"base_ex_top{int(q*100)}"] = round(100 * float(base_v[base_v < cut].mean()), 2)
        report["main"][S]["diag"] = diag
        report["main"][S]["targets"] = {"T_dev": round(100 * T_dev, 2), "T_full": round(100 * T_full, 2),
                                        "T_walk_mean": round(100 * float(T_walk.mean()), 2)}
        lines += [f"## {label}", "",
                  f"- 开发期(2019~2023)每笔平均 **{100*T_dev:+.2f}%**；全样本(含前视) **{100*T_full:+.2f}%**；滚动因果目标均值 {100*T_walk.mean():+.2f}%", "",
                  md_table(rows, ["方案", "笔数", "每笔均值%", "中位数%", "胜率%", "PF", "平均持有日", "每持仓日%", "触发率%", "样本外N3%"]), ""]
        # 分段（基准 vs 开发期阈值方案）
        s = variants[f"达标尾盘卖 T_dev={100*T_dev:.1f}%"]
        seg = []
        for p in ("P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12", "N3"):
            m = phase_of(sd_v, p)
            if m.sum() == 0:
                continue
            seg.append([p, int(m.sum()), f"{100*base_v[m].mean():+.2f}", f"{100*s['ret'][m].mean():+.2f}",
                        f"{100*(s['ret'][m].mean()-base_v[m].mean()):+.2f}", round(float(s["days"][m].mean()), 1)])
        lines += ["### 分段：固定 20 日 vs 达标尾盘离场（T_dev，固定在开发期，样本外无前视）", "",
                  md_table(seg, ["窗口", "笔数", "基准均值%", "方案均值%", "差值pp", "方案持有日"]), ""]
        lines += ["### 为什么：达标就走究竟砍掉了什么", "",
                  md_table([["触发离场笔数", diag["hit_n"]],
                            ["其中：锁定到的平均收益%", f"{diag['hit_locked_avg']:+.2f}"],
                            ["其中：若继续持到 20 日的平均收益%", f"{diag['hit_hold20_avg']:+.2f}"],
                            ["未触发笔数（仍持满 20 日）", diag["miss_n"]],
                            ["未触发笔平均收益%", f"{diag['miss_avg']:+.2f}"],
                            ["基准去掉最高 5% 交易后均值%", f"{diag['base_ex_top5']:+.2f}"],
                            ["基准去掉最高 10% 交易后均值%", f"{diag['base_ex_top10']:+.2f}"],
                            ["基准去掉最高 20% 交易后均值%", f"{diag['base_ex_top20']:+.2f}"]],
                           ["诊断项", "值"]), ""]
        pfr = []
        for nm, st in report["main"][S].items():
            if nm == "targets" or "portfolio" not in st:
                continue
            row = [nm, st["portfolio"]["n"], f"{st['portfolio']['tot']:+.1f}"]
            for p in ("W1", "W2", "R12", "N3"):
                row.append(f"{st['portfolio_phases'].get(p, {}).get('tot', 0):+.1f}")
            pfr.append(row)
        lines += ["### 组合口径（K=20 名额 / 每日最多新开 5 只，累计名义收益）", "",
                  "生产推荐**不限同时持有数量**，这个口径只用于说明「名额复用」能带来什么；它不是用户实际持仓体验。", "",
                  md_table(pfr, ["方案", "可开仓笔数", "全样本累计%", "W1%", "W2%", "R12%", "N3%"]), ""]

    # 泛化检验
    g = gen
    gr = [[k, v["n"], f"{v['avg']:+.2f}", v["win"], v["pf"], v["days"], v["per_day"], v.get("hit", "")]
          for k, v in g["scan"].items()]
    hr = [[k, v["n"], f"{v['avg']:+.2f}", v["win"], v["pf"], v["days"], v["per_day"]] for k, v in g["horizons"].items()]
    lines += ["## 泛化检验：全部中线策略的历史持仓路径", "",
              f"覆盖 {g['n_records']} 条 base 逐笔记录 / {g['n_prefix']} 个策略前缀 / {g['n_valid']} 条有效配对，"
              f"去重后 {g['n_unique']} 个唯一 (股票,买入日) 路径。", "",
              f"- 每个策略用**自己的历史平均收益**做目标：基准 {g['base']['avg']:+.2f}%/笔 → 方案 {g['eod']['avg']:+.2f}%/笔，"
              f"平均持有 {g['base']['days']}→{g['eod']['days']} 日，触发率 {g['hit_rate']}%，"
              f"**{g['prefix_worse']}/{g['n_prefix']} 个策略跑输基准**。", "",
              "### 阈值敏感性（唯一路径池，避免多策略重复权重）", "",
              md_table(gr, ["目标T", "笔数", "每笔均值%", "胜率%", "PF", "平均持有日", "每持仓日%", "触发率%"]), "",
              "### 20 个交易日是否合理：固定持有期扫描（同一池）", "",
              md_table(hr, ["持有日", "笔数", "每笔均值%", "胜率%", "PF", "持有日", "每持仓日%"]), ""]
    report["generalized"] = {k: v for k, v in g.items() if k not in ("ac", "ao", "lk", "by_prefix")}
    report["generalized"]["by_prefix_sample"] = g["by_prefix"][:20]

    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    (OUT / "report.md").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\n产物：{OUT/'report.md'} / {OUT/'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
