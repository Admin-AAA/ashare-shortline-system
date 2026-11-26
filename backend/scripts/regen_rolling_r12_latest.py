"""用已有重放 staging 按现行口径（中长线 + 短线都只算卖出日 ≤ cutoff）重算 rolling_r12_latest.json，不重跑重放。

只读：staging 选股文件、库里日K与已结算推荐。重放元数据（replay/crashed/window/cutoff）沿用原结果文件；
中长线与短线用生产函数 midlong_window_stats / short_window_stats 重算，counts / positive_ratio / method 用生产
verdict_summary / method_text。策略表取现行 ms.STRATEGIES 与 expected_short_strategies，已退役的不再进分母。

默认只预览（打印修前修后对照、写对照文件）；加 --write 才覆盖 latest 与同截止日归档，写前先备份 latest。
用法：.venv/bin/python scripts/regen_rolling_r12_latest.py --root <output/strategy_health> [--month 2026-11] [--write]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import rolling_r12 as rr  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-11")
    ap.add_argument("--root", default=str(rr.OUT_ROOT))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)
    latest = root / "rolling_r12_latest.json"
    old = json.loads(latest.read_text(encoding="utf-8"))
    man = json.loads((rr.month_dir(root, a.month) / "manifest.json").read_text(encoding="utf-8"))
    assert old["cutoff"] == man["cutoff"], (old["cutoff"], man["cutoff"])
    cutoff, ws = man["cutoff"], man["window_start"]

    from app.db.session import SessionLocal
    from app.services.daily_output_check import expected_short_strategies

    db = SessionLocal()
    try:
        midlong = rr.midlong_window_stats(db, rr.month_dir(root, a.month) / "picks", ws, cutoff)
        short = rr.short_window_stats(db, ws, cutoff)
        # 对照：今天的库、旧口径（按信号日，含截止日后才卖出的笔），只为把「口径变化」和「这两天新结算」分开
        expected = expected_short_strategies(db)
        segs = rr.short_segments(ws, cutoff, short["replay_coverage_end"])
        signal_basis: dict[str, list[float]] = {}
        for seg in (segs["replay"], segs["live"]):
            for sid, (inn, late) in rr.short_segment_returns(db, seg, cutoff, expected).items():
                signal_basis.setdefault(sid, []).extend(inn + late)
    finally:
        db.close()

    # 原结果文件里的选股抛异常 / 整窗取不到数，沿用原判定（重算不重放，修不了这些）
    for sid, c in (old.get("crashed") or {}).items():
        if sid in midlong:
            midlong[sid] = {**midlong[sid], "verdict": "error", "error": c.get("error"),
                            "error_first_day": c.get("first_day"), "error_days": c.get("days")}
    for sid in (old.get("replay") or {}).get("unavailable_strategies") or []:
        if sid in midlong:
            midlong[sid] = {**midlong[sid], "verdict": "unavailable",
                            "unavailable_days": (old["midlong"].get(sid) or {}).get("unavailable_days")}

    now = _dt.datetime.now().replace(microsecond=0)
    doc = {**old, "generated_at": now.isoformat(), **rr.verdict_summary(midlong, short),
           "midlong": midlong, "short": short, "method": rr.method_text(short),
           "recount": {"replay_generated_at": old.get("generated_at"),
                       "note": "未重跑重放：沿用 %s 的 staging，按 %s 的日K与已结算推荐、现行口径重算" % (old.get("generated_at"), now.date())}}

    def cmp_row(o: dict | None, n: dict) -> dict:
        return {"before": {k: (o or {}).get(k) for k in ("n", "avg", "verdict")},
                "after": {k: n.get(k) for k in ("n", "avg", "verdict", "open")}}

    short_cmp = {}
    for sid, n in short["strategies"].items():
        sb = signal_basis.get(sid) or []
        short_cmp[sid] = {**cmp_row(old["short"]["strategies"].get(sid), n),
                          "today_signal_basis": {"n": len(sb), "avg": round(sum(sb) / len(sb), 2) if sb else None},
                          "replay_after": n["replay_segment"], "live_after": n["live_segment"]}
    report = {
        "generated_at": now.isoformat(), "cutoff": cutoff,
        "before_file": {"generated_at": old.get("generated_at"), "counts": old.get("counts"),
                        "positive_ratio": old.get("positive_ratio")},
        "after": {"counts": doc["counts"], "positive_ratio": doc["positive_ratio"]},
        "short": short_cmp,
        "midlong": {sid: cmp_row(old["midlong"].get(sid), n) for sid, n in midlong.items()},
        "dropped_retired": sorted(set(old["midlong"]) - set(midlong)) + sorted(set(old["short"]["strategies"]) - set(short["strategies"])),
    }
    out = root / f"rolling_r12_recount_short_{now:%Y%m%d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    print("before", old.get("counts"), old.get("positive_ratio"), "| after", doc["counts"], doc["positive_ratio"])
    print("dropped (retired):", report["dropped_retired"])
    for sid, r in short_cmp.items():
        b, f, t = r["before"], r["after"], r["today_signal_basis"]
        print(f"short {sid:34s} file {b['n']}/{b['avg']}/{b['verdict']} | today-signal {t['n']}/{t['avg']} | "
              f"after {f['n']}/{f['avg']}/{f['verdict']} open {f['open']} | live {r['live_after']['n']}/{r['live_after']['avg']} "
              f"late {r['live_after']['sold_after_cutoff']}")
    for sid, r in report["midlong"].items():
        if r["before"]["verdict"] != r["after"]["verdict"]:
            print(f"midlong flip {sid}: {r['before']} -> {r['after']}")
    if not a.write:
        print("preview only (加 --write 才写 latest)")
        return
    backup = root / f"rolling_r12_latest.before_{now:%Y%m%d}.json"
    if not backup.exists():
        shutil.copy2(latest, backup)
    payload = json.dumps(doc, ensure_ascii=False, indent=1)
    archive = root / f"rolling_r12_{cutoff}.json"
    if archive.exists() and archive.read_bytes() != backup.read_bytes():
        shutil.copy2(archive, root / f"rolling_r12_{cutoff}.before_{now:%Y%m%d}.json")
    archive.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    print("wrote", latest, archive, "backup", backup)


if __name__ == "__main__":
    main()
