"""Assemble an effective Agent Plugin from project policy and source packages."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .agents import catalogs_from_agents_file
from .config import load_project_config, root_agent_file, string_list
from .models import CONFIG_NAME, LOCK_NAME, PLUGIN_SCHEMA, EffectiveSkill, ResolvedSource, Resolution, ResolutionError, STATE_DIR
from .overlays import apply_patch, extend_skill, replace_skill, skill_files
from .sources import fetch_source

# Re-export the portable contract used by callers and tests.
__all__ = ["LOCK_NAME", "PLUGIN_SCHEMA", "ResolutionError", "resolve", "write_resolution"]


def _active_catalogs(project_root: Path, working_directory: Path, root_agent_path: Path) -> tuple[str, ...]:
    try:
        relative = working_directory.resolve().relative_to(project_root.resolve())
    except ValueError as error:
        raise ResolutionError("Working directory must be inside the project root") from error

    directories = [project_root]
    current = project_root
    for part in relative.parts:
        current = current / part
        directories.append(current)

    catalogs: list[str] = []
    for index, directory in enumerate(directories):
        agent_path = root_agent_path if index == 0 else directory / "AGENTS.md"
        catalogs.extend(catalogs_from_agents_file(agent_path))
    return tuple(dict.fromkeys(catalogs))


def _requested_skills(config: dict, sources: dict[str, ResolvedSource], active_catalogs: tuple[str, ...]) -> tuple[str, ...]:
    requested = list(string_list(config.get("core"), f"{CONFIG_NAME}.core"))
    for source in sources.values():
        requested.extend(f"{source.source.id}/{skill}" for skill in source.source.core)
    for catalog_ref in active_catalogs:
        source_id, catalog = catalog_ref.split("/", 1)
        source = sources.get(source_id)
        if source is None:
            raise ResolutionError(f"AGENTS.md enables unknown source catalog: {catalog_ref}")
        if catalog not in source.source.catalogs:
            raise ResolutionError(f"AGENTS.md enables unknown catalog: {catalog_ref}")
        requested.extend(f"{source_id}/{skill}" for skill in source.source.catalogs[catalog])
    return tuple(dict.fromkeys(requested))


def _select_shared_skills(requested: tuple[str, ...], sources: dict[str, ResolvedSource]) -> list[tuple[str, str, ResolvedSource]]:
    selected: list[tuple[str, str, ResolvedSource]] = []
    names: set[str] = set()
    for item in requested:
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
    return selected


def _add_local_skills(project_root: Path, selected: list[tuple[str, str, ResolvedSource | None]]) -> None:
    local_root = project_root / STATE_DIR / "local-skills"
    names = {skill_name for _, skill_name, _ in selected}
    if not local_root.exists():
        return
    for child in sorted(local_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in names:
            raise ResolutionError(f"Local skill '{child.name}' duplicates a shared skill; declare an explicit override")
        skill_files(child)
        names.add(child.name)
        selected.append(("local", child.name, None))


def _resolved_skill_files(
    source: ResolvedSource | None,
    source_id: str,
    skill_name: str,
    project_root: Path,
    override_items: dict,
) -> tuple[dict[Path, bytes], str, str | None]:
    if source is None:
        return skill_files(project_root / STATE_DIR / "local-skills" / skill_name), "local", None
    files = skill_files(source.root / "skills" / skill_name)
    override = override_items.get(f"{source_id}/{skill_name}")
    if override is None:
        return files, "shared", source.commit
    if override.mode == "extend":
        return extend_skill(files, override.path), "extend", source.commit
    if override.mode == "patch":
        return apply_patch(files, override.path), "patch", source.commit
    return replace_skill(override.path), "replace", source.commit


def _effective_manifest() -> bytes:
    manifest = {
        "$schema": PLUGIN_SCHEMA,
        "name": "plugin-loom.effective",
        "version": "1.0.0",
        "description": "Generated effective skills for this project. Do not edit by hand.",
    }
    return (json.dumps(manifest, indent=2) + "\n").encode("utf-8")


def _build_lock(sources: dict[str, ResolvedSource], working_directory: Path, project_root: Path, catalogs: tuple[str, ...], skills: list[EffectiveSkill], files: dict[Path, bytes]) -> dict:
    return {
        "version": 1,
        "sources": [
            {"id": source.source.id, "repo": source.source.repo, "ref": source.source.ref, "commit": source.commit}
            for source in sources.values()
        ],
        "workingDirectory": str(working_directory.relative_to(project_root) or "."),
        "catalogs": list(catalogs),
        "skills": [
            {"name": skill.name, "source": skill.source_id, "commit": skill.source_commit, "mode": skill.mode}
            for skill in skills
        ],
        "files": [
            {"path": path.as_posix(), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in sorted(files.items())
        ],
    }


def resolve(project_root: Path, working_directory: Path | None = None) -> Resolution:
    project_root = project_root.resolve()
    working_directory = (working_directory or project_root).resolve()
    config, configured_sources, override_items = load_project_config(project_root)
    sources = {item.id: fetch_source(project_root, item) for item in configured_sources}
    catalogs = _active_catalogs(project_root, working_directory, root_agent_file(project_root, config))
    selected: list[tuple[str, str, ResolvedSource | None]] = list(_select_shared_skills(_requested_skills(config, sources, catalogs), sources))
    _add_local_skills(project_root, selected)

    files: dict[Path, bytes] = {Path("plugin.json"): _effective_manifest()}
    effective_skills: list[EffectiveSkill] = []
    for source_id, skill_name, source in selected:
        skill_content, mode, commit = _resolved_skill_files(
            source,
            source_id,
            skill_name,
            project_root,
            override_items,
        )
        for relative, content in skill_content.items():
            files[Path("skills") / skill_name / relative] = content
        effective_skills.append(EffectiveSkill(skill_name, source_id, skill_name, commit, mode))

    return Resolution(files, tuple(effective_skills), _build_lock(sources, working_directory, project_root, catalogs, effective_skills, files))


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
