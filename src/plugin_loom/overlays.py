"""Read skills safely and apply explicit local overlay modes."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .models import ResolutionError


def skill_files(skill_root: Path) -> dict[Path, bytes]:
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


def extend_skill(base_files: dict[Path, bytes], extension_root: Path) -> dict[Path, bytes]:
    extension_path = extension_root / "SKILL.md"
    if not extension_path.is_file():
        raise ResolutionError(f"Extend override must contain {extension_path}")
    files = dict(base_files)
    base = files[Path("SKILL.md")].decode("utf-8").rstrip()
    extension = extension_path.read_text(encoding="utf-8").strip()
    files[Path("SKILL.md")] = (base + "\n\n## Project overlay\n\n" + extension + "\n").encode("utf-8")
    for path, content in skill_files(extension_root).items():
        if path != Path("SKILL.md"):
            files[path] = content
    return files


def apply_patch(base_files: dict[Path, bytes], patch_path: Path) -> dict[Path, bytes]:
    if not patch_path.is_file():
        raise ResolutionError(f"Patch override must be a file: {patch_path}")
    with tempfile.TemporaryDirectory(prefix="plugin-loom-patch-") as temporary:
        temporary_root = Path(temporary)
        for relative, content in base_files.items():
            output = temporary_root / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        command = ["git", "apply", "--check", "--unsafe-paths", str(patch_path)]
        result = subprocess.run(command, cwd=temporary_root, capture_output=True, text=True)
        if result.returncode != 0:
            raise ResolutionError(result.stderr.strip() or "Patch does not apply to the pinned source skill")
        result = subprocess.run(command[0:2] + ["--unsafe-paths", str(patch_path)], cwd=temporary_root, capture_output=True, text=True)
        if result.returncode != 0:
            raise ResolutionError(result.stderr.strip() or "Unable to apply patch")
        return skill_files(temporary_root)


def replace_skill(replacement_root: Path) -> dict[Path, bytes]:
    return skill_files(replacement_root)
