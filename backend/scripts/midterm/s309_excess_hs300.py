"""§309：每笔策略收益 − 同持有期沪深300涨幅（按笔平均），写入中长线 quality 与短线 short_quality_metrics。

只读：index_daily(000300.SH) + §282 保存的逐笔文件。只展示，不参与达标判定。
用法：cd backend && .venv/bin/python scripts/midterm/s309_excess_hs300.py [--dry]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
METRICS = BACKEND / "app/services/midterm_backtest_metrics.json"
SHORT_Q = BACKEND / "app/policies/short_quality_metrics.json"
T = BACKEND / "output/research/s282/trades"
SHORT_TRADES = BACKEND / "output/research/s282/short_trades_base.json"

MID_LONG = {  # 策略 id -> 逐笔文件前缀（R12/N3 两个窗口）
    "mid-small-cap-weekly": "r25__SCW_h20",
    "mid-incentive-option": "r25__IO_h20",
    "mid-earnings-accel": "r25__G_h20",
    "mid-forecast-beat": "r25__FA_h20",
    "mid-repurchase-plan": "r25__RP_r05_h20",
    "mid-block-trade-premium": "r25__BTP_h20",
    "mid-lowprice-smallcap-weekly": "r104__LPS_W_h20",
    "mid-smallcap-dividend-weekly": "r118__SDY_W_h20",
    "mid-smallcap-lowps-weekly": "r119__SPS_W_h20",
    "mid-smallcap-reversal-weekly": "r120__SMR_W",
    "long-lowturn-lowvol": "r16__LTV_h60",
    "long-dividend-lowvol": "r19__DVLV_h60",
    "long-growth-streak": "r102__CG4_h60",
    "long-net-cash-switch": "r98__NCS_h60",
}
SHORT_SKILLS = ["chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader"]


def load_hs300() -> pd.DataFrame:
    from sqlalchemy import text
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT trade_date, open, close FROM index_daily WHERE ts_code='000300.SH' "
                               "ORDER BY trade_date")).all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["d", "open", "close"])
    df["d"] = df["d"].astype(str).str[:10]
    return df.set_index("d")


def _norm(d) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]


def summarize(strat_ret_pct: list[float], idx_ret_pct: list[float]) -> dict:
    n = len(strat_ret_pct)
    ex = [a - b for a, b in zip(strat_ret_pct, idx_ret_pct)]
    return {"excess_hs300": round(float(sum(ex) / n), 2), "hs300_avg": round(float(sum(idx_ret_pct) / n), 2),
            "beat_hs300": round(100 * sum(1 for x in ex if x > 0) / n, 1)}


def idx_returns(ix: pd.DataFrame, entries, exits, exit_field: str) -> list[float]:
    out = []
    for e, x in zip(entries, exits):
        e, x = _norm(e), _norm(x)
        if e not in ix.index or x not in ix.index:
            raise KeyError(f"沪深300 缺 {e} 或 {x}（本地日线从 2023-05-29 起）")
        out.append(float((ix.at[x, exit_field] / ix.at[e, "open"] - 1) * 100))
    return out


def main(dry: bool = False) -> None:
    ix = load_hs300()
    print("HS300", ix.index.min(), ix.index.max(), len(ix))
    metrics = json.loads(METRICS.read_text())
    rows = []
    for sid, pre in MID_LONG.items():
        q = metrics[sid]["quality"]
        for w in ("R12", "N3"):
            d = pd.read_parquet(T / f"{pre}__{w}.parquet")
            assert len(d) == q[w]["n"] and abs(d["ret"].mean() * 100 - q[w]["avg"]) < 0.011, (sid, w, len(d), q[w]["n"])
            res = summarize((d["ret"] * 100).tolist(), idx_returns(ix, d["entry"], d["exit"], "open"))
            q[w].update(res)
            rows.append((sid, w, len(d), q[w]["avg"], res))
        q["hs300_basis"] = "逐笔：策略净收益(已扣0.3%) − 同持有期沪深300（买入日开盘→卖出日开盘，不扣成本）；§309，只展示"
    # 短线
    sq = json.loads(SHORT_Q.read_text())
    st = json.loads(SHORT_TRADES.read_text())
    sens = {}
    for key, win in (("R12", ["12m"]), ("N3", ["12m", "EXT"])):
        allt = [t for w in win for t in st[w]]
        groups = {"ALL": allt, **{s: [t for t in allt if t["sid"] == s] for s in SHORT_SKILLS}}
        for g, ts in groups.items():
            rets = [t["net"] for t in ts]
            en = [t["en"] for t in ts]
            ex = [t["ex"] for t in ts]
            res = summarize(rets, idx_returns(ix, en, ex, "close"))
            alt = summarize(rets, idx_returns(ix, en, ex, "open"))
            sens[(g, key)] = round(res["excess_hs300"] - alt["excess_hs300"], 3)
            res["n_base_draw"] = len(ts)
            sq["skills"][g][key].update(res)
            rows.append((g, key, len(ts), round(sum(rets) / len(rets), 2), res))
        for g in groups:
            sq["skills"][g]["hs300_basis"] = ("逐笔：策略净收益 − 同持有期沪深300（买入日开盘→卖出日收盘，不扣成本）；"
                                              "用短线 BASE 抽样那一次的逐笔（与 21 次抽样平均的笔数/均值略有差异）；§309，只展示")
    for r in rows:
        print(r)
    print("短线 收盘 vs 开盘口径差（个百分点）：", sens)
    if not dry:
        METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=1))
        SHORT_Q.write_text(json.dumps(sq, ensure_ascii=False, indent=1))
        print("written")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
