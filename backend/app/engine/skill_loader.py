"""Manifest-backed StrategySkill loading and validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.engine.signal import ENTRY_MODE_TARGET_TOUCH, VALID_ENTRY_MODES


_VALID_MODES = frozenset({"declarative", "imperative"})
_VALID_STATUSES = frozenset({"active", "disabled", "deprecated"})


class SkillManifestError(ValueError):
    """A skill manifest cannot provide the canonical strategy descriptor contract."""


@dataclass(frozen=True)
class StrategySkill:
    """Raw strategy artifact whose business identity is owned by manifest.json.

    ``skill_id``/``name``/``version`` are retained as compatibility projections for
    existing callers. They are populated only from the validated manifest and never
    inferred from strategy.yaml, SKILL.md frontmatter, or the directory name.
    """

    skill_id: str
    name: str
    version: str
    path: Path
    manifest: dict[str, Any]
    skill_frontmatter: dict[str, Any]
    skill_body: str
    skill_hash: str | None
    reference_files: list[dict[str, Any]]
    contract: dict[str, Any] | None
    contract_hash: str | None
    enabled: bool

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def mode(self) -> str:
        return str(self.manifest["mode"])

    @property
    def status(self) -> str:
        return str(self.manifest["status"])

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.manifest["capabilities"])

    @property
    def requirements(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.manifest["requirements"])

    @property
    def deprecated(self) -> bool:
        return self.status == "deprecated"

    @property
    def deprecation_reason(self) -> str | None:
        value = self.manifest.get("deprecation_reason")
        return str(value) if value else None

    @property
    def replacement_strategy_id(self) -> str | None:
        value = self.manifest.get("replacement_strategy_id")
        return str(value) if value else None

    @property
    def executable(self) -> bool:
        if self.mode == "declarative":
            return self.contract is not None and bool(self.contract.get("contract", {}).get("executable", True))
        return self.impl_class is not None

    @property
    def agent_enabled(self) -> bool:
        return bool(self.skill_body.strip())

    @property
    def impl_class(self) -> str | None:
        value = self.manifest.get("impl_class")
        return str(value) if value else None


def load_skills(skills_dir: Path) -> list[StrategySkill]:
    if not skills_dir.exists():
        return []
    return [load_skill(path) for path in sorted(path for path in skills_dir.iterdir() if path.is_dir())]


def load_skill(path: Path) -> StrategySkill:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise SkillManifestError(f"策略目录 '{path}' 缺少唯一身份来源 manifest.json。")
    manifest = _validate_manifest(_read_json(manifest_path), manifest_path)
    strategy_path = path / "strategy.yaml"
    skill_path = path / "SKILL.md"
    skill_frontmatter, skill_body = _read_skill_md(skill_path) if skill_path.exists() else ({}, "")
    contract = _read_yaml(strategy_path) if strategy_path.exists() else None
    mode = str(manifest["mode"])
    if mode == "declarative" and contract is None:
        raise SkillManifestError(f"声明式策略 '{manifest['id']}' 缺少 strategy.yaml contract。")
    if mode == "imperative" and not manifest.get("impl_class"):
        raise SkillManifestError(f"命令式策略 '{manifest['id']}' 缺少 manifest.impl_class。")

    return StrategySkill(
        skill_id=str(manifest["id"]),
        name=str(manifest["name"]),
        version=str(manifest["version"]),
        path=path,
        manifest=manifest,
        skill_frontmatter=skill_frontmatter,
        skill_body=skill_body,
        skill_hash=hashlib.sha256(skill_path.read_bytes()).hexdigest() if skill_path.exists() else None,
        reference_files=_discover_reference_files(path),
        contract=contract,
        contract_hash=hashlib.sha256(strategy_path.read_bytes()).hexdigest() if strategy_path.exists() else None,
        enabled=bool(manifest["enabled"]),
    )


def validate_skill(skill: StrategySkill) -> list[str]:
    errors: list[str] = []
    try:
        _validate_manifest(skill.manifest, skill.path / "manifest.json")
    except SkillManifestError as exc:
        errors.append(str(exc))
    if not (skill.path / "SKILL.md").exists():
        errors.append("missing SKILL.md")
    if not skill.skill_body.strip():
        errors.append("empty SKILL.md body")
    if skill.mode == "declarative" and skill.contract is not None:
        if not skill.contract.get("id"):
            errors.append("strategy.yaml missing id")
        if not skill.contract.get("name"):
            errors.append("strategy.yaml missing name")
        if not isinstance(skill.contract.get("scoring", {}).get("rules", []), list):
            errors.append("scoring.rules must be a list")
        for setup in skill.contract.get("setups", []) or []:
            entry_mode = str(setup.get("entry_mode") or ENTRY_MODE_TARGET_TOUCH)
            if entry_mode not in VALID_ENTRY_MODES:
                errors.append(
                    f"setup '{setup.get('name', '')}' entry_mode must be one of "
                    f"{sorted(VALID_ENTRY_MODES)}"
                )
    return errors


def _validate_manifest(raw: Any, manifest_path: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkillManifestError(f"{manifest_path}: manifest 必须是 JSON object。")
    manifest = dict(raw)
    required_strings = ("id", "name", "version", "mode", "status")
    for field_name in required_strings:
        value = manifest.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise SkillManifestError(f"{manifest_path}: manifest.{field_name} 必须为非空字符串。")
        manifest[field_name] = value.strip()
    if manifest["mode"] not in _VALID_MODES:
        raise SkillManifestError(f"{manifest_path}: manifest.mode 必须是 declarative 或 imperative。")
    if manifest["status"] not in _VALID_STATUSES:
        raise SkillManifestError(f"{manifest_path}: manifest.status 必须是 active、disabled 或 deprecated。")
    if not isinstance(manifest.get("enabled"), bool):
        raise SkillManifestError(f"{manifest_path}: manifest.enabled 必须为布尔值（status 的兼容投影）。")
    expected_enabled = manifest["status"] == "active"
    if manifest["enabled"] is not expected_enabled:
        raise SkillManifestError(
            f"{manifest_path}: manifest.enabled 必须等于 status='{manifest['status']}' 的兼容投影。"
        )
    for field_name in ("requirements", "capabilities"):
        values = manifest.get(field_name)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise SkillManifestError(f"{manifest_path}: manifest.{field_name} 必须为字符串列表。")
        manifest[field_name] = [value.strip() for value in values]
    if manifest["status"] == "deprecated":
        reason = manifest.get("deprecation_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SkillManifestError(f"{manifest_path}: deprecated 策略必须提供 deprecation_reason。")
        manifest["deprecation_reason"] = reason.strip()
    for field_name in ("deprecation_reason", "replacement_strategy_id"):
        value = manifest.get(field_name)
        if value not in (None, "") and not isinstance(value, str):
            raise SkillManifestError(f"{manifest_path}: manifest.{field_name} 必须为字符串。")
    signal_metadata = manifest.get("signal_metadata")
    if signal_metadata is not None:
        if not isinstance(signal_metadata, dict):
            raise SkillManifestError(f"{manifest_path}: manifest.signal_metadata 必须为 JSON object。")
        for field_name in ("weight", "strategy_independence"):
            value = signal_metadata.get(field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SkillManifestError(
                    f"{manifest_path}: manifest.signal_metadata.{field_name} 必须为数字。"
                )
        manifest["signal_metadata"] = dict(signal_metadata)
    return manifest


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillManifestError(f"无法读取 manifest '{path}'：{exc}") from exc
    if not isinstance(data, dict):
        raise SkillManifestError(f"{path}: manifest 必须是 JSON object。")
    return data


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _read_skill_md(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    text = raw.strip()
    if not text.startswith("---"):
        return {}, raw
    end = text.find("---", 3)
    if end == -1:
        return {}, raw
    frontmatter_text = text[3:end].strip()
    body = text[end + 3 :].lstrip()
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
        if isinstance(frontmatter, dict):
            return frontmatter, body
    except yaml.YAMLError:
        pass
    return _parse_inline_frontmatter(frontmatter_text), body


def _parse_inline_frontmatter(text: str) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {}
    for key in ["name", "displayName", "version", "description"]:
        marker = f"{key}:"
        if marker not in text:
            continue
        start = text.find(marker) + len(marker)
        next_positions = [
            text.find(f" {candidate}:", start)
            for candidate in ["name", "displayName", "version", "description", "last_updated", "knowledge_cutoff"]
            if text.find(f" {candidate}:", start) != -1
        ]
        end = min(next_positions) if next_positions else len(text)
        frontmatter[key] = text[start:end].strip().strip('"')
    return frontmatter


def _discover_reference_files(path: Path) -> list[dict[str, Any]]:
    references_dir = path / "references"
    if not references_dir.exists() or not references_dir.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for item in sorted(references_dir.rglob("*")):
        if not item.is_file() or item.name.startswith("."):
            continue
        if item.suffix.lower() not in {".md", ".txt", ".yaml", ".yml", ".json"}:
            continue
        try:
            rel = item.relative_to(path).as_posix()
        except ValueError:
            rel = item.name
        files.append({
            "path": rel,
            "size": item.stat().st_size,
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
        })
    return files
