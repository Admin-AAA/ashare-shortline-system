from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

from sqlalchemy.orm import Session


@dataclass
class AgentSessionContext:
    """一次 Agent 推荐运行的共享上下文。

    session_factory 是可调用对象（如 SessionLocal），每次调用返回新的独立 Session。
    工具函数应使用 `with ctx.session_factory() as db:` 获取短生命周期连接，
    避免跨线程共享 Session 导致的并发问题。
    """

    session_factory: Callable[[], Session]
    skills: list[Any]  # list of StrategySkill
    snapshot: dict[str, Any]
    stocks_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    strategy_signals_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


class AgentSessionRegistry:
    """线程安全的 Agent 会话注册表。"""

    _lock: Lock = Lock()
    _sessions: dict[str, AgentSessionContext] = {}

    @classmethod
    def register(
        cls,
        session_id: str,
        session_factory: Callable[[], Session],
        skills: list[Any],
        snapshot: dict[str, Any],
    ) -> None:
        with cls._lock:
            cls._sessions[session_id] = AgentSessionContext(
                session_factory=session_factory,
                skills=skills,
                snapshot=snapshot,
            )

    @classmethod
    def get(cls, session_id: str) -> AgentSessionContext | None:
        with cls._lock:
            return cls._sessions.get(session_id)

    @classmethod
    def unregister(cls, session_id: str) -> None:
        with cls._lock:
            cls._sessions.pop(session_id, None)
