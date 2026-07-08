"""运维管理 API：成本追踪、系统健康、调度任务、数据源监控。"""

from fastapi import APIRouter, Depends, HTTPException
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
from app.core.config import get_settings, BACKEND_ENV_FILE
from pydantic import BaseModel

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── 系统 LLM 接口（agent_skill_*）配置 / 测试 ──────────────────────────
class SystemLlmConfigUpdate(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # 传 "" 或 "********" 表示保留现有 Key
    timeout_seconds: float | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class SystemLlmTestPayload(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout_seconds: float | None = None
    temperature: float | None = None
    max_tokens: int | None = None


def _read_system_llm_config() -> dict:
    s = get_settings()
    has_key = bool(s.agent_skill_api_key)
    return {
        "runtime": s.agent_skill_runtime,
        "base_url": s.agent_skill_base_url,
        "model": s.agent_skill_model,
        "api_key": "********" if has_key else "",
        "has_api_key": has_key,
        "timeout_seconds": s.agent_skill_timeout_seconds,
        "temperature": s.agent_temperature,
        "max_tokens": s.agent_max_tokens,
    }


def _write_env_kv(updates: dict[str, str]) -> None:
    """更新 .env 中指定键值（不存在则追加）。幂等，保留注释与顺序。"""
    path = BACKEND_ENV_FILE
    lines = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    index: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        index[key] = i
    for k, v in updates.items():
        val = str(v)
        if k in index:
            lines[index[k]] = f"{k}={val}"
        else:
            lines.append(f"{k}={val}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


@router.get("/system-llm")
def get_system_llm_config():
    return _read_system_llm_config()


@router.post("/system-llm")
def update_system_llm_config(payload: SystemLlmConfigUpdate):
    """保存系统 LLM 接口配置：热更新内存配置 + 持久化到 .env。"""
    s = get_settings()
    updates: dict[str, str] = {}

    if payload.base_url is not None:
        s.agent_skill_base_url = payload.base_url
        updates["ASHARE_AGENT_SKILL_BASE_URL"] = payload.base_url
    if payload.model is not None:
        s.agent_skill_model = payload.model
        updates["ASHARE_AGENT_SKILL_MODEL"] = payload.model
    if payload.api_key is not None and payload.api_key not in ("", "********"):
        s.agent_skill_api_key = payload.api_key
        updates["ASHARE_AGENT_SKILL_API_KEY"] = payload.api_key
    if payload.timeout_seconds is not None:
        s.agent_skill_timeout_seconds = payload.timeout_seconds
        updates["ASHARE_AGENT_SKILL_TIMEOUT_SECONDS"] = str(payload.timeout_seconds)
    if payload.temperature is not None:
        s.agent_temperature = payload.temperature
        updates["ASHARE_AGENT_TEMPERATURE"] = str(payload.temperature)
    if payload.max_tokens is not None:
        s.agent_max_tokens = payload.max_tokens
        updates["ASHARE_AGENT_MAX_TOKENS"] = str(payload.max_tokens)

    if updates:
        _write_env_kv(updates)

    return {
        "ok": True,
        "updated": list(updates.keys()),
        "config": _read_system_llm_config(),
    }


@router.post("/system-llm/test")
async def test_system_llm_config(payload: SystemLlmTestPayload):
    """用当前或表单给定的参数，真实发起一次 OpenAI 兼容调用验证连通性。"""
    import time

    from openai import AsyncOpenAI

    s = get_settings()
    base_url = (payload.base_url or "").strip() or s.agent_skill_base_url
    model = (payload.model or "").strip() or s.agent_skill_model
    api_key = payload.api_key
    if not api_key or api_key == "********":
        api_key = s.agent_skill_api_key
    timeout = float(payload.timeout_seconds or s.agent_skill_timeout_seconds)

    if not api_key:
        return {"ok": False, "error": "未配置 API Key，无法测试", "latency_ms": 0, "model": model}
    if not base_url:
        return {"ok": False, "error": "未配置 Base URL，无法测试", "latency_ms": 0, "model": model}

    t0 = time.monotonic()
    try:
        async with AsyncOpenAI(
            api_key=api_key, base_url=base_url.rstrip("/"), timeout=min(timeout, 30)
        ) as client:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是 A股短线系统的测试助手，请简要确认连接状态。"},
                    {"role": "user", "content": "请只回复「连接成功」四个字。"},
                ],
                temperature=0,
                max_tokens=16,
            )
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            content = (resp.choices[0].message.content or "").strip()
            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "model": model,
                "sample": content[:200],
            }
    except Exception as exc:  # noqa: BLE001 - 测试接口需把任意异常透传给前端
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "error": str(exc)[:500],
            "latency_ms": elapsed_ms,
            "model": model,
        }


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
    return diagnose_all(db, force_refresh=bool(force_refresh))


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
    from app.services.temporal_consistency import check_run_temporal_consistency
    from app.services.outcome_service import _cached as outcome_cached

    cache_key = "temporal_consistency"
    def _compute():
        return check_run_temporal_consistency(db)
    return outcome_cached(cache_key, _compute)


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
    from app.services.scheduler_service import run_task_now

    run_task_now(task_id)
    return {"ok": True}


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
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(cron_expression, timezone="Asia/Shanghai")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="cron_expression is invalid") from exc


def _reload_scheduler() -> int:
    from app.services.scheduler_service import reload_tasks

    return reload_tasks()
