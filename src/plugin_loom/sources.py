"""Acquisition and validation of versioned Agent Plugin sources."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import load_yaml_mapping, string_list
from .models import PLUGIN_NAME_PATTERN, PLUGIN_SCHEMA, ResolvedSource, ResolutionError, STATE_DIR, Source


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ResolutionError(f"Command failed: {' '.join(command)}\n{detail}")
    return result.stdout.strip()


def validate_plugin_manifest(root: Path) -> dict[str, Any]:
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


def _load_catalogs(root: Path) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    catalog_path = root / "plugin-loom.catalogs.yaml"
    catalog_data = load_yaml_mapping(catalog_path) if catalog_path.exists() else {}
    if catalog_data and catalog_data.get("version") != 1:
        raise ResolutionError(f"{catalog_path} must set version: 1")
    core = string_list(catalog_data.get("core"), f"{catalog_path}.core")
    raw_catalogs = catalog_data.get("catalogs", {})
    if not isinstance(raw_catalogs, dict):
        raise ResolutionError(f"{catalog_path}.catalogs must be a mapping")
    catalogs = {
        name: string_list(skills, f"{catalog_path}.catalogs.{name}")
        for name, skills in raw_catalogs.items()
        if isinstance(name, str) and name
    }
    if len(catalogs) != len(raw_catalogs):
        raise ResolutionError(f"{catalog_path}.catalogs keys must be non-empty strings")
    return core, catalogs


def fetch_source(project_root: Path, source: Source) -> ResolvedSource:
    cache_root = project_root / STATE_DIR / "cache" / source.id
    if not (cache_root / ".git").exists():
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--no-checkout", source.repo, str(cache_root)])
    _run(["git", "fetch", "--tags", "--force", "origin", source.ref], cwd=cache_root)
    commit = _run(["git", "rev-parse", "FETCH_HEAD^{commit}"], cwd=cache_root)
    _run(["git", "checkout", "--detach", "--force", commit], cwd=cache_root)
    validate_plugin_manifest(cache_root)
    core, catalogs = _load_catalogs(cache_root)
    return ResolvedSource(source, cache_root, commit, core, catalogs)
