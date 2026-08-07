"""Acquisition and validation of versioned Agent Plugin sources."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .models import CONFIG_NAME, PLUGIN_NAME_PATTERN, PLUGIN_SCHEMA, ResolvedSource, ResolutionError, STATE_DIR, Source


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


def fetch_source(project_root: Path, source: Source) -> ResolvedSource:
    cache_root = project_root / STATE_DIR / "cache" / source.id
    if not (cache_root / ".git").exists():
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--no-checkout", source.repo, str(cache_root)])
    _run(["git", "fetch", "--tags", "--force", "origin", source.ref], cwd=cache_root)
    commit = _run(["git", "rev-parse", "FETCH_HEAD^{commit}"], cwd=cache_root)
    _run(["git", "checkout", "--detach", "--force", commit], cwd=cache_root)
    validate_plugin_manifest(cache_root)
    legacy_catalog_path = cache_root / "plugin-loom.catalogs.yaml"
    if legacy_catalog_path.exists():
        raise ResolutionError(
            f"{legacy_catalog_path} is no longer supported; move its core and catalogs into the source entry in {CONFIG_NAME}"
        )
    return ResolvedSource(source, cache_root, commit)
