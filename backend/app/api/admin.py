"""运维管理 API：成本追踪、系统健康、调度任务、数据源监控。"""
import logging
import threading
import time

from fastapi import Depends, HTTPException
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.cost_service import (
    cost_summary,
    delete_scheduled_task,
    list_scheduled_task_runs,
    list_scheduled_tasks,
    record_task_run,
    system_health,
    upsert_scheduled_task,
)
from app.services.data_health_service import diagnose_all
from app.services.data_gap_remediation_service import (
    DEFAULT_DAILY_KBAR_CODE_LIMIT,
    remediate_data_health_gaps,
)
from app.services.data_source_health import active_alerts, readiness_check
from app.services.model_router_service import (
    create_model_route,
    delete_model_route,
    list_model_routes,
    reorder_model_routes,
    test_model_route as test_model_route_service,
    update_model_route as update_model_route_service,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    return system_health(db)


@router.get("/costs")
def get_costs(days: int = 30, db: Session = Depends(get_db)):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    return cost_summary(db, days=days)


@router.get("/tasks")
def get_tasks(db: Session = Depends(get_db)):
    return list_scheduled_tasks(db)


@router.get("/task-runs")
def get_task_runs(
    limit: int = 100,
    task_id: str | None = None,
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return list_scheduled_task_runs(db, limit=limit, task_id=task_id)


@router.get("/source-health")
def get_source_health(db: Session = Depends(get_db)):
    """各数据源健康状态。"""
    from app.services.data_sources import list_data_sources
    return list_data_sources(db)


@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db)):
    """当前活跃告警。"""
    return active_alerts(db)


@router.get("/readiness")
def get_readiness(db: Session = Depends(get_db)):
    """系统就绪检查。"""
    return readiness_check(db)


@router.get("/data-health")
def get_data_health(force_refresh: bool = False, db: Session = Depends(get_db)):
    """数据质量诊断：各表覆盖情况、滞后天数、健康评分、问题列表。

    结果按参考交易日缓存，默认命中缓存。force_refresh=true 跳过缓存（详情页
    「重新诊断」按钮、修复任务完成后调用）。
    """
    result = diagnose_all(db, force_refresh=bool(force_refresh))
    # 健康评分原本只反映数据库表的结构性指标（95 分时 data/ 下分钟K缓存也可能已停更两个月）。
    # 2026-10-26 起：把统一数据质量巡检（data_quality_monitor）的红/黄项折算成扣分——
    # score = 原库表评分(score_db) − 巡检扣分(score_penalty)，规则见 score_detail.rule。
    try:
        from app.services.data_quality_monitor import load_report, quality_score_penalty

        rep = load_report()
        pen = quality_score_penalty(rep)
        base = result.get("score")
        extra: dict = {"score_db": base, "score_detail": {**pen, "score_db": base}}
        if isinstance(base, (int, float)) and pen["penalty"]:
            extra["score"] = max(0, int(base) - int(pen["penalty"]))
        if rep:
            extra["data_quality"] = {
                "overall": rep.get("overall"), "summary": rep.get("summary"), "generated_at": rep.get("generated_at"),
                "expected_trade_date": rep.get("expected_trade_date"),
                "red": [i["key"] for i in rep.get("items", []) if i.get("severity") == "red"],
            }
        result = {**result, **extra}
    except Exception:  # noqa: BLE001 - 附加信息，失败不影响原健康接口
        pass
    return result


@router.get("/data-quality")
def get_data_quality(refresh: bool = False, db: Session = Depends(get_db)):
    """统一数据新鲜度与质量巡检结果（数据库表 + 文件数据集 + research 静态文件 + 调度任务 + 日志静默失败）。

    默认读最近一次巡检落盘的报告（盘前 08:50 / 18:00 / 21:40 定时跑）；refresh=true 立即重跑一次（只读、不改告警登记）。
    """
    from app.services.data_quality_monitor import load_report, run_data_quality_check

    if refresh:
        rep = run_data_quality_check(db, alert=False)
        if rep.get("overall") != "error":
            return rep
    return load_report() or {"overall": "unknown", "summary": {}, "items": [],
                             "message": "尚无巡检报告：等定时任务或 ?refresh=true 触发一次"}


# ── 数据源管理（配置页 Tab）：provider 列表 / 启停 / 测试 ──


@router.get("/data-sources")
def list_data_sources_api(db: Session = Depends(get_db)):
    """列出配置页可管理的 Tushare Provider 与独立市场数据源。"""
    from app.services.data_sources import list_management_sources

    return {"providers": list_management_sources(db)}


@router.get("/data-gateway")
def data_gateway_topology_api(db: Session = Depends(get_db)):
    """数据网关拓扑：数据类型 → 有序 provider → 开关 + live 熔断状态。"""
    from app.services.datasource.topology import build_topology

    return build_topology(db)


