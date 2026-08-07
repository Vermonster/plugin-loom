"""Shared domain types and portable Agent Plugins constants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_NAME = "plugin-loom.yaml"
LOCK_NAME = "plugin-loom.lock"
STATE_DIR = ".plugin-loom"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


class ResolutionError(RuntimeError):
    """Raised when a project cannot produce an unambiguous effective plugin."""


@dataclass(frozen=True)
class Source:
    id: str
    repo: str
    ref: str
    core: tuple[str, ...]
    catalogs: dict[str, tuple[str, ...]]


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
