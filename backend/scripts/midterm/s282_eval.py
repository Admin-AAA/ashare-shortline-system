"""§282 评估：把逐笔捕获（output/research/s282/trades）+ §278 各阶段 JSON 汇成新标准表。用法：python s282_eval.py"""
import json, sys, glob
from pathlib import Path
import polars as pl
sys.path.insert(0, str(Path(__file__).parent))
from s282_quality import tail_metrics, overall_label, verdict

B = Path(__file__).resolve().parents[2] / "output/research"
S278, T = B / "s278", B / "s282/trades"
PHN = ["P19", "P20", "P21", "W0", "W00", "W1", "W2", "R12"]
# 策略 id -> (§278 json, json 里的名字, horizon, 现役/下线, 捕获 tag)
MAP = {
    "mid-small-cap-weekly": ("r25", "SCW", "mid", "live", "r25"),
    "mid-lowprice-smallcap-weekly": ("r104", "LPS_W", "mid", "live", "r104"),
    "mid-repurchase-plan": ("r25", "RP_r05", "mid", "live", "r25"),
    "mid-earnings-accel": ("r25", "EA_h20", "mid", "live", "r25"),
    "mid-forecast-beat": ("r25", "F_any_h20", "mid", "live", "r25"),
    "mid-incentive-option": ("r25", "INC_opt_h20", "mid", "live", "r25"),
    "mid-block-trade-premium": ("r25", "BT_prem_h20", "mid", "live", "r25"),
    "long-lowturn-lowvol": ("r16", "LTV_h60", "long", "live", "r16"),
    "long-dividend-lowvol": ("r19", "DVLV_h60", "long", "live", "r19"),
    "long-growth-streak": ("r102", "CG4_h60", "long", "live", "r102"),
    "long-repurchase-big": ("r25", "RP_big_h60", "long", "retired", "r25"),
    "long-current-debt-down": ("r88", "CLD_h60", "long", "retired", "r88"),
    "long-dividend-increase": ("r48", "DINC_h60", "long", "retired", "r48"),
    "long-undist-profit-growth": ("r94", "UDP_h60", "long", "retired", "r94"),
    "long-net-cash-switch": ("r98", "NCS_h60", "long", "retired", "r98"),
}


def pk(ph): return next(x for x in ("P19", "P20", "P21", "W00", "W0", "W1", "W2", "R12", "N3") if ph.startswith(x))


def load_json(tag):
    if tag == "r104":
        return None
    return json.load(open(S278 / f"{tag}_new.json"))


def find_parquet(tag, ph, jn, ref):
    eng = json.load(open(T / f"{tag}__{ph}__engine.json"))
    best = None
    for k, m in eng.items():
        name = k.rsplit("__", 1)[0]
        if abs(m["n"] - ref["n"]) < 0.5 and abs(m["avg"] - ref["avg"]) < 0.02:
            best = (name, m) if best is None else best
    return best


