"""一次性重建三窗口逐笔交易+信号特征，落盘供反复分析（只读，不碰生产）。

输出 output/winrate_trades_dump.json：每笔交易带 signal 特征 + 模拟结果。
之后所有 多策略分工 / 资金流 / 入场特征 分析都从这个 dump 读，不重复打 DB。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from si_path_a_signal_backtest import (
    PathABacktestRunner, CHAMPION_CONFIG, BACKTEST_WINDOWS, SignalRecord,
)
from app.db.session import SessionLocal

WINDOWS = ["2025_H1", "2025_H2", "2026_H1"]


def main() -> int:
    out_rows = []
    db = SessionLocal()
    try:
        for w in WINDOWS:
            # 子类化 runner，钩住 actionable 信号，把特征并到 trade
            runner = PathABacktestRunner(db, config=dict(CHAMPION_CONFIG))
            # 包一层 _simulate_trade 以附带信号特征
            orig_sim = runner._simulate_trade

            def sim_with_feat(sr, bars_by_code, horizon_days, market_context, _o=orig_sim):
                t = _o(sr, bars_by_code, horizon_days, market_context)
                if t is None:
                    return None
                # 把信号特征塞到 trade（借用动态属性，最后序列化时取）
                t._sig = {
                    "score": sr.score, "pos60": sr.pos60, "chg20": sr.chg20,
                    "is_limit_up": sr.is_limit_up, "signal_gap_pct": sr.signal_gap_pct,
                    "industry_momentum": sr.industry_momentum, "vol5_20": sr.vol5_20,
                    "day_pct_chg": sr.day_pct_chg, "dsa_signal_score": sr.dsa_signal_score,
                }
                return t
            runner._simulate_trade = sim_with_feat

            m = runner.run(*BACKTEST_WINDOWS[w], window_label=w)
            trades = [t for t in runner.trades[w] if t.entry_triggered]
            for t in trades:
                sig = getattr(t, "_sig", {})
                out_rows.append({
                    "window": w,
                    "trade_date": t.trade_date, "month": t.trade_date[:7],
                    "code": t.code, "strategy_id": t.strategy_id,
                    "market_regime": t.market_regime,
                    "entry_date": t.entry_date, "exit_date": t.exit_date,
                    "exit_reason": t.exit_reason,
                    "net_return_pct": t.net_return_pct,
                    "gross_return_pct": t.gross_return_pct,
                    "holding_days": t.holding_days,
                    **{f"sig_{k}": v for k, v in sig.items()},
                })
            print(f"  {w}: {len(trades)} 笔已落盘  WR={m.win_rate_pct}%  均净={m.avg_net_return_pct}%")
    finally:
        db.close()

    out = Path(__file__).resolve().parents[1] / "output" / "winrate_trades_dump.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(out_rows, ensure_ascii=False))
    print(f"\n总计 {len(out_rows)} 笔 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
