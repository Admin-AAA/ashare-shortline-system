"""动态退出状态机 MVP 反事实验证脚本（正确口径）。

对同一批 formal/completed 样本分别运行：
  - fixed_causal：_simulate_outcome + exit_resolver=_resolve_exit_causal(dynamic_trailing=False)
  - dynamic_causal：_simulate_outcome + exit_resolver=_resolve_exit_causal(dynamic_trailing=True)

入场解析、holding_bars 起点、executed_entry_price（含买入滑点）、除权重算全部复用生产
_simulate_outcome 时序——盘后推荐排除信号日，盘中推荐不重复信号日。移动保护使用含
滑点的 executed_entry_price（生产 _simulate_outcome 把它作为 entry_price 传给 exit resolver）。

样本限定 recommendation_runs.run_mode='formal' AND status='completed'。
指标复用 scripts.backtest_cost_aware.compute_metrics：输入 executed_return（含滑点、未扣
成本），内部 net_return = executed_return - 0.07% 只扣一次成本，胜率/平均净收益/盈亏比
（平均盈利/平均亏损绝对值）均基于该净收益，不重复扣成本。

时间前后段按唯一 trade_date 的日期边界切分——同一交易日不拆到两段；同 trade_date 内
按 recommendation_id 升序稳定排序。脚本只读，不写数据库；任何模拟异常直接抛出并输出
recommendation_id。数据库 Session 用 try/finally 关闭。

用法：cd backend && .venv/bin/python scripts/verify_dynamic_exit_policy.py
"""
from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import DailyKbar  # noqa: E402
from app.services.outcome_simulation import (  # noqa: E402
    ENTRY_SLIPPAGE_BPS,
    EXIT_SLIPPAGE_BPS,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    _resolve_exit_causal,
    _simulate_outcome,
)
from scripts.backtest_cost_aware import BacktestRow, compute_metrics  # noqa: E402

HORIZON_DAYS = 5
LOAD_BARS_DAYS = 16  # 信号日 + horizon 5 + 入场候选窗口 2 + 缓冲


def load_bars(db, code: str, signal_date: str) -> tuple[list[DailyKbar], list[str]]:
    """取 signal_date 起的日K（含信号日），返回 (bars, dates) 供 _simulate_outcome。"""
    result = db.execute(
        text(
            "SELECT trade_date, open, high, low, close, pct_chg, volume, amount "
            "FROM daily_kbars WHERE code = :code AND trade_date >= :signal_date "
            "ORDER BY trade_date ASC LIMIT :limit"
        ),
        {"code": code, "signal_date": signal_date, "limit": LOAD_BARS_DAYS},
    )
    bars: list[DailyKbar] = []
    for row in result:
        bars.append(
            DailyKbar(
                code=code,
                trade_date=str(row[0]),
                open=float(row[1] or 0),
                high=float(row[2] or 0),
                low=float(row[3] or 0),
                close=float(row[4] or 0),
                pct_chg=float(row[5]) if row[5] is not None else 0.0,
                volume=float(row[6]) if row[6] is not None else 1000000.0,
                amount=float(row[7]) if row[7] is not None else 10000000.0,
            )
        )
    return bars, [str(b.trade_date) for b in bars]


def make_resolver(dynamic_trailing: bool):
    """返回 _simulate_outcome 的 exit_resolver，绑定 dynamic_trailing。"""
    return functools.partial(_resolve_exit_causal, dynamic_trailing=dynamic_trailing)


def _stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """用 backtest_cost_aware.compute_metrics 算胜率/平均净收益/盈亏比/最大单笔亏损。

    BacktestRow.executed_return 含滑点未扣成本；net_return property 内部扣一次 0.07%。
    compute_metrics 的 win_rate/avg_net_return/pl_ratio/max_loss_net 全基于 net_return，
    不重复扣成本。
    """
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
    return {
        "n": metrics["n"],
        "win_rate": round(metrics["win_rate"] * 100, 2),
        "avg_net": metrics["avg_net_return"],
        "pl_ratio": metrics["pl_ratio"],
        "max_loss": metrics["max_loss_net"],
        "reason_dist": metrics["exit_distribution"],
    }


