"""Pure diagnostics for the loaded strategy and preset registries.

This module deliberately consumes ``StrategySkill`` objects and serialized preset
facts.  It does not read ``strategy.yaml`` or repair a preset.  The loader remains
the single source of parsed strategy contracts; this module only checks the facts
that the loader and the preset registry already published.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.engine.skill_loader import StrategySkill
from app.services.registry.data_source import DEFAULT_PRESETS


@dataclass(frozen=True, slots=True)
class RegistryDiagnostic:
    """One deterministic registry configuration finding."""

    code: str
    message: str
    severity: str = "error"
    strategy_id: str | None = None
    preset_id: str | None = None
    group: str | None = None
    available_values: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "blocking": self.blocking,
            "strategy_id": self.strategy_id,
            "preset_id": self.preset_id,
            "group": self.group,
            "available_values": list(self.available_values),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class RegistryDiagnosticsReport:
    """Pure result of validating one loaded strategy/preset registry snapshot."""

    errors: tuple[RegistryDiagnostic, ...] = ()
    informational: tuple[RegistryDiagnostic, ...] = ()
    strategy_ids: tuple[str, ...] = ()
    preset_ids: tuple[str, ...] = ()
    available_preset_ids: tuple[str, ...] = ()
    declared_groups: tuple[str, ...] = ()
    configured_groups: tuple[str, ...] = ()
    strategy_presets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unassigned_strategy_ids: tuple[str, ...] = ()
    preset_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    executable_preset_ids: tuple[str, ...] = ()
    strategy_groups: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    active_executable_strategy_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def blocking_errors(self) -> tuple[RegistryDiagnostic, ...]:
        return self.errors

    @property
    def blockers(self) -> tuple[RegistryDiagnostic, ...]:
        return self.errors

    @property
    def warnings(self) -> tuple[RegistryDiagnostic, ...]:
        return self.informational

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.errors)

    @property
    def informational_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.informational)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.to_dict() for item in self.errors],
            "informational": [item.to_dict() for item in self.informational],
            "strategy_ids": list(self.strategy_ids),
            "preset_ids": list(self.preset_ids),
            "available_preset_ids": list(self.available_preset_ids),
            "declared_groups": list(self.declared_groups),
            "configured_groups": list(self.configured_groups),
            "strategy_presets": {
                strategy_id: list(preset_ids)
                for strategy_id, preset_ids in sorted(self.strategy_presets.items())
            },
            "unassigned_strategy_ids": list(self.unassigned_strategy_ids),
            "preset_groups": {
                preset_id: list(groups)
                for preset_id, groups in sorted(self.preset_groups.items())
            },
            "executable_preset_ids": list(self.executable_preset_ids),
            "strategy_groups": {
                strategy_id: list(groups)
                for strategy_id, groups in sorted(self.strategy_groups.items())
            },
            "active_executable_strategy_ids": list(self.active_executable_strategy_ids),
        }

    def require_strategy(self, strategy_id: str) -> None:
        normalized = str(strategy_id).strip()
        if normalized not in self.strategy_ids:
            raise UnknownRegistryValueError("strategy", normalized, self.strategy_ids)

    def require_preset(self, preset_id: str) -> None:
        normalized = str(preset_id).strip()
        if normalized not in self.preset_ids:
            raise UnknownRegistryValueError("preset", normalized, self.available_preset_ids)

    def require_group(self, group: str) -> None:
        normalized = str(group).strip()
        if normalized not in self.configured_groups:
            raise UnknownRegistryValueError("group", normalized, self.configured_groups)

    def require_executable(self, preset_id: str | None = None) -> None:
        """Reject a formal evaluation when its requested registry is unsafe.

        Global diagnostics remain visible in ``errors``.  A formal run only blocks
        on findings that can affect its requested preset; an unrelated experiment
        preset must not silently inject itself into the formal run or make the
        formal opening preset unusable.
        """

        normalized = str(preset_id or "").strip()
        errors: list[RegistryDiagnostic] = []
        if normalized and normalized not in self.preset_ids:
            errors.append(
                RegistryDiagnostic(
                    code="unknown_preset_id",
                    preset_id=normalized,
                    message=(
                        f"未知 preset_id '{normalized}'；可用值："
                        f"{_format_values(self.available_preset_ids)}"
                    ),
                    available_values=self.available_preset_ids,
                )
            )
        target_groups = set(self.preset_groups.get(normalized, ()))
        target_strategy_ids = {
            strategy_id
            for strategy_id, assigned in self.strategy_presets.items()
            if normalized in assigned
        }
        expected_target_ids = {
            strategy_id
            for strategy_id in self.active_executable_strategy_ids
            if set(self.strategy_groups.get(strategy_id, ())) & target_groups
        }
        for strategy_id in sorted(expected_target_ids - target_strategy_ids):
            errors.append(
                RegistryDiagnostic(
                    code="active_strategy_not_in_target_preset",
                    strategy_id=strategy_id,
                    preset_id=normalized,
                    message=(
                        f"active 策略 '{strategy_id}' 声明属于目标预设组，"
                        f"但未进入 preset '{normalized}'；可用策略："
                        f"{_format_values(target_strategy_ids)}"
                    ),
                    available_values=tuple(sorted(target_strategy_ids)),
                )
            )
        for issue in self.errors:
            if issue.code in {"duplicate_strategy_id", "duplicate_preset_id"}:
                errors.append(issue)
                continue
            if issue.preset_id and issue.preset_id != normalized:
                continue
            if issue.code in {
                "active_strategy_unknown_declared_group",
                "inactive_strategy_unknown_declared_group",
                "empty_executable_group",
                "imperative_strategy_declares_preset_group",
            }:
                if target_groups and issue.group not in target_groups:
                    continue
            if issue.code == "active_strategy_unassigned" and issue.strategy_id not in target_strategy_ids:
                continue
            errors.append(issue)

        if normalized and normalized in self.preset_ids and normalized not in self.executable_preset_ids:
            errors.append(
                RegistryDiagnostic(
                    code="empty_executable_preset",
                    preset_id=normalized,
                    message=f"preset '{normalized}' 没有 active 且 executable 的策略。",
                    available_values=self.strategy_ids,
                )
            )
        if errors:
            raise RegistryDiagnosticsError(
                RegistryDiagnosticsReport(
                    errors=tuple(errors),
                    informational=self.informational,
                    strategy_ids=self.strategy_ids,
                    preset_ids=self.preset_ids,
                    available_preset_ids=self.available_preset_ids,
                    declared_groups=self.declared_groups,
                    configured_groups=self.configured_groups,
                    strategy_presets=self.strategy_presets,
                    unassigned_strategy_ids=self.unassigned_strategy_ids,
                    preset_groups=self.preset_groups,
                    executable_preset_ids=self.executable_preset_ids,
                    strategy_groups=self.strategy_groups,
                    active_executable_strategy_ids=self.active_executable_strategy_ids,
                )
            )


class RegistryDiagnosticsError(ValueError):
    """A formal evaluation cannot proceed with an invalid registry snapshot."""

    def __init__(self, report: RegistryDiagnosticsReport) -> None:
        self.report = report
        summary = "; ".join(item.message for item in report.errors)
        super().__init__(f"策略/预设注册诊断失败：{summary}")


class UnknownRegistryValueError(ValueError):
    """An explicitly requested strategy, preset, or group is not registered."""

    def __init__(self, kind: str, value: str, available_values: Iterable[str]) -> None:
        self.kind = str(kind)
        self.value = str(value)
        self.available_values = tuple(sorted({str(item) for item in available_values}))
        label = {"strategy": "策略", "preset": "预设", "group": "预设组"}.get(self.kind, self.kind)
        super().__init__(
            f"未知{label} '{self.value}'；可用值：{_format_values(self.available_values)}"
        )


def build_registry_diagnostics(
    skills: Iterable[StrategySkill],
    presets: Iterable[Mapping[str, Any] | Any],
    *,
    canonical_presets: Iterable[Mapping[str, Any]] = DEFAULT_PRESETS,
) -> RegistryDiagnosticsReport:
    """Validate loaded strategy facts against loaded preset facts.

    ``canonical_presets`` is the existing static group-to-preset registry.  It is
    metadata only: this function never inserts, deletes, or changes DB preset rows.
    ``presets`` is normally the result of ``list_presets(db)`` and therefore
    represents what is actually executable in this process.
    """

    skill_rows = tuple(skills)
    preset_rows = tuple(_as_mapping(item) for item in presets)
    canonical_rows = tuple(dict(item) for item in canonical_presets)

    strategy_ids = tuple(sorted(str(skill.skill_id) for skill in skill_rows))
    preset_ids = tuple(sorted(str(item.get("preset_id") or "") for item in preset_rows if item.get("preset_id")))
    canonical_ids = tuple(
        sorted(str(item.get("preset_id") or "") for item in canonical_rows if item.get("preset_id"))
    )
    available_preset_ids = tuple(sorted(set(preset_ids) | set(canonical_ids)))

    errors: list[RegistryDiagnostic] = []
    informational: list[RegistryDiagnostic] = []
    _append_duplicates(
        errors,
        values=strategy_ids,
        code="duplicate_strategy_id",
        label="strategy_id",
        available_values=tuple(sorted(set(strategy_ids))),
    )
    _append_duplicates(
        errors,
        values=preset_ids,
        code="duplicate_preset_id",
        label="preset_id",
        available_values=tuple(sorted(set(preset_ids))),
    )

    declarations: dict[str, tuple[str, ...]] = {
        str(skill.skill_id): tuple(sorted(_declared_groups(skill))) for skill in skill_rows
    }
    declared_groups = tuple(sorted({group for groups in declarations.values() for group in groups}))
    configured_groups = tuple(
        sorted(
            {
                group
                for item in canonical_rows
                for group in _groups_from_preset(item)
            }
        )
    )
    declared_set = set(declared_groups)
    configured_set = set(configured_groups)
    preset_groups: dict[str, tuple[str, ...]] = {
        str(item.get("preset_id") or ""): _groups_from_preset(item)
        for item in canonical_rows
        if item.get("preset_id")
    }
    for item in preset_rows:
        preset_id = str(item.get("preset_id") or "")
        if preset_id and _groups_from_preset(item):
            preset_groups[preset_id] = _groups_from_preset(item)
    # Formal and experiment presets use the same check; formal errors receive a
    # dedicated code so the formal preflight can report the exact boundary.
    group_preset_rows = (*canonical_rows, *tuple(item for item in preset_rows if _groups_from_preset(item)))
    seen_group_references: set[tuple[str, str]] = set()
    for item in group_preset_rows:
        preset_id = str(item.get("preset_id") or "")
        for group in _groups_from_preset(item):
            reference = (preset_id, group)
            if reference in seen_group_references:
                continue
            seen_group_references.add(reference)
            if group in declared_set:
                continue
            code = "formal_preset_unknown_group" if _is_formal_preset(item) else "preset_unknown_group"
            errors.append(
                RegistryDiagnostic(
                    code=code,
                    preset_id=preset_id or None,
                    group=group,
                    message=(
                        f"preset '{preset_id}' 引用了未知预设组 '{group}'；"
                        f"可用值：{_format_values(declared_groups)}"
                    ),
                    available_values=declared_groups,
                )
            )

    # A strategy may declare a group that has no canonical executable preset.
    # Disabled/deprecated declarations are retained as informational findings so
    # a dormant strategy does not block today's formal run.
    for skill in skill_rows:
        skill_id = str(skill.skill_id)
        for group in declarations.get(skill_id, ()):
            if group in configured_set:
                continue
            issue = RegistryDiagnostic(
                code=(
                    "active_strategy_unknown_declared_group"
                    if _is_active(skill)
                    else "inactive_strategy_unknown_declared_group"
                ),
                severity="error" if _is_active(skill) else "info",
                strategy_id=skill_id,
                group=group,
                message=(
                    f"策略 '{skill_id}' 声明了未知预设组 '{group}'；"
                    f"可用值：{_format_values(configured_groups)}"
                ),
                available_values=configured_groups,
            )
            (errors if issue.blocking else informational).append(issue)

    # Groups with no runnable strategy are a configuration blocker.  Unknown
    # groups are already reported above and are not duplicated as empty groups.
    for group in configured_groups:
        members = [
            skill
            for skill in skill_rows
            if group in declarations.get(str(skill.skill_id), ())
            and _is_active(skill)
            and bool(skill.executable)
        ]
        if members:
            continue
        errors.append(
            RegistryDiagnostic(
                code="empty_executable_group",
                group=group,
                message=(
                    f"预设组 '{group}' 没有 active 且 executable 的策略；"
                    f"可用值：{_format_values(declared_groups)}"
                ),
                available_values=declared_groups,
            )
        )

    # 命令式技能（无 strategy.yaml 契约，``skill.executable`` 仍为真）不得声明进入
    # 选股预设组：研究/重放口径要求 ``skill.contract`` 才能同构复现候选与打分
    # （``ProductionRuleReplayCandidateBuilder._selected_skills`` 直接跳过无契约技能），
    # 因此 imperative 技能一旦进入选股预设，"研究三窗通过、生产重建亏损"会静默并存。
    # 证据：2026-09-18 高位放量滞涨预警技能混入 preset_formal_opening，生产重建三窗
    # Σ−66.36 而同一政策/权重/窗口的研究 A 臂 Σ+116.26（见 L-132）。
    for skill in skill_rows:
        skill_id = str(skill.skill_id)
        groups = declarations.get(skill_id, ())
        if not groups or not _is_active(skill) or str(skill.mode).lower() != "imperative":
            continue
        for group in groups:
            errors.append(
                RegistryDiagnostic(
                    code="imperative_strategy_declares_preset_group",
                    strategy_id=skill_id,
                    group=group,
                    message=(
                        f"命令式策略 '{skill_id}' 声明进入选股预设组 '{group}'，但它没有 "
                        f"strategy.yaml 契约，研究/重放口径无法同构复现（L-132）。"
                    ),
                    available_values=declared_groups,
                )
            )

    # Explicit DB references must point to a loaded strategy identity.  The
    # registry is observed exactly as-is; no missing row is synthesized.
    known_strategy_ids = set(strategy_ids)
    explicit_memberships: dict[str, set[str]] = {strategy_id: set() for strategy_id in known_strategy_ids}
    executable_preset_ids: set[str] = set()
    for item in preset_rows:
        preset_id = str(item.get("preset_id") or "")
        referenced = _preset_skill_ids(item)
        executable_reference = False
        for strategy_id in referenced:
            if strategy_id not in known_strategy_ids:
                errors.append(
                    RegistryDiagnostic(
                        code="preset_references_unknown_strategy",
                        preset_id=preset_id or None,
                        strategy_id=strategy_id,
                        message=(
                            f"preset '{preset_id}' 显式引用不存在策略 '{strategy_id}'；"
                            f"可用值：{_format_values(strategy_ids)}"
                        ),
                        available_values=strategy_ids,
                    )
                )
                continue
            explicit_memberships.setdefault(strategy_id, set()).add(preset_id)
            skill = next(skill for skill in skill_rows if str(skill.skill_id) == strategy_id)
            if not _is_active(skill) or not bool(skill.executable):
                errors.append(
                    RegistryDiagnostic(
                        code="preset_references_unavailable_strategy",
                        preset_id=preset_id or None,
                        strategy_id=strategy_id,
                        message=(
                            f"preset '{preset_id}' 引用了不可执行策略 '{strategy_id}'；"
                            f"可用策略：{_format_values(strategy_ids)}"
                        ),
                        available_values=strategy_ids,
                        details={"status": str(skill.status), "executable": bool(skill.executable)},
                    )
                )
            if _is_active(skill) and bool(skill.executable) and _is_active_preset(item):
                executable_reference = True
        if executable_reference:
            executable_preset_ids.add(preset_id)

    # Active strategies must be visible in at least one actual executable preset.
    # This is intentionally based on explicit registry rows, not declarations in
    # strategy.yaml; declarations do not auto-register a strategy.
    for skill in skill_rows:
        skill_id = str(skill.skill_id)
        if not _is_active(skill) or not bool(skill.executable):
            if not explicit_memberships.get(skill_id):
                informational.append(
                    RegistryDiagnostic(
                        code="inactive_strategy_unregistered",
                        severity="info",
                        strategy_id=skill_id,
                        message=f"非 active 策略 '{skill_id}' 未注册到任何 preset（仅提示）。",
                        available_values=strategy_ids,
                    )
                )
            continue
        if not (explicit_memberships.get(skill_id) & executable_preset_ids):
            errors.append(
                RegistryDiagnostic(
                    code="active_strategy_unassigned",
                    strategy_id=skill_id,
                    message=(
                        f"active 策略 '{skill_id}' 未进入任何 executable preset；"
                        f"可用 preset：{_format_values(preset_ids)}"
                    ),
                    available_values=preset_ids,
                )
            )

    def _issue_key(item: RegistryDiagnostic) -> tuple[str, str, str, str, str]:
        return (
            item.code,
            str(item.strategy_id or ""),
            str(item.preset_id or ""),
            str(item.group or ""),
            item.message,
        )

    errors.sort(key=_issue_key)
    informational.sort(key=_issue_key)
    strategy_presets = {
        strategy_id: tuple(sorted(preset_ids_for_strategy))
        for strategy_id, preset_ids_for_strategy in sorted(explicit_memberships.items())
    }
    unassigned_strategy_ids = tuple(
        sorted(
            str(skill.skill_id)
            for skill in skill_rows
            if _is_active(skill)
            and bool(skill.executable)
            and not (explicit_memberships.get(str(skill.skill_id), set()) & executable_preset_ids)
        )
    )

    return RegistryDiagnosticsReport(
        errors=tuple(errors),
        informational=tuple(informational),
        strategy_ids=strategy_ids,
        preset_ids=preset_ids,
        available_preset_ids=available_preset_ids,
        declared_groups=declared_groups,
        configured_groups=configured_groups,
        strategy_presets=strategy_presets,
        unassigned_strategy_ids=unassigned_strategy_ids,
        preset_groups=preset_groups,
        executable_preset_ids=tuple(sorted(executable_preset_ids)),
        strategy_groups=declarations,
        active_executable_strategy_ids=tuple(
            sorted(
                str(skill.skill_id)
                for skill in skill_rows
                if _is_active(skill) and bool(skill.executable)
            )
        ),
    )


# Keep the names used by the P0-3 implementation brief concise and discoverable.
diagnose_registry = build_registry_diagnostics
build_strategy_registration_report = build_registry_diagnostics
validate_strategy_registration = build_registry_diagnostics
StrategyRegistrationReport = RegistryDiagnosticsReport


def _append_duplicates(
    target: list[RegistryDiagnostic],
    *,
    values: Sequence[str],
    code: str,
    label: str,
    available_values: tuple[str, ...],
) -> None:
    for value, count in sorted(Counter(values).items()):
        if count < 2 or not value:
            continue
        target.append(
            RegistryDiagnostic(
                code=code,
                message=(
                    f"{label} '{value}' 重复注册 {count} 次；"
                    f"可用值：{_format_values(available_values)}"
                ),
                strategy_id=value if label == "strategy_id" else None,
                preset_id=value if label == "preset_id" else None,
                available_values=available_values,
                details={"count": count},
            )
        )


def _as_mapping(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    result: dict[str, Any] = {}
    for key in ("preset_id", "status", "skills", "run_mode"):
        if hasattr(value, key):
            result[key] = getattr(value, key)
    return result


def _groups_from_preset(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw = item.get("skill_groups") or ()
    if isinstance(raw, str):
        raw = (raw,)
    return tuple(sorted({str(group).strip() for group in raw if str(group).strip()}))


def _preset_skill_ids(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw = item.get("skills") or item.get("strategy_ids") or ()
    if isinstance(raw, Mapping):
        raw = (raw,)
    ids: list[str] = []
    for row in raw:
        if isinstance(row, Mapping):
            value = row.get("strategy_id") or row.get("skill_id")
        else:
            value = row
        if value is not None and str(value).strip():
            ids.append(str(value).strip())
    return tuple(ids)


def _declared_groups(skill: StrategySkill) -> set[str]:
    groups: set[str] = set()
    for source in (skill.contract, skill.manifest):
        meta = source.get("meta") if isinstance(source, Mapping) else None
        raw = meta.get("default_preset_groups") if isinstance(meta, Mapping) else None
        if isinstance(raw, Mapping):
            groups.update(str(group).strip() for group in raw if str(group).strip())
    return groups


def _is_active(skill: StrategySkill) -> bool:
    return str(skill.status).lower() == "active" and bool(skill.enabled)


def _is_active_preset(item: Mapping[str, Any]) -> bool:
    return str(item.get("status") or "active").lower() == "active"


def _is_formal_preset(item: Mapping[str, Any]) -> bool:
    preset_id = str(item.get("preset_id") or "")
    return str(item.get("run_mode") or "").lower() == "formal" or preset_id.startswith("preset_formal_")


def _format_values(values: Iterable[str]) -> str:
    normalized = tuple(sorted({str(value) for value in values if str(value)}))
    return ", ".join(normalized) if normalized else "（无）"


__all__ = [
    "RegistryDiagnostic",
    "RegistryDiagnosticsError",
    "RegistryDiagnosticsReport",
    "StrategyRegistrationReport",
    "UnknownRegistryValueError",
    "build_registry_diagnostics",
    "build_strategy_registration_report",
    "diagnose_registry",
    "validate_strategy_registration",
]
