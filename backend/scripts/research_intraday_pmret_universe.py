"""为【全候选宇宙】构建信号日下午收益 pm_ret 查找（一次性，缓存）。
冻结：holdout 快照全部卡 (dt,code)，原生 15min；前瞻：canonical run 全部候选，5min 重采样。
pm_ret = 15:00 收盘 / 13:15（下午第一根）开盘 - 1（%）。输出两个 dict pkl。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json
from scripts.research_canonical_two_window import build_forward_canonical, RUN
OUT = B / "output" / "research"


def _eng():
    for line in (B / ".env").read_text().splitlines():
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))


def _pmret_from_bars(sub: pd.DataFrame, freq: str):
    sub = sub.sort_values("tt").reset_index(drop=True)
    if freq != "15min":  # 5min -> 15min 每3根
        g = sub.groupby(sub.index // 3)
        o = g.o.first().values; c = g.c.last().values
    else:
        o, c = sub.o.values, sub.c.values
    n = len(c)
    if n < 9: return None  # 需含下午
    pm_open, close = o[8], c[-1]
    if not pm_open or pm_open <= 0: return None
    return (close / pm_open - 1) * 100


def build_lookup(eng, pairs: pd.DataFrame, freq: str) -> dict:
    look = {}
    dates = sorted(pairs.dt.unique())
    for m in sorted({str(d)[:7] for d in dates}):
        md = [d for d in dates if str(d).startswith(m)]
        codes = sorted(pairs[pairs.dt.isin(md)].code.unique().tolist())
        if not codes: continue
        sql = text("""SELECT trade_date dt, code, trade_time tt, open o, close c
                      FROM minute_kbars WHERE freq=:f AND trade_date BETWEEN :d0 AND :d1 AND code IN :c
                      ORDER BY code, trade_time""")
        df = pd.read_sql(sql, eng, params={"f": freq, "d0": min(md), "d1": max(md),
                                          "c": tuple(codes)})
        df["dt"] = df.dt.astype(str); df["code"] = df.code.astype(str)
        for (dt, code), sub in df.groupby(["dt", "code"], sort=False):
            v = _pmret_from_bars(sub, freq)
            if v is not None: look[(dt, code)] = v
        print(f"  {freq} {m}: pairs-month={len(md)} lookups cum={len(look)}")
    return look


def main():
    eng = _eng()
    # frozen 全候选
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    fp = []
    for d in snap.days:
        for card in _thaw_json(d.cards):
            fp.append((d.signal_date, str(card.get("code"))))
    fp = pd.DataFrame(fp, columns=["dt", "code"]).drop_duplicates()
    print("frozen pairs", len(fp))
    fz = build_lookup(eng, fp, "15min")
    pd.to_pickle(fz, OUT / "pmret_lookup_frozen.pkl")
    # forward 全候选
    fwdf = build_forward_canonical()[["dt", "code"]].copy()
    fwdf["dt"] = fwdf.dt.astype(str); fwdf["code"] = fwdf.code.astype(str)
    fwdf = fwdf.drop_duplicates()
    print("forward pairs", len(fwdf))
    fw = build_lookup(eng, fwdf, "5min")
    pd.to_pickle(fw, OUT / "pmret_lookup_forward.pkl")
    print("done frozen", len(fz), "forward", len(fw))


if __name__ == "__main__":
    main()