def _print_stats(label: str, fixed: dict, dyn: dict) -> None:
    fr = fixed["pl_ratio"] if fixed["pl_ratio"] != float("inf") else 999.0
    dr = dyn["pl_ratio"] if dyn["pl_ratio"] != float("inf") else 999.0
    print(f"\n[{label}]")
    print(f"  {'metric':<14}{'fixed_causal':>14}{'dynamic_causal':>16}{'delta':>10}")
    print(f"  {'-'*52}")
    print(f"  {'n':<14}{fixed['n']:>14}{dyn['n']:>16}{dyn['n']-fixed['n']:>+10}")
    print(f"  {'win_rate%':<14}{fixed['win_rate']:>14.2f}{dyn['win_rate']:>16.2f}{dyn['win_rate']-fixed['win_rate']:>+10.2f}")
    print(f"  {'avg_net%':<14}{fixed['avg_net']:>14.3f}{dyn['avg_net']:>16.3f}{dyn['avg_net']-fixed['avg_net']:>+10.3f}")
    print(f"  {'pl_ratio':<14}{fr:>14.3f}{dr:>16.3f}{dr-fr:>+10.3f}")
    print(f"  {'max_loss%':<14}{fixed['max_loss']:>14.3f}{dyn['max_loss']:>16.3f}{dyn['max_loss']-fixed['max_loss']:>+10.3f}")
    print(f"  exit_reason_dist (fixed) : {fixed['reason_dist']}")
    print(f"  exit_reason_dist (dynamic): {dyn['reason_dist']}")


def _evaluate_thresholds(full, first, second) -> dict[str, bool]:
    f_fixed, f_dyn = full
    checks = {
        "avg_net_dynamic_>=_fixed": f_dyn["avg_net"] >= f_fixed["avg_net"] - 1e-9,
        "pl_ratio_dynamic_>=_fixed": (
            f_dyn["pl_ratio"] != float("inf")
            and (f_fixed["pl_ratio"] == float("inf") or f_dyn["pl_ratio"] >= f_fixed["pl_ratio"] - 1e-9)
        ),
        "win_rate_drop_<=_2pp": f_fixed["win_rate"] - f_dyn["win_rate"] <= 2.0 + 1e-9,
        "max_loss_not_worse_>_0.3pp": f_dyn["max_loss"] >= f_fixed["max_loss"] - 0.3 - 1e-9,
    }
    f_first_delta = first[1]["avg_net"] - first[0]["avg_net"]
    f_second_delta = second[1]["avg_net"] - second[0]["avg_net"]
    checks["no_half_collapses_other_half_booms"] = not (
        (f_first_delta > 1.0 and f_second_delta < -1.0)
        or (f_second_delta > 1.0 and f_first_delta < -1.0)
    )
    return checks


