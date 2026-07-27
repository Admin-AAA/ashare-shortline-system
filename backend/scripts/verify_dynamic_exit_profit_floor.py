"""利润地板动态退出的开发/验证分离反事实实验。

候选只允许 25% 与 50% 两档利润锁定比例。前 2/3 唯一交易日用于选择候选，
后 1/3 唯一交易日只做一次验证；同一交易日不得跨段。
"""
from __future__ import annotations

import functools
import json
import sys
from math import ceil
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.services.outcome_simulation import (  # noqa: E402
    _resolve_exit_causal,
    _resolve_exit_legacy_noncausal,
    _simulate_outcome,
)
from scripts.backtest_cost_aware import BacktestRow, compute_metrics  # noqa: E402
from scripts.verify_dynamic_exit_policy import HORIZON_DAYS, load_bars  # noqa: E402


CANDIDATE_RATIOS = {
    "profit_floor_25": 0.25,
    "profit_floor_50": 0.50,
}


def split_development_validation(samples: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    """按唯一交易日前 2/3、后 1/3 切分，同日样本保持在同一段。"""
    order = sorted(
        range(len(samples)),
        key=lambda i: (samples[i]["trade_date"], samples[i]["recommendation_id"]),
    )
    unique_dates = sorted({samples[i]["trade_date"] for i in order})
    if len(unique_dates) <= 1:
        return order, []
    development_date_count = min(len(unique_dates) - 1, ceil(len(unique_dates) * 2 / 3))
    validation_start = unique_dates[development_date_count]
    development = [i for i in order if samples[i]["trade_date"] < validation_start]
    validation = [i for i in order if samples[i]["trade_date"] >= validation_start]
    return development, validation


def passes_production_gate(fixed: dict[str, float], candidate: dict[str, float]) -> bool:
    """候选必须增厚风险调整后收益，并降低每笔交易承担的下行损失。"""
    return all(
        (
            candidate["avg_net"] >= fixed["avg_net"] - 1e-9,
            candidate["sharpe"] >= fixed["sharpe"] - 1e-9,
            candidate["profit_factor"] >= fixed["profit_factor"] - 1e-9,
            fixed["win_rate"] - candidate["win_rate"] <= 2.0 + 1e-9,
            candidate["max_loss"] >= fixed["max_loss"] - 0.3 - 1e-9,
            candidate["expected_downside"] <= fixed["expected_downside"] + 1e-9,
        )
    )


def select_development_candidate(
    fixed: dict[str, float],
    candidates: dict[str, dict[str, float]],
) -> str | None:
    """只在开发段选择；未全过门槛时不得进入验证段候选。"""
    eligible = [
        (name, stats)
        for name, stats in candidates.items()
        if passes_production_gate(fixed, stats)
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item[1]["avg_net"], item[1]["profit_factor"]))[0]


def _resolver(*, dynamic: bool, ratio: float = 0.25):
    return functools.partial(
        _resolve_exit_causal,
        dynamic_trailing=dynamic,
        trailing_policy="profit_floor",
        profit_lock_ratio=ratio,
    )


def _stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        BacktestRow(
            recommendation_id=s["recommendation_id"],
            trade_date=s["trade_date"],
            code=s["code"],
            executed_return=s["executed_return"],
            outcome_label="success" if s["executed_return"] > 0.07 else "failed",
            exit_reason=s["exit_reason"],
            holding_days=int(s.get("holding_days", 0) or 0),
            final_score=0.0,
        )
        for s in samples
    ]
    metrics = compute_metrics(rows)
    wins = [row.net_return for row in rows if row.net_return > 0]
    losses = [row.net_return for row in rows if row.net_return <= 0]
    total_gain = sum(wins)
    total_loss_abs = sum(abs(value) for value in losses)
    return {
        "n": metrics["n"],
        "win_rate": round(metrics["win_rate"] * 100, 2),
        "avg_net": metrics["avg_net_return"],
        "avg_win": metrics["avg_win"],
        "avg_loss_abs": metrics["avg_loss_abs"],
        "pl_ratio": metrics["pl_ratio"],
        "profit_factor": round(total_gain / total_loss_abs, 4) if total_loss_abs > 0 else float("inf"),
        "expected_downside": round(total_loss_abs / len(rows), 4) if rows else 0.0,
        "sharpe": metrics["sharpe_per_trade"],
        "max_loss": metrics["max_loss_net"],
        "reason_dist": metrics["exit_distribution"],
    }


def _print_comparison(label: str, fixed: dict[str, Any], candidate: dict[str, Any]) -> None:
    print(f"\n[{label}]")
    for key in (
        "n",
        "win_rate",
        "avg_net",
        "avg_win",
        "avg_loss_abs",
        "pl_ratio",
        "profit_factor",
        "expected_downside",
        "sharpe",
        "max_loss",
    ):
        fixed_value = fixed[key]
        candidate_value = candidate[key]
        delta = candidate_value - fixed_value
        print(f"  {key:<14} fixed={fixed_value:>9.4f}  candidate={candidate_value:>9.4f}  delta={delta:>+9.4f}")
    print(f"  fixed exits    : {fixed['reason_dist']}")
    print(f"  candidate exits: {candidate['reason_dist']}")


