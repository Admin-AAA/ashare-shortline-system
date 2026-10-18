"""Service layer. Wires engine ports to service adapters (lazy, no eager imports)."""
from app.engine import ports as _ports


def _active_model_routes(*args, **kwargs):
    from app.services.model_router_service import get_active_model_routes

    return get_active_model_routes(*args, **kwargs)


def _record_task_run(*args, **kwargs):
    from app.services.cost_service import record_task_run

    return record_task_run(*args, **kwargs)


def _skill_agent_create(*args, **kwargs):
    from app.agents.agno_skill_agent import create_skill_agent

    return create_skill_agent(*args, **kwargs)


def _skill_agent_run(*args, **kwargs):
    from app.agents.agno_skill_agent import run_skill_agent

    return run_skill_agent(*args, **kwargs)


_ports.register(_ports.ACTIVE_MODEL_ROUTES, _active_model_routes)
_ports.register(_ports.RECORD_TASK_RUN, _record_task_run)
_ports.register(_ports.SKILL_AGENT_CREATE, _skill_agent_create)
_ports.register(_ports.SKILL_AGENT_RUN, _skill_agent_run)
