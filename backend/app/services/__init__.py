"""Service layer. Wires engine ports to service adapters (lazy, no eager imports)."""
from app.engine import ports as _ports


def _active_model_routes(*args, **kwargs):
    from app.services.model_router_service import get_active_model_routes

    return get_active_model_routes(*args, **kwargs)


def _record_task_run(*args, **kwargs):
    from app.services.cost_service import record_task_run

    return record_task_run(*args, **kwargs)


_ports.register(_ports.ACTIVE_MODEL_ROUTES, _active_model_routes)
_ports.register(_ports.RECORD_TASK_RUN, _record_task_run)
