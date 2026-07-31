"""PATH-A 策略信号回测 — Champion 跨窗口稳定性验证

在 2025-2026 历史数据上运行完整推荐管线，按生产配置（20cm-only + no-DS + structural_strong veto）
后过滤，用生产级退出模拟（stop_loss/-4.5%、take_profit/+6.0%、trailing profit_lock=0.30）
计算 T+1 入场扣费后净收益。

适用场景：
- L-018 策略信号回测（PATH-A）替代 formal 重放（PATH-B）
- L-019 小样本 champion 跨窗口验证
- champion 参数网格扫描去重

用法：
    cd backend && .venv/bin/python scripts/si_path_a_backtest.py --start 2025-06-01 --end 2025-06-30
    cd backend && .venv/bin/python scripts/si_path_a_backtest.py --start 2025-06-01 --end 2025-06-30 --challenger stop_loss=-5.5
    cd backend && .venv/bin/python scripts/si_path_a_backtest.py --start 2025-01-01 --end 2025-12-31 --batch
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND_ROOT / ".env")

from sqlalchemy import func, or_, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun  # noqa: E402
from app.services.factors.registry import bootstrap_default_factor_registry  # noqa: E402
from app.services.backtest.challenger_decision import (  # noqa: E402
    PerformanceSnapshot,
    evaluate_challenger,
)

# ── Production champion 参数 ──
CHAMPION_STOP_LOSS_PCT = -4.5
CHAMPION_TAKE_PROFIT_PCT = 6.0
CHAMPION_TRAILING_RATIO = 0.50
NET_COST_PCT = 0.07
HORIZON_DAYS = 5
# 回测模式下没有 LLM 分数加成，规则打分顶在 65-70。用 50（规则闸门下限）让
# 候选进入，出票后再踩 final_score 最低质量门槛（>=50）。入口/退出全按生产参数。
SCORE_LINE = 50.0
BOARD_20CM_PREFIXES = ("300", "301", "688", "689")
EXCLUDED_SKILL_IDS = {"daily-stock-analysis-skill"}

OUTPUT_DIR = BACKEND_ROOT.parent / "output"


def _is_20cm(code: str) -> bool:
    return str(code).startswith(BOARD_20CM_PREFIXES)


def _loads(raw: str | None) -> dict:
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _is_production_recommendation(rec: Recommendation) -> bool:
    """按生产配置判定推荐是否应被 20cm-only + no-DS champion 保留。

    三个条件：
    1. 20cm 板块（300/301/688/689）
    2. 非 daily-stock-analysis-skill 独占票
    3. 结构性强势 hard veto 票剔除（persisted score_line_meta.decision_regime）
    """
    code = str(rec.code or "")
    if not _is_20cm(code):
        return False

    # 检查是否是 daily-stock-analysis-skill 独占票
    signals = json.loads(rec.skill_breakdown_json or "[]")
    if not isinstance(signals, list) or len(signals) == 0:
        return False
    skill_ids = {
        str(s.get("strategy_id") or s.get("skill_id") or "")
        for s in signals if isinstance(s, dict)
    }
    if skill_ids == EXCLUDED_SKILL_IDS:
        return False  # 纯 DS 票，跳过

    # 检查结构性强势 hard veto
    feature = _loads(rec.feature_snapshot_json)
    score_line_meta = feature.get("score_line_meta") or {}
    decision_regime = score_line_meta.get("decision_regime") or ""
    # 兼容旧数据：score_line_meta 尚未持久化 decision_regime 时，从 feature_snapshot 读取
    if not decision_regime:
        decision_regime = feature.get("market_regime") or feature.get("market_base") or ""
    if str(decision_regime) == "结构性强势":
        return False

    return True


def _trading_dates_in_range(db, start: str, end: str) -> list[str]:
    rows = db.execute(
        select(DailyKbar.trade_date, func.count().label("cnt"))
        .where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
        .group_by(DailyKbar.trade_date)
        .having(func.count() >= 100)
        .order_by(DailyKbar.trade_date.asc())
    ).all()
    return [r.trade_date for r in rows]


def _simulate_exit_for_records(
    recs: list[Recommendation],
    stop_loss_pct: float = CHAMPION_STOP_LOSS_PCT,
    take_profit_pct: float = CHAMPION_TAKE_PROFIT_PCT,
    trailing_ratio: float = CHAMPION_TRAILING_RATIO,
) -> list[dict]:
    """用生产级 exit 模拟（T+1 入场 + stop/tp/trailing + 扣费）计算每笔净收益。

    不依赖 RecommendationOutcome 表（旧 btrec outcome 可能用不同参数标注）。
    """
    from app.services.outcome_simulation import (
        _resolve_exit,
        _resolve_stop_loss_price,
        _resolve_take_profit_price,
        _simulate_outcome,
        TRAILING_STOP_PROFIT_LOCK_RATIO as _PROD_RATIO,
    )
    import app.services.outcome_simulation as sim_module
    from app.services.outcome_service import normalized_outcome_label

    # Temporarily override constants for challenger parameter exploration
    # (only when args specify non-champion values)
    orig_stop = sim_module.DEFAULT_STOP_LOSS_PCT
    orig_tp = sim_module.DEFAULT_TAKE_PROFIT_PCT
    orig_ratio = sim_module.TRAILING_STOP_PROFIT_LOCK_RATIO
    sim_module.DEFAULT_STOP_LOSS_PCT = stop_loss_pct
    sim_module.DEFAULT_TAKE_PROFIT_PCT = take_profit_pct
    sim_module.TRAILING_STOP_PROFIT_LOCK_RATIO = trailing_ratio

    # Also need to adjust MAX_STOP to be slightly wider
    orig_max_stop = sim_module.MAX_STOP_LOSS_PCT
    sim_module.MAX_STOP_LOSS_PCT = stop_loss_pct - 0.2

    try:
        # Load bars
        codes = sorted({str(rec.code) for rec in recs if rec.code})
        db = SessionLocal()
        try:
            bars = list(db.scalars(
                select(DailyKbar)
                .where(DailyKbar.code.in_(codes))
                .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
            ).all())
        finally:
            db.close()

        bars_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
        dates_by_code: dict[str, list[str]] = defaultdict(list)
        for bar in bars:
            c = str(bar.code)
            bars_by_code[c].append(bar)
            dates_by_code[c].append(str(bar.trade_date))

        # Pre-resolve entry for efficiency
        from app.services.outcome_simulation import _resolve_entry, CAUSAL_STRATEGY_ENTRY

        records: list[dict] = []
        for rec in recs:
            code = str(rec.code or "")
            code_bars = bars_by_code.get(code, [])
            code_dates = dates_by_code.get(code, [])
            if not code_bars or not code_dates:
                continue

            sim = _simulate_outcome(
                rec,
                code_bars,
                code_dates,
                HORIZON_DAYS,
                entry_policy=CAUSAL_STRATEGY_ENTRY,
            )
            if not sim:
                continue
            if not sim.get("entry_triggered"):
                continue

            label = normalized_outcome_label(
                type("FakeOutcome", (), {
                    "outcome_label": sim.get("outcome_label", ""),
                    "entry_triggered": sim.get("entry_triggered", False),
                })()
            )
            if label not in ("success", "failed"):
                continue

            records.append({
                "recommendation_id": str(rec.recommendation_id),
                "code": code,
                "trade_date": str(rec.trade_date),
                "exit_date": str(sim.get("exit_date") or ""),
                "net_return": float(sim.get("executed_return") or 0.0) - NET_COST_PCT,
                "exit_reason": str(sim.get("exit_reason") or ""),
            })
        return records
    finally:
        sim_module.DEFAULT_STOP_LOSS_PCT = orig_stop
        sim_module.DEFAULT_TAKE_PROFIT_PCT = orig_tp
        sim_module.TRAILING_STOP_PROFIT_LOCK_RATIO = orig_ratio
        sim_module.MAX_STOP_LOSS_PCT = orig_max_stop


def _portfolio_metrics(records: list[dict]) -> tuple[PerformanceSnapshot, dict]:
    returns = [r["net_return"] for r in records]
    if not returns:
        raise ValueError("empty records — cannot compute portfolio metrics")

    wins = [v for v in returns if v > 0]
    losses = [v for v in returns if v < 0]
    profit_factor = sum(wins) / -sum(losses) if losses else float("inf")

    # Portfolio equity curve by exit_date
    by_exit_date: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_exit_date[r["exit_date"]].append(r["net_return"])
    daily_returns = [
        sum(by_exit_date[d]) / len(by_exit_date[d])
        for d in sorted(by_exit_date)
    ]
    equity = 1.0
    peak = 1.0
    max_drawdown_pct = 0.0
    for dr in daily_returns:
        equity *= 1.0 + dr / 100.0
        peak = max(peak, equity)
        max_drawdown_pct = min(max_drawdown_pct, (equity / peak - 1.0) * 100.0)
    cumulative_net_return_pct = (equity - 1.0) * 100.0

    snapshot = PerformanceSnapshot(
        average_net_return_pct=sum(returns) / len(returns),
        cumulative_net_return_pct=cumulative_net_return_pct,
        win_rate_pct=len(wins) / len(returns) * 100.0,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
    )
    details = {
        "n": len(returns),
        "trade_days": len({r["trade_date"] for r in records}),
        "exit_days": len(daily_returns),
        "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
        "max_loss_pct": min(returns) if returns else 0.0,
        **snapshot.__dict__,
    }
    return snapshot, details


def _paired_cluster_bootstrap(
    champion_records: list[dict], challenger_records: list[dict]
) -> dict[str, Any]:
    """按 trade_date 分簇、同 code+date 配对的固定种子 10,000 次 bootstrap。"""
    champion_map = {
        (record["code"], record["trade_date"]): record["net_return"]
        for record in champion_records
    }
    challenger_map = {
        (record["code"], record["trade_date"]): record["net_return"]
        for record in challenger_records
    }
    common = sorted(set(champion_map) & set(challenger_map))
    if not common:
        return {"paired_n": 0, "n_dates": 0, "mean_diff_pct": None, "ci95_pct": [None, None]}
    by_date: dict[str, list[float]] = defaultdict(list)
    for key in common:
        by_date[key[1]].append(challenger_map[key] - champion_map[key])
    clusters = list(by_date.values())
    diffs = [value for cluster in clusters for value in cluster]
    rng = random.Random(20260731)
    boot_means: list[float] = []
    for _ in range(10000):
        sampled = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        flat = [value for cluster in sampled for value in cluster]
        boot_means.append(sum(flat) / len(flat))
    boot_means.sort()
    return {
        "paired_n": len(common),
        "n_dates": len(clusters),
        "mean_diff_pct": sum(diffs) / len(diffs),
        "ci95_pct": [boot_means[250], boot_means[9750]],
    }


def _compare_cached_exit(
    start_date: str,
    end_date: str,
    *,
    challenger_trailing_ratio: float,
) -> dict[str, Any]:
    """在同一缓存推荐集上只改变 trailing ratio，调用唯一生产判定器。"""
    with SessionLocal() as db:
        recs = _load_persisted_recs(db, start_date, end_date)
        population = [rec for rec in recs if _is_production_recommendation(rec)]
    champion_records = _simulate_exit_for_records(
        population,
        stop_loss_pct=CHAMPION_STOP_LOSS_PCT,
        take_profit_pct=CHAMPION_TAKE_PROFIT_PCT,
        trailing_ratio=CHAMPION_TRAILING_RATIO,
    )
    challenger_records = _simulate_exit_for_records(
        population,
        stop_loss_pct=CHAMPION_STOP_LOSS_PCT,
        take_profit_pct=CHAMPION_TAKE_PROFIT_PCT,
        trailing_ratio=challenger_trailing_ratio,
    )
    champion_snapshot, champion_metrics = _portfolio_metrics(champion_records)
    challenger_snapshot, challenger_metrics = _portfolio_metrics(challenger_records)
    decision = evaluate_challenger(champion_snapshot, challenger_snapshot)
    return {
        "window": f"{start_date}~{end_date}",
        "population_n": len(population),
        "champion": champion_metrics,
        "challenger": challenger_metrics,
        "decision": decision.to_dict(),
        "bootstrap": _paired_cluster_bootstrap(champion_records, challenger_records),
    }


def _load_persisted_recs(db, start_date: str, end_date: str) -> list[Recommendation]:
    """读取已落库的 btrec_ 推荐，不重跑管线。

    推荐集只由入场侧（策略/聚合/score_line）决定，与 stop/tp/trailing 无关。
    参数扫描时管线只需跑一次，后续变体复用同一批推荐即可，省掉 80-160s/day 的重算。
    每个 (trade_date) 取最新 run，避免历史多次 run 叠加。
    """
    runs = db.execute(
        select(
            RecommendationRun.run_id,
            RecommendationRun.trade_date,
            RecommendationRun.started_at,
            RecommendationRun.params_json,
        )
        .where(
            RecommendationRun.run_mode == "backtest",
            RecommendationRun.trade_date >= start_date,
            RecommendationRun.trade_date <= end_date,
            select(Recommendation.recommendation_id)
            .where(
                Recommendation.run_id == RecommendationRun.run_id,
                Recommendation.recommendation_id.like("btrec_%"),
            )
            .exists(),
        )
        .order_by(RecommendationRun.trade_date.asc(), RecommendationRun.started_at.desc())
    ).all()
    latest_run_by_date: dict[str, str] = {}
    for row in runs:
        latest_run_by_date.setdefault(str(row.trade_date), str(row.run_id))
    if not latest_run_by_date:
        return []
    return list(
        db.scalars(
            select(Recommendation)
            .where(Recommendation.run_id.in_(list(latest_run_by_date.values())))
            .order_by(Recommendation.trade_date.asc(), Recommendation.code.asc())
        ).all()
    )


def _paired_bootstrap_diff(champ_records: list[dict], challenger_records: list[dict]) -> dict[str, Any]:
    """按 trade_date cluster 对齐同一批股票，固定种子 bootstrap 95% CI。"""
    champ_map = {(r["code"], r["trade_date"]): r["net_return"] for r in champ_records}
    chall_map = {(r["code"], r["trade_date"]): r["net_return"] for r in challenger_records}
    common = sorted(set(champ_map) & set(chall_map))
    if not common:
        return {"paired_n": 0, "n_dates": 0, "mean_diff_pct": None, "ci95_pct": [None, None]}
    by_date: dict[str, list[float]] = defaultdict(list)
    for key in common:
        by_date[key[1]].append(chall_map[key] - champ_map[key])
    clusters = list(by_date.values())
    diffs = [chall_map[key] - champ_map[key] for key in common]
    mean_diff = sum(diffs) / len(diffs)
    rng = random.Random(20260731)
    boot_means = []
    for _ in range(10000):
        sampled = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        flat = [value for cluster in sampled for value in cluster]
        boot_means.append(sum(flat) / len(flat))
    boot_means.sort()
    return {
        "paired_n": len(common),
        "n_dates": len(clusters),
        "mean_diff_pct": mean_diff,
        "ci95_pct": [boot_means[250], boot_means[9750]],
    }


def _run_cached_comparison(
    start_date: str,
    end_date: str,
    champion_params: dict[str, float],
    challenger_params: dict[str, float],
) -> dict[str, Any]:
    """在同一缓存推荐集上比较一个 exit 变量。"""
    with SessionLocal() as db:
        recs = _load_persisted_recs(db, start_date, end_date)
        population = [rec for rec in recs if _is_production_recommendation(rec)]
        champ_records = _simulate_exit_for_records(population, **champion_params)
        challenger_records = _simulate_exit_for_records(population, **challenger_params)
    champ_snapshot, champ_metrics = _portfolio_metrics(champ_records)
    chall_snapshot, chall_metrics = _portfolio_metrics(challenger_records)
    decision = evaluate_challenger(champ_snapshot, chall_snapshot)
    return {
        "window": f"{start_date}~{end_date}",
        "population_n": len(population),
        "champion": champ_metrics,
        "challenger": chall_metrics,
        "decision": decision.to_dict(),
        "paired_bootstrap": _paired_bootstrap_diff(champ_records, challenger_records),
    }


def run_path_a_backtest(
    start_date: str,
    end_date: str,
    *,
    stop_loss_pct: float = CHAMPION_STOP_LOSS_PCT,
    take_profit_pct: float = CHAMPION_TAKE_PROFIT_PCT,
    trailing_ratio: float = CHAMPION_TRAILING_RATIO,
    reuse_recs: bool = False,
) -> dict[str, Any]:
    """在 [start_date, end_date] 区间内运行 PATH-A 策略信号回测。

    1. 用 run_backtest 在每个交易日重跑推荐管线
    2. label_pending_outcomes 标注结果
    3. 后过滤（20cm-only + no-DS + structural_strong veto）
    4. 用指定 exit 参数模拟并计算指标
    """
    from app.services.backtest_service import run_backtest

    # Bootstrap factor registry (required by backtest pipeline)
    bootstrap_default_factor_registry()

    db = SessionLocal()
    try:
        dates = _trading_dates_in_range(db, start_date, end_date)
        if not dates:
            return {"status": "no_data", "message": f"区间 {start_date}~{end_date} 无交易日"}

        print(f"PATH-A backtest: {start_date} ~ {end_date}, {len(dates)} trade_days")
        print(f"Params: stop={stop_loss_pct}%, tp=+{take_profit_pct}%, trailing_ratio={trailing_ratio}")

        if reuse_recs:
            # 复用已落库推荐：推荐集与 exit 参数无关，管线不必重跑
            t0 = time.perf_counter()
            recs = _load_persisted_recs(db, start_date, end_date)
            elapsed = time.perf_counter() - t0
            if not recs:
                return {
                    "status": "empty",
                    "message": f"区间无已落库 btrec_ 推荐，请先不带 --reuse-recs 跑一次 {start_date}~{end_date}",
                }
            print(f"Reused persisted recs: {len(recs)} ({elapsed:.1f}s, pipeline skipped)")
        else:
            t0 = time.perf_counter()
            result = run_backtest(
                db,
                start_date,
                end_date,
                horizon_days=HORIZON_DAYS,
                score_line=SCORE_LINE,
                skill_id=None,
            )
            elapsed = time.perf_counter() - t0
            print(f"Pipeline elapsed: {elapsed:.1f}s ({elapsed/len(dates):.1f}s/day)")

            run_ids = set()
            for r in result.get("results", []):
                if r.get("status") == "ok" and r.get("run_id"):
                    run_ids.add(r["run_id"])

            if not run_ids:
                return {"status": "empty", "message": "无成功回测日", "result": result}

            recs = list(db.scalars(
                select(Recommendation)
                .where(Recommendation.run_id.in_(list(run_ids)))
            ).all())

        total_recs = len(recs)
        print(f"Total backtest recs: {total_recs}")

        # Post-filter to production universe
        prod_recs = [r for r in recs if _is_production_recommendation(r)]
        print(f"After production filter (20cm + no-DS + no-struct-strong): {len(prod_recs)} (dropped {total_recs - len(prod_recs)})")

        # Simulate exits
        records = _simulate_exit_for_records(
            prod_recs,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_ratio=trailing_ratio,
        )
        print(f"Executed trades: {len(records)}")

        if not records:
            return {
                "status": "empty",
                "message": "生产过滤后无可执行交易",
                "total_recs": total_recs,
                "prod_recs": len(prod_recs),
            }

        snapshot, details = _portfolio_metrics(records)

        # Exit reason breakdown
        exit_counts: dict[str, int] = defaultdict(int)
        for r in records:
            exit_counts[r["exit_reason"]] += 1
        details["exit_breakdown"] = dict(sorted(exit_counts.items()))

        # By year
        by_year: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            by_year[r["trade_date"][:4]].append(r)
        year_metrics = {}
        for yr, yr_records in sorted(by_year.items()):
            if len(yr_records) >= 3:
                _, yd = _portfolio_metrics(yr_records)
                year_metrics[yr] = yd

        return {
            "status": "completed",
            "window": f"{start_date}~{end_date}",
            "trade_days_scanned": len(dates),
            "total_recs": total_recs,
            "prod_recs": len(prod_recs),
            "params": {
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "trailing_ratio": trailing_ratio,
                "horizon_days": HORIZON_DAYS,
                "score_line": SCORE_LINE,
            },
            "metrics": details,
            "by_year": year_metrics,
            "elapsed_s": round(elapsed, 1),
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="PATH-A strategy signal backtest")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--stop-loss", type=float, default=CHAMPION_STOP_LOSS_PCT,
                        help=f"止损 %（default={CHAMPION_STOP_LOSS_PCT}）")
    parser.add_argument("--take-profit", type=float, default=CHAMPION_TAKE_PROFIT_PCT,
                        help=f"止盈 %（default={CHAMPION_TAKE_PROFIT_PCT}）")
    parser.add_argument("--trailing-ratio", type=float, default=CHAMPION_TRAILING_RATIO,
                        help=f"Trailing profit lock ratio（default={CHAMPION_TRAILING_RATIO}）")
    parser.add_argument("--batch", action="store_true",
                        help="分月运行（推荐用于跨年大窗口）")
    parser.add_argument("--reuse-recs", action="store_true",
                        help="复用区间内最新 btrec_ 推荐，仅重算出场（参数扫描应使用）")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 JSON 路径（default=stdout）")
    args = parser.parse_args()

    if args.batch:
        # Split into months and run sequentially
        from calendar import monthrange
        start_year, start_month = map(int, args.start.split("-")[:2])
        end_year, end_month = map(int, args.end.split("-")[:2])

        all_results: list[dict] = []
        y, m = start_year, start_month
        while (y < end_year) or (y == end_year and m <= end_month):
            _, last_day = monthrange(y, m)
            month_start = f"{y}-{m:02d}-01"
            month_end = f"{y}-{m:02d}-{last_day}"
            if month_start < args.start:
                month_start = args.start
            if month_end > args.end:
                month_end = args.end
            print(f"\n{'='*60}")
            print(f"BATCH: {month_start} ~ {month_end}")
            print(f"{'='*60}")
            try:
                result = run_path_a_backtest(
                    month_start, month_end,
                    stop_loss_pct=args.stop_loss,
                    take_profit_pct=args.take_profit,
                    trailing_ratio=args.trailing_ratio,
                    reuse_recs=args.reuse_recs,
                )
                all_results.append(result)
            except Exception as exc:
                print(f"ERROR in {month_start}~{month_end}: {exc}")
                all_results.append({"status": "error", "window": f"{month_start}~{month_end}", "error": str(exc)})
            y, m = (y, m + 1) if m < 12 else (y + 1, 1)

        # Aggregate all records across batches
        all_records: list[dict] = []
        total_trade_days = set()
        for r in all_results:
            if r.get("status") == "completed":
                # Re-collect records from each batch
                # (simpler: just aggregate the per-batch metrics)
                m = r.get("metrics", {})
                if m.get("n", 0) > 0:
                    total_trade_days.update(range(m.get("trade_days", 0)))

        combined = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": {
                "stop_loss_pct": args.stop_loss,
                "take_profit_pct": args.take_profit,
                "trailing_ratio": args.trailing_ratio,
            },
            "batches": all_results,
        }

        # Print summary
        valid_batches = [r for r in all_results if r.get("status") == "completed" and r.get("metrics", {}).get("n", 0) > 0]
        if valid_batches:
            print(f"\n{'='*60}")
            print(f"SUMMARY: {len(valid_batches)} batches with trades")
            for b in valid_batches:
                m = b["metrics"]
                print(f"  {b['window']}: n={m['n']} WR={m['win_rate_pct']:.1f}% avg_net={m['average_net_return_pct']:.2f}% PF={m['profit_factor']:.2f} MDD={m['max_drawdown_pct']:.2f}%")
    else:
        result = run_path_a_backtest(
            args.start, args.end,
            stop_loss_pct=args.stop_loss,
            take_profit_pct=args.take_profit,
            trailing_ratio=args.trailing_ratio,
            reuse_recs=args.reuse_recs,
        )
        combined = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **result,
        }

    json_str = json.dumps(combined, ensure_ascii=False, indent=2, default=str)
    if args.output:
        output_path = Path(args.output) if args.output.startswith("/") else OUTPUT_DIR / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_str)
        print(f"\nSaved to: {output_path}")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
