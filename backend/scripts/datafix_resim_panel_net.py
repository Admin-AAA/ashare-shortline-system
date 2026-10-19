"""§106b 数据修复复核：单策略面板 net_by_code / exit_by_code 中受 DB ma5/ma10=0 影响的均线破位出场重算。

背景：daily_kbars ma5/10/20/60 在 2025-01-02~01-14、2026-01-19~07-22 全市场为 0（另有 07-23~10-16 零星），
已由系统架构专家重算（commits 4294973 / 1e0b06d / 43211a1 / fc89366）。面板卡片特征走 research_runner 的
`_number(ma) or _mean(closes)` 兜底，不受影响；但面板回放时 `_simulate_card` 的 ma_break_exit 读 DB ma5，
为 0 时跳过 → 坏窗口内破位出场从未触发，面板 net_by_code 偏差（只影响声明了 ma_break_exit 的策略）。

做法：对面板全部卡片用现行 `_simulate_card`（现行 strategy.yaml + 修复后的 DB 日K/均线）重模拟；
只回写"新旧任一方出场原因为 ma_break_close"且净收益/出场原因不同的卡（其余差异只报告不改）。
默认只读（dry-run）；--write 先把原面板备份到 /tmp/<sid>_<window>_entry_panel.pre-s106b.json 再原子写回。
用法：.venv/bin/python scripts/datafix_resim_panel_net.py [--sids sector-relative-strength-leader] [--windows 12m] [--write]
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.services.ma_break_exit import MA_BREAK_REASON, load_ma_break_windows
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_stability_gates import panels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default=",".join(sorted(load_ma_break_windows())))
    ap.add_argument("--windows", default="12m",
                    help="面板窗口（逗号分隔）。默认只 12m：/strategies 单策略指标只读 12m solo 面板；EXT/W0/W00 面板早于 §67/§71 出场调参，"
                         "研究脚本本就逐笔重模拟、不读其 net_by_code，且与现行出场有非均线差异，不回写")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    with SessionLocal() as db:
        for sid in [s for s in a.sids.split(",") if s]:
            for w, f in panels(sid).items():
                if w not in a.windows.split(","):
                    continue
                p = json.loads(f.read_text())
                need = defaultdict(set)
                for d in p["days"]:
                    for c in d["cards"]:
                        if str(c.get("code")) in d["net_by_code"]:
                            need[d["signal_date"]].add(str(c.get("code")))
                bars = fetch_bars(db, need)
                n_cards = upd = other = 0
                rows = []
                for d in p["days"]:
                    for c in d["cards"]:
                        code = str(c.get("code"))
                        if code not in d["net_by_code"]:
                            continue
                        n_cards += 1
                        day = Day(); day.signal_date = d["signal_date"]
                        day.outcome_bars = {code: tuple(bars[d["signal_date"]].get(code, ())[:lim])}
                        tr = (PE._simulate_card(day, c, ep) or {}).get("trade")
                        ok = bool(tr and tr.executed)
                        nn = float(tr.net_return_pct) if ok else None
                        ne = str(getattr(tr, "exit_reason", "")) if ok else None
                        on, oe = d["net_by_code"].get(code), d["exit_by_code"].get(code)
                        same = (on is None and nn is None) or (on is not None and nn is not None and abs(on - nn) < 1e-6 and oe == ne)
                        if same:
                            continue
                        if MA_BREAK_REASON in (oe, ne):
                            upd += 1
                            rows.append((d["signal_date"], code, on, oe, nn, ne))
                            d["net_by_code"][code], d["exit_by_code"][code] = nn, ne
                        else:
                            other += 1
                            print(f"  [other-diff, not written] {sid} {w} {d['signal_date']} {code} {on}/{oe} -> {nn}/{ne}", flush=True)
                print(f"{sid} {w}: cards {n_cards}, ma_break diffs {upd}, other diffs {other}", flush=True)
                for r in rows:
                    print("   ", r, flush=True)
                if a.write and upd:
                    bak = Path("/tmp") / f"{sid}_{w}_entry_panel.pre-s106b.json"
                    if not bak.exists():
                        bak.write_text(f.read_text())
                    tmp = f.with_suffix(".tmp")
                    tmp.write_text(json.dumps(p, ensure_ascii=False))
                    os.replace(tmp, f)
                    print(f"   written {f} (backup {bak})", flush=True)
                del p


if __name__ == "__main__":
    main()
