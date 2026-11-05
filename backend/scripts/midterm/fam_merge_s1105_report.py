"""把 fam_merge_s1105.py 的 overlap/merge 结果 JSON 汇成 Markdown 表。用法：FAM_OUT=<目录> python fam_merge_s1105_report.py"""
import json
import os
from pathlib import Path

OUT = Path(os.environ.get("FAM_OUT") or ".")
SHORT = {"mid-": ""}


def s(x):
    return x.replace("mid-", "")


def f(v, nd=2, sign=True):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


lines = []
for fam in "ABCDEF":
    m = OUT / f"fam_merge_{fam}.json"
    o = OUT / f"fam_overlap_{fam}.json"
    if not m.exists():
        continue
    r, ov = json.loads(m.read_text()), json.loads(o.read_text())
    v = r["verdict"]
    lines.append(f"### 家族 {fam} → `{r['sid']}`：{'合并' if v['merge'] else '不合并'}")
    lines.append("")
    lines.append(f"N3 回测里被≥2 个成员同时买入的 (票,入场日) 占比 {ov['bt_multi_share']:.0%}。")
    lines.append("")
    lines.append("| 成员对 | N3 回测 Jaccard | N3 包含率 | 实盘 Jaccard | 实盘包含率 | 实盘含截断候选 Jaccard |")
    lines.append("|---|---|---|---|---|---|")
    for p in sorted(ov["pairs"], key=lambda p: -(p["live_jaccard"] or 0)):
        lines.append(f"| {s(p['a'])} / {s(p['b'])} | {f(p['bt_jaccard'], 2, False)} | {f(p['bt_contain'], 2, False)} | "
                     f"{f(p['live_jaccard'], 2, False)} | {f(p['live_contain'], 2, False)} | {f(p['cand_jaccard'], 2, False)} |")
    lines.append("")
    lines.append("| 策略 | R12 笔 | R12 每笔 | R12 PF | R12 回撤 | N3 笔 | N3 每笔 | N3 PF | N3 回撤 | 每持有日(N3) | 正段 | W0 | W00 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    PH = ["P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12"]
    rows = [(f"**合并 {r['sid']}**", r["phases"])] + [(s(k), st) for k, st in r["member_stats"].items()]
    for name, P in rows:
        a, b = P.get("R12", {}), P.get("N3", {})
        pos = sum(1 for ph in PH if P.get(ph, {}).get("n") and P[ph]["avg"] > 0)
        nph = sum(1 for ph in PH if P.get(ph, {}).get("n"))
        lines.append(f"| {name} | {a.get('n')} | {f(a.get('avg'))} | {f(a.get('pf'), 2, False)} | {f(a.get('mdd'), 1)} | "
                     f"{b.get('n')} | {f(b.get('avg'))} | {f(b.get('pf'), 2, False)} | {f(b.get('mdd'), 1)} | {f(b.get('per_day'), 3)} | "
                     f"{pos}/{nph} | {f(P.get('W0', {}).get('avg'))} | {f(P.get('W00', {}).get('avg'))} |")
    mm = v["member_mean"]
    lines.append(f"| 成员平均 | {mm['R12_n']} | {f(mm['R12_avg'])} | | {f(mm['R12_mdd'], 1)} | {mm['N3_n']} | {f(mm['N3_avg'])} | | {f(mm['N3_mdd'], 1)} | | | | |")
    lines.append("")
    a, b = r["phases"]["R12"], r["phases"]["N3"]
    lines.append("合并后收益来源（R12 / N3）：两端各去5%均值 "
                 f"{f(a['trim_both5'])} / {f(b['trim_both5'])}；去掉最赚5%均值 {f(a['trim5'])} / {f(b['trim5'])}；"
                 f"前10笔占总利润 {f(100 * a['top10_share'], 0, False) if a['top10_share'] else '—'}% / "
                 f"{f(100 * b['top10_share'], 0, False) if b['top10_share'] else '—'}%；中位数 {f(a['median'])} / {f(b['median'])}；{v['label'][:3]}。")
    fails = list(v["s282"]["fail"]) + [k for k, ok in v["vs_mean"].items() if not ok]
    lines.append(f"判定：§282 {'过' if v['s282']['pass'] else '不过'}（正段 {v['s282']['pos_phases']}/8）；"
                 f"不比成员平均差 {'过' if all(v['vs_mean'].values()) else '不过'}。未过项：{('、'.join(fails)) or '无'}。")
    lines.append("")
print("\n".join(lines))
