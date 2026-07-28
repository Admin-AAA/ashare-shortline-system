"""G0-G4 北极星分级的纯函数判定。

本模块不访问数据库、不调用模型，只把 self_check 收集到的事实转换成机器可读状态。
状态语义：PASS=门禁通过，WARN=证据不足/测量缺口，FAIL=阻断，BLOCKED=被前置门禁阻断。
"""

from __future__ import annotations

from typing import Any


MIN_OOS_SAMPLE = 30
MIN_OOS_TRADE_DAYS = 10
MIN_PROFIT_FACTOR = 1.0
FORMAL_RUNTIME_SLO_SECONDS = 3600
API_P95_SLO_SECONDS = 2.0


def _result(status: str, summary: str, **metrics: Any) -> dict[str, Any]:
    return {"status": status, "summary": summary, "metrics": metrics}


def evaluate_g0(
    *,
    core_rows: int,
    latest_core_date: str | None,
    latest_formal_date: str | None,
    orphan_outcomes: int,
    evidence_keys_unique: bool,
) -> dict[str, Any]:
    problems: list[str] = []
    if core_rows < 3000:
        problems.append(f"最新日K仅{core_rows}行")
    if latest_formal_date and (not latest_core_date or latest_core_date < latest_formal_date):
        problems.append(f"日K{latest_core_date or '空'}落后formal {latest_formal_date}")
    if orphan_outcomes:
        problems.append(f"{orphan_outcomes}条孤立outcome")
    if not evidence_keys_unique:
        problems.append("因子证据(code,trade_date)未去重")
    status = "FAIL" if problems else "PASS"
    summary = "；".join(problems) if problems else "核心数据、outcome引用与因子去重口径有效"
    return _result(
        status,
        summary,
        latest_core_date=latest_core_date,
        latest_formal_date=latest_formal_date,
        core_rows=core_rows,
        orphan_outcomes=orphan_outcomes,
    )


def evaluate_g1(
    *,
    full: dict[str, Any] | None,
    forward: dict[str, Any] | None,
    forward_trade_days: int,
    version_isolated: bool,
) -> dict[str, Any]:
    if full is None:
        return _result("WARN", "无已结算LIVE formal，无法评估收益质量")
    if forward is None or forward.get("n", 0) < MIN_OOS_SAMPLE or forward_trade_days < MIN_OOS_TRADE_DAYS:
        n = 0 if forward is None else int(forward.get("n", 0))
        return _result(
            "WARN",
            f"OOS门禁未满：n={n}（需≥{MIN_OOS_SAMPLE}），交易日={forward_trade_days}（需≥{MIN_OOS_TRADE_DAYS}）",
            full=full,
            forward=forward,
            forward_trade_days=forward_trade_days,
        )
    if not version_isolated:
        return _result(
            "WARN",
            "OOS混合多个execution_version，只作诊断，禁止据此晋级/下线",
            full=full,
            forward=forward,
            forward_trade_days=forward_trade_days,
        )
    healthy = forward.get("net_avg", 0) > 0 and forward.get("net_pl", 0) >= MIN_PROFIT_FACTOR
    return _result(
        "PASS" if healthy else "FAIL",
        "版本隔离OOS净期望/PF为正" if healthy else "版本隔离OOS净期望≤0或PF<1",
        full=full,
        forward=forward,
        forward_trade_days=forward_trade_days,
    )


def evaluate_g2(
    *,
    missing_formal_dates: list[str],
    failed_scheduler_tasks: list[str],
    stale_running_runs: int,
) -> dict[str, Any]:
    problems: list[str] = []
    if missing_formal_dates:
        problems.append(f"缺formal交易日={','.join(missing_formal_dates)}")
    if failed_scheduler_tasks:
        problems.append(f"失败调度={','.join(failed_scheduler_tasks)}")
    if stale_running_runs:
        problems.append(f"失联running={stale_running_runs}")
    return _result(
        "FAIL" if problems else "PASS",
        "；".join(problems) if problems else "最近交易日formal与调度生命周期正常",
        missing_formal_dates=missing_formal_dates,
        failed_scheduler_tasks=failed_scheduler_tasks,
        stale_running_runs=stale_running_runs,
    )


def evaluate_g3(
    *,
    formal_p95_seconds: int | None,
    api_p95_seconds: float | None,
) -> dict[str, Any]:
    if formal_p95_seconds is not None and formal_p95_seconds > FORMAL_RUNTIME_SLO_SECONDS:
        return _result("FAIL", f"formal p95={formal_p95_seconds}s超过{FORMAL_RUNTIME_SLO_SECONDS}s",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)
    if api_p95_seconds is not None and api_p95_seconds > API_P95_SLO_SECONDS:
        return _result("FAIL", f"API p95={api_p95_seconds}s超过{API_P95_SLO_SECONDS}s",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)
    if formal_p95_seconds is None or api_p95_seconds is None:
        return _result("WARN", "性能测量不完整（formal或API p95缺失）",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)
    return _result("PASS", "formal/API p95满足预算",
                   formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)


def evaluate_g4(
    *,
    prerequisites: list[dict[str, Any]],
    candidate_oos: dict[str, Any] | None,
) -> dict[str, Any]:
    blockers = [r["status"] for r in prerequisites if r["status"] == "FAIL"]
    if blockers:
        return _result("BLOCKED", "G0/G2/G3存在阻断项，策略扩张关闭")
    if not candidate_oos:
        return _result("WARN", "无待晋级候选")
    n = int(candidate_oos.get("n", 0))
    days = int(candidate_oos.get("trade_days", 0))
    if n < MIN_OOS_SAMPLE or days < MIN_OOS_TRADE_DAYS:
        return _result("WARN", f"候选OOS门禁未满：n={n}（需≥{MIN_OOS_SAMPLE}），交易日={days}（需≥{MIN_OOS_TRADE_DAYS}）",
                       candidate_oos=candidate_oos)
    healthy = (candidate_oos.get("net_expectancy") or 0) > 0 and (
        candidate_oos.get("profit_factor") or 0
    ) >= MIN_PROFIT_FACTOR
    return _result("PASS" if healthy else "FAIL",
                   "候选可进入challenger" if healthy else "候选OOS净期望/PF不达标",
                   candidate_oos=candidate_oos)


def choose_next_action(snapshot: dict[str, dict[str, Any]]) -> str:
    if snapshot["G0"]["status"] == "FAIL":
        return "T5 证据/数据一致性"
    if snapshot["G2"]["status"] == "FAIL":
        return "T12 pipeline稳定性"
    if snapshot["G3"]["status"] == "FAIL":
        return "T4 性能优化"
    if snapshot["G1"]["status"] in {"FAIL", "WARN"}:
        return "T2 样本外验证/等待新样本"
    return "T1 论文策略孵化"
