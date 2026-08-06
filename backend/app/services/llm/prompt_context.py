"""Deterministic, versioned context projection for single-call skill reviews."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Mapping, Sequence

from app.engine.canonical import CanonicalValue, canonical_fingerprint, canonical_json


PROMPT_CONTEXT_SCHEMA_VERSION = 1
PROMPT_CONTEXT_POLICY_ID = "skill-review-context-v1"
_ALLOWED_SECTIONS = (
    "market_snapshot",
    "review_identity",
    "rule_signal",
    "research",
    "stock",
)
_BUSINESS_SORT_KEYS = (
    "trade_date",
    "date",
    "code",
    "security_code",
    "id",
    "name",
    "title",
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?:\n\n|\n|[。！？.!?](?=\s|$))")
_DROP = object()


MARKET_SNAPSHOT_FIELDS = (
    "trade_date",
    "sample_count",
    "up_count",
    "down_count",
    "limit_up_count",
    "limit_down_count",
    "main_industries",
    "breadth",
)
RULE_SIGNAL_FIELDS = (
    "phase",
    "score",
    "dsa_signal_score",
    "setup_match",
    "evidence",
    "risk_flags",
    "score_cap",
    "runtime",
)
RESEARCH_FIELDS = ("summary", "records", "metrics", "notes")
STOCK_FIELDS = (
    "code", "name", "industry", "price", "pct",
    "turnover_rate", "amount_yi", "free_mv_yi",
    "dist20", "dist60", "ma5", "ma10", "ma20", "ma60",
    "vol5_20", "off_high_pct", "max_dd20",
    "upper_shadow_count20", "limit_up_days20",
    "primary_excess20", "primary_excess60",
    "industry_capacity_like_count", "industry_positive_excess20_count",
    "data_source", "quote_source", "kline_source",
    "latest_trade_date", "data_quality",
    "is_main_theme", "is_shortline_leader_candidate", "shortline_leader_score",
    "market_sentiment_phase",
    "limit_up_overheat_score", "limit_up_overheat_level",
    "sector_ladder_completeness_score", "sector_ladder_level",
    "sector_ladder_limit_up_count", "sector_ladder_height",
    "capital_resonance_score", "capital_resonance_signal", "capital_resonance_source",
    "is_20cm_stock", "twenty_cm_risk_score", "twenty_cm_risk_gate", "twenty_cm_risk_reason",
    "dragon_tiger_buy_dispersion", "dragon_tiger_inst_buy_ratio",
    "shortline_factor_explanations",
    "is_limit_up", "limit_up_consecutive_days", "limit_up_open_times",
    "limit_up_fd_amount_yi", "limit_up_limit_amount_yi", "limit_up_turnover_ratio",
    "limit_up_first_time", "limit_up_last_time",
    "concept_sector_name", "concept_sector_heat_rank",
    "dragon_tiger_is_on_list", "dragon_tiger_recent_count",
    "main_net_inflow_yi", "main_net_inflow_ratio",
    "super_large_net_inflow_yi", "large_net_inflow_yi", "small_net_inflow_yi",
    "latest_news", "news_risk_count", "positive_catalyst_count",
    "support_ma5", "support_ma10", "dsa_support_score",
    "dsa_trend_score", "dsa_bias_score", "dsa_volume_score",
    "dsa_macd_score", "dsa_rsi_score", "dsa_signal_score",
    "trend_status", "ma_alignment", "macd_status", "macd_signal",
    "macd_dif", "macd_dea", "macd_bar",
    "rsi_6", "rsi_12", "rsi_24", "rsi_status", "rsi_signal",
    "bias_ma5", "bias_ma10", "bias_ma20",
    "volume_status", "volume_trend", "volume_ratio_5d",
    "volume_ratio_vs_5d", "volume_ratio_vs_20d",
    "volume_breakout_signal", "volume_shrink_pullback",
    "pos20", "pos60", "pos120", "chg20", "chg60", "breakout20", "breakout60",
)


class PromptContextTooLarge(ValueError):
    """Required context cannot fit the configured policy without corruption."""

    def __init__(self, *, policy_id: str, estimated_tokens: int, max_tokens: int) -> None:
        self.policy_id = policy_id
        self.estimated_tokens = estimated_tokens
        self.max_tokens = max_tokens
        super().__init__(
            f"prompt context exceeds policy {policy_id}: "
            f"estimated_tokens={estimated_tokens}, max_tokens={max_tokens}"
        )


@dataclass(frozen=True, slots=True)
class PromptFieldRule(CanonicalValue):
    section: str
    field: str
    priority: int = 50
    max_items: int = 8
    max_text_chars: int = 1200
    required: bool = False

    def __post_init__(self) -> None:
        if self.section not in _ALLOWED_SECTIONS:
            raise ValueError(f"unknown prompt context section: {self.section}")
        if not self.field or "." in self.field:
            raise ValueError("prompt context field must be a non-empty top-level name")
        if self.priority < 0:
            raise ValueError("prompt context field priority must be non-negative")
        if self.max_items < 1 or self.max_text_chars < 1:
            raise ValueError("prompt context field limits must be positive")

    @property
    def path(self) -> str:
        return f"{self.section}.{self.field}"


@dataclass(frozen=True, slots=True)
class PromptContextPolicy(CanonicalValue):
    policy_id: str
    schema_version: int
    section_order: tuple[str, ...]
    field_rules: tuple[PromptFieldRule, ...]
    max_estimated_tokens: int
    required_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.policy_id or self.schema_version < 1:
            raise ValueError("prompt context policy identity is invalid")
        if self.max_estimated_tokens < 1:
            raise ValueError("prompt context token limit must be positive")
        if len(set(self.section_order)) != len(self.section_order):
            raise ValueError("prompt context sections must be unique")
        unknown_sections = set(self.section_order) - set(_ALLOWED_SECTIONS)
        if unknown_sections:
            raise ValueError(f"unknown prompt context sections: {sorted(unknown_sections)}")
        paths = [rule.path for rule in self.field_rules]
        if len(set(paths)) != len(paths):
            raise ValueError("prompt context field rules must be unique")
        if any(rule.section not in self.section_order for rule in self.field_rules):
            raise ValueError("prompt context field rule references an excluded section")
        unknown_required = set(self.required_paths) - set(paths)
        if unknown_required:
            raise ValueError(f"unknown required prompt paths: {sorted(unknown_required)}")
        declared_required = {rule.path for rule in self.field_rules if rule.required}
        if declared_required != set(self.required_paths):
            raise ValueError("required prompt paths and required field rules must match")


@dataclass(frozen=True, slots=True)
class PromptTruncationAudit(CanonicalValue):
    original_estimated_tokens: int
    projected_estimated_tokens: int
    removed_sections: tuple[str, ...]
    removed_fields: tuple[str, ...]
    removed_list_items: int
    truncated_text_fields: tuple[str, ...]
    preserved_input_order_paths: tuple[str, ...]
    truncation_reason: str | None


@dataclass(frozen=True, slots=True)
class PromptContextProjection(CanonicalValue):
    policy_id: str
    policy_schema_version: int
    original_fingerprint: str
    projected_context: Mapping[str, Any]
    projected_fingerprint: str
    audit: PromptTruncationAudit

    def section(self, name: str) -> Mapping[str, Any]:
        value = self.projected_context.get(name, {})
        return value if isinstance(value, Mapping) else {}

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema_version": PROMPT_CONTEXT_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "policy_schema_version": self.policy_schema_version,
            "original_fingerprint": self.original_fingerprint,
            "projected_fingerprint": self.projected_fingerprint,
            **self.audit.canonical_payload(),
        }


@dataclass(slots=True)
class _AuditState:
    removed_fields: set[str]
    removed_sections: set[str]
    removed_list_items: int
    truncated_text_fields: set[str]
    preserved_input_order_paths: set[str]


def _rule(
    section: str,
    field: str,
    *,
    priority: int = 50,
    max_items: int = 8,
    max_text_chars: int = 1200,
    required: bool = False,
) -> PromptFieldRule:
    return PromptFieldRule(
        section=section,
        field=field,
        priority=priority,
        max_items=max_items,
        max_text_chars=max_text_chars,
        required=required,
    )


def _default_field_rules() -> tuple[PromptFieldRule, ...]:
    required = {
        "market_snapshot.trade_date",
        "review_identity.skill_id",
        "stock.code",
        "stock.name",
        "stock.price",
        "stock.pct",
    }
    low_priority_stock = {
        "data_source", "quote_source", "kline_source", "latest_news",
        "shortline_factor_explanations", "capital_resonance_source",
    }
    high_priority_stock = {
        "data_quality", "dsa_signal_score", "support_ma5", "support_ma10",
        "ma5", "ma10", "ma20", "ma60", "rsi_6", "rsi_12", "rsi_24",
        "macd_dif", "macd_dea", "macd_bar", "bias_ma5", "bias_ma10",
        "bias_ma20", "turnover_rate", "amount_yi", "pos20", "pos60",
        "chg20", "off_high_pct", "max_dd20",
    }
    rules: list[PromptFieldRule] = []
    for field in MARKET_SNAPSHOT_FIELDS:
        path = f"market_snapshot.{field}"
        rules.append(_rule("market_snapshot", field, priority=85, required=path in required))
    rules.append(_rule("review_identity", "skill_id", priority=100, required=True, max_text_chars=160))
    for field in RULE_SIGNAL_FIELDS:
        path = f"rule_signal.{field}"
        rules.append(
            _rule(
                "rule_signal",
                field,
                priority=90 if field in {"evidence", "risk_flags", "dsa_signal_score"} else 100,
                required=path in required,
            )
        )
    for field in RESEARCH_FIELDS:
        rules.append(_rule("research", field, priority=10, max_items=5, max_text_chars=800))
    for field in STOCK_FIELDS:
        path = f"stock.{field}"
        priority = 20 if field in low_priority_stock else 85 if field in high_priority_stock else 60
        if path in required:
            priority = 100
        rules.append(_rule("stock", field, priority=priority, required=path in required))
    return tuple(rules)


_DEFAULT_RULES = _default_field_rules()
DEFAULT_PROMPT_CONTEXT_POLICY = PromptContextPolicy(
    policy_id=PROMPT_CONTEXT_POLICY_ID,
    schema_version=PROMPT_CONTEXT_SCHEMA_VERSION,
    section_order=_ALLOWED_SECTIONS,
    field_rules=_DEFAULT_RULES,
    max_estimated_tokens=4500,
    required_paths=tuple(rule.path for rule in _DEFAULT_RULES if rule.required),
)


def estimate_prompt_tokens(value: Any) -> int:
    """Deterministic estimate used only for context trimming, never billing."""
    return max(1, math.ceil(len(canonical_json(value)) / 1.8))


def _primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("prompt context does not support non-finite floats")
        return 0.0 if value == 0 else value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    raise TypeError(f"unsupported prompt context value: {type(value).__name__}")


def _semantic_prefix(text: str, max_chars: int) -> str | None:
    if len(text) <= max_chars:
        return text
    boundaries = [match.end() for match in _SENTENCE_BOUNDARY_RE.finditer(text[: max_chars + 1])]
    if not boundaries:
        return None
    prefix = text[: boundaries[-1]].rstrip()
    return prefix or None


def _list_sort_key(item: Any) -> tuple[str, ...] | None:
    if not isinstance(item, Mapping):
        return None
    values = tuple(str(item.get(key) or "") for key in _BUSINESS_SORT_KEYS)
    return values if any(values) else None


def _sort_list_items(items: list[Any]) -> list[Any]:
    """按稳定业务键排序；无业务键时保持输入顺序（确定性 canonical 步骤）。"""
    sort_keys = [_list_sort_key(item) for item in items]
    if items and all(key is not None for key in sort_keys):
        return [item for _, item in sorted(zip(sort_keys, items), key=lambda pair: pair[0])]
    return items


def _canonicalize_lists(value: Any) -> Any:
    """递归地把所有 dict 列表按业务键排序（无条件，保证顺序无关的指纹一致）。"""
    if isinstance(value, Mapping):
        return {str(key): _canonicalize_lists(item) for key, item in value.items()}
    if isinstance(value, list):
        return _sort_list_items([_canonicalize_lists(item) for item in value])
    return value


def _truncate_value(
    value: Any,
    *,
    rule: PromptFieldRule,
    path: str,
    state: _AuditState,
) -> Any:
    if isinstance(value, str):
        prefix = _semantic_prefix(value, rule.max_text_chars)
        if prefix is None:
            if rule.required:
                return value
            # 无可用句子/段落边界：按 max_text_chars 保留前缀（字符边界）并标记截断，
            # 不整段丢弃，避免大段无标点文本（如长新闻摘要）被静默清空。
            state.truncated_text_fields.add(path)
            return value[: rule.max_text_chars].rstrip()
        if prefix != value:
            state.truncated_text_fields.add(path)
        return prefix
    if isinstance(value, list):
        items = list(value)
        sort_keys = [_list_sort_key(item) for item in items]
        if items and all(key is not None for key in sort_keys):
            items = [item for _, item in sorted(zip(sort_keys, items), key=lambda pair: pair[0])]
        elif len(items) > 1:
            state.preserved_input_order_paths.add(path)
        if len(items) > rule.max_items:
            state.removed_list_items += len(items) - rule.max_items
            items = items[: rule.max_items]
        projected: list[Any] = []
        for index, item in enumerate(items):
            clean = _truncate_value(item, rule=rule, path=f"{path}[{index}]", state=state)
            if clean is _DROP:
                state.removed_list_items += 1
            else:
                projected.append(clean)
        return projected
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key in sorted(value):
            clean = _truncate_value(value[key], rule=rule, path=f"{path}.{key}", state=state)
            if clean is not _DROP:
                projected[str(key)] = clean
        return projected
    return value


def project_prompt_context(
    *,
    stock: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    rule_signal: Mapping[str, Any] | None,
    research: Mapping[str, Any] | None,
    skill_id: str,
    policy: PromptContextPolicy = DEFAULT_PROMPT_CONTEXT_POLICY,
) -> PromptContextProjection:
    """Whitelist, canonicalize and semantically trim one review context."""
    skill_id = str(skill_id or "").strip()
    code = str((stock or {}).get("code") or "").strip()
    if not skill_id or not code:
        raise ValueError("prompt context requires non-empty skill_id and stock.code")

    sources: dict[str, Mapping[str, Any]] = {
        "market_snapshot": snapshot or {},
        "review_identity": {"skill_id": skill_id},
        "rule_signal": rule_signal or {"runtime": "agent_only"},
        "research": research or {},
        "stock": stock or {},
    }
    rules_by_section: dict[str, list[PromptFieldRule]] = {name: [] for name in policy.section_order}
    for rule in policy.field_rules:
        rules_by_section[rule.section].append(rule)

    state = _AuditState(set(), set(), 0, set(), set())
    original: dict[str, dict[str, Any]] = {}
    for section in policy.section_order:
        source = sources[section]
        allowed = {rule.field for rule in rules_by_section[section]}
        for field in source:
            if field not in allowed:
                state.removed_fields.add(f"{section}.{field}")
        section_value: dict[str, Any] = {}
        for rule in rules_by_section[section]:
            if rule.field in source:
                section_value[rule.field] = _primitive(source[rule.field])
            # required 缺失时不注入 null：由下游 required 校验失败或后续保守处理决定，
            # 避免把「缺规则评分」伪装成「runtime 为 null」的错误信号。
        if section_value or any(rule.required for rule in rules_by_section[section]):
            original[section] = section_value
        elif source:
            state.removed_sections.add(section)

    # 列表按业务键排序是无条件 canonical 步骤（白名单后、指纹前），保证
    # 顺序无关的等价列表得到相同 fingerprint。截断/审计仍在超限路径单独处理。
    original = _canonicalize_lists(original)

    original_fingerprint = canonical_fingerprint(
        {"policy_id": policy.policy_id, "context": original}
    )
    original_tokens = estimate_prompt_tokens(original)
    # 初始构建：白名单 + canonicalize，保留原值语义。直接的句子/列表截断不在此
    # 无条件执行——只在超限时（下方淘汰循环）触发，避免额度内过度收紧默认审查语义。
    projected: dict[str, dict[str, Any]] = {}
    for section in policy.section_order:
        source = original.get(section, {})
        if source:
            projected[section] = dict(source)

    removable = sorted(
        (rule for rule in policy.field_rules if not rule.required),
        key=lambda rule: (rule.priority, policy.section_order.index(rule.section), rule.field),
    )
    for rule in removable:
        if estimate_prompt_tokens(projected) <= policy.max_estimated_tokens:
            break
        section_value = projected.get(rule.section)
        if not section_value or rule.field not in section_value:
            continue
        # 超限时：先尝试在语义边界裁剪该字段；若裁剪后仍超限，下一轮整字段移除。
        current = section_value[rule.field]
        trimmed = _truncate_value(current, rule=rule, path=rule.path, state=state)
        if trimmed is not _DROP and trimmed != current:
            section_value[rule.field] = trimmed
            if estimate_prompt_tokens(projected) <= policy.max_estimated_tokens:
                continue
        # 仍超限（或不可裁剪）：整字段移除。
        del section_value[rule.field]
        state.removed_fields.add(rule.path)
        if not section_value:
            del projected[rule.section]
            state.removed_sections.add(rule.section)

    projected_tokens = estimate_prompt_tokens(projected)
    if projected_tokens > policy.max_estimated_tokens:
        raise PromptContextTooLarge(
            policy_id=policy.policy_id,
            estimated_tokens=projected_tokens,
            max_tokens=policy.max_estimated_tokens,
        )
    missing_required = [
        path for path in policy.required_paths
        if path.split(".", 1)[0] not in projected
        or path.split(".", 1)[1] not in projected[path.split(".", 1)[0]]
    ]
    if missing_required:
        raise RuntimeError(f"prompt context lost required paths: {missing_required}")

    projected_fingerprint = canonical_fingerprint(
        {"policy_id": policy.policy_id, "context": projected}
    )
    truncated = bool(
        state.removed_fields
        or state.removed_sections
        or state.removed_list_items
        or state.truncated_text_fields
    )
    audit = PromptTruncationAudit(
        original_estimated_tokens=original_tokens,
        projected_estimated_tokens=projected_tokens,
        removed_sections=tuple(sorted(state.removed_sections)),
        removed_fields=tuple(sorted(state.removed_fields)),
        removed_list_items=state.removed_list_items,
        truncated_text_fields=tuple(sorted(state.truncated_text_fields)),
        preserved_input_order_paths=tuple(sorted(state.preserved_input_order_paths)),
        truncation_reason="policy_limits" if truncated else None,
    )
    return PromptContextProjection(
        policy_id=policy.policy_id,
        policy_schema_version=policy.schema_version,
        original_fingerprint=original_fingerprint,
        projected_context=projected,
        projected_fingerprint=projected_fingerprint,
        audit=audit,
    )


def canonical_section_json(projection: PromptContextProjection, section: str) -> str:
    return canonical_json(projection.section(section))


__all__ = [
    "DEFAULT_PROMPT_CONTEXT_POLICY",
    "MARKET_SNAPSHOT_FIELDS",
    "PROMPT_CONTEXT_POLICY_ID",
    "PROMPT_CONTEXT_SCHEMA_VERSION",
    "PromptContextPolicy",
    "PromptContextProjection",
    "PromptContextTooLarge",
    "PromptFieldRule",
    "PromptTruncationAudit",
    "RESEARCH_FIELDS",
    "RULE_SIGNAL_FIELDS",
    "STOCK_FIELDS",
    "canonical_section_json",
    "estimate_prompt_tokens",
    "project_prompt_context",
]
