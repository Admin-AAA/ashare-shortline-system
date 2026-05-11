import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StrategySkill:
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
    def executable(self) -> bool:
        return self.contract is not None and bool(self.contract.get("contract", {}).get("executable", True))

    @property
    def agent_enabled(self) -> bool:
        return bool(self.skill_body.strip())


def load_skills(skills_dir: Path) -> list[StrategySkill]:
    if not skills_dir.exists():
        return []
    skills = []
    for path in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skills.append(load_skill(path))
    return skills


def load_skill(path: Path) -> StrategySkill:
    manifest_path = path / "manifest.json"
    strategy_path = path / "strategy.yaml"
    skill_path = path / "SKILL.md"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    skill_frontmatter, skill_body = _read_skill_md(skill_path) if skill_path.exists() else ({}, "")
    contract = _read_yaml(strategy_path) if strategy_path.exists() else None
    reference_files = _discover_reference_files(path)
    skill_id = manifest.get("id") or (contract or {}).get("id") or path.name
    name = manifest.get("name") or skill_frontmatter.get("displayName") or (contract or {}).get("name") or skill_id
    version = str(
        manifest.get("version")
        or skill_frontmatter.get("version")
        or (contract or {}).get("meta", {}).get("version")
        or "0.1.0"
    )
    skill_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest() if skill_path.exists() else None
    contract_hash = hashlib.sha256(strategy_path.read_bytes()).hexdigest() if strategy_path.exists() else None
    return StrategySkill(
        skill_id=skill_id,
        name=name,
        version=version,
        path=path,
        manifest=manifest,
        skill_frontmatter=skill_frontmatter,
        skill_body=skill_body,
        skill_hash=skill_hash,
        reference_files=reference_files,
        contract=contract,
        contract_hash=contract_hash,
        enabled=bool(manifest.get("enabled", (contract or {}).get("enabled", True))),
    )


def validate_skill(skill: StrategySkill) -> list[str]:
    errors: list[str] = []
    if not (skill.path / "SKILL.md").exists():
        errors.append("missing SKILL.md")
    if not skill.skill_body.strip():
        errors.append("empty SKILL.md body")
    if skill.contract is None:
        return errors
    if not skill.contract.get("id"):
        errors.append("strategy.yaml missing id")
    if not skill.contract.get("name"):
        errors.append("strategy.yaml missing name")
    if not isinstance(skill.contract.get("scoring", {}).get("rules", []), list):
        errors.append("scoring.rules must be a list")
    return errors


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
        files.append(
            {
                "path": rel,
                "size": item.stat().st_size,
                "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
            }
        )
    return files
