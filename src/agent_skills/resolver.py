"""Resolve project skill selections into a generated Agent Plugin package.

The Agent Plugins v1 standard defines the portable package boundary. This module
intentionally keeps source pinning, catalogs, and local overlays in a separate
project policy file rather than adding non-standard fields to plugin.json.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONFIG_NAME = "agent-skills.yaml"
LOCK_NAME = "agent-skills.lock"
STATE_DIR = ".agent-skills"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


class ResolutionError(RuntimeError):
    """Raised when a project cannot produce an unambiguous effective plugin."""


@dataclass(frozen=True)
class Source:
    id: str
    repo: str
    ref: str


@dataclass(frozen=True)
class Override:
    target: str
    mode: str
    path: Path
    reason: str | None


@dataclass(frozen=True)
class ResolvedSource:
    source: Source
    root: Path
    commit: str
    core: tuple[str, ...]
    catalogs: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class EffectiveSkill:
    name: str
    source_id: str
    source_skill: str
    source_commit: str | None
    mode: str


@dataclass(frozen=True)
class Resolution:
    files: dict[Path, bytes]
    skills: tuple[EffectiveSkill, ...]
    lock: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ResolutionError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ResolutionError(f"{path} must contain a YAML mapping")
    return raw


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ResolutionError(f"Command failed: {' '.join(command)}\n{detail}")
    return result.stdout.strip()


def _require_string(data: dict[str, Any], field: str, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise ResolutionError(f"{context}.{field} must be a non-empty string")
    return value


def _string_list(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ResolutionError(f"{context} must be a list of non-empty strings")
    return tuple(dict.fromkeys(value))


def load_project_config(project_root: Path) -> tuple[dict[str, Any], tuple[Source, ...], dict[str, Override]]:
    path = project_root / CONFIG_NAME
    if not path.exists():
        raise ResolutionError(f"Missing {CONFIG_NAME} in {project_root}")
    data = _load_yaml(path)
    if data.get("version") != 1:
        raise ResolutionError(f"{CONFIG_NAME}.version must be 1")

    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ResolutionError(f"{CONFIG_NAME}.sources must be a non-empty list")
    sources: list[Source] = []
    source_ids: set[str] = set()
    for index, raw in enumerate(raw_sources):
        context = f"sources[{index}]"
        if not isinstance(raw, dict):
            raise ResolutionError(f"{context} must be a mapping")
        source = Source(
            id=_require_string(raw, "id", context),
            repo=_require_string(raw, "repo", context),
            ref=_require_string(raw, "ref", context),
        )
        if source.id in source_ids:
            raise ResolutionError(f"Duplicate source id: {source.id}")
        source_ids.add(source.id)
        sources.append(source)

    raw_overrides = data.get("overrides", {})
    if not isinstance(raw_overrides, dict):
        raise ResolutionError("overrides must be a mapping")
    overrides: dict[str, Override] = {}
    for target, raw in raw_overrides.items():
        if not isinstance(target, str) or "/" not in target or not isinstance(raw, dict):
            raise ResolutionError("Each override must use 'source-id/skill' as its key")
        mode = _require_string(raw, "mode", f"overrides.{target}")
        if mode not in {"extend", "patch", "replace"}:
            raise ResolutionError(f"overrides.{target}.mode must be extend, patch, or replace")
        raw_path = _require_string(raw, "path", f"overrides.{target}")
        override_path = (project_root / raw_path).resolve()
        if not override_path.is_relative_to(project_root.resolve()):
            raise ResolutionError(f"Override path escapes the project: {raw_path}")
        reason = raw.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ResolutionError(f"overrides.{target}.reason must be a string")
        if mode == "replace" and not reason:
            raise ResolutionError(f"overrides.{target}.reason is required for replace")
        overrides[target] = Override(target, mode, override_path, reason)
    return data, tuple(sources), overrides


def _validate_plugin_manifest(root: Path) -> dict[str, Any]:
    path = root / "plugin.json"
    if not path.is_file():
        raise ResolutionError(f"Agent Plugin source is missing {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ResolutionError(f"Invalid plugin.json in {root}: {error}") from error
    if not isinstance(manifest, dict):
        raise ResolutionError(f"plugin.json in {root} must be an object")
    allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
    extra = sorted(set(manifest) - allowed)
    if extra:
        raise ResolutionError(f"plugin.json in {root} has unsupported top-level field(s): {', '.join(extra)}")
    if manifest.get("$schema") != PLUGIN_SCHEMA:
        raise ResolutionError(f"plugin.json in {root} must declare $schema {PLUGIN_SCHEMA}")
    name = manifest.get("name")
    if not isinstance(name, str) or not PLUGIN_NAME_PATTERN.fullmatch(name) or len(name) > 64:
        raise ResolutionError(f"plugin.json in {root} has an invalid Agent Plugins name")
    return manifest


def _fetch_source(project_root: Path, source: Source) -> ResolvedSource:
    cache_root = project_root / STATE_DIR / "cache" / source.id
    git_dir = cache_root / ".git"
    if not git_dir.exists():
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--no-checkout", source.repo, str(cache_root)])
    _run(["git", "fetch", "--tags", "--force", "origin", source.ref], cwd=cache_root)
    commit = _run(["git", "rev-parse", "FETCH_HEAD^{commit}"], cwd=cache_root)
    _run(["git", "checkout", "--detach", "--force", commit], cwd=cache_root)
    _validate_plugin_manifest(cache_root)

    catalog_path = cache_root / "agent-skills.catalogs.yaml"
    catalog_data = _load_yaml(catalog_path) if catalog_path.exists() else {}
    if catalog_data and catalog_data.get("version") != 1:
        raise ResolutionError(f"{catalog_path} must set version: 1")
    core = _string_list(catalog_data.get("core"), f"{catalog_path}.core")
    raw_catalogs = catalog_data.get("catalogs", {})
    if not isinstance(raw_catalogs, dict):
        raise ResolutionError(f"{catalog_path}.catalogs must be a mapping")
    catalogs = {name: _string_list(skills, f"{catalog_path}.catalogs.{name}") for name, skills in raw_catalogs.items() if isinstance(name, str) and name}
    if len(catalogs) != len(raw_catalogs):
        raise ResolutionError(f"{catalog_path}.catalogs keys must be non-empty strings")
    return ResolvedSource(source, cache_root, commit, core, catalogs)


def _active_catalogs(project_root: Path, working_directory: Path) -> tuple[str, ...]:
    try:
        relative = working_directory.resolve().relative_to(project_root.resolve())
    except ValueError as error:
        raise ResolutionError("Working directory must be inside the project root") from error
    directories = [project_root]
    current = project_root
    for part in relative.parts:
        current = current / part
        directories.append(current)

    enabled: list[str] = []
    for directory in directories:
        agents_path = directory / "AGENTS.md"
        if not agents_path.is_file():
            continue
        in_agent_skills = False
        for line in agents_path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^#{1,6}\s+Agent skills\s*$", line, re.IGNORECASE):
                in_agent_skills = True
                continue
            if in_agent_skills and re.match(r"^#{1,6}\s+", line):
                in_agent_skills = False
            if in_agent_skills:
                match = re.match(r"^\s*-\s+([a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*)\s*$", line)
                if match:
                    enabled.append(match.group(1))
    return tuple(dict.fromkeys(enabled))


def _skill_files(skill_root: Path) -> dict[Path, bytes]:
    skill_root = skill_root.resolve()
    skill_path = skill_root / "SKILL.md"
    if not skill_path.is_file():
        raise ResolutionError(f"Skill is missing SKILL.md: {skill_root}")
    files: dict[Path, bytes] = {}
    for path in skill_root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(skill_root):
            raise ResolutionError(f"Skill file escapes its package boundary: {path}")
        files[path.relative_to(skill_root)] = resolved.read_bytes()
    return files


def _extend_skill(files: dict[Path, bytes], extension_root: Path) -> dict[Path, bytes]:
    extension_path = extension_root / "SKILL.md"
    if not extension_path.is_file():
        raise ResolutionError(f"Extend override must contain {extension_path}")
    base = files[Path("SKILL.md")].decode("utf-8").rstrip()
    extension = extension_path.read_text(encoding="utf-8").strip()
    files = dict(files)
    files[Path("SKILL.md")] = (base + "\n\n## Project overlay\n\n" + extension + "\n").encode("utf-8")
    for path, content in _skill_files(extension_root).items():
        if path != Path("SKILL.md"):
            files[path] = content
    return files


def _apply_patch(files: dict[Path, bytes], patch_path: Path) -> dict[Path, bytes]:
    if not patch_path.is_file():
        raise ResolutionError(f"Patch override must be a file: {patch_path}")
    temporary = patch_path.parent / ".agent-skills-patch-work"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    try:
        for relative, content in files.items():
            output = temporary / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        _run(["git", "apply", "--check", "--unsafe-paths", str(patch_path)], cwd=temporary)
        _run(["git", "apply", "--unsafe-paths", str(patch_path)], cwd=temporary)
        return _skill_files(temporary)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def resolve(project_root: Path, working_directory: Path | None = None) -> Resolution:
    project_root = project_root.resolve()
    working_directory = (working_directory or project_root).resolve()
    config, configured_sources, overrides = load_project_config(project_root)
    sources = {source.source.id: source for source in (_fetch_source(project_root, item) for item in configured_sources)}

    requested = list(_string_list(config.get("core"), f"{CONFIG_NAME}.core"))
    for source in sources.values():
        requested.extend(f"{source.source.id}/{skill}" for skill in source.core)
    for catalog_ref in _active_catalogs(project_root, working_directory):
        source_id, catalog = catalog_ref.split("/", 1)
        source = sources.get(source_id)
        if source is None:
            raise ResolutionError(f"AGENTS.md enables unknown source catalog: {catalog_ref}")
        if catalog not in source.catalogs:
            raise ResolutionError(f"AGENTS.md enables unknown catalog: {catalog_ref}")
        requested.extend(f"{source_id}/{skill}" for skill in source.catalogs[catalog])

    selected: list[tuple[str, str, ResolvedSource | None]] = []
    names: set[str] = set()
    for item in dict.fromkeys(requested):
        if "/" not in item:
            raise ResolutionError(f"Skill selection must be source-id/skill: {item}")
        source_id, skill_name = item.split("/", 1)
        source = sources.get(source_id)
        if source is None:
            raise ResolutionError(f"Unknown source in skill selection: {item}")
        if not (source.root / "skills" / skill_name / "SKILL.md").is_file():
            raise ResolutionError(f"Source skill does not exist: {item}")
        if skill_name in names:
            raise ResolutionError(f"Duplicate effective skill name: {skill_name}")
        names.add(skill_name)
        selected.append((source_id, skill_name, source))

    local_root = project_root / STATE_DIR / "local-skills"
    if local_root.exists():
        for child in sorted(local_root.iterdir()):
            if not child.is_dir():
                continue
            if child.name in names:
                raise ResolutionError(f"Local skill '{child.name}' duplicates a shared skill; declare an explicit override")
            _skill_files(child)
            names.add(child.name)
            selected.append(("local", child.name, None))

    output_files: dict[Path, bytes] = {}
    effective_skills: list[EffectiveSkill] = []
    for source_id, skill_name, source in selected:
        target = f"{source_id}/{skill_name}"
        override = overrides.get(target)
        if source is None:
            files = _skill_files(local_root / skill_name)
            mode = "local"
            commit = None
        else:
            files = _skill_files(source.root / "skills" / skill_name)
            mode = "shared"
            commit = source.commit
            if override:
                if override.mode == "extend":
                    files = _extend_skill(files, override.path)
                elif override.mode == "replace":
                    files = _skill_files(override.path)
                else:
                    files = _apply_patch(files, override.path)
                mode = override.mode
        for relative, content in files.items():
            output_files[Path("skills") / skill_name / relative] = content
        effective_skills.append(EffectiveSkill(skill_name, source_id, skill_name, commit, mode))

    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "agent-skills.effective",
        "version": "1.0.0",
        "description": "Generated effective skills for this project. Do not edit by hand.",
    }
    output_files[Path("plugin.json")] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    lock = {
        "version": 1,
        "sources": [
            {"id": source.source.id, "repo": source.source.repo, "ref": source.source.ref, "commit": source.commit}
            for source in sources.values()
        ],
        "workingDirectory": str(working_directory.relative_to(project_root) or "."),
        "catalogs": list(_active_catalogs(project_root, working_directory)),
        "skills": [
            {"name": skill.name, "source": skill.source_id, "commit": skill.source_commit, "mode": skill.mode}
            for skill in effective_skills
        ],
        "files": [
            {"path": path.as_posix(), "sha256": _sha256(content)}
            for path, content in sorted(output_files.items())
        ],
    }
    return Resolution(output_files, tuple(effective_skills), lock)


def write_resolution(project_root: Path, resolution: Resolution) -> Path:
    output_root = project_root / STATE_DIR / "effective"
    if output_root.exists():
        shutil.rmtree(output_root)
    for relative, content in resolution.files.items():
        path = output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (project_root / LOCK_NAME).write_text(json.dumps(resolution.lock, indent=2) + "\n", encoding="utf-8")
    return output_root
