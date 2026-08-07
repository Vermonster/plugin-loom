"""Project policy parsing for the resolver-specific YAML manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import CONFIG_NAME, Override, ResolutionError, Source


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ResolutionError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ResolutionError(f"{path} must contain a YAML mapping")
    return raw


def string_list(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ResolutionError(f"{context} must be a list of non-empty strings")
    return tuple(dict.fromkeys(value))


def _require_string(data: dict[str, Any], field: str, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise ResolutionError(f"{context}.{field} must be a non-empty string")
    return value


def _load_sources(data: dict[str, Any]) -> tuple[Source, ...]:
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
    return tuple(sources)


def _load_overrides(project_root: Path, data: dict[str, Any]) -> dict[str, Override]:
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
    return overrides


def root_agent_file(project_root: Path, data: dict[str, Any]) -> Path:
    """Return the configured root agent-instruction file within the project."""
    raw_path = data.get("rootAgentFile", "AGENTS.md")
    if not isinstance(raw_path, str) or not raw_path:
        raise ResolutionError(f"{CONFIG_NAME}.rootAgentFile must be a non-empty relative path")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ResolutionError(f"{CONFIG_NAME}.rootAgentFile must be a relative path")
    path = (project_root / candidate).resolve()
    if not path.is_relative_to(project_root.resolve()) or path == project_root.resolve():
        raise ResolutionError(f"{CONFIG_NAME}.rootAgentFile must name a file inside the project")
    return path


def load_project_config(project_root: Path) -> tuple[dict[str, Any], tuple[Source, ...], dict[str, Override]]:
    path = project_root / CONFIG_NAME
    if not path.exists():
        raise ResolutionError(f"Missing {CONFIG_NAME} in {project_root}")
    data = load_yaml_mapping(path)
    if data.get("version") != 1:
        raise ResolutionError(f"{CONFIG_NAME}.version must be 1")
    return data, _load_sources(data), _load_overrides(project_root, data)
