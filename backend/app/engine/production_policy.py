"""Versioned production selection policy and its public evaluation interface."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.engine.backtest import canonical_fingerprint


_POLICY_METADATA_FIELDS = frozenset({"policy_id", "version"})


@dataclass(frozen=True)
class ProductionSelectionPolicy:
    """All production rules that can remove an otherwise scored candidate."""

    policy_id: str = "production-selection"
    version: int = 1
    board_universe: str | None = None
    excluded_20cm_buckets: tuple[str, ...] = (
        "daily-stock-analysis-skill",
        "chao-gu-yang-jia-skill",
    )
    # 2026-08-02 用户决策：不保留 regime hard_veto（8/1 引入后 7 月 14 天被
    # 999 线全拦，候选最高分 88.7~100 零产出）。关闭后结构性强势/震荡日按
    # 普通分数线评估；如需恢复置 True（改 JSON 即生效，无需改代码）。
    structural_strong_hard_veto: bool = False
    oscillation_hard_veto: bool = False
    strong_score_penalty: float = 8.0
    strong_support_ma5_penalty: float = 6.0
    weak_low_chg20_threshold: float = 15.0
    weak_low_chg20_penalty: float = 12.0
    panic_limit_down_threshold: int = 100
    panic_score_penalty: float = 12.0
    market_base_score_adjustments: tuple[tuple[str, float], ...] = (
        ("强牛", 6.0), ("偏多", 0.0), ("震荡", 0.0), ("弱势", 2.0),
    )
    sentiment_score_adjustments: tuple[tuple[str, float], ...] = (
        ("高潮", 5.0), ("活跃", 3.0), ("平淡", 0.0), ("冰点", -3.0),
    )
    climax_nonweak_extra: float = 8.0
    score_line_minimum: float = 35.0
    score_line_maximum: float = 120.0
    # 弱势 regime 分数线封顶（2026-08-02 修复）：7 月弱市 formal 0 产出根因之一为
    # FORMAL_SCORE_LINE_FLOOR=72 + 弱势 +2 调整把线抬到 74，而弱市 gate 分普遍
    # 偏低（<58）——降线到 60 档让弱市反转信号有产出（诊断：弱势·高潮 78.1% WR）。
    # None = 不封顶（保持历史行为）。
    weak_regime_score_cap: float | None = 60.0
    dynamic_soft_floor_enabled: bool = True
    dynamic_soft_floor_offset: float = 1.0
    dynamic_soft_floor_base_discount: float = 8.0
    dynamic_soft_floor_max_gap: float = 20.0
    dynamic_soft_floor_disabled_regimes: tuple[str, ...] = ("震荡",)
    output_limit_enabled: bool = True
    daily_output_limit: int = 10
    climax_output_divisor: int = 2
    weak_ice_output_limit: int = 2
    # regime×情绪 组合的分数线硬覆盖（L-060）：优先级高于 market_base/sentiment/
    # climax 调整叠加。键为 "regime·sentiment"（如 "强牛·高潮"），值为固定分数线。
    # 用于精细放行已 OOS 验证正期望的组合（强牛·高潮/强牛·活跃/震荡·活跃用72），
    # 而保持负期望组合（偏多·高潮85）的拦截——单参数叠加无法表达这种二维区分。
    regime_sentiment_line_overrides: tuple[tuple[str, float], ...] = ()

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "board_universe": self.board_universe,
            "excluded_20cm_buckets": list(self.excluded_20cm_buckets),
            "structural_strong_hard_veto": self.structural_strong_hard_veto,
            "oscillation_hard_veto": self.oscillation_hard_veto,
            "strong_score_penalty": self.strong_score_penalty,
            "strong_support_ma5_penalty": self.strong_support_ma5_penalty,
            "weak_low_chg20_threshold": self.weak_low_chg20_threshold,
            "weak_low_chg20_penalty": self.weak_low_chg20_penalty,
            "panic_limit_down_threshold": self.panic_limit_down_threshold,
            "panic_score_penalty": self.panic_score_penalty,
            "market_base_score_adjustments": [list(item) for item in self.market_base_score_adjustments],
            "sentiment_score_adjustments": [list(item) for item in self.sentiment_score_adjustments],
            "climax_nonweak_extra": self.climax_nonweak_extra,
            "score_line_minimum": self.score_line_minimum,
            "score_line_maximum": self.score_line_maximum,
            "weak_regime_score_cap": self.weak_regime_score_cap,
            "dynamic_soft_floor_enabled": self.dynamic_soft_floor_enabled,
            "dynamic_soft_floor_offset": self.dynamic_soft_floor_offset,
            "dynamic_soft_floor_base_discount": self.dynamic_soft_floor_base_discount,
            "dynamic_soft_floor_max_gap": self.dynamic_soft_floor_max_gap,
            "dynamic_soft_floor_disabled_regimes": list(self.dynamic_soft_floor_disabled_regimes),
            "output_limit_enabled": self.output_limit_enabled,
            "daily_output_limit": self.daily_output_limit,
            "climax_output_divisor": self.climax_output_divisor,
            "weak_ice_output_limit": self.weak_ice_output_limit,
            "regime_sentiment_line_overrides": [list(item) for item in self.regime_sentiment_line_overrides],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    def with_rule_patch(self, rule_id: str, value: Any) -> "ProductionSelectionPolicy":
        aliases = {
            "board_universe": "board_universe",
            "excluded_strategy_buckets": "excluded_20cm_buckets",
            "structural_strong_hard_veto": "structural_strong_hard_veto",
            "oscillation_hard_veto": "oscillation_hard_veto",
        }
        field_name = aliases.get(str(rule_id), str(rule_id))
        if (
            field_name in _POLICY_METADATA_FIELDS
            or field_name not in PATCHABLE_PRODUCTION_RULE_IDS
        ):
            raise ValueError(f"unsupported production selection rule: {rule_id}")
        tuple_string_fields = {
            "excluded_20cm_buckets",
            "dynamic_soft_floor_disabled_regimes",
        }
        if field_name in tuple_string_fields:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{rule_id} patch must be a list")
            value = tuple(str(item) for item in value)
        if field_name in {"market_base_score_adjustments", "sentiment_score_adjustments", "regime_sentiment_line_overrides"}:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{rule_id} patch must be a list")
            value = tuple((str(key), float(amount)) for key, amount in value)
        return replace(self, version=self.version + 1, **{field_name: value})


# Explicitly document and constrain the fields that a one-rule experiment may
# change.  This prevents a caller from using a policy patch as an escape hatch
# for identity/fingerprint metadata or future non-selection fields.
PATCHABLE_PRODUCTION_RULE_IDS = frozenset(
    field_name
    for field_name in ProductionSelectionPolicy.__dataclass_fields__
    if field_name not in _POLICY_METADATA_FIELDS
)


_POLICY_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "policies"


def load_production_selection_policy(
    policy_id: str = "production-selection",
    version: int = 1,
) -> ProductionSelectionPolicy:
    """Load the repository-owned artifact used by production and backtests."""
    if policy_id != "production-selection" or version != 1:
        raise ValueError(f"unknown production selection policy: {policy_id}@{version}")
    path = _POLICY_ARTIFACT_DIR / "production_selection.v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("production selection policy artifact must be an object")
    payload["excluded_20cm_buckets"] = tuple(payload.get("excluded_20cm_buckets") or ())
    payload["market_base_score_adjustments"] = tuple(
        (str(key), float(value)) for key, value in payload.get("market_base_score_adjustments") or ()
    )
    payload["sentiment_score_adjustments"] = tuple(
        (str(key), float(value)) for key, value in payload.get("sentiment_score_adjustments") or ()
    )
    payload["dynamic_soft_floor_disabled_regimes"] = tuple(
        str(value) for value in payload.get("dynamic_soft_floor_disabled_regimes") or ()
    )
    payload["regime_sentiment_line_overrides"] = tuple(
        (str(key), float(value)) for key, value in payload.get("regime_sentiment_line_overrides") or ()
    )
    policy = ProductionSelectionPolicy(**payload)
    if policy.policy_id != policy_id or policy.version != version:
        raise ValueError("production selection policy artifact identity mismatch")
    return policy


DEFAULT_PRODUCTION_SELECTION_POLICY = load_production_selection_policy()


@dataclass(frozen=True)
class SelectionPolicyContext:
    score_line: float
    output_count: int
    market_snapshot: Mapping[str, Any]
    risk_template: str = "balanced"
    display_limit: int | None = None
    agent_available: bool = True
    same_day_industry_counts: Mapping[str, int] | None = None
    same_day_recommendation_count: int = 0


@dataclass(frozen=True)
class PolicyVerdictStep:
    rule_id: str
    action: str
    reason: str
    before_score_line: float | None = None
    after_score_line: float | None = None
    eliminated: bool = False


@dataclass(frozen=True)
class CandidateVerdict:
    code: str
    selected: bool
    drop_owner: str | None
    steps: tuple[PolicyVerdictStep, ...]


@dataclass(frozen=True)
class PolicyEvaluationResult:
    recommendations: tuple[Mapping[str, Any], ...]
    watchlist: tuple[Mapping[str, Any], ...]
    degraded_watchlist: tuple[Mapping[str, Any], ...]
    verdicts: tuple[CandidateVerdict, ...]
    policy_fingerprint: str
    verdict_summary: Mapping[str, int] = field(default_factory=dict)


def _verdict_for(
    card: Mapping[str, Any],
    recommended_codes: frozenset[str],
    base_score_line: float,
) -> CandidateVerdict:
    code = str(card.get("code") or "")
    meta = card.get("score_line_meta") or {}
    drop_owner = card.get("policy_drop_owner")
    steps = [
        PolicyVerdictStep(
            rule_id=str(item.get("rule_id") or "unknown"),
            action=str(item.get("action") or "observe"),
            reason=str(item.get("reason") or ""),
            before_score_line=item.get("before_score_line"),
            after_score_line=item.get("after_score_line"),
            eliminated=bool(item.get("eliminated") or item.get("action") == "drop"),
        )
        for item in (card.get("policy_verdict_steps") or [])
    ]
    if not drop_owner and meta.get("regime_struct_strong_hard_veto"):
        drop_owner = "structural_strong_hard_veto"
    if not drop_owner and (
        meta.get("regime_oscillation_hard_veto")
        or card.get("_regime_oscillation_hard_veto")
    ):
        drop_owner = "oscillation_hard_veto"
    if not drop_owner and code not in recommended_codes:
        drop_owner = (
            "portfolio_output_limit" if meta.get("passed") else "score_line"
        )
    effective = meta.get("effective_score_line")
    trace_rules = (
        ("strong_support_ma5_penalty", meta.get("regime_support_ma5_penalty")),
        ("strong_score_penalty", meta.get("regime_strong_penalty")),
        ("weak_low_chg20_penalty", meta.get("regime_weak_low_chg20_penalty")),
        ("panic_score_penalty", meta.get("panic_day_penalty")),
    )
    traced = {step.rule_id for step in steps}
    for rule_id, amount in trace_rules:
        if amount and rule_id not in traced:
            steps.append(
                PolicyVerdictStep(
                    rule_id=rule_id,
                    action="raise_score_line",
                    reason=f"effective score line +{float(amount):g}",
                    before_score_line=base_score_line,
                    after_score_line=float(effective) if effective is not None else None,
                )
            )
    if not steps or code in recommended_codes or drop_owner in {
        "score_line",
        "structural_strong_hard_veto",
        "oscillation_hard_veto",
    }:
        action = "select" if code in recommended_codes else "drop"
        steps.append(
            PolicyVerdictStep(
                rule_id=str(drop_owner or "score_line"),
                action=action,
                reason=str((meta.get("block_reason") or drop_owner or "selected")),
                before_score_line=float(meta.get("base_score_line") or base_score_line),
                after_score_line=float(effective) if effective is not None else None,
                eliminated=action == "drop",
            )
        )
    if isinstance(card, dict):
        if drop_owner:
            card["policy_drop_owner"] = str(drop_owner)
        card["policy_verdict_steps"] = [
            {
                "rule_id": step.rule_id,
                "action": step.action,
                "reason": step.reason,
                "before_score_line": step.before_score_line,
                "after_score_line": step.after_score_line,
                "eliminated": step.eliminated,
            }
            for step in steps
        ]
    return CandidateVerdict(
        code=code,
        selected=code in recommended_codes,
        drop_owner=str(drop_owner) if drop_owner else None,
        steps=tuple(steps),
    )


def evaluate_selection_policy(
    cards: list[dict[str, Any]],
    context: SelectionPolicyContext,
    policy: ProductionSelectionPolicy = DEFAULT_PRODUCTION_SELECTION_POLICY,
) -> PolicyEvaluationResult:
    """Evaluate every scored card once and return recommendations plus full verdicts."""

    from app.engine.aggregator import apply_pool_rules

    raw = apply_pool_rules(
        cards,
        output_count=context.output_count,
        score_line=context.score_line,
        market_snapshot=dict(context.market_snapshot),
        risk_template=context.risk_template,
        display_limit=context.display_limit,
        return_watchlist=True,
        agent_available=context.agent_available,
        same_day_industry_counts=(
            dict(context.same_day_industry_counts)
            if context.same_day_industry_counts is not None else None
        ),
        same_day_recommendation_count=context.same_day_recommendation_count,
        board_universe=policy.board_universe,
        selection_policy=policy,
    )
    recommendations = tuple(raw.get("recommendations") or ())
    selected_codes = frozenset(str(card.get("code") or "") for card in recommendations)
    verdicts = tuple(
        _verdict_for(card, selected_codes, context.score_line) for card in cards
    )
    summary: dict[str, int] = {}
    for item in verdicts:
        owner = item.drop_owner or "selected"
        summary[owner] = summary.get(owner, 0) + 1
    return PolicyEvaluationResult(
        recommendations=recommendations,
        watchlist=tuple(raw.get("watchlist") or ()),
        degraded_watchlist=tuple(raw.get("degraded_watchlist") or ()),
        verdicts=verdicts,
        policy_fingerprint=policy.fingerprint,
        verdict_summary=MappingProxyType(summary),
    )


__all__ = [
    "CandidateVerdict",
    "DEFAULT_PRODUCTION_SELECTION_POLICY",
    "PolicyEvaluationResult",
    "PolicyVerdictStep",
    "PATCHABLE_PRODUCTION_RULE_IDS",
    "ProductionSelectionPolicy",
    "SelectionPolicyContext",
    "evaluate_selection_policy",
    "load_production_selection_policy",
]