def main():
    out = {}
    for sid, (tag, jn, hz, status, ctag) in MAP.items():
        eng = {ph: json.load(open(T / f"{ctag}__{ph}__engine.json")) for ph in ("R12", "N3")}
        rec = {"horizon": hz, "status": status, "json_name": jn}
        if tag == "r104":
            # LPS_W 全阶段来自本次捕获
            phases = {}
            for ph in PHN + ["N3"]:
                e = json.load(open(T / f"r104__{ph}__engine.json"))
                m = e.get("LPS_W_h20__" + ph)
                phases[ph] = m
            ref = {"R12": phases["R12"], "N3": phases["N3"]}
            name = "LPS_W_h20"
            ph_avg = {ph: (phases[ph]["avg"] if phases[ph] else None) for ph in PHN}
        else:
            js = load_json(tag)[jn]
            ph_js = {pk(k): v for k, v in js.items()}
            ref = {"R12": ph_js["R12"], "N3": ph_js["N3"]}
            ph_avg = {ph: ph_js[ph]["avg"] for ph in PHN}
            name = None
        res = {}
        for ph in ("R12", "N3"):
            if tag == "r104":
                nm = "LPS_W_h20"
            else:
                f = find_parquet(ctag, ph, jn, ref[ph])
                assert f, (sid, ph, "找不到与 §278 一致的捕获", ref[ph])
                nm = f[0]
            t = pl.read_parquet(T / f"{ctag}__{nm}__{ph}.parquet").sort("entry")
            rets = [100 * x for x in t["ret"].to_list()]
            tm = tail_metrics(rets)
            r = ref[ph]
            assert abs(tm["n"] - r["n"]) < 0.5 and abs(tm["avg"] - r["avg"]) < 0.02, (sid, ph, tm["n"], tm["avg"], r["n"], r["avg"])
            res[ph] = {"n": int(r["n"]), "avg": r["avg"], "pf": r["pf"], "mdd": r["mdd"], "win": r.get("win"),
                       "streak": tm["streak"], "median": round(tm["median"], 2), "trim5": round(tm["trim5"], 2), "trim_both5": round(tm["trim_both5"], 2),
                       "top10_share": None if tm["top10_share"] is None else round(tm["top10_share"], 3),
                       "top10_red": tm["top10_red"], "robust": tm["robust"], "label": tm["label"],
                       "excess": r.get("excess"), "capture": nm}
        rec.update(res)
        rec["phases"] = {k: ph_avg[k] for k in PHN}
        rec["overall_label"] = overall_label(res["R12"], res["N3"])
        rec["verdict"] = verdict(hz, res["R12"], res["N3"], rec["phases"])
        out[sid] = rec
    json.dump(out, open(B / "s282/quality_mid_long.json", "w"), ensure_ascii=False, indent=1)
    print(f"{'策略':34}{'状态':8}| R12 n/avg/PF/MDD | N3 n/avg/PF/MDD | 连亏 | 正阶段 | W0/W00 | trim5 R12/N3 | top10 R12/N3 | 中位 R12/N3 | 标签 | 达标/未过项")
    for sid, r in out.items():
        a, b, v, p = r["R12"], r["N3"], r["verdict"], r["phases"]
        print(f"{sid:34}{r['status']:8}| {a['n']}/{a['avg']:+.2f}/{a['pf']:.2f}/{a['mdd']:.1f} | {b['n']}/{b['avg']:+.2f}/{b['pf']:.2f}/{b['mdd']:.1f} | {a['streak']}/{b['streak']} | {v['pos_phases']}/8 | {p['W0']:+.1f}/{p['W00']:+.1f} | {a['trim5']:+.2f}/{b['trim5']:+.2f} | {a['top10_share']}/{b['top10_share']} | {a['median']:+.2f}/{b['median']:+.2f} | {r['overall_label'][:3]} | {'达标' if v['pass'] else '未达标:' + ','.join(v['fail'])}")


if __name__ == "__main__":
    main()


def short_main():
    r = json.load(open(B / "s282/short_quality.json"))
    out = {}
    print("短线")
    for k in ["ALL"] + [s for s in r["12m"] if s != "ALL"]:
        a, b = r["12m"][k], r["N3"][k]
        ph = {w: r[w][k]["avg"] if r[w].get(k) else None for w in ("EXT", "12m", "W0", "W00")}
        v = verdict("short", a, b, ph)
        lab = overall_label(a, b)
        out[k] = {"R12": a, "N3": b, "phases": ph, "verdict": v, "label": lab,
                  "W0": r["W0"].get(k), "W00": r["W00"].get(k)}
        print(f"{k:34}| R12 {a['n']:.0f}/{a['avg']:+.2f}/{a['pf']:.2f}/{a['mdd']:.1f} | N3 {b['n']:.0f}/{b['avg']:+.2f}/{b['pf']:.2f}/{b['mdd']:.1f} | 连亏 {a['streak']:.1f}/{b['streak']:.1f} | 窗口 {ph} | trim5 {a['trim5']:+.2f}/{b['trim5']:+.2f} | top10 {a['top10_share']}/{b['top10_share']} | 中位 {a['median']:+.2f}/{b['median']:+.2f} | {lab[:3]} | {'达标' if v['pass'] else '未达标:' + ','.join(v['fail'])}")
    json.dump(out, open(B / "s282/quality_short.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__" and "short" in sys.argv:
    short_main()
