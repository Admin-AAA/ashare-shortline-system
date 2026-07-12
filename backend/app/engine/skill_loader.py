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
        """该 skill 能否被策略分发管线执行（对应 ``strategy_registry.get(...).evaluate``
        是否会被调用）。

        声明式路径（有 ``strategy.yaml``）沿用原有语义：``contract`` 存在且未被
        ``contract.executable`` 显式关闭。命令式路径（``impl_class`` 声明，
        ``contract is None``）本任务（3.4，需求 4.1/4.2）新增：只要声明了
        ``impl_class`` 就视为可执行——命令式 skill 没有 ``strategy.yaml``，
        "executable" 的判断依据从「合同是否放行」换成「是否能被装配出一个
        ``Strategy`` 实例」，两者殊途同归，都是"这个 skill_id 在
        ``strategy_registry`` 里能拿到一个可 evaluate 的实例"这件事的谓词。
        没有这一步，``workbench_service``/``backtest_service`` 里
        ``registry.get(...).evaluate(...) if skill.executable else None`` 会让
        命令式策略永远拿不到调用机会，与声明式路径不共享同一套分发闭环
        （需求 4.4：aggregator 不应区分策略来源）。
        """
        if self.contract is not None:
            return bool(self.contract.get("contract", {}).get("executable", True))
        return self.impl_class is not None

    @property
    def agent_enabled(self) -> bool:
        return bool(self.skill_body.strip())

    @property
    def impl_class(self) -> str | None:
        """manifest 里声明的命令式策略实现类引用（任务 3.4，需求 4.1）。

        格式：``"module.path:ClassName"``（点号分隔模块路径 + 冒号分隔类名，与
        Python 生态 entry_points 惯例一致，便于未来若要接入 ``importlib.metadata``
        风格的插件发现）。声明了该字段的 skill 由
        ``app.engine.strategy.instantiate_strategy`` 动态 import 并实例化为命令式
        ``Strategy`` 实现；未声明则装配层回退为 ``DeclarativeStrategy``（需求 4.2）。

        命令式 skill 可以没有 ``strategy.yaml``（``contract is None``），只需
        ``manifest.json``（含 ``impl_class``）+ ``SKILL.md`` + 对应 Python 类
        ——``validate_skill`` 现有「``contract is None`` 时提前返回」的逻辑已天然
        支持这条路径，无需改动。
        """
        value = self.manifest.get("impl_class")
        return str(value) if value else None


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
