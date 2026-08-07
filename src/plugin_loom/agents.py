"""Read and update the Plugin Loom section in project agent-instruction files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import ResolutionError


PLUGIN_LOOM_HEADING = re.compile(r"^#{1,6}\s+Plugin Loom\s*$", re.IGNORECASE)
MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+")
CATALOG_REFERENCE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*)$")
CATALOG_ITEM = re.compile(r"^-\s+([a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*)\s*$")
WHEN_ITEM = re.compile(r"^\s+-\s+When to include:\s+(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class CatalogActivation:
    catalog: str
    when: str | None


def catalogs_from_agents_file(path: Path) -> list[str]:
    """Return catalog references declared in the file's Plugin Loom section."""
    return [activation.catalog for activation in catalog_activations_from_agents_file(path)]


def catalog_activations_from_agents_file(path: Path) -> list[CatalogActivation]:
    """Return catalog references and optional inclusion metadata from an agent file."""
    if not path.is_file():
        return []
    catalogs: list[CatalogActivation] = []
    in_plugin_loom = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if PLUGIN_LOOM_HEADING.match(line):
            in_plugin_loom = True
            continue
        if in_plugin_loom and MARKDOWN_HEADING.match(line):
            in_plugin_loom = False
        if in_plugin_loom:
            catalog_match = CATALOG_ITEM.match(line)
            if catalog_match:
                catalogs.append(CatalogActivation(catalog_match.group(1), None))
                continue
            when_match = WHEN_ITEM.match(line)
            if when_match and catalogs:
                catalogs[-1] = CatalogActivation(catalogs[-1].catalog, when_match.group(1))
    return catalogs


def enable_catalog(path: Path, catalog: str, when: str) -> bool:
    """Add a catalog to the Plugin Loom section, preserving other instructions.

    Returns ``True`` when the file changed. Multiple Plugin Loom sections are
    rejected because there is no unambiguous safe edit target.
    """
    if not CATALOG_REFERENCE.fullmatch(catalog):
        raise ResolutionError("Catalog must use source-id/catalog-name syntax")
    if not when.strip():
        raise ResolutionError("Catalog inclusion metadata cannot be empty")

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    section_starts = [index for index, line in enumerate(lines) if PLUGIN_LOOM_HEADING.match(line)]
    if len(section_starts) > 1:
        raise ResolutionError(f"{path} has multiple Plugin Loom sections; consolidate them before enabling a catalog")

    activations = catalog_activations_from_agents_file(path)
    updated_activations = [
        CatalogActivation(catalog, when.strip()) if item.catalog == catalog else item for item in activations
    ]
    if catalog not in {item.catalog for item in activations}:
        updated_activations.append(CatalogActivation(catalog, when.strip()))
    if updated_activations == activations:
        return False
    section = ["## Plugin Loom", "", "Enable catalogs:", ""]
    for activation in updated_activations:
        section.extend([f"- {activation.catalog}", f"  - When to include: {activation.when or 'Not specified.'}"])

    if section_starts:
        start = section_starts[0]
        end = start + 1
        while end < len(lines) and not MARKDOWN_HEADING.match(lines[end]):
            end += 1
        updated_lines = [*lines[:start], *section, *lines[end:]]
        updated = "\n".join(updated_lines).rstrip() + "\n"
    else:
        prefix = existing.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + "\n".join(section) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return True
