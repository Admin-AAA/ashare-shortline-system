"""按策略并行回测：每个策略独立评估其信号在最近一年的真实表现。

回答"哪个已有策略行/不行"。单策略口径：该策略在全市场每日信号取 top-N
过线(score>=threshold)，T+1 开盘入场 + 因果出场引擎 + 费用。不走 production
selection 的跨策略聚合（那是组合层，单策略评估不需要）。
多进程并行（每策略一个进程，独立 DB 连接）。只读，不写库。

用法: python scripts/winrate_per_strategy_parallel.py [--start 2025-08-16 --end 2026-08-14 --top-n 6 --score-line 60 --workers 6]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class _Bar:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    pct_chg: float
    tradable: bool = True


def _eval_strategy(skill_id: str, start: str, end: str, top_n: int, score_line: float) -> dict:
    """在子进程中评估单个策略。每次新建会话避免跨进程共享连接。"""
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.engine.skill_loader import load_skills
    from app.engine.strategy_runner import run_strategy
    from app.services.market_source.snapshot import _breadth_from_counts
    from app.services.outcome_simulation import _resolve_exit_causal, limit_pct_for_code
    from sqlalchemy import select
    from sqlalchemy.orm import load_only
    from app.models.tables import DailyKbar, Stock
    from si_path_a_signal_backtest import CHAMPION_CONFIG
    from si_path_a_signal_backtest import PathABacktestRunner

    FEE = (0.0001 * 2 + 0.0005) * 100
    SLIP = 10.0
    MAXGAP = 10.0

    db = SessionLocal()
    try:
        skills = {s.skill_id: s for s in load_skills(get_settings().skills_dir)
                  if s.enabled and s.executable and s.contract}
        skill = skills.get(skill_id)
        if skill is None:
            return {"strategy_id": skill_id, "error": "not_executable"}

        # 复用 PATH-A 的特征构造器(单策略实例,只为用它的 _bar_to_stock_dict/_market_context)
        helper = PathABacktestRunner(db, config=dict(CHAMPION_CONFIG), skills=[skill])

        trade_dates = [str(r[0]) for r in db.execute(
            select(DailyKbar.trade_date).where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
            .group_by(DailyKbar.trade_date).order_by(DailyKbar.trade_date)).all()]
        if not trade_dates:
            return {"strategy_id": skill_id, "n": 0}

        lookahead_end = (date.fromisoformat(trade_dates[-1]) + timedelta(days=19)).isoformat()
        lookback_start = (date.fromisoformat(trade_dates[0]) - timedelta(days=180)).isoformat()
        kbars = list(db.scalars(
            select(DailyKbar).options(load_only(
                DailyKbar.code, DailyKbar.trade_date, DailyKbar.open, DailyKbar.high, DailyKbar.low,
                DailyKbar.close, DailyKbar.volume, DailyKbar.amount, DailyKbar.pct_chg,
                DailyKbar.turnover_rate, DailyKbar.ma5, DailyKbar.ma10, DailyKbar.ma20, DailyKbar.ma60,
                DailyKbar.macd_dif, DailyKbar.macd_dea, DailyKbar.rsi_12, DailyKbar.breakout20,
                DailyKbar.breakout60, DailyKbar.chip_cost_15, DailyKbar.chip_cost_85, DailyKbar.bias_5,
                DailyKbar.vol_ratio_5, DailyKbar.vol_ratio_20, DailyKbar.max_dd20, DailyKbar.max_dd60,
                DailyKbar.upper_shadow_count20))
            .where(DailyKbar.trade_date >= lookback_start, DailyKbar.trade_date <= lookahead_end)
            .order_by(DailyKbar.code, DailyKbar.trade_date)))

        bars_by_code: dict[str, dict[str, Any]] = defaultdict(dict)
        bars_by_date: dict[str, list] = defaultdict(list)
        for b in kbars:
            bars_by_code[str(b.code)][str(b.trade_date)] = b
            bars_by_date[str(b.trade_date)].append(b)

        from typing import Any
        trades = []
        n_signals = 0
        for td in trade_dates:
            day_bars = bars_by_date.get(td, [])
            if not day_bars:
                continue
            mc = helper._market_context_for_date(td, bars_by_date)
            day_signals = []
            for bar in day_bars:
                code = str(bar.code)
                sd = helper._bar_to_stock_dict(bar, bars_by_code, td, mc)
                if sd is None:
                    continue
                try:
                    sig = run_strategy(skill, sd, [])
                except Exception:
                    continue
                n_signals += 1
                score = float(sig.get("score", 0))
                if sig.get("hard_veto") or score < score_line:
                    continue
                day_signals.append((score, code, bar, sig))
            # 取 top_n
            day_signals.sort(key=lambda x: x[0], reverse=True)
            for score, code, bar, sig in day_signals[:top_n]:
                # T+1 入场模拟
                future = [b for d2, b in sorted(bars_by_code[code].items()) if str(d2) > td]
                if len(future) < 2:
                    continue
                eb = future[0]
                eo = float(eb.open or 0)
                if eo <= 0:
                    continue
                sig_close = float(bar.close or 0)
                gap = (eo / sig_close - 1) * 100 if sig_close > 0 else 0
                if gap > MAXGAP:
                    continue
                if float(eb.volume or 0) <= 0:
                    continue
                ep = eo * (1 + SLIP / 1e4)
                holding = future[:6]
                try:
                    xp, xd, reason = _resolve_exit_causal(
                        holding, stop_loss_price=ep * 0.955, take_profit_price=ep * 1.06,
                        code=code, entry_price=ep, dynamic_trailing=True,
                        trailing_policy="profit_floor", profit_lock_ratio=0.5,
                        activate_pct=None, min_lock_pct=None)
                except Exception:
                    continue
                if not xp or xp <= 0:
                    continue
                xp2 = xp * (1 - SLIP / 1e4)
                net = (xp2 / ep - 1) * 100 - FEE
                # 持仓期内最高价(用于算"没吃到的利润"/追高回落)
                peak_close = max((float(b.close or 0) for b in holding if float(b.close or 0) > 0), default=ep)
                trades.append({
                    "td": td, "net": net, "reason": reason, "score": score,
                    "regime": mc["regime"], "code": code, "month": td[:7],
                    # 入场特征(亏损挖掘用)
                    "pos60": float(sd.get("pos60", 50)), "chg20": float(sd.get("chg20", 0)),
                    "vol5_20": float(sd.get("vol5_20", 1.0)), "day_pct": float(sd.get("pct", 0)),
                    "turnover": float(sd.get("turnover_rate", 0)), "amount_yi": float(sd.get("amount_yi", 0)),
                    "bias5": float(sd.get("bias_5", 0) or 0), "max_dd20": float(sd.get("max_dd20", 0)),
                    "off_high": float(sd.get("off_high_pct", 0)), "dist20": float(sd.get("dist20", 0)),
                    "ind_mom": float(sd.get("industry_momentum", 0.5)),
                    "gap_pct": gap, "peak_ret": (peak_close / ep - 1) * 100,
                })

        if not trades:
            return {"strategy_id": skill_id, "n": 0, "n_signals": n_signals}
        n = len(trades)
        wins = [t for t in trades if t["net"] > 0]
        losses = [t for t in trades if t["net"] <= 0]
        wr = len(wins) / n * 100
        avg = sum(t["net"] for t in trades) / n
        avgw = sum(t["net"] for t in wins) / len(wins) if wins else 0
        avgl = sum(t["net"] for t in losses) / len(losses) if losses else 0
        pf = sum(t["net"] for t in wins) / abs(sum(t["net"] for t in losses)) if losses and sum(t["net"] for t in losses) else 0
        # 逐月均净(符号一致性)
        bymon = defaultdict(list)
        for t in trades:
            bymon[t["td"][:7]].append(t["net"])
        mon_avg = {m: round(sum(v) / len(v), 3) for m, v in sorted(bymon.items())}
        mon_signs = "".join("+" if v > 0 else "-" for v in mon_avg.values())
        # 出场分布
        byreason = defaultdict(int)
        for t in trades:
            byreason[t["reason"]] += 1
        return {
            "strategy_id": skill_id, "n": n, "n_signals": n_signals,
            "win_rate": round(wr, 2), "avg_net": round(avg, 4),
            "avg_win": round(avgw, 3), "avg_loss": round(avgl, 3),
            "profit_factor": round(pf, 3), "pl_ratio": round(abs(avgw / avgl), 2) if avgl else 0,
            "monthly_avg": mon_avg, "monthly_signs": mon_signs,
            "exit_breakdown": dict(byreason),
            "trades": trades,  # 逐笔(含入场特征)供亏损挖掘; 主进程落盘
        }
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-08-16")
    ap.add_argument("--end", default="2026-08-14")
    ap.add_argument("--top-n", type=int, default=6)
    ap.add_argument("--score-line", type=float, default=60.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.engine.skill_loader import load_skills
    db = SessionLocal()
    try:
        skill_ids = [s.skill_id for s in load_skills(get_settings().skills_dir)
                     if s.enabled and s.executable and s.contract]
    finally:
        db.close()

    print(f"并行评估 {len(skill_ids)} 策略 | 窗口 {args.start}~{args.end} | top{args.top_n} 线{args.score_line} | {args.workers} 进程")
    results = []
    # spawn 而非 fork：避免子进程继承主进程已打开的 SQLAlchemy DB 连接导致死锁。
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as ex:
        futs = {ex.submit(_eval_strategy, sid, args.start, args.end, args.top_n, args.score_line): sid for sid in skill_ids}
        for fut in as_completed(futs):
            sid = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                print(f"  ✓ {sid}: n={r.get('n', 0)} WR={r.get('win_rate', 0)}% 均净={r.get('avg_net', 0)}%")
            except Exception as e:
                print(f"  ✗ {sid}: {str(e)[:80]}")
                results.append({"strategy_id": sid, "error": str(e)[:200]})

    results.sort(key=lambda r: r.get("avg_net", -999))
    print(f"\n{'策略':<38}{'n':>6}{'胜率%':>7}{'均净%':>9}{'盈亏比':>7}{'PF':>7}  逐月符号")
    for r in results:
        if r.get("error") or r.get("n", 0) == 0:
            print(f"{r['strategy_id']:<38}{'ERR/0':>6}")
            continue
        print(f"{r['strategy_id']:<38}{r['n']:>6}{r['win_rate']:>7.1f}{r['avg_net']:>+9.3f}{r['pl_ratio']:>7.2f}{r['profit_factor']:>7.2f}  {r['monthly_signs']}")

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        # 汇总(去trades) + 逐笔分开落盘
        summary = [{k: v for k, v in r.items() if k != "trades"} for r in results]
        Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        all_trades = []
        for r in results:
            for t in (r.get("trades") or []):
                all_trades.append({"strategy_id": r["strategy_id"], **t})
        trades_path = args.output.replace(".json", "_trades.json")
        Path(trades_path).write_text(json.dumps(all_trades, ensure_ascii=False))
        print(f"\n汇总 {args.output} | 逐笔 {trades_path} ({len(all_trades)} 笔)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
