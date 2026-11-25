"""调度接线的静态守卫：注册 / deployment 同步 / 退役暂停都必须有真实调用点。

2026-11-24 全量体检发现的同一类缺陷：函数写好了但零调用点（_ensure_* 系列在
2026-08-02 之前完全没接线），以及只同步 enabled 任务、退役任务的 deployment
带着 active schedule 继续每天触发。这里用 AST 校验调用点，不依赖 Prefect server。
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _function_node(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(BACKEND)} 里找不到 {name}")


def test_startup_registers_tasks_and_syncs_deployments() -> None:
    """FastAPI 启动必须同时做 DB 注册与 Prefect deployment 同步。

    只注册不同步时，worker 长期不重启期间新增的任务拿不到 deployment。
    """
    called = _called_names(_function_node(BACKEND / "app" / "main.py", "on_startup"))
    assert "ensure_scheduled_task_registrations" in called
    assert "_sync_prefect_deployments" in called


def test_deployment_sync_runs_off_the_event_loop() -> None:
    """RunnerDeployment.apply 带 @async_dispatch：在事件循环线程里调用会返回未 await
    的 coroutine，同步「成功」但什么都没写。必须放进独立线程。"""
    node = _function_node(BACKEND / "app" / "main.py", "_sync_prefect_deployments")
    called = _called_names(node)
    assert "Thread" in called
    assert "schedule_all_enabled_tasks" in called
    assert "pause_disabled_task_deployments" in called


def test_every_sync_call_site_also_pauses_retired_deployments() -> None:
    for rel, func in (
        ("app/api/admin.py", "_reload_scheduler"),
        ("scripts/run_prefect_worker.py", "main"),
    ):
        called = _called_names(_function_node(BACKEND / rel, func))
        assert "schedule_all_enabled_tasks" in called, rel
        assert "pause_disabled_task_deployments" in called, rel


def test_deployment_sync_requires_resident_client() -> None:
    """没有常驻 server 时 apply() 会写进用完即关的临时 server，必须先过客户端闸门。"""
    for func in ("schedule_all_enabled_tasks", "pause_disabled_task_deployments"):
        called = _called_names(_function_node(BACKEND / "app" / "orchestration" / "deployments.py", func))
        assert "require_resident_prefect_client" in called, func