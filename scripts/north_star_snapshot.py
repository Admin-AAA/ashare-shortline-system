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
# G3 API p95 判定的最低样本量。api_request_metrics 是被动记录前端访问，样本天然稀疏
# 且常集中在用户浏览的几分钟内（如 07-29 15:46~16:17 的 /api/strategies 全表扫描）。
# nearest-rank p95 在小样本下对单个异常值极敏感：n=186 时第 177 名恰好 2078ms，
# 差 78ms 过阈就把 G3 判 FAIL、连锁 G4 BLOCKED、误导 NEXT_ACTION 到 T4。
# 样本不足时降级为 WARN（测量不完整）而非 FAIL，避免单点噪声触发策略扩张阻断。
MIN_API_P95_SAMPLE = 500
# p99/p95 比值超过此阈值说明 p95 是单点/少数异常拉高的（p50/p90 都正常），
# 同样降级为 WARN 而非 FAIL。3x 覆盖实测：07-29 噪声日 p99=25s/p95=2.078s≈12x，
# 正常日 p99/p95 应接近 1.3~1.5。
API_P95_NOISE_RATIO = 3.0


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
    recovered_scheduler_tasks: list[str] | None = None,
    replay_recovered_dates: list[str] | None = None,
) -> dict[str, Any]:
    """G2 衡量 pipeline 生命周期，不衡量样本资格。

    安全网对漏跑交易日的补跑是 historical_replay completed run——它闭合 G2 的
    断档（与 check_formal_recommendation_gap 同口径，否则安全网认为已修、G2
    永远 FAIL，形成不可清除的假警报），但绝不计入 OOS/G1（OFFICIAL_SAMPLE_SQL
    仍只认 live_forward）。补跑修复的日期在 summary 中标注保留可见性。
    """
    recovered_scheduler_tasks = recovered_scheduler_tasks or []
    replay_recovered_dates = replay_recovered_dates or []
    unresolved_dates = [d for d in missing_formal_dates if d not in replay_recovered_dates]
    problems: list[str] = []
    if unresolved_dates:
        problems.append(f"缺formal交易日={','.join(unresolved_dates)}")
    if failed_scheduler_tasks:
        problems.append(f"失败调度={','.join(failed_scheduler_tasks)}")
    if stale_running_runs:
        problems.append(f"失联running={stale_running_runs}")
    summary = "；".join(problems) if problems else "最近交易日formal与调度生命周期正常"
    notes: list[str] = []
    if replay_recovered_dates:
        notes.append(f"漏跑日已由安全网补跑修复（replay不计入OOS）：{','.join(replay_recovered_dates)}")
    if not problems and recovered_scheduler_tasks:
        notes.append(f"历史失败已由后续安全网恢复：{','.join(recovered_scheduler_tasks)}")
    if notes:
        summary += f"（{'；'.join(notes)}）"
    return _result(
        "FAIL" if problems else "PASS",
        summary,
        missing_formal_dates=unresolved_dates,
        replay_recovered_dates=replay_recovered_dates,
        failed_scheduler_tasks=failed_scheduler_tasks,
        recovered_scheduler_tasks=recovered_scheduler_tasks,
        stale_running_runs=stale_running_runs,
    )


def split_recovered_scheduler_failures(
    *,
    failed_tasks: list[tuple[str, Any]],
    missing_formal_dates: list[str],
    stale_running_runs: int,
    safety_net_status: str | None,
    safety_net_last_run_at: Any,
) -> tuple[list[str], list[str]]:
    """区分当前调度失败与已被后续 formal 安全网完整恢复的历史失败。"""
    unresolved: list[str] = []
    recovered: list[str] = []
    recovery_healthy = (
        safety_net_status == "completed"
        and safety_net_last_run_at is not None
        and not missing_formal_dates
        and stale_running_runs == 0
    )
    for task_id, failed_at in failed_tasks:
        repaired = False
        if task_id == "post_sync_formal_recommendation" and recovery_healthy and failed_at is not None:
            try:
                repaired = safety_net_last_run_at > failed_at
            except TypeError:
                repaired = str(safety_net_last_run_at) > str(failed_at)
        (recovered if repaired else unresolved).append(task_id)
    return unresolved, recovered


def evaluate_g3(
    *,
    formal_p95_seconds: int | None,
    api_p95_seconds: float | None,
    api_p95_sample: int | None = None,
    api_p99_seconds: float | None = None,
) -> dict[str, Any]:
    if formal_p95_seconds is not None and formal_p95_seconds > FORMAL_RUNTIME_SLO_SECONDS:
        return _result("FAIL", f"formal p95={formal_p95_seconds}s超过{FORMAL_RUNTIME_SLO_SECONDS}s",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)
    api_is_noise = False
    noise_reason = ""
    if api_p95_seconds is not None and api_p95_sample is not None and api_p95_sample < MIN_API_P95_SAMPLE:
        api_is_noise = True
        noise_reason = f"API p95={api_p95_seconds}s样本不足{api_p95_sample}<{MIN_API_P95_SAMPLE}"
    elif (
        api_p95_seconds is not None
        and api_p99_seconds is not None
        and api_p95_seconds > 0
        and api_p99_seconds / api_p95_seconds > API_P95_NOISE_RATIO
    ):
        api_is_noise = True
        noise_reason = (f"API p95={api_p95_seconds}s疑单点噪声"
                        f"（p99={api_p99_seconds}s/p95比{api_p99_seconds / api_p95_seconds:.1f}x>{API_P95_NOISE_RATIO}）")
    if api_p95_seconds is not None and api_p95_seconds > API_P95_SLO_SECONDS and not api_is_noise:
        return _result("FAIL", f"API p95={api_p95_seconds}s超过{API_P95_SLO_SECONDS}s",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)
    if api_is_noise:
        return _result("WARN", f"性能测量不可信（{noise_reason}），降级不阻断",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds,
                       api_p95_sample=api_p95_sample, api_p99_seconds=api_p99_seconds)
    if formal_p95_seconds is None or api_p95_seconds is None:
        return _result("WARN", "性能测量不完整（formal或API p95缺失）",
                       formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds)
    return _result("PASS", "formal/API p95满足预算",
                   formal_p95_seconds=formal_p95_seconds, api_p95_seconds=api_p95_seconds,
                   api_p95_sample=api_p95_sample, api_p99_seconds=api_p99_seconds)


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
