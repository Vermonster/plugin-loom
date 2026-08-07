from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from agent_skills.resolver import PLUGIN_SCHEMA, ResolutionError, resolve, write_resolution
from agent_skills.cli import main


def _git(path: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)


def _create_source(path: Path) -> None:
    (path / "skills" / "clinical-safety").mkdir(parents=True)
    (path / "skills" / "testing").mkdir(parents=True)
    (path / "plugin.json").write_text(
        json.dumps({"$schema": PLUGIN_SCHEMA, "name": "example.skills"}) + "\n",
        encoding="utf-8",
    )
    (path / "agent-skills.catalogs.yaml").write_text(
        "version: 1\ncore:\n  - testing\ncatalogs:\n  healthcare:\n    - clinical-safety\n",
        encoding="utf-8",
    )
    (path / "skills" / "testing" / "SKILL.md").write_text(
        "---\nname: testing\ndescription: Test safely.\n---\n\n# Testing\n",
        encoding="utf-8",
    )
    (path / "skills" / "clinical-safety" / "SKILL.md").write_text(
        "---\nname: clinical-safety\ndescription: Review clinical risk.\n---\n\n# Clinical safety\n",
        encoding="utf-8",
    )
    _git(path, "init")
    _git(path, "config", "user.email", "tests@example.com")
    _git(path, "config", "user.name", "Tests")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "Initial plugin")
    _git(path, "tag", "v1.0.0")


class ResolverTests(unittest.TestCase):
    def test_sync_resolves_catalog_core_and_extend_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            source.mkdir()
            project.mkdir()
            _create_source(source)
            (project / "AGENTS.md").write_text(
                "## Agent skills\n\nEnable catalogs:\n\n- example/healthcare\n",
                encoding="utf-8",
            )
            extension = project / ".agent-skills" / "overrides" / "testing"
            extension.mkdir(parents=True)
            (extension / "SKILL.md").write_text("Always preserve fixtures.", encoding="utf-8")
            config = {
                "version": 1,
                "sources": [{"id": "example", "repo": str(source), "ref": "v1.0.0"}],
                "core": ["example/testing"],
                "overrides": {"example/testing": {"mode": "extend", "path": ".agent-skills/overrides/testing"}},
            }
            (project / "agent-skills.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

            resolution = resolve(project)
            self.assertEqual([skill.name for skill in resolution.skills], ["testing", "clinical-safety"])
            self.assertEqual(resolution.skills[0].mode, "extend")
            self.assertIn(b"## Project overlay", resolution.files[Path("skills/testing/SKILL.md")])
            output = write_resolution(project, resolution)
            manifest = json.loads((output / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["$schema"], PLUGIN_SCHEMA)
            self.assertTrue((project / "agent-skills.lock").exists())
            self.assertEqual(main(["check", "--project-root", str(project)]), 0)

    def test_duplicate_shared_and_local_skill_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            source.mkdir()
            project.mkdir()
            _create_source(source)
            config = {
                "version": 1,
                "sources": [{"id": "example", "repo": str(source), "ref": "v1.0.0"}],
                "core": ["example/testing"],
            }
            (project / "agent-skills.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
            local = project / ".agent-skills" / "local-skills" / "testing"
            local.mkdir(parents=True)
            (local / "SKILL.md").write_text("---\nname: testing\ndescription: Local.\n---\n", encoding="utf-8")

            with self.assertRaisesRegex(ResolutionError, "duplicates a shared skill"):
                resolve(project)

    def test_patch_override_is_bound_to_the_pinned_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            source.mkdir()
            project.mkdir()
            _create_source(source)
            patch = project / ".agent-skills" / "overrides" / "testing.patch"
            patch.parent.mkdir(parents=True)
            patch.write_text(
                "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -6 +6 @@\n-# Testing\n+# Project testing\n",
                encoding="utf-8",
            )
            config = {
                "version": 1,
                "sources": [{"id": "example", "repo": str(source), "ref": "v1.0.0"}],
                "core": ["example/testing"],
                "overrides": {"example/testing": {"mode": "patch", "path": ".agent-skills/overrides/testing.patch"}},
            }
            (project / "agent-skills.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

            resolution = resolve(project)
            self.assertIn(b"# Project testing", resolution.files[Path("skills/testing/SKILL.md")])

    def test_invalid_source_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            source.mkdir()
            project.mkdir()
            (source / "plugin.json").write_text('{"name": "Missing schema"}\n', encoding="utf-8")
            _git(source, "init")
            _git(source, "config", "user.email", "tests@example.com")
            _git(source, "config", "user.name", "Tests")
            _git(source, "add", ".")
            _git(source, "commit", "-m", "Invalid plugin")
            config = {"version": 1, "sources": [{"id": "example", "repo": str(source), "ref": "HEAD"}]}
            (project / "agent-skills.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

            with self.assertRaisesRegex(ResolutionError, "must declare \\$schema"):
                resolve(project)