def _load_samples() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]], int]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT r.recommendation_id, r.code, r.trade_date, r.recommend_price, "
                "r.recommended_at, r.feature_snapshot_json "
                "FROM recommendation_outcomes o "
                "JOIN recommendations r ON o.recommendation_id = r.recommendation_id "
                "JOIN recommendation_runs run ON r.run_id = run.run_id "
                "WHERE o.outcome_label IN ('success', 'failed') "
                "AND run.run_mode = 'formal' AND run.status = 'completed' "
                "AND r.feature_snapshot_json IS NOT NULL AND r.code IS NOT NULL "
                "ORDER BY r.trade_date ASC, r.recommendation_id ASC"
            )
        ).all()

        fixed_samples: list[dict[str, Any]] = []
        candidate_samples = {name: [] for name in CANDIDATE_RATIOS}
        production_samples: list[dict[str, Any]] = []
        skipped = 0

        for row in rows:
            rec_id, code, trade_date, recommend_price, recommended_at, snapshot_json = row
            rec = type(
                "Rec",
                (),
                {
                    "recommendation_id": rec_id,
                    "code": code,
                    "trade_date": str(trade_date),
                    "recommend_price": float(recommend_price) if recommend_price else 0.0,
                    "recommended_at": recommended_at,
                    "feature_snapshot_json": snapshot_json,
                },
            )()
            bars, dates = load_bars(db, str(code), str(trade_date))
            if len(bars) < 2:
                skipped += 1
                continue

            resolvers = {
                "fixed": _resolver(dynamic=False),
                "production": _resolve_exit_legacy_noncausal,
                **{
                    name: _resolver(dynamic=True, ratio=ratio)
                    for name, ratio in CANDIDATE_RATIOS.items()
                },
            }
            simulations: dict[str, dict[str, Any] | None] = {}
            for name, resolver in resolvers.items():
                try:
                    simulations[name] = _simulate_outcome(
                        rec,
                        bars,
                        dates,
                        horizon_days=HORIZON_DAYS,
                        exit_resolver=resolver,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"模拟异常 policy={name} recommendation_id={rec_id} "
                        f"code={code} trade_date={trade_date}: {exc!r}"
                    ) from exc

            available = {name: sim is not None and sim.get("entry_triggered") for name, sim in simulations.items()}
            if not any(available.values()):
                skipped += 1
                continue
            if not all(available.values()):
                raise RuntimeError(
                    f"策略样本不一致 recommendation_id={rec_id}: {available}"
                )

            base = {
                "recommendation_id": str(rec_id),
                "trade_date": str(trade_date),
                "code": str(code),
            }
            for name, sim in simulations.items():
                assert sim is not None
                sample = {
                    **base,
                    "executed_return": float(sim["executed_return"]),
                    "exit_reason": str(sim["exit_reason"]),
                    "holding_days": int(sim.get("holding_days", 0) or 0),
                }
                if name == "fixed":
                    fixed_samples.append(sample)
                elif name == "production":
                    production_samples.append(sample)
                else:
                    candidate_samples[name].append(sample)
    finally:
        db.close()

    return fixed_samples, candidate_samples, production_samples, skipped


def _cut(samples: list[dict[str, Any]], indices: list[int]) -> list[dict[str, Any]]:
    return [samples[i] for i in indices]


def main() -> int:
    fixed_samples, candidate_samples, production_samples, skipped = _load_samples()
    development_idx, validation_idx = split_development_validation(fixed_samples)
    development_dates = sorted({fixed_samples[i]["trade_date"] for i in development_idx})
    validation_dates = sorted({fixed_samples[i]["trade_date"] for i in validation_idx})

    if not development_dates or not validation_dates:
        print(
            f"样本: total={len(fixed_samples)}, skipped={skipped}; "
            "唯一交易日不足（≤1 个），无法划分开发/验证段，退出（不产生结论）。"
        )
        return 1

    print(
        f"样本: total={len(fixed_samples)}, skipped={skipped}, "
        f"development={len(development_idx)} ({development_dates[0]}..{development_dates[-1]}), "
        f"validation={len(validation_idx)} ({validation_dates[0]}..{validation_dates[-1]})"
    )
    print(f"冻结候选: {CANDIDATE_RATIOS}")

    fixed_development = _stats(_cut(fixed_samples, development_idx))
    candidate_development = {
        name: _stats(_cut(samples, development_idx))
        for name, samples in candidate_samples.items()
    }
    for name, stats in candidate_development.items():
        _print_comparison(f"开发段 {name}", fixed_development, stats)
        print(f"  development_gate={'PASS' if passes_production_gate(fixed_development, stats) else 'FAIL'}")

    selected = select_development_candidate(fixed_development, candidate_development)
    if selected is None:
        print("\n结论: 开发段无候选通过全部门槛；验证段不用于改参，不切生产。")
        return 1

    print(f"\n开发段选中: {selected} (ratio={CANDIDATE_RATIOS[selected]:.2f})")
    fixed_validation = _stats(_cut(fixed_samples, validation_idx))
    selected_validation = _stats(_cut(candidate_samples[selected], validation_idx))
    fixed_full = _stats(fixed_samples)
    selected_full = _stats(candidate_samples[selected])
    production_full = _stats(production_samples)

    _print_comparison("冻结验证段", fixed_validation, selected_validation)
    validation_pass = passes_production_gate(fixed_validation, selected_validation)
    print(f"  validation_gate={'PASS' if validation_pass else 'FAIL'}")
    _print_comparison("全样本", fixed_full, selected_full)
    full_pass = passes_production_gate(fixed_full, selected_full)
    print(f"  full_gate={'PASS' if full_pass else 'FAIL'}")

    print("\n[当前生产事后混合，仅作有偏上界参考]")
    print(json.dumps(production_full, ensure_ascii=False, indent=2))

    all_pass = validation_pass and full_pass
    print(
        "\n结论: "
        + (
            f"{selected} 达到因果生产切换门槛，可以进入生产切换与回归保护。"
            if all_pass
            else f"{selected} 未通过冻结验证，不切生产。"
        )
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
