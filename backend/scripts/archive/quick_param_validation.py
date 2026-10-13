"""
隔夜持股法参数敏感性快速验证 (M1+M2里程碑)。

M1: 3组参数 (baseline/V1/V2)，日期范围 2026-03-01~2026-05-25，
每组长时间超时（1800秒，kill掉标记为超时，继续下一组），
产出 baseline vs best_variant 的9-KPI对比。

M2: 交易成本影响分析 — 扣成本前 (gross) vs 扣成本后 (net) 对比，
费率：印花税0.05%+手续费0.03%+滑点0.1%（单边），往返31bps。

用法: nohup python scripts/quick_param_validation.py > logs/param_validation_3m.log 2>&1 &
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import logging
import multiprocessing as mp
import queue as _queue
from time import perf_counter

from app.db.session import SessionLocal
from app.services.evaluation_metrics import compute_full_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quick_param_validation")

START_DATE = "2026-03-01"
END_DATE = "2026-05-25"
TIMEOUT_SEC = 1800

PARAM_GROUPS = [
    {
        "label": "基线版 (基准参数)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V1: 放宽涨跌幅 (2~6%)",
        "MIN_PCT": 2.0, "MAX_PCT": 6.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V2: 收紧VWAP回踩 (+90%站线率)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.90,
        "TAIL_VWAP_MAX_GAP_BPS": 120.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
]


def _run_backtest_worker(q: mp.Queue, params: dict, start: str, end: str):
    """Run backtest in child process with given params."""
    import app.services.overnight_holding_service as mod
    for key, val in params.items():
        if key != "label" and hasattr(mod, key):
            setattr(mod, key, val)

    from app.services.overnight_holding_service import run_overnight_holding_backtest
    db = SessionLocal()
    try:
        result = run_overnight_holding_backtest(db, start, end)
        q.put(result)
    finally:
        db.close()


def run_single_param_set(params: dict, start: str, end: str, timeout: int) -> dict:
    """Run one param set in subprocess with timeout. Kill & mark timeout if exceeded."""
    label = params.get("label", "unknown")
    logger.info("▶ 开始参数组 [%s]", label)

    q = mp.Queue()
    p = mp.Process(target=_run_backtest_worker, args=(q, params, start, end))
    p.start()

    started = perf_counter()
    result = None
    try:
        result = q.get(timeout=timeout)
        elapsed = perf_counter() - started
        logger.info("  ✅ [%s] 完成 (%.1fs)", label, elapsed)
        result["_elapsed_sec"] = round(elapsed, 1)
        result["_timeout"] = False
    except _queue.Empty:
        p.terminate()
        p.join()
        elapsed = perf_counter() - started
        logger.warning("  ⏱️  [%s] 超时 (%.1fs), 已终止", label, elapsed)
        result = {
            "status": "timeout",
            "aggregate_metrics": {},
            "comparison": {},
            "_label": label,
            "_elapsed_sec": round(elapsed, 1),
            "_timeout": True,
        }

    p.join(timeout=3)
    if p.is_alive():
        p.kill()
        p.join()

    result["_label"] = label
    return result


def _cost_metrics(result: dict, mode: str) -> dict:
    """从 recommendation_records 计算扣成本前(gross)或后(net)指标。"""
    records = result.get("recommendation_records", [])
    if not records:
        return {"average_net_return": 0, "recommendation_accuracy": 0, "profit_loss_ratio": 0, "max_drawdown": 0}
    if mode == "gross":
        trades = []
        for rec in records:
            t = dict(rec)
            t["net_return_pct"] = t.get("gross_return_pct", t.get("net_return_pct", 0))
            trades.append(t)
        return compute_full_metrics(trades)
    return result.get("aggregate_metrics", {})


def format_kpi(metrics: dict) -> str:
    if not metrics:
        return "  N/A"
    return (
        f"  推荐准确率:     {metrics.get('recommendation_accuracy', 0):.1%}\n"
        f"  平均净收益率:   {metrics.get('average_net_return', 0):.2%}\n"
        f"  中位净收益率:   {metrics.get('median_net_return', 0):.2%}\n"
        f"  盈亏比:         {metrics.get('profit_loss_ratio', 0):.2f}\n"
        f"  最大回撤:       {metrics.get('max_drawdown', 0):.2%}\n"
        f"  正收益月比率:   {metrics.get('positive_month_ratio', 0):.1%}\n"
        f"  超额收益:       {metrics.get('benchmark_excess_return', 0):.2%}\n"
        f"  样本置信度:     {metrics.get('sample_confidence', 'N/A')}\n"
        f"  交易总笔数:     {metrics.get('trade_count', 0)}"
    )


def main():
    # Use fork so child inherits sys.path and imports
    mp.set_start_method("fork", force=True)

    print("=" * 90)
    print("隔夜持股法 - 参数敏感性快速验证 (M1)")
    print(f"日期区间: {START_DATE} ~ {END_DATE} ({TIMEOUT_SEC}s 超时/组)")
    print(f"参数组数: {len(PARAM_GROUPS)} 组 (baseline + V1 + V2)")
    print("=" * 90)

    all_results = []

    for i, params in enumerate(PARAM_GROUPS):
        label = params.get("label", f"组合_{i}")
        print(f"\n▶ 正在运行 [{i+1}/{len(PARAM_GROUPS)}] {label}")
        print("-" * 60)

        result = run_single_param_set(params, START_DATE, END_DATE, TIMEOUT_SEC)
        all_results.append(result)

        if result.get("_timeout"):
            print(f"  ⏱️  超时 (> {TIMEOUT_SEC}s)，已跳过")
        elif result.get("status") == "completed":
            metrics = result.get("aggregate_metrics", {})
            elapsed = result.get("_elapsed_sec", 0)
            print(f"  ✅ 完成 ({elapsed:.1f}s)")
            print(f"     交易笔数: {metrics.get('trade_count', 0)}")
            print(f"     准确率:   {metrics.get('recommendation_accuracy', 0):.1%}")
            print(f"     平均收益: {metrics.get('average_net_return', 0):.2%}")
        else:
            print(f"  ❌ 失败: {result.get('status', result.get('message', 'unknown'))}")

    # ── 9-KPI 对比 ──
    print("\n\n" + "=" * 90)
    print("BASELINE vs BEST_VARIANT — 9-KPI 对比")
    print("=" * 90)

    successful = [r for r in all_results if r.get("status") == "completed" and not r.get("_timeout")]
    timeout_groups = [r for r in all_results if r.get("_timeout")]

    if timeout_groups:
        print(f"\n⚠️  超时跳过: {', '.join(r['_label'] for r in timeout_groups)}")

    if not successful:
        print("\n❌ 无成功结果，无法对比。")
    else:
        baseline = next((r for r in successful if "基线" in r.get("_label", "")), successful[0])
        best = max(successful, key=lambda r: (
            r.get("aggregate_metrics", {}).get("recommendation_accuracy", 0)
        ))

        baseline_metrics = baseline.get("aggregate_metrics", {})
        best_metrics = best.get("aggregate_metrics", {})

        print(f"\n📊 基线: [{baseline.get('_label', '?')}]")
        print(format_kpi(baseline_metrics))
        print()
        print(f"🏆 最优: [{best.get('_label', '?')}]")
        print(format_kpi(best_metrics))

        # Delta
        print(f"\n📈 差异 (最优 - 基线):")
        delta_keys = [
            ("recommendation_accuracy", "推荐准确率", "pct"),
            ("average_net_return", "平均净收益率", "pct"),
            ("median_net_return", "中位净收益率", "pct"),
            ("profit_loss_ratio", "盈亏比", "float"),
            ("max_drawdown", "最大回撤", "pct_rev"),
            ("positive_month_ratio", "正收益月比率", "pct"),
            ("benchmark_excess_return", "超额收益", "pct"),
            ("trade_count", "交易笔数", "int"),
        ]
        for key, label, unit in delta_keys:
            bv = baseline_metrics.get(key, 0)
            tv = best_metrics.get(key, 0)
            delta = tv - bv
            if unit == "pct":
                print(f"  {label}: {bv:.1%} → {tv:.1%}  ({delta:+.1%})")
            elif unit == "pct_rev":
                print(f"  {label}: {bv:.1%} → {tv:.1%}  ({delta:+.1%})")
            elif unit == "float":
                print(f"  {label}: {bv:.2f} → {tv:.2f}  ({delta:+.2f})")
            elif unit == "int":
                print(f"  {label}: {bv} → {tv}  ({delta:+d})")

        # Walk-Forward info
        print(f"\n{'─' * 60}")
        print("Walk-Forward 样本外验证:")
        for r in successful:
            comparison = r.get("comparison", {})
            wf = comparison.get("walk_forward", {})
            if wf.get("eligible"):
                test_agg = wf.get("test_aggregate", {})
                print(f"  [{r.get('_label', '?')}]:")
                print(f"    样本外笔数:   {test_agg.get('trade_count', 0)}")
                print(f"    样本外准确率: {test_agg.get('recommendation_accuracy', 0):.1%}")
                print(f"    样本外收益率: {test_agg.get('average_net_return', 0):.2%}")
            else:
                print(f"  [{r.get('_label', '?')}]: 样本量不足或区间过短，walk-forward 不可用")
        print(f"{'─' * 60}")

        # Final conclusion
        print(f"\n{'─' * 60}")
        if best.get("_label") == baseline.get("_label"):
            print("结论: 基线版是最优组合，V1/V2 未带来提升。")
        else:
            improvement = best_metrics.get("recommendation_accuracy", 0) - baseline_metrics.get("recommendation_accuracy", 0)
            direction = "优于" if improvement > 0 else "未显著优于"
            print(f"结论: [{best.get('_label', '?')}] {direction}基线版"
                  f"{f'，准确率变化 {improvement:+.1%}' if abs(improvement) > 0.001 else ''}。")
        print(f"{'─' * 60}")

    # ── M2: 交易成本影响分析 ──
    print("\n\n" + "=" * 90)
    print("M2: 交易成本影响分析 — 扣成本前 vs 扣成本后")
    print("费率: 印花税0.05%+手续费0.03%+滑点0.1%（单边）| 往返成本31bps")
    print("=" * 90)

    cost_analysis_groups = [r for r in all_results if not r.get("_timeout") and r.get("status") == "completed" and r.get("recommendation_records")]
    if not cost_analysis_groups:
        print("\n  无有效交易记录，无法计算成本影响。")
    else:
        for r in cost_analysis_groups:
            label = r.get("_label", "?")
            records = r.get("recommendation_records", [])
            if not records:
                continue

            # 扣成本后：直接从 aggregate_metrics 取（使用 net_return_pct）
            post = r.get("aggregate_metrics", {})
            post_avg = post.get("average_net_return", 0)
            post_acc = post.get("recommendation_accuracy", 0)
            post_plr = post.get("profit_loss_ratio", 0)
            post_mdd = post.get("max_drawdown", 0)

            # 扣成本前：用 gross_return_pct 替换 net_return_pct
            pre_trades = []
            for rec in records:
                t = dict(rec)
                t["net_return_pct"] = t.get("gross_return_pct", t.get("net_return_pct", 0))
                pre_trades.append(t)
            pre_metrics = compute_full_metrics(pre_trades)
            pre_avg = pre_metrics.get("average_net_return", 0)
            pre_acc = pre_metrics.get("recommendation_accuracy", 0)
            pre_plr = pre_metrics.get("profit_loss_ratio", 0)
            pre_mdd = pre_metrics.get("max_drawdown", 0)

            cost_avg = pre_avg - post_avg
            cost_acc = pre_acc - post_acc

            print(f"\n  [{label}]  ({len(records)} 笔交易)")
            print(f"  {'指标':<16} {'扣成本前':>12} {'扣成本后':>12} {'Δ成本冲击':>12}")
            print(f"  {'─'*56}")
            print(f"  {'平均收益率':<16} {pre_avg:>11.3f}% {post_avg:>11.3f}% {cost_avg:>+11.3f}%")
            print(f"  {'准确率':<16} {pre_acc:>11.1%} {post_acc:>11.1%} {cost_acc:>+11.1%}")
            print(f"  {'盈亏比':<16} {pre_plr:>11.2f} {post_plr:>11.2f} {pre_plr-post_plr:>+11.2f}")
            print(f"  {'最大回撤':<16} {pre_mdd:>11.2%} {post_mdd:>11.2%} {post_mdd-pre_mdd:>+11.2%}")
            print(f"  {'往返成本':<16} {'':>12} {'31bps':>12} {'':>12}")

    # ── M1+M2 联合总结 ──

    # ── JSON summary ──
    summary = {
        "start_date": START_DATE,
        "end_date": END_DATE,
        "groups_total": len(PARAM_GROUPS),
        "successful": len(successful),
        "timeout": len(timeout_groups),
        "baseline": {
            "label": successful[0].get("_label", "?") if successful else None,
            "metrics": successful[0].get("aggregate_metrics", {}) if successful else None,
        } if successful else None,
        "best_variant": {
            "label": max(successful, key=lambda r: r.get("aggregate_metrics", {}).get("recommendation_accuracy", 0)).get("_label", "?"),
            "metrics": max(successful, key=lambda r: r.get("aggregate_metrics", {}).get("recommendation_accuracy", 0)).get("aggregate_metrics", {}),
        } if successful else None,
        "all_results": [
            {
                "label": r.get("_label", "?"),
                "status": "timeout" if r.get("_timeout") else r.get("status"),
                "elapsed_sec": r.get("_elapsed_sec", 0),
                "metrics": r.get("aggregate_metrics") if not r.get("_timeout") else None,
            }
            for r in all_results
        ],
        "cost_analysis": {
            "rate_config": {
                "stamp_tax": 0.05,
                "commission": 0.03,
                "slippage": 0.1,
                "round_trip_bps": 31,
                "unit": "per_side",
            },
            "by_group": [
                {
                    "label": r.get("_label", "?"),
                    "trade_count": len(r.get("recommendation_records", [])),
                    "pre_cost": {
                        "avg_return": _cost_metrics(r, "gross").get("average_net_return"),
                        "accuracy": _cost_metrics(r, "gross").get("recommendation_accuracy"),
                        "profit_loss_ratio": _cost_metrics(r, "gross").get("profit_loss_ratio"),
                        "max_drawdown": _cost_metrics(r, "gross").get("max_drawdown"),
                    },
                    "post_cost": {
                        "avg_return": r.get("aggregate_metrics", {}).get("average_net_return"),
                        "accuracy": r.get("aggregate_metrics", {}).get("recommendation_accuracy"),
                        "profit_loss_ratio": r.get("aggregate_metrics", {}).get("profit_loss_ratio"),
                        "max_drawdown": r.get("aggregate_metrics", {}).get("max_drawdown"),
                    },
                }
                for r in all_results
                if not r.get("_timeout") and r.get("status") == "completed" and r.get("recommendation_records")
            ],
        } if any(not r.get("_timeout") and r.get("status") == "completed" and r.get("recommendation_records") for r in all_results) else {"note": "无有效交易记录"},
    }
    print(f"\n\nJSON Summary:\n{json.dumps(summary, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
