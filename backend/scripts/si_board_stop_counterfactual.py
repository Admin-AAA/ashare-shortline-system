"""Self-improve T-board-stop: 10cm 板块差异化止损宽度反事实验证（backtest 口径）。

主假设（写死，单变量）：
  如果 10cm 主板/中小板的亏损主要由「固定止损过紧 → 正常回撤被扫损 + 隔夜跳空低开
  穿透止损」驱动（clean≥72 formal 中 10cm stop_loss 簇 n=49 占 37%、sum≈-339.6%，
  而入场时点 final_score/consensus_score 与未止损票无法区分 → 无 entry 过滤可做），
  那么只对 10cm 放宽固定止损宽度（champion -5.5% → challenger 更宽），在相同股票、
  相同入场、相同 causal 退出状态机、相同成本下，10cm 平均单笔净收益应改善，
  且 20cm 不受影响、最大单笔亏损不突破 STOP_LOSS_GAP_CAP_PCT。

证据路径：LIVE 样本前瞻不足（前瞻 clean≥72 已结算 3/10），按状态机切换到 backtest
反事实。本脚本用同一批 clean≥72 formal 推荐，冻结入场价/入场日/止盈价，只把 10cm 的
stop_loss_price 换成更宽档位，经生产 `_simulate_outcome` + 自定义 `exit_resolver`
（仅改 stop 价、不动其它任何逻辑）重放，得到 champion/challenger 配对差值。

诚实性（L-001/L-002）：
  - champion 与所有 challenger 都由本脚本同一口径重放，**不引用存储 outcome**
    （存储 outcome 混有低 score_line 历史污染，不作 champion）。
  - 标签 `backtest`；不与 LIVE formal 直接算差值。
  - 配对 cluster bootstrap 按 trade_date 分组，固定种子，报告 95% CI。

用法：
  backend/.venv/bin/python backend/scripts/si_board_stop_counterfactual.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import random

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import DailyKbar, Recommendation, RecommendationRun  # noqa: E402
from app.services.official_sample_scope import official_sample_conditions  # noqa: E402
from app.services.outcome_simulation import (  # noqa: E402
    DEFAULT_STOP_LOSS_PCT,
    _resolve_exit,
    _simulate_outcome,
)

#  clean formal 口径：与 self_check D 项 clean≥72 对齐
SCORE_LINE_FLOOR = 72.0
NET_COST_PCT = 0.07  # 与 self_check 净成本口径一致（扣 0.07%/笔）

# champion / challenger 止损档位（只对 10cm 生效；20cm 全程 -5.5% 不变）
CHAMPION_STOP = DEFAULT_STOP_LOSS_PCT          # -5.5
CHALLENGER_STOPS = [-7.0, -8.5, -10.0, -12.0]  # 更宽档

BOOTSTRAP_N = 10000
BOOTSTRAP_SEED = 20260730


def _board(code: str) -> str:
    c = str(code or "")
    return "20cm" if c.startswith(("300", "301", "688", "689")) else "10cm"


def _make_exit_resolver(stop_pct_10cm: float):
    """生成只改 10cm stop 价的 exit_resolver，其余逻辑=生产 _resolve_exit。"""

    def resolver(holding_bars, stop_loss_price, take_profit_price, *, code="", entry_price=0.0):
        sp = stop_loss_price
        if _board(code) == "10cm" and entry_price > 0:
            sp = round(entry_price * (1 + stop_pct_10cm / 100.0), 2)
        return _resolve_exit(
            holding_bars, sp, take_profit_price, code=code, entry_price=entry_price
        )

    return resolver


def _replay(db, stop_pct_10cm: float) -> list[dict]:
    """用指定 10cm 止损档重放全部 clean≥72 formal 推荐，返回逐笔净收益记录。"""
    recs = db.scalars(
        select(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(*official_sample_conditions())
        .where(Recommendation.final_score >= SCORE_LINE_FLOOR)
        .order_by(Recommendation.trade_date.asc())
    ).all()

    codes = sorted({str(r.code) for r in recs})
    bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
    for bar in db.scalars(
        select(DailyKbar).where(DailyKbar.code.in_(codes)).order_by(
            DailyKbar.code.asc(), DailyKbar.trade_date.asc()
        )
    ).all():
        bars_by_code[str(bar.code)].append(bar)

    resolver = _make_exit_resolver(stop_pct_10cm)
    records: list[dict] = []
    for rec in recs:
        code = str(rec.code)
        bars = bars_by_code.get(code) or []
        dates = [str(b.trade_date) for b in bars]
        if not bars:
            continue
        for horizon in (3,):  # 主 horizon=3（与 D 项主口径一致）
            sim = _simulate_outcome(rec, bars, dates, horizon, exit_resolver=resolver)
            if not sim or not sim.get("entry_triggered"):
                continue
            label = sim.get("outcome_label")
            if label not in ("success", "failed"):
                continue
            gross = float(sim.get("executed_return") or 0.0)
            records.append(
                {
                    "code": code,
                    "board": _board(code),
                    "trade_date": str(rec.trade_date),
                    "exit_reason": sim.get("exit_reason"),
                    "gross_return": gross,
                    "net_return": gross - NET_COST_PCT,
                }
            )
    return records


def _metrics(records: list[dict]) -> dict:
    rets = [r["net_return"] for r in records]
    if not rets:
        return {"n": 0}
    wins = sum(1 for x in rets if x > 0)
    gw = sum(x for x in rets if x > 0)
    gl = -sum(x for x in rets if x < 0)
    return {
        "n": len(rets),
        "win_rate": round(wins / len(rets) * 100, 1),
        "avg_net": round(sum(rets) / len(rets), 3),
        "sum_net": round(sum(rets), 1),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_loss": round(min(rets), 2),
    }


def _paired_bootstrap_diff(champ: list[dict], chall: list[dict]) -> dict:
    """按 trade_date 分组的 paired cluster bootstrap，主判定量=chall-champ 平均单笔净收益差。

    用 (code, trade_date) 对齐两批重放的同一批交易；cluster=trade_date。
    """
    def key(r):
        return (r["code"], r["trade_date"])

    cmap = {key(r): r["net_return"] for r in champ}
    smap = {key(r): r["net_return"] for r in chall}
    common = sorted(set(cmap) & set(smap))
    if not common:
        return {"paired_n": 0}

    # cluster by trade_date
    by_date: dict[str, list[float]] = defaultdict(list)
    for k in common:
        d = k[1]
        by_date[d].append(smap[k] - cmap[k])
    dates = sorted(by_date)
    clusters = [by_date[d] for d in dates]

    diffs = [smap[k] - cmap[k] for k in common]
    mean_diff = sum(diffs) / len(diffs)

    rng = random.Random(BOOTSTRAP_SEED)
    boot_means = []
    for _ in range(BOOTSTRAP_N):
        sample = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        flat = [x for c in sample for x in c]
        boot_means.append(sum(flat) / len(flat))
    boot_means.sort()
    lo = boot_means[int(0.025 * BOOTSTRAP_N)]
    hi = boot_means[int(0.975 * BOOTSTRAP_N)]
    return {
        "paired_n": len(common),
        "n_dates": len(dates),
        "mean_diff": round(mean_diff, 3),
        "ci95": [round(lo, 3), round(hi, 3)],
        "ci_excludes_zero": bool(lo > 0 or hi < 0),
    }


def main() -> None:
    db = SessionLocal()
    try:
        print(f"[config] score_floor={SCORE_LINE_FLOOR} net_cost={NET_COST_PCT}% "
              f"champion_stop={CHAMPION_STOP}% challengers={CHALLENGER_STOPS}")
        champion = _replay(db, CHAMPION_STOP)
        champ_10 = [r for r in champion if r["board"] == "10cm"]
        champ_20 = [r for r in champion if r["board"] == "20cm"]
        print(f"\n=== champion (10cm stop={CHAMPION_STOP}%) ===")
        print(f"  ALL : {_metrics(champion)}")
        print(f"  10cm: {_metrics(champ_10)}")
        print(f"  20cm: {_metrics(champ_20)}")
        # champion 10cm 出场结构
        ec = defaultdict(list)
        for r in champ_10:
            ec[r["exit_reason"]].append(r["net_return"])
        print("  10cm 出场结构:")
        for reason, vals in sorted(ec.items(), key=lambda kv: sum(kv[1])):
            print(f"    {reason:32s} n={len(vals):3d} avg={sum(vals)/len(vals):+.2f}% sum={sum(vals):+.1f}%")

        print("\n=== challengers (只改 10cm stop；20cm 恒 -5.5%) ===")
        for stop in CHALLENGER_STOPS:
            chall = _replay(db, stop)
            chall_10 = [r for r in chall if r["board"] == "10cm"]
            chall_20 = [r for r in chall if r["board"] == "20cm"]
            m10 = _metrics(chall_10)
            m20 = _metrics(chall_20)
            boot = _paired_bootstrap_diff(champ_10, chall_10)
            # 20cm 不变性校验
            same20 = abs(m20.get("sum_net", 0) - _metrics(champ_20).get("sum_net", 0)) < 1e-6
            print(f"\n  -- 10cm stop={stop}% --")
            print(f"     10cm: {m10}")
            print(f"     20cm unchanged: {same20} ({m20.get('sum_net')})")
            print(f"     paired bootstrap (10cm, chall-champ): {boot}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
