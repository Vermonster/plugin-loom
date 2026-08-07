"""Command line interface for resolving project Plugin Loom."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

import yaml

from . import __version__
from .agents import enable_catalog
from .config import load_project_config, root_agent_file
from .models import CONFIG_NAME, LOCK_NAME, STATE_DIR, ResolutionError
from .resolver import resolve, write_resolution
from .sources import fetch_source


def _project_root(value: str | None) -> Path:
    return Path(value or Path.cwd()).resolve()


def _working_directory(project_root: Path, value: str | None) -> Path:
    return Path(value).resolve() if value else project_root


def _sync(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project_root)
    resolution = resolve(project_root, _working_directory(project_root, args.cwd))
    output = write_resolution(project_root, resolution)
    print(f"Resolved {len(resolution.skills)} skill(s) into {output}")
    print(f"Wrote {LOCK_NAME}")
    return 0


def _check(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project_root)
    resolution = resolve(project_root, _working_directory(project_root, args.cwd))
    lock_path = project_root / LOCK_NAME
    if not lock_path.is_file():
        raise ResolutionError(f"Missing {LOCK_NAME}; run 'plugin-loom sync'")
    try:
        current_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ResolutionError(f"Invalid {LOCK_NAME}: {error}") from error
    if current_lock != resolution.lock:
        raise ResolutionError(f"{LOCK_NAME} is stale; run 'plugin-loom sync'")

    output_root = project_root / STATE_DIR / "effective"
    for relative, expected in resolution.files.items():
        output = output_root / relative
        if not output.is_file() or output.read_bytes() != expected:
            raise ResolutionError(f"Generated package is stale at {relative}; run 'plugin-loom sync'")
    actual_paths = {path.relative_to(output_root) for path in output_root.rglob("*") if path.is_file()} if output_root.exists() else set()
    unexpected = sorted(actual_paths - set(resolution.files))
    if unexpected:
        raise ResolutionError(f"Generated package has unexpected file(s): {', '.join(str(path) for path in unexpected)}")
    print(f"Configuration, lock, and generated package are valid: {len(resolution.skills)} effective skill(s)")
    return 0


def _list(args: argparse.Namespace) -> int:
    if not args.effective:
        raise ResolutionError("Only 'list --effective' is supported in v0.1")
    project_root = _project_root(args.project_root)
    resolution = resolve(project_root, _working_directory(project_root, args.cwd))
    for skill in resolution.skills:
        version = skill.source_commit[:12] if skill.source_commit else "local"
        print(f"{skill.name}\t{skill.source_id}\t{version}\t{skill.mode}")
    return 0


def _diff(args: argparse.Namespace) -> int:
    if not args.effective:
        raise ResolutionError("Only 'diff --effective' is supported in v0.1")
    project_root = _project_root(args.project_root)
    resolution = resolve(project_root, _working_directory(project_root, args.cwd))
    current_root = project_root / STATE_DIR / "effective"
    current_files = {path.relative_to(current_root): path.read_bytes() for path in current_root.rglob("*") if path.is_file()} if current_root.exists() else {}
    changed = False
    for path in sorted(set(current_files) | set(resolution.files)):
        before = current_files.get(path, b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        after = resolution.files.get(path, b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        if before == after:
            continue
        changed = True
        sys.stdout.writelines(difflib.unified_diff(before, after, fromfile=f"current/{path}", tofile=f"expected/{path}"))
    if not changed:
        print("No effective-skill changes.")
    return 0


def _update(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project_root)
    path = project_root / CONFIG_NAME
    data, sources, _ = load_project_config(project_root)
    if args.source not in {source.id for source in sources}:
        raise ResolutionError(f"Unknown source: {args.source}")
    for source in data["sources"]:
        if source["id"] == args.source:
            source["ref"] = args.to
            break
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"Updated {args.source} to {args.to} in {path}")
    if not args.no_sync:
        return _sync(argparse.Namespace(project_root=str(project_root), cwd=args.cwd))
    return 0


def _init(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project_root)
    path = project_root / CONFIG_NAME
    if path.exists() and not args.force:
        raise ResolutionError(f"{path} already exists; pass --force to replace it")
    data = {
        "version": 1,
        "sources": [{"id": args.source_id, "repo": args.repo, "ref": args.ref}],
        "rootAgentFile": "AGENTS.md",
        "core": [],
        "overrides": {},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (project_root / STATE_DIR).mkdir(parents=True, exist_ok=True)
    print(f"Initialized {path}")
    return 0


def _enable(args: argparse.Namespace) -> int:
    project_root = _project_root(args.project_root)
    config, sources, _ = load_project_config(project_root)
    if "/" not in args.catalog:
        raise ResolutionError("Catalog must use source-id/catalog-name syntax")
    source_id, catalog_name = args.catalog.split("/", 1)
    source = next((item for item in sources if item.id == source_id), None)
    if source is None:
        raise ResolutionError(f"Unknown source: {source_id}")
    resolved_source = fetch_source(project_root, source)
    if catalog_name not in resolved_source.catalogs:
        raise ResolutionError(f"Unknown catalog: {args.catalog}")

    if args.path:
        scope = (project_root / args.path).resolve()
        if not scope.is_relative_to(project_root):
            raise ResolutionError("Catalog path must be inside the project root")
        agent_path = scope / "AGENTS.md"
    else:
        scope = project_root
        agent_path = root_agent_file(project_root, config)
    changed = enable_catalog(agent_path, args.catalog, args.when)
    action = "Enabled or updated" if changed else "Already enabled"
    print(f"{action} {args.catalog} in {agent_path.relative_to(project_root)}")
    if args.no_sync:
        return 0
    return _sync(argparse.Namespace(project_root=str(project_root), cwd=str(scope)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plugin-loom", description="Resolve versioned Agent Plugin skills with project-local overlays.")
    parser.add_argument("--version", action="version", version=f"plugin-loom {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def project_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--project-root", help="Project containing plugin-loom.yaml")
        command.add_argument("--cwd", help="Working directory used for scoped agent-file catalog activation")

    sync = subparsers.add_parser("sync", help="Resolve and materialize the effective Agent Plugin")
    project_arguments(sync)
    sync.set_defaults(handler=_sync)

    check = subparsers.add_parser("check", help="Validate sources, catalogs, overlays, and effective skills")
    project_arguments(check)
    check.set_defaults(handler=_check)

    list_command = subparsers.add_parser("list", help="List resolved skills")
    project_arguments(list_command)
    list_command.add_argument("--effective", action="store_true", help="List the effective skills for the current scope")
    list_command.set_defaults(handler=_list)

    diff = subparsers.add_parser("diff", help="Show pending changes to materialized skills")
    project_arguments(diff)
    diff.add_argument("--effective", action="store_true", help="Diff the effective skills")
    diff.set_defaults(handler=_diff)

    update = subparsers.add_parser("update", help="Change a source ref and synchronize it")
    project_arguments(update)
    update.add_argument("source", help="Configured source id")
    update.add_argument("--to", required=True, help="New Git ref")
    update.add_argument("--no-sync", action="store_true", help="Only update the configuration")
    update.set_defaults(handler=_update)

    init = subparsers.add_parser("init", help="Create a starter project configuration")
    init.add_argument("--project-root", help="Project containing plugin-loom.yaml")
    init.add_argument("--source-id", required=True, help="Stable source identifier")
    init.add_argument("--repo", required=True, help="Git repository URL or local path")
    init.add_argument("--ref", required=True, help="Git ref to pin")
    init.add_argument("--force", action="store_true", help="Replace an existing configuration")
    init.set_defaults(handler=_init)

    enable = subparsers.add_parser("enable", help="Enable a source catalog in an agent-instruction file")
    project_arguments(enable)
    enable.add_argument("catalog", help="Catalog to enable as source-id/catalog-name")
    enable.add_argument("--when", required=True, help="When the catalog should be included in agent context")
    enable.add_argument("--path", help="Project-relative directory for a scoped AGENTS.md")
    enable.add_argument("--no-sync", action="store_true", help="Only update the agent-instruction file")
    enable.set_defaults(handler=_enable)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except ResolutionError as error:
        print(f"plugin-loom: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
