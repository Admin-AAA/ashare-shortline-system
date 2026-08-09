#!/usr/bin/env python3
"""只读生成当前策略半年回测归因矩阵。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import and_, select

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import BacktestCandidate, BacktestInputArtifact, BacktestRunV2, BacktestTrade  # noqa: E402
from app.services.backtest.strategy_profile import (  # noqa: E402
    STRATEGY_PROFILE_EXPERIMENT_ID,
    StrategyBacktestProfiles,
)


def regime_name(value: Any) -> str:
    text = str(value or "未知")
    if "强势" in text:
        return "强势"
    if "弱势" in text:
        return "弱势"
    if "震荡" in text:
        return "震荡"
    return text


def score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score < 70:
        return "<70"
    if score < 80:
        return "70-79"
    if score < 85:
        return "80-84"
    return "85+"


def first_stock(payload: bytes) -> dict[str, Any] | None:
    marker = payload.find(b'"stocks":[')
    if marker < 0:
        return None
    start = payload.find(b"{", marker + len(b'"stocks":['))
    if start < 0:
        return None
    # 首个对象远小于 100KB；只截取对象前缀，避免解码整份 12MB 快照。
    text = payload[start : start + 100_000].decode("utf-8", errors="ignore")
    value, _ = json.JSONDecoder().raw_decode(text)
    return value if isinstance(value, dict) else None


def mean(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows) if rows else 0.0


def stat_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "—"
    return f"{mean(rows, 'net'):+.3f}% (n={len(rows)})"


def main() -> int:
    db = SessionLocal()
    try:
        service = StrategyBacktestProfiles(db)
        targets = {target.strategy_id: target for target in service.current_targets()}
        runs: dict[str, BacktestRunV2] = {}
        for strategy_id, target in targets.items():
            run = db.scalar(
                select(BacktestRunV2)
                .where(
                    BacktestRunV2.experiment_id == STRATEGY_PROFILE_EXPERIMENT_ID,
                    BacktestRunV2.strategy_id == strategy_id,
                    BacktestRunV2.strategy_version == target.strategy_version,
                    BacktestRunV2.date_from == target.date_from,
                    BacktestRunV2.date_to == target.date_to,
                    BacktestRunV2.status == "completed",
                )
                .order_by(BacktestRunV2.finished_at.desc(), BacktestRunV2.created_at.desc())
            )
            if run is not None:
                runs[strategy_id] = run

        dates_by_snapshot: dict[str, set[str]] = defaultdict(set)
        for run in runs.values():
            dates = db.scalars(
                select(BacktestTrade.signal_date)
                .where(BacktestTrade.backtest_id == run.backtest_id)
                .distinct()
            ).all()
            dates_by_snapshot[run.snapshot_id].update(str(value) for value in dates)

        regimes: dict[str, dict[str, dict[str, Any]]] = {}
        for snapshot_id, dates in dates_by_snapshot.items():
            by_date: dict[str, dict[str, Any]] = {}
            rows = db.execute(
                select(BacktestInputArtifact.signal_date, BacktestInputArtifact.payload).where(
                    BacktestInputArtifact.snapshot_id == snapshot_id,
                    BacktestInputArtifact.artifact_scope == "signal_input",
                    BacktestInputArtifact.signal_date.in_(sorted(dates)),
                )
            ).yield_per(20)
            for signal_date, payload in rows:
                stock = first_stock(bytes(payload))
                if stock is not None:
                    by_date[str(signal_date)] = stock
            regimes[snapshot_id] = by_date

        observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for strategy_id, run in sorted(runs.items()):
            stmt = (
                select(BacktestTrade.trade_json, BacktestCandidate.signal_json)
                .outerjoin(
                    BacktestCandidate,
                    and_(
                        BacktestCandidate.backtest_id == BacktestTrade.backtest_id,
                        BacktestCandidate.signal_date == BacktestTrade.signal_date,
                        BacktestCandidate.code == BacktestTrade.code,
                    ),
                )
                .where(BacktestTrade.backtest_id == run.backtest_id)
            )
            market_by_date = regimes.get(run.snapshot_id, {})
            for trade_json, signal_json in db.execute(stmt).yield_per(1000):
                trade = json.loads(str(trade_json))
                if str(trade.get("outcome_label")) == "not_executed" or trade.get("entry_date") is None:
                    continue
                signal = json.loads(str(signal_json)) if signal_json else {}
                market = market_by_date.get(str(trade.get("signal_date")), {})
                phase = str(signal.get("phase") or "未标注")
                setup = str(signal.get("setup_match") or signal.get("entry_rule") or "未标注")
                if phase in {"未分类", "未标注", ""} and setup not in {"未分类", "未标注", ""}:
                    phase = setup
                observations[strategy_id].append(
                    {
                        "regime": regime_name(market.get("market_regime")),
                        "score": float(signal["score"]) if signal.get("score") is not None else None,
                        "bucket": score_bucket(float(signal["score"]) if signal.get("score") is not None else None),
                        "phase": phase,
                        "gross": float(trade.get("gross_return_pct") or 0.0),
                        "net": float(trade.get("net_return_pct") or 0.0),
                        "exit": str(trade.get("exit_reason") or "未标注"),
                    }
                )

        print("=== 归因矩阵口径 ===")
        print("窗口=2026-01-31~2026-07-29；仅统计已执行且已结算交易。gross-net 为回测记录的成本拖累。")
        print("情绪周期=信号日全市场 market_regime；结构性强势并入强势。")

        print("\n=== A. 信号质量 × 交易成本 ===")
        print("strategy | n | grossWR | netWR | grossAvg | netAvg | costDrag | gross+→net- | officialPL | totalRet | MDD")
        for strategy_id, rows in sorted(observations.items()):
            n = len(rows)
            gross_wr = sum(row["gross"] > 0 for row in rows) * 100 / n
            net_wr = sum(row["net"] > 0 for row in rows) * 100 / n
            gross_avg = mean(rows, "gross")
            net_avg = mean(rows, "net")
            flips = sum(row["gross"] > 0 and row["net"] <= 0 for row in rows)
            profile = service.read(targets[strategy_id]) or {}
            print(
                f"{strategy_id} | {n} | {gross_wr:.1f}% | {net_wr:.1f}% | {gross_avg:+.3f}% | {net_avg:+.3f}% | "
                f"{gross_avg-net_avg:.3f}pp | {flips} ({flips*100/n:.1f}%) | "
                f"{profile.get('profit_loss_ratio', '—')} | {profile.get('total_return_pct', '—')}% | {profile.get('max_drawdown_pct', '—')}%"
            )

        print("\n=== B. 情绪周期矩阵：净均收益（n） ===")
        regimes_order = ["强势", "震荡", "弱势", "未知"]
        print("strategy | " + " | ".join(regimes_order) + " | 最佳有效周期 | 最差有效周期")
        for strategy_id, rows in sorted(observations.items()):
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[row["regime"]].append(row)
            cells = []
            for regime in regimes_order:
                values = grouped.get(regime, [])
                cells.append(stat_text(values))
            eligible = [(key, values) for key, values in grouped.items() if len(values) >= 20]
            best = max(eligible, key=lambda item: mean(item[1], "net")) if eligible else None
            worst = min(eligible, key=lambda item: mean(item[1], "net")) if eligible else None
            best_text = "—" if best is None else f"{best[0]} {mean(best[1], 'net'):+.3f}%/n={len(best[1])}"
            worst_text = "—" if worst is None else f"{worst[0]} {mean(worst[1], 'net'):+.3f}%/n={len(worst[1])}"
            print(f"{strategy_id} | " + " | ".join(cells) + f" | {best_text} | {worst_text}")

        print("\n=== C. 信号质量矩阵：分数桶净均收益（n） ===")
        buckets = ["<70", "70-79", "80-84", "85+", "unknown"]
        print("strategy | " + " | ".join(buckets) + " | 高分是否单调变好")
        for strategy_id, rows in sorted(observations.items()):
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[row["bucket"]].append(row)
            cells = [stat_text(grouped.get(key, [])) for key in buckets]
            bucket_order = {"<70": 0, "70-79": 1, "80-84": 2, "85+": 3}
            eligible = sorted(
                (
                    (key, mean(values, "net"))
                    for key, values in grouped.items()
                    if key != "unknown" and len(values) >= 20
                ),
                key=lambda item: bucket_order[item[0]],
            )
            monotonic = "样本不足" if len(eligible) < 2 else ("是" if all(eligible[i][1] <= eligible[i + 1][1] for i in range(len(eligible) - 1)) else "否")
            print(f"{strategy_id} | " + " | ".join(cells) + f" | {monotonic}")

        print("\n=== D. 买卖点矩阵：入场阶段 / 退出原因 ===")
        print("strategy | 主导入场阶段 | 最佳入场阶段 | 最差入场阶段 | 主导退出原因 | 最伤收益退出原因")
        for strategy_id, rows in sorted(observations.items()):
            phase_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            exit_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                phase_groups[row["phase"]].append(row)
                exit_groups[row["exit"]].append(row)
            phase_values = [(key, values) for key, values in phase_groups.items() if len(values) >= 10]
            phase_values = phase_values or list(phase_groups.items())
            best_phase = max(phase_values, key=lambda item: mean(item[1], "net"))
            worst_phase = min(phase_values, key=lambda item: mean(item[1], "net"))
            dominant_phase = max(phase_values, key=lambda item: len(item[1]))
            exit_values = list(exit_groups.items())
            dominant_exit = max(exit_values, key=lambda item: len(item[1]))
            worst_exit = min(exit_values, key=lambda item: mean(item[1], "net"))
            def label(item: tuple[str, list[dict[str, Any]]]) -> str:
                return f"{item[0]} (n={len(item[1])}, {mean(item[1], 'net'):+.3f}%)"
            print(f"{strategy_id} | {label(dominant_phase)} | {label(best_phase)} | {label(worst_phase)} | {label(dominant_exit)} | {label(worst_exit)}")

        print("\n=== E. 全策略退出原因汇总 ===")
        exit_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rows in observations.values():
            for row in rows:
                exit_groups[row["exit"]].append(row)
        total = sum(len(values) for values in exit_groups.values())
        for key, values in sorted(exit_groups.items(), key=lambda item: -len(item[1])):
            loss_sum = sum(min(0.0, row["net"]) for row in values)
            print(f"{key}: n={len(values)}, 占比={len(values)*100/total:.1f}%, 净均={mean(values, 'net'):+.3f}%, 净损失合计={loss_sum:+.1f}%")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