def _split_by_date(samples: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    """按唯一 trade_date 的日期边界切前后半段——同一 trade_date 不拆到两段。

    样本先按 (trade_date, recommendation_id) 稳定排序；收集唯一 trade_date 升序，
    按日期数中点切，前半段日期集合与后半段日期集合不相交。
    """
    order = sorted(
        range(len(samples)),
        key=lambda i: (samples[i]["trade_date"], samples[i]["recommendation_id"]),
    )
    unique_dates = sorted({samples[i]["trade_date"] for i in order})
    mid_date_idx = len(unique_dates) // 2
    # 唯一日期数为 1 时全部归前半，后半为空（极小样本保护）
    if mid_date_idx == 0:
        return order, []
    split_date = unique_dates[mid_date_idx]
    first_idx = [i for i in order if samples[i]["trade_date"] < split_date]
    second_idx = [i for i in order if samples[i]["trade_date"] >= split_date]
    return first_idx, second_idx


def main() -> int:
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
                "AND r.feature_snapshot_json IS NOT NULL "
                "AND r.code IS NOT NULL "
                "ORDER BY r.trade_date ASC"
            )
        ).all()

        fixed_samples: list[dict[str, Any]] = []
        dyn_samples: list[dict[str, Any]] = []
        skipped = 0
        for row in rows:
            rec_id, code, trade_date, recommend_price, recommended_at, snapshot_json = row
            snapshot = json.loads(snapshot_json) if snapshot_json else {}
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
            # 异常必须抛出（禁止静默剔除）；同时跑 fixed / dynamic 两个 resolver
            try:
                sim_fixed = _simulate_outcome(
                    rec, bars, dates, horizon_days=HORIZON_DAYS,
                    exit_resolver=make_resolver(False),
                )
                sim_dyn = _simulate_outcome(
                    rec, bars, dates, horizon_days=HORIZON_DAYS,
                    exit_resolver=make_resolver(True),
                )
            except Exception as exc:
                raise RuntimeError(
                    f"模拟异常 recommendation_id={rec_id} code={code} trade_date={trade_date}: {exc!r}"
                ) from exc
            # 未触发 / 跳空高开 / 不可成交 → 两套都 None 才剔除；只一套 None 视为异常
            if sim_fixed is None and sim_dyn is None:
                skipped += 1
                continue
            if sim_fixed is None or sim_dyn is None:
                raise RuntimeError(
                    f"两套 resolver 结果不一致 recommendation_id={rec_id} "
                    f"fixed={sim_fixed is not None} dynamic={sim_dyn is not None}"
                )
            if not sim_fixed.get("entry_triggered") or not sim_dyn.get("entry_triggered"):
                skipped += 1
                continue
            rid = str(rec_id)
            td = str(trade_date)
            cd = str(code)
            fixed_samples.append({
                "recommendation_id": rid,
                "trade_date": td,
                "code": cd,
                "executed_return": float(sim_fixed["executed_return"]),
                "exit_reason": sim_fixed["exit_reason"],
                "holding_days": sim_fixed.get("holding_days", 0),
            })
            dyn_samples.append({
                "recommendation_id": rid,
                "trade_date": td,
                "code": cd,
                "executed_return": float(sim_dyn["executed_return"]),
                "exit_reason": sim_dyn["exit_reason"],
                "holding_days": sim_dyn.get("holding_days", 0),
            })
    finally:
        db.close()

    print(f"参数: activate={TRAILING_STOP_ACTIVATE_PCT}, callback={TRAILING_STOP_CALLBACK_PCT}, "
          f"horizon={HORIZON_DAYS}")
    print(f"成本口径: 买入滑点{ENTRY_SLIPPAGE_BPS}bps / 卖出滑点{EXIT_SLIPPAGE_BPS}bps + "
          f"统一交易成本 0.07%/笔（佣金万2双边+印花税0.05%卖出，由 backtest_cost_aware.BacktestRow.net_return 单次扣除）")
    print(f"样本: formal/completed 全集 {len(rows)} 笔，有效对比 {len(fixed_samples)} 笔，"
          f"剔除 {skipped} 笔（未触发/跳空高开/不可成交/数据不足）")

    first_idx, second_idx = _split_by_date(fixed_samples)
    full = (_stats(fixed_samples), _stats(dyn_samples))
    first = (
        _stats([fixed_samples[i] for i in first_idx]),
        _stats([dyn_samples[i] for i in first_idx]),
    )
    second = (
        _stats([fixed_samples[i] for i in second_idx]),
        _stats([dyn_samples[i] for i in second_idx]),
    )

    _print_stats("全样本", *full)
    _print_stats(f"时间前半段（{len(first_idx)} 笔）", *first)
    _print_stats(f"时间后半段（{len(second_idx)} 笔）", *second)

    checks = _evaluate_thresholds(full, first, second)
    print("\n生产切换门槛:")
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    all_pass = all(checks.values())
    print(
        f"\n结论: {'达到生产切换门槛' if all_pass else '未达门槛——保留实验函数与报告，不替换生产 _resolve_exit'}"
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
