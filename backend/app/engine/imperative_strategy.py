"""Behavior-only contract and descriptor-bound adapter for imperative strategies.

Imperative implementations calculate a :class:`StrategyDecision` only.  They do
not own a business id, display name, version, run mode, weight, or other
manifest metadata.  Bootstrap validates that constraint and wraps each
implementation in :class:`DescriptorBoundImperativeStrategy`, which projects
its decision into the canonical :class:`~app.engine.signal.Signal` contract.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol, cast, runtime_checkable

from app.engine.signal import ENTRY_MODE_TARGET_TOUCH, Signal
from app.engine.strategy_runner import _evidence_score_cap, _passes_universe

if TYPE_CHECKING:
    from app.engine.strategy import StrategyContext, StrategyDescriptor


_IDENTITY_OR_METADATA_ATTRIBUTES = (
    "id",
    "skill_id",
    "strategy_id",
    "strategy_name",
    "name",
    "display_name",
    "version",
    "version_id",
    "declared_version",
    "effective_version",
    "mode",
    "run_mode",
    "status",
    "enabled",
    "requirements",
    "capabilities",
    "deprecation_reason",
    "replacement_strategy_id",
    "weight",
    "signal_weight",
    "strategy_independence",
    "metadata",
    "signal_metadata",
    "manifest",
    "descriptor",
    # ``universe`` is the sole optional implementation-side hook.  The old
    # prefilter hook would create a second, untracked execution declaration.
    "prefilter",
)


class ImperativeStrategyProtocolError(TypeError):
    """An imperative implementation does not honor the behavior-only contract."""


@dataclass(frozen=True)
class StrategyDecision:
    """Behavioral result returned by an imperative implementation.

    This deliberately contains no strategy identity or descriptor metadata.  The
    descriptor-bound adapter owns those fields when it converts the decision to
    a ``Signal``.
    """

    score: float
    phase: str = "未分类"
    score_cap: float = 100.0
    hard_veto: bool = False
    veto_reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    setup_match: str = ""
    entry_rule: str = ""
    invalid_rule: str = ""
    entry_mode: str = ENTRY_MODE_TARGET_TOUCH
    setup_quality: float = 50.0
    evidence: list[str] = field(default_factory=list)


@runtime_checkable
class ImperativeStrategy(Protocol):
    """The only required imperative implementation behavior.

    Implementations may additionally expose an optional ``universe`` mapping
    compatible with the declarative strategy universe section.  All business
    identity and manifest-declared metadata are intentionally absent.
    """

    def evaluate(self, stock: dict[str, Any], ctx: "StrategyContext") -> StrategyDecision:
        """Calculate a decision for one stock without assembling a Signal."""
        ...


def validate_imperative_implementation(implementation: object) -> None:
    """Validate an implementation before descriptor binding at bootstrap time."""
    if not isinstance(implementation, ImperativeStrategy):
        raise ImperativeStrategyProtocolError("缺少可调用的 evaluate(stock, ctx) 方法。")
    _validate_evaluate_signature(implementation.evaluate)

    duplicate = _declared_identity_or_metadata(implementation)
    if duplicate is not None:
        attribute, value = duplicate
        if attribute == "prefilter":
            raise ImperativeStrategyProtocolError(
                "命令式实现仅可声明可选 universe hook，不能声明 prefilter；"
                "过滤声明必须来自 manifest/StrategyDescriptor。"
            )
        raise ImperativeStrategyProtocolError(
            f"实现不得重复声明 {attribute}={value!r}；"
            "strategy_id、名称、版本、运行模式和声明元数据只能来自 manifest/StrategyDescriptor。"
        )

    universe = getattr(implementation, "universe", {})
    if not isinstance(universe, Mapping):
        raise ImperativeStrategyProtocolError("可选 universe hook 必须为 mapping。")
    for key in ("include", "exclude"):
        values = universe.get(key, ())
        if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
            raise ImperativeStrategyProtocolError(
                f"可选 universe hook 的 {key} 必须为字符串列表。"
            )


def _validate_evaluate_signature(evaluate: object) -> None:
    """Require the runtime callable shape promised by ``ImperativeStrategy``."""
    try:
        signature = inspect.signature(cast(Any, evaluate))
    except (TypeError, ValueError) as exc:
        raise ImperativeStrategyProtocolError(
            "evaluate(stock, ctx) 方法签名无法验证。"
        ) from exc
    try:
        signature.bind({}, object())
    except TypeError as exc:
        raise ImperativeStrategyProtocolError(
            "evaluate 必须能以 (stock, ctx) 两个位置参数调用。"
        ) from exc


def _declared_identity_or_metadata(implementation: object) -> tuple[str, object] | None:
    instance_values = vars(implementation) if hasattr(implementation, "__dict__") else {}
    for attribute in _IDENTITY_OR_METADATA_ATTRIBUTES:
        if attribute in instance_values:
            return attribute, instance_values[attribute]
    for owner in type(implementation).__mro__:
        owner_values = vars(owner)
        for attribute in _IDENTITY_OR_METADATA_ATTRIBUTES:
            if attribute in owner_values:
                return attribute, owner_values[attribute]
    return None


class DescriptorBoundImperativeStrategy:
    """Adapter that injects immutable descriptor identity into imperative output."""

    __slots__ = ("_implementation", "_universe", "descriptor")

    def __init__(self, implementation: ImperativeStrategy, descriptor: "StrategyDescriptor") -> None:
        validate_imperative_implementation(implementation)
        self._implementation = implementation
        self._universe = dict(cast(Mapping[str, Any], getattr(implementation, "universe", {})))
        self.descriptor = descriptor

    def evaluate(self, stock: dict[str, Any], ctx: "StrategyContext") -> Signal:
        context = dict(stock)
        industry = stock.get("industry")
        context["is_main_theme"] = bool(
            stock.get("is_main_theme")
            or (industry in set(ctx.main_industries) and industry != "未分类")
        )

        if not ctx.skip_universe_check and not _passes_universe({"universe": self._universe}, context):
            return self._assemble_signal(
                StrategyDecision(
                    score=0.0,
                    phase="排除",
                    hard_veto=True,
                    veto_reason="不在策略股票池",
                    risk_flags=["不符合股票池"],
                ),
                score=0.0,
                score_cap=100.0,
            )

        decision = self._implementation.evaluate(context, ctx)
        if not isinstance(decision, StrategyDecision):
            raise ImperativeStrategyProtocolError(
                f"命令式策略 '{self.descriptor.strategy_id}' 的 evaluate 必须返回 StrategyDecision。"
            )

        data_quality = dict(stock.get("data_quality") or {})
        missing_fields = set(data_quality.get("missing_fields") or [])
        data_completeness = float(
            stock.get("data_completeness_score") or data_quality.get("data_completeness_score") or 100.0
        )
        evidence_quality = float(
            stock.get("evidence_quality_score") or data_quality.get("evidence_quality_score") or 100.0
        )
        coverage_cap = _evidence_score_cap(
            sample_count=int(stock.get("market_sample_count") or 0),
            data_completeness=data_completeness,
            evidence_quality=evidence_quality,
            missing_fields=missing_fields,
        )
        score_cap = min(decision.score_cap, coverage_cap)
        risk_flags = list(decision.risk_flags)
        evidence = list(decision.evidence)
        if coverage_cap < 82:
            risk_flags.append("样本或证据覆盖不足，评分已降权")
            evidence.append(f"诚实性上限: {round(coverage_cap, 1)}")
        return self._assemble_signal(
            decision,
            score=max(0.0, min(decision.score, score_cap)),
            score_cap=score_cap,
            risk_flags=risk_flags,
            evidence=evidence,
        )

    def _assemble_signal(
        self,
        decision: StrategyDecision,
        *,
        score: float,
        score_cap: float,
        risk_flags: list[str] | None = None,
        evidence: list[str] | None = None,
    ) -> Signal:
        descriptor = self.descriptor
        return {
            "strategy_id": descriptor.strategy_id,
            "strategy_name": descriptor.name,
            "version_id": descriptor.effective_version,
            "strategy_independence": max(0.0, min(1.0, float(descriptor.strategy_independence))),
            "weight": round(float(descriptor.signal_weight), 3),
            "phase": decision.phase,
            "score": round(min(score, score_cap), 2),
            "score_cap": round(score_cap, 2),
            "confidence": round(min(0.95, max(0.2, score / 100)), 2),
            "setup_match": decision.setup_match,
            "entry_rule": decision.entry_rule,
            "invalid_rule": decision.invalid_rule,
            "entry_mode": decision.entry_mode,
            "setup_quality": decision.setup_quality,
            "hard_veto": decision.hard_veto,
            "veto_reason": decision.veto_reason,
            "risk_flags": list(decision.risk_flags if risk_flags is None else risk_flags),
            "evidence": list(decision.evidence if evidence is None else evidence),
        }


__all__ = [
    "DescriptorBoundImperativeStrategy",
    "ImperativeStrategy",
    "ImperativeStrategyProtocolError",
    "StrategyDecision",
    "validate_imperative_implementation",
]
