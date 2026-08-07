from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from plugin_loom.resolver import PLUGIN_SCHEMA, ResolutionError, resolve, write_resolution
from plugin_loom.cli import main


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


def _source_config(source: Path, ref: str = "v1.0.0") -> dict:
    return {
        "id": "example",
        "repo": str(source),
        "ref": ref,
        "core": ["testing"],
        "catalogs": {"healthcare": ["clinical-safety"], "fhir": ["clinical-safety"]},
    }


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
                "## Plugin Loom\n\nEnable catalogs:\n\n- example/healthcare\n",
                encoding="utf-8",
            )
            extension = project / ".plugin-loom" / "overrides" / "testing"
            extension.mkdir(parents=True)
            (extension / "SKILL.md").write_text("Always preserve fixtures.", encoding="utf-8")
            config = {
                "version": 1,
                "sources": [_source_config(source)],
                "core": ["example/testing"],
                "overrides": {"example/testing": {"mode": "extend", "path": ".plugin-loom/overrides/testing"}},
            }
            (project / "plugin-loom.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

            resolution = resolve(project)
            self.assertEqual([skill.name for skill in resolution.skills], ["testing", "clinical-safety"])
            self.assertEqual(resolution.skills[0].mode, "extend")
            self.assertIn(b"## Project overlay", resolution.files[Path("skills/testing/SKILL.md")])
            output = write_resolution(project, resolution)
            manifest = json.loads((output / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["$schema"], PLUGIN_SCHEMA)
            self.assertTrue((project / "plugin-loom.lock").exists())
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
                "sources": [_source_config(source)],
                "core": ["example/testing"],
            }
            (project / "plugin-loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
            local = project / ".plugin-loom" / "local-skills" / "testing"
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
            patch = project / ".plugin-loom" / "overrides" / "testing.patch"
            patch.parent.mkdir(parents=True)
            patch.write_text(
                "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -6 +6 @@\n-# Testing\n+# Project testing\n",
                encoding="utf-8",
            )
            config = {
                "version": 1,
                "sources": [_source_config(source)],
                "core": ["example/testing"],
                "overrides": {"example/testing": {"mode": "patch", "path": ".plugin-loom/overrides/testing.patch"}},
            }
            (project / "plugin-loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

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
            (project / "plugin-loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

            with self.assertRaisesRegex(ResolutionError, "must declare \\$schema"):
                resolve(project)

    def test_legacy_catalog_sidecar_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            source.mkdir()
            project.mkdir()
            _create_source(source)
            (source / "plugin-loom.catalogs.yaml").write_text("version: 1\n", encoding="utf-8")
            _git(source, "add", ".")
            _git(source, "commit", "-m", "Add legacy catalog sidecar")
            config = {"version": 1, "sources": [_source_config(source, "HEAD")]}
            (project / "plugin-loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

            with self.assertRaisesRegex(ResolutionError, "no longer supported"):
                resolve(project)

    def test_enable_updates_root_and_scoped_agent_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            project = root / "project"
            source.mkdir()
            project.mkdir()
            _create_source(source)
            config = {
                "version": 1,
                "sources": [_source_config(source)],
                "rootAgentFile": ".agents/root.md",
                "core": [],
                "overrides": {},
            }
            (project / "plugin-loom.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

            self.assertEqual(
                main(
                    [
                        "enable",
                        "example/healthcare",
                        "--when",
                        "Working on clinical workflows.",
                        "--project-root",
                        str(project),
                    ]
                ),
                0,
            )
            root_agents = project / ".agents" / "root.md"
            self.assertEqual(
                root_agents.read_text(encoding="utf-8"),
                "## Plugin Loom\n\nEnable catalogs:\n\n- example/healthcare\n  - When to include: Working on clinical workflows.\n",
            )
            self.assertTrue((project / "plugin-loom.lock").is_file())
            self.assertEqual([skill.name for skill in resolve(project).skills], ["testing", "clinical-safety"])

            self.assertEqual(
                main(
                    [
                        "enable",
                        "example/fhir",
                        "--when",
                        "Working on FHIR integrations.",
                        "--project-root",
                        str(project),
                        "--no-sync",
                    ]
                ),
                0,
            )
            self.assertEqual(
                root_agents.read_text(encoding="utf-8"),
                "## Plugin Loom\n\nEnable catalogs:\n\n- example/healthcare\n  - When to include: Working on clinical workflows.\n- example/fhir\n  - When to include: Working on FHIR integrations.\n",
            )

            scoped_agents = project / "services" / "fhir" / "AGENTS.md"
            scoped_agents.parent.mkdir(parents=True)
            scoped_agents.write_text("# FHIR service\n\nFollow local conventions.\n", encoding="utf-8")
            self.assertEqual(
                main(
                    [
                        "enable",
                        "example/healthcare",
                        "--when",
                        "Working on clinical workflows.",
                        "--project-root",
                        str(project),
                        "--path",
                        "services/fhir",
                        "--no-sync",
                    ]
                ),
                0,
            )
            self.assertEqual(
                scoped_agents.read_text(encoding="utf-8"),
                "# FHIR service\n\nFollow local conventions.\n\n## Plugin Loom\n\nEnable catalogs:\n\n- example/healthcare\n  - When to include: Working on clinical workflows.\n",
            )
            self.assertEqual(
                main(
                    [
                        "enable",
                        "example/fhir",
                        "--when",
                        "Working on FHIR integrations.",
                        "--project-root",
                        str(project),
                        "--no-sync",
                    ]
                ),
                0,
            )
            self.assertEqual(root_agents.read_text(encoding="utf-8").count("example/healthcare"), 1)
