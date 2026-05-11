from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class AgentSessionContext:
    """一次 Agent 推荐运行的共享上下文。

    每次推荐运行时注册一个会话，持有数据库连接、已加载的 Skill 列表、
    市场快照以及线程间共享的缓存（股票详情、策略信号）。
    """

    db: Session
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
        db: Session,
        skills: list[Any],
        snapshot: dict[str, Any],
    ) -> None:
        with cls._lock:
            cls._sessions[session_id] = AgentSessionContext(
                db=db,
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
