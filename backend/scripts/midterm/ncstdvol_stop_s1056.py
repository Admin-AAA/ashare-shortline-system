#!/usr/bin/env python3
"""§1056 NCSTDVOL_h20 移动保护止损验证（预注册见 docs/strategy-research.md §1056）。

只做三件事，且都必须是「新增信息」：
  1) 更长历史：2019-01~2026-10 全段逐 phase；
  2) 分市值窗口：信号日 total_mv 横截面三分位（小/中/大）各自独立判定；
  3) 不确定性：按信号日聚类的块 bootstrap 95% CI + 符号置换检验 p 值。

规则集与判定门槛在预注册里已写死，本脚本不得增删规则、不得改门槛。
所有模拟输入按 records 顺序取路径；模拟输出即为 per-record，不再按 hid 二次索引（L-202）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
import lt_data as L  # noqa: E402
import ncstdvol_s735 as M735  # noqa: E402
import exit_stop_research as S  # noqa: E402

c = pl.col
SAMPLE_END = S.SAMPLE_END
DEV_END = S.DEV_END
HOLD = S.HOLD
OUT = BACKEND / "output" / "research" / "s1056_ncstdvol_stop"
OUT.mkdir(parents=True, exist_ok=True)

# 预注册固定的 7 条规则，跑后不增删
RULES = [
    ("BASE", None),
    ("M5_5", dict(kind="trail", a=0.05, d=0.05)),
    ("M8_5", dict(kind="trail", a=0.08, d=0.05)),
    ("M5_8", dict(kind="trail", a=0.05, d=0.08)),
    ("M8_8", dict(kind="trail", a=0.08, d=0.08)),
    ("BE10", dict(kind="be", a=0.10)),
    ("SL8", dict(kind="sl", sl=0.08)),
]
CANDIDATES = ["M5_5", "M8_5", "M5_8", "M8_8"]
PHASES = ["P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12"]


# ──────────────────────────── 选股（复用 §735 口径） ────────────────────────────
def build_picks(panel: pl.DataFrame) -> pl.DataFrame:
    df = M735.prep()
    df = df.filter(c("NCSTDVOL").fill_null(False) & (c("trade_date") <= SAMPLE_END))
    df = df.with_columns(c("code").cast(pl.Utf8).alias("code"))
    df = (df.sort(["trade_date", "sc_ncstdvol", "code"], descending=[False, True, False], nulls_last=True)
            .with_columns(pl.int_range(pl.len()).over("trade_date").alias("rank")))
    picks = df.filter(c("rank") < 5)
    idx = panel.select("code", "trade_date", "row").rename({"trade_date": "sd"})
    p = picks.rename({"trade_date": "sd"}).join(idx, on=["code", "sd"], how="left")
    return (p.select("code", "sd", "row", "sc_ncstdvol")
             .with_columns((c("row") + 1).alias("row0"))
             .with_row_index("hid"))


def market_cap_bucket(code: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """按信号日横截面 total_mv 三分位：0 小 / 1 中 / 2 大；缺失记 -1。

    daily_basic 是周频快照（年均 ~51 个日期），因此按 code 做 PIT 向后 join 取最近一期，
    分位在「该快照日的全市场」内计算，不能用信号样本内部排序。
    """
    b = (L.basic().select("code", c("trade_date").alias("bd"), c("total_mv").cast(pl.Float64))
         .filter(c("total_mv").is_not_null() & (c("total_mv") > 0))
         .sort("code", "bd"))
    b = b.with_columns((c("total_mv").rank().over("bd") / pl.len().over("bd")).alias("mvpct"))
    key = (pl.DataFrame({"code": pl.Series(code, dtype=pl.Utf8),
                         "sd": pl.Series(sd, dtype=pl.Utf8)})
           .with_row_index("i").sort("code", "sd"))
    j = key.join_asof(b, left_on="sd", right_on="bd", by="code", strategy="backward").sort("i")
    pct = j["mvpct"].to_numpy()
    out = np.full(len(code), -1, dtype=int)
    ok = np.isfinite(pct)
    out[ok & (pct < 1 / 3)] = 0
    out[ok & (pct >= 1 / 3) & (pct < 2 / 3)] = 1
    out[ok & (pct >= 2 / 3)] = 2
    return out


# ──────────────────────────── 不确定性 ────────────────────────────
def block_bootstrap_ci(sd: np.ndarray, delta: np.ndarray, iters: int = 1000, seed: int = 1056):
    """按信号日整块有放回重采样，返回每笔均值差的 95% CI。"""
    rng = np.random.default_rng(seed)
    dates = np.array(sorted(set(sd.tolist())))
    groups = [delta[sd == d] for d in dates]
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([g.size for g in groups])
    k = len(dates)
    out = np.empty(iters)
    for t in range(iters):
        pick = rng.integers(0, k, k)
        out[t] = sums[pick].sum() / max(cnts[pick].sum(), 1)
    return (float(100 * np.quantile(out, 0.025)), float(100 * np.quantile(out, 0.975)))


def sign_flip_p(delta: np.ndarray, iters: int = 2000, seed: int = 1056) -> float:
    """配对符号置换检验：零假设下 Δ 关于 0 对称。"""
    rng = np.random.default_rng(seed)
    obs = abs(delta.sum())
    cnt = 0
    for _ in range(iters):
        if abs((delta * rng.choice([-1.0, 1.0], delta.size)).sum()) >= obs - 1e-12:
            cnt += 1
    return (cnt + 1) / (iters + 1)


def md(rows, header) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


# ──────────────────────────── 主流程 ────────────────────────────
def main() -> None:
    panel = S.load_panel()
    p = build_picks(panel)
    ac, ao, al, lk, pdv = S.make_paths(panel, p.select("hid", "code", "row0"))
    valid, px, _ = S.valid_mask(ac, ao, al)
    vi = np.flatnonzero(valid)

    sd = pdv[:, 0][vi]
    code = p["code"].to_numpy()[vi]
    exd = pdv[:, HOLD][vi]
    base = ao[:, HOLD][vi] / px[vi] - 1 - S.COST
    mv = market_cap_bucket(code, sd)

    print(f"[样本] 信号 {p.height} 笔，有效 {len(vi)} 笔，日期 {sd.min()}~{sd.max()}", flush=True)
    print(f"[市值] 小 {int((mv==0).sum())} / 中 {int((mv==1).sum())} / 大 {int((mv==2).sum())} / 缺失 {int((mv<0).sum())}", flush=True)

    # 各规则模拟（输入按 records 顺序，输出 per-record，不再二次索引）
    sims = {}
    for name, kw in RULES:
        if kw is None:
            sims[name] = dict(ret=base.copy(), days=np.full(len(vi), float(HOLD)), hit=np.zeros(len(vi), bool))
        else:
            r = S.simulate_stop(ac[vi], ao[vi], al[vi], lk[vi], **kw)
            sims[name] = dict(ret=r["ret"], days=r["days"], hit=r["hit"])

    # ── 1) 规则总表（全样本 / 开发期 / 样本外）──
    dev = sd <= DEV_END
    oos = sd > DEV_END
    rows, summary = [], {}
    for name, _ in RULES:
        r, d, h = sims[name]["ret"], sims[name]["days"], sims[name]["hit"]
        st, st_dev, st_oos = S.stats(r, d), S.stats(r[dev], d[dev]), S.stats(r[oos], d[oos])
        delta = r - base
        lo, hi = block_bootstrap_ci(sd, delta)
        pv = sign_flip_p(delta)
        rec = dict(n=st["n"], avg=st["avg"], med=st["med"], win=st["win"], pf=st["pf"], std=st["std"],
                   p10=st["p10"], days=st["days"],
                   dev_n=st_dev["n"], dev_avg=st_dev["avg"], oos_n=st_oos["n"], oos_avg=st_oos["avg"],
                   d_all=round(float(100 * delta.mean()), 2),
                   d_dev=round(float(100 * delta[dev].mean()), 2),
                   d_oos=round(float(100 * delta[oos].mean()), 2),
                   ci_lo=round(lo, 2), ci_hi=round(hi, 2), p=round(pv, 3),
                   hit=round(100 * float(h.mean()), 1))
        summary[name] = rec
        rows.append([name, st["n"], f"{st['avg']:+.2f}", f"{st_dev['avg']:+.2f}", f"{st_oos['avg']:+.2f}",
                     f"{rec['d_all']:+.2f}", f"{rec['d_dev']:+.2f}", f"{rec['d_oos']:+.2f}",
                     f"[{rec['ci_lo']:+.2f}, {rec['ci_hi']:+.2f}]", f"{rec['p']:.3f}",
                     st["win"], st["pf"], st["std"], st["p10"], st["days"], rec["hit"]])

    # ── 2) 逐 phase（候选规则）──
    ph_rows, ph_detail = [], {}
    for name in ["BASE"] + CANDIDATES + ["BE10", "SL8"]:
        r = sims[name]["ret"]
        row = [name]
        det = {}
        for ph in PHASES:
            m = S.phase_of(sd, ph)
            if m.sum() == 0:
                row.append("—")
                continue
            v = 100 * float((r - base)[m].mean())
            det[ph] = dict(n=int(m.sum()), d=round(v, 2))
            row.append(f"{v:+.2f}({int(m.sum())})")
        ph_rows.append(row)
        ph_detail[name] = det

    # ── 3) 分市值三档 ──
    mv_rows, mv_detail = [], {}
    for name in CANDIDATES:
        r = sims[name]["ret"]
        row = [name]
        det = {}
        for b, tag in ((0, "小"), (1, "中"), (2, "大")):
            m = mv == b
            if m.sum() < 40:
                row.append(f"—(n={int(m.sum())})")
                det[tag] = dict(n=int(m.sum()), d=None)
                continue
            v = 100 * float((r - base)[m].mean())
            row.append(f"{v:+.2f}(n={int(m.sum())})")
            det[tag] = dict(n=int(m.sum()), d=round(v, 2))
        mv_rows.append(row)
        mv_detail[name] = det

    # ── 4) 组合口径（K20/max_new5）──
    port_rows, port_detail = [], {}
    for name, _ in RULES:
        pf = S.portfolio(sd, code, exd, sims[name]["ret"])
        ratio = round(pf["tot"] / abs(pf["mdd"]), 2) if pf["mdd"] else 99.0
        port_detail[name] = dict(**pf, ratio=ratio)
        port_rows.append([name, pf["n"], pf["tot"], pf["mdd"], ratio, pf["max_hold"]])

    # ── 5) 结构诊断（不改规则/门槛，仅描述收益从哪来）──
    r5, h5 = sims["M5_5"]["ret"], sims["M5_5"]["hit"].astype(bool)
    qs = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
    diag_rows = [["BASE"] + [f"{100*float(np.quantile(base, q)):+.1f}" for q in qs]
                 + [f"{100*float(base.max()):+.1f}", f"{100*float(base.min()):+.1f}"],
                 ["M5_5"] + [f"{100*float(np.quantile(r5, q)):+.1f}" for q in qs]
                 + [f"{100*float(r5.max()):+.1f}", f"{100*float(r5.min()):+.1f}"]]
    split_rows = [
        ["止损卖出（触发）", int(h5.sum()), f"{100*float(r5[h5].mean()):+.2f}",
         f"{100*float(base[h5].mean()):+.2f}", f"{100*float((r5-base)[h5].mean()):+.2f}"],
        ["未触发（持满）", int((~h5).sum()), f"{100*float(r5[~h5].mean()):+.2f}",
         f"{100*float(base[~h5].mean()):+.2f}", f"{100*float((r5-base)[~h5].mean()):+.2f}"],
    ]
    trim_rows = []
    for cut in (0, 5, 10, 20):
        k = int(len(base) * cut / 100)
        bs = float(np.sort(base)[::-1][k:].mean())
        rs = float(np.sort(r5)[::-1][k:].mean())
        trim_rows.append([f"去掉最赚 {cut}%", f"{100*bs:+.2f}", f"{100*rs:+.2f}", f"{100*(rs-bs):+.2f}"])
    dl = np.sort(r5 - base)[::-1]
    tot_d = float(dl.sum())
    conc_rows = [[f"前 {k} 笔", f"{100*float(dl[:k].sum()):+.1f}pp",
                  f"{100*float(dl[:k].sum())/max(tot_d,1e-9):.0f}%"] for k in (5, 10, 20)]
    rest = dl[10:]
    rest_avg = 100 * float(rest.mean())
    conc_rows.append([f"其余 {rest.size} 笔", f"{100*float(rest.sum()):+.1f}pp",
                      f"{100*float(rest.sum())/max(tot_d,1e-9):.0f}%"])
    all_d = 100 * float(dl.mean())
    conc_rows.append(["其余笔每笔均值", f"{rest_avg:+.2f}pp", f"（全样本 {all_d:+.2f}pp）"])
    # 四个参数的离散度：判定差异是否只是噪音
    spread = [summary[n]["d_all"] for n in CANDIDATES] + [summary[n]["d_oos"] for n in CANDIDATES]
    spread_note = (f"4 个候选 Δ全 范围 {min(summary[n]['d_all'] for n in CANDIDATES):+.2f}~"
                   f"{max(summary[n]['d_all'] for n in CANDIDATES):+.2f}pp，"
                   f"Δ样本外 {min(summary[n]['d_oos'] for n in CANDIDATES):+.2f}~"
                   f"{max(summary[n]['d_oos'] for n in CANDIDATES):+.2f}pp；"
                   f"波动 {min(summary[n]['std'] for n in CANDIDATES):.2f}~{max(summary[n]['std'] for n in CANDIDATES):.2f}%")

    # ── 6) 门槛判定 A~H（对 4 个候选逐一判定；全部满足才算通过）──
    gate_rows, gate_detail = [], {}
    for name in CANDIDATES:
        s = summary[name]
        d = ph_detail[name]
        pos_ph = sum(1 for v in d.values() if v["d"] >= 0)
        n_ph = len(d)
        mv_ok = [v["d"] for v in mv_detail[name].values() if v["d"] is not None and v["n"] >= 40]
        mv_pos = sum(1 for v in mv_ok if v >= 0)
        base_port = port_detail["BASE"]["ratio"]
        checks = {
            "A 全历史Δ≥−0.20pp": ("pass" if s["d_all"] >= -0.20 else "fail") if s["n"] >= 200 else "insufficient",
            "B 样本外Δ>0": ("pass" if s["d_oos"] > 0 else "fail") if s["oos_n"] >= 60 else "insufficient",
            "C 开发期Δ≥0": ("pass" if s["d_dev"] >= 0 else "fail") if s["dev_n"] >= 80 else "insufficient",
            "D 分段≥6/8为正": ("pass" if pos_ph >= 6 else "fail") if n_ph >= 8 else "insufficient",
            "E CI下界>−0.50pp": ("pass" if s["ci_lo"] > -0.50 else "fail"),
            "F 波动降≥10%": ("pass" if s["std"] <= 0.90 * summary["BASE"]["std"] else "fail"),
            "G 市值≥2档为正": ("pass" if mv_pos >= 2 else "fail") if len(mv_ok) >= 2 else "insufficient",
            "H 组合累计/回撤改善": ("pass" if port_detail[name]["ratio"] > base_port else "fail"),
        }
        n_pass = sum(1 for v in checks.values() if v == "pass")
        n_ins = sum(1 for v in checks.values() if v == "insufficient")
        verdict = "PASS(留候选池)" if n_pass == 8 else ("INSUFFICIENT" if n_ins else "FAIL(否决)")
        gate_detail[name] = dict(checks=checks, n_pass=n_pass, n_insufficient=n_ins, verdict=verdict)
        gate_rows.append([name] + [f"{k.split()[0]}:{v}" for k, v in checks.items()] + [f"{n_pass}/8", verdict])

    # ── 报告 ──
    L_ = ["# §1056 NCSTDVOL_h20 移动保护止损验证", "",
          f"预注册：`docs/strategy-research.md` §1056（写于跑本策略任何止损收益之前）。样本区间 {SAMPLE_END} 前，"
          f"信号 {p.height} 笔 / 有效 {len(vi)} 笔，成本 0.3%，T+1 开盘买，持 20 交易日上限。", "",
          "**这 4 组参数本来就是同一批数据挑出来的；本报告只把「更长历史 / 分市值 / 不确定性量化」当作新增信息。**", "",
          "## 一、规则总表（同笔配对，Δ 相对固定持 20 日）", "",
          md(rows, ["规则", "笔数", "全样本%", "开发期%", "样本外%", "Δ全%", "Δ开发%", "Δ样本外%",
                    "Δ 95%CI(块bootstrap)", "符号置换 p", "胜率%", "PF", "波动%", "P10%", "持有日", "触发率%"]), "",
          "## 二、逐 phase 每笔均值差（Δpp，括号内为该段笔数）", "",
          md(ph_rows, ["规则"] + PHASES), "",
          "## 三、分市值三档（信号日 total_mv 横截面三分位；<40 笔判 insufficient 不解读）", "",
          md(mv_rows, ["规则", "小市值", "中市值", "大市值"]), "",
          "## 四、组合口径（K=20 名额 / 每日最多新开 5 只 / 每笔 1/20 名义）", "",
          md(port_rows, ["规则", "开仓数", "累计名义%", "已实现回撤%", "累计÷回撤", "最大占用名额"]), "",
          "## 五、结构诊断：M5_5 的改善从哪来", "",
          "分位对比（M5_5 相对基准的收益分布变化）", "",
          md(diag_rows, ["规则"] + [f"P{int(q*100)}" for q in qs] + ["Max", "Min"]), "",
          "触发/未触发拆分（「这批若持满」= 同笔持满 20 日的收益）", "",
          md(split_rows, ["分组", "笔数", "该组实际%", "该组若持满%", "差值pp"]), "",
          "右尾依赖度（去掉最赚的若干笔后每笔均值）", "",
          md(trim_rows, ["口径", "BASE%", "M5_5%", "差值pp"]), "",
          "Δ 集中度（正贡献是否只由少数笔贡献）", "",
          md(conc_rows, ["口径", "累计Δ", "占全部正Δ比例"]), "",
          f"参数平原离散度：{spread_note}。", "",
          "## 六、预注册门槛判定（8 条全过才算通过）", "",
          md(gate_rows, ["规则", "A", "B", "C", "D", "E", "F", "G", "H", "通过数", "判定"]), ""]

    # 结论段
    passed = [n for n, g in gate_detail.items() if g["verdict"].startswith("PASS")]
    insuf = [n for n, g in gate_detail.items() if g["verdict"] == "INSUFFICIENT"]
    L_ += ["## 七、结论", ""]
    if passed:
        L_ += [f"- 通过预注册门槛的候选：**{', '.join(passed)}**。按要求仍只留在候选池，本轮不改生产配置。"]
    elif insuf:
        L_ += [f"- **样本不足（{', '.join(insuf)}）**：任一门槛无法计算，整体记 insufficient，不得降级为「倾向通过」。"]
    else:
        L_ += ["- **4 个候选全部未通过预注册门槛**：NCSTDVOL 维持固定持 20 日，否决本轮止损改动。"]
    L_ += [f"- 基准（固定持 20 日）：全样本 {summary['BASE']['avg']:+.2f}%/笔（n={summary['BASE']['n']}）、"
           f"开发期 {summary['BASE']['dev_avg']:+.2f}%、样本外 {summary['BASE']['oos_avg']:+.2f}%、"
           f"波动 {summary['BASE']['std']:.2f}%、持有 {summary['BASE']['days']:.1f} 日。", ""]

    (OUT / "report.md").write_text("\n".join(L_), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(dict(
        meta=dict(signals=int(p.height), valid=int(len(vi)), start=str(sd.min()), end=str(sd.max()),
                  rules=[n for n, _ in RULES], maxt=len(p)),
        rules=summary, phases=ph_detail, market_cap=mv_detail, portfolios=port_detail,
        gates=gate_detail,
        structure=dict(quantiles=diag_rows, split=split_rows, trim=trim_rows, concentration=conc_rows,
                       spread=spread_note),
    ), ensure_ascii=False, indent=1), encoding="utf-8")
    print("[完成]", json.dumps({k: v["verdict"] for k, v in gate_detail.items()}, ensure_ascii=False), flush=True)
    for name in ["BASE"] + CANDIDATES:
        s = summary[name]
        print(f"  {name:6s} n={s['n']:3d} all={s['avg']:+.2f} dev={s['dev_avg']:+.2f} oos={s['oos_avg']:+.2f} "
              f"d_all={s['d_all']:+.2f} d_oos={s['d_oos']:+.2f} CI=[{s['ci_lo']:+.2f},{s['ci_hi']:+.2f}] p={s['p']:.3f}", flush=True)


if __name__ == "__main__":
    main()