@router.post("/data-gateway/switch")
def data_gateway_switch_api(payload: dict, db: Session = Depends(get_db)):
    """启停一个网关开关 {source, source_type, enabled}（写 data_source_config，各进程 5s 内生效）。"""
    from app.services.data_sources import set_gateway_switch
    from app.services.datasource.topology import build_topology

    source, source_type = str(payload.get("source") or ""), str(payload.get("source_type") or "")
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled is required")
    if not set_gateway_switch(db, source, source_type, bool(payload["enabled"])):
        raise HTTPException(status_code=404, detail=f"不是网关开关: {source}/{source_type}")
    return build_topology(db)


@router.post("/data-sources/reorder")
def reorder_providers_api(payload: dict, db: Session = Depends(get_db)):
    """重排当前 Tushare Pro Provider 的路由优先级（运行时即时生效，不动 config）。

    payload: {"provider_names": ["promax", "quantdata"]}（主源在前）。
    不在路由链里的名字传入会被忽略。
    """
    from app.services.data_sources import list_management_sources, set_provider_priorities

    names = payload.get("provider_names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HTTPException(status_code=400, detail="provider_names must be a list of strings")
    set_provider_priorities(db, names)
    return {"providers": list_management_sources(db)}


@router.post("/data-sources/{provider_name}/enabled")
def set_provider_enabled_api(provider_name: str, payload: dict, db: Session = Depends(get_db)):
    """启停某 provider（运行时即时生效，不动 config）。payload: {"enabled": true/false}"""
    from app.services.data_sources import set_provider_enabled

    enabled = bool(payload.get("enabled", True))
    result = set_provider_enabled(db, provider_name, enabled)
    if result is None:
        raise HTTPException(status_code=404, detail=f"provider 未接入: {provider_name}")
    return result


@router.post("/data-sources/{provider_name}/test")
def test_provider_api(provider_name: str, db: Session = Depends(get_db)):
    """探针测试某 provider 当前是否可用，返回 {ok, latency_ms, status, error}。"""
    from app.services.data_sources import test_provider

    try:
        return test_provider(db, provider_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"provider 未接入: {provider_name}")


@router.post("/data-health/remediate")
def remediate_data_health(payload: dict | None = None, db: Session = Depends(get_db)):
    payload = payload or {}
    if not bool(payload.get("dry_run", False)):
        from app.services.scheduler_service import start_data_health_remediation_now

        return start_data_health_remediation_now(payload)

    result = remediate_data_health_gaps(
        db,
        target_tables=payload.get("target_tables"),
        dry_run=True,
        capital_flow_code_limit=int(payload.get("capital_flow_code_limit", 200)),
        daily_kbar_code_limit=int(payload.get("daily_kbar_code_limit", DEFAULT_DAILY_KBAR_CODE_LIMIT)),
        minute_kbar_code_limit=int(payload.get("minute_kbar_code_limit", 300)),
        post_diagnosis=bool(payload.get("post_diagnosis", True)),
    )
    return result


@router.get("/data-health/remediate/status")
def get_data_health_remediation_status():
    from app.services.scheduler_service import get_latest_data_health_remediation_task_run

    return {"task_run": get_latest_data_health_remediation_task_run()}


@router.get("/temporal-consistency")
def get_temporal_consistency(
    db: Session = Depends(get_db),
):
    """时态一致性检查：验证推荐/回测数据没有时间穿越问题。

    检查维度：
    - 推荐日期 vs 日K线是否存在
    - 特征快照是否引用未来日期
    - Outcome 标注日期 vs 推荐日期（入场必须在推荐日之后）
    """
    return _temporal_consistency_cached(db)


# 时态一致性：30s 内视为新鲜（与原 _cached 相同）；过期但 10 分钟内先返回旧结果并在
# 后台单飞刷新（stale-while-revalidate），无缓存时同步单飞计算。计算逻辑不变。
_TC_FRESH_SECONDS = 30.0
_TC_STALE_SECONDS = 600.0
_tc_cache: dict[str, tuple[float, object]] = {}
_tc_state_lock = threading.Lock()
_tc_compute_lock = threading.Lock()


def _tc_compute(db) -> object:
    from app.services.temporal_consistency import check_run_temporal_consistency

    result = check_run_temporal_consistency(db)
    with _tc_state_lock:
        _tc_cache["v"] = (time.time(), result)
    return result


def _tc_background_refresh() -> None:
    if not _tc_compute_lock.acquire(blocking=False):
        return
    try:
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            _tc_compute(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 - 后台刷新失败保留旧值，下次请求重试
        logging.getLogger(__name__).warning("temporal-consistency background refresh failed", exc_info=True)
    finally:
        _tc_compute_lock.release()


def _temporal_consistency_cached(db):
    with _tc_state_lock:
        entry = _tc_cache.get("v")
    now = time.time()
    if entry is not None:
        age = now - entry[0]
        if age < _TC_FRESH_SECONDS:
            return entry[1]
        if age < _TC_STALE_SECONDS:
            threading.Thread(target=_tc_background_refresh, daemon=True).start()
            return entry[1]
    with _tc_compute_lock:
        with _tc_state_lock:
            entry = _tc_cache.get("v")
        if entry is not None and time.time() - entry[0] < _TC_FRESH_SECONDS:
            return entry[1]
        return _tc_compute(db)


def invalidate_temporal_consistency_cache() -> None:
    with _tc_state_lock:
        _tc_cache.clear()


@router.get("/model-routes")
def get_model_routes(db: Session = Depends(get_db)):
    return list_model_routes(db)


@router.post("/model-routes")
def create_model_route_api(payload: dict, db: Session = Depends(get_db)):
    return create_model_route(db, payload)


@router.post("/model-routes/test")
async def test_model_route_api(payload: dict, db: Session = Depends(get_db)):
    return await test_model_route_service(db, payload)


@router.patch("/model-routes/{route_id}")
def update_model_route_api(route_id: str, payload: dict, db: Session = Depends(get_db)):
    route = update_model_route_service(db, route_id, payload)
    if route is None:
        raise HTTPException(status_code=404, detail="model route not found")
    return route


@router.delete("/model-routes/{route_id}")
def delete_model_route_api(route_id: str, db: Session = Depends(get_db)):
    if not delete_model_route(db, route_id):
        raise HTTPException(status_code=404, detail="model route not found")
    return {"deleted": route_id}


@router.post("/model-routes/reorder")
def reorder_model_routes_api(payload: dict, db: Session = Depends(get_db)):
    route_ids = payload.get("route_ids")
    if not isinstance(route_ids, list):
        raise HTTPException(status_code=400, detail="route_ids must be a list")
    return reorder_model_routes(db, [str(route_id) for route_id in route_ids if str(route_id).strip()])


@router.post("/tasks")
def create_task(payload: dict, db: Session = Depends(get_db)):
    task = _upsert_task_from_payload(db, payload)
    _reload_scheduler()
    return task


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, payload: dict, db: Session = Depends(get_db)):
    existing = next((t for t in list_scheduled_tasks(db) if t["task_id"] == task_id), None)
    if not existing:
        raise HTTPException(status_code=404, detail="task not found")
    merged = {**existing, **{k: v for k, v in payload.items() if v is not None}, "task_id": task_id}
    task = _upsert_task_from_payload(db, merged)
    _reload_scheduler()
    return task


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    if not delete_scheduled_task(db, task_id):
        raise HTTPException(status_code=404, detail="task not found")
    _reload_scheduler()
    return {"deleted": task_id}


@router.post("/tasks/{task_id}/run")
def run_task_now(task_id: str, db: Session = Depends(get_db)):
    if not any(task["task_id"] == task_id for task in list_scheduled_tasks(db)):
        raise HTTPException(status_code=404, detail="task not found")
    from app.orchestration import trigger_scheduled_task

    ref = trigger_scheduled_task(task_id)
    return {"ok": True, "orchestration_run_id": ref.run_id}


@router.post("/tasks/{task_id}/_record")
def record_task(task_id: str, payload: dict, db: Session = Depends(get_db)):
    record_task_run(db, task_id, payload.get("status", "unknown"), payload.get("error"))
    return {"ok": True}


@router.post("/tasks/_reload")
def reload_tasks():
    """热重载调度器任务（Cron 配置变更后调用）。"""
    count = _reload_scheduler()
    return {"ok": True, "active_jobs": count}


def _upsert_task_from_payload(db: Session, payload: dict) -> dict:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    task_type = str(payload.get("task_type") or "").strip()
    if task_type not in {"data_sync", "recommendation_run", "preset_matrix_run", "pool_refresh", "data_backfill", "formal_safety_net"}:
        raise HTTPException(status_code=400, detail="task_type is invalid")

    payload_data = dict(payload.get("payload") or {})
    enabled = bool(payload.get("enabled", True))
    if task_type == "recommendation_run" and enabled and not payload_data.get("preset_id"):
        raise HTTPException(status_code=400, detail="preset_id is required")

    cron_expression = str(payload.get("cron_expression") or "").strip()
    if not cron_expression:
        trigger_time = str(payload.get("trigger_time") or "").strip()
        cron_expression = _daily_cron(trigger_time)
    _validate_cron(cron_expression)

    task = upsert_scheduled_task(
        db,
        task_id=task_id,
        name=str(payload.get("name") or task_id).strip(),
        task_type=task_type,
        cron_expression=cron_expression,
        payload=payload_data,
        enabled=enabled,
    )
    return next(item for item in list_scheduled_tasks(db) if item["task_id"] == task.task_id)
def _daily_cron(trigger_time: str) -> str:
    try:
        hour, minute = [int(part) for part in trigger_time.split(":", 1)]
    except Exception as exc:
        raise HTTPException(status_code=400, detail="trigger_time must be HH:MM") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise HTTPException(status_code=400, detail="trigger_time must be HH:MM")
    return f"{minute} {hour} * * *"


def _validate_cron(cron_expression: str) -> None:
    from prefect.schedules import Cron

    try:
        Cron(cron_expression, timezone="Asia/Shanghai")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="cron_expression is invalid") from exc


def _reload_scheduler() -> int:
    from app.orchestration import schedule_all_enabled_tasks

    return schedule_all_enabled_tasks().scheduled_task_count

from app.services.cache_hooks import register as _register_cache_hook  # noqa: E402

_register_cache_hook("outcome_metrics", invalidate_temporal_consistency_cache)
