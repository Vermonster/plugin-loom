# Plugin Loom Concepts and Workflow

This guide explains how `plugin-loom` turns shared Agent Plugin packages into the effective skill set for one project and one working directory.

## 1. The portable boundary: a source plugin

Each shared source is an [Agent Plugin](https://agent-plugins.org/specification): a Git repository with a root `plugin.json` and skills in immediate child directories of `skills/`.

```text
reason-health-skills/
├── plugin.json
└── skills/
    ├── git-workflow/
    │   └── SKILL.md
    ├── planning/
    │   └── SKILL.md
    ├── deploy/
    │   └── SKILL.md
    └── testing/
        └── SKILL.md
```

The source plugin remains portable: an Agent Plugins-compatible client can discover `plugin.json` and `skills/*/SKILL.md` without knowing anything about `plugin-loom`. Plugin Loom keeps source selection, source-level core skills, and named catalogs in the consuming project's single `plugin-loom.yaml`.

If an earlier source uses `plugin-loom.catalogs.yaml`, move its `core` and `catalogs` entries under the matching source in `plugin-loom.yaml`, then remove the sidecar. Plugin Loom rejects that legacy sidecar so catalog policy cannot be split across two files.

## 2. Install the CLI and register a source plugin

Install the CLI into an isolated environment:

```bash
pipx install plugin-loom
```

At a project root, create a starter project configuration:

```bash
cd my-project
plugin-loom init \
  --source-id reason-health \
  --repo https://github.com/example/reason-health-plugins.git \
  --ref v1.8.0
```

Or write `plugin-loom.yaml` directly:

```yaml
version: 1
sources:
  - id: reason-health
    repo: https://github.com/example/reason-health-plugins.git
    ref: v1.8.0
    # Source-level skills selected in every scope.
    core:
      - git-workflow
    # Named groups enabled from agent files.
    catalogs:
      adlc:
        - planning
        - implementation
        - code-review
      release-process:
        - deploy
      qa:
        - testing
        - test-automation
      frontend-design:
        - frontend-design
        - accessibility

# The root catalog file; AGENTS.md is the default when this is omitted.
rootAgentFile: AGENTS.md

# Project-level core selections are always available, regardless of directory.
core:
  - reason-health/code-review

overrides: {}
```

`sync` fetches the declared Git ref, resolves it to an exact commit, and writes that commit into `plugin-loom.lock`.

```bash
plugin-loom sync
plugin-loom check
```

Commit these project-owned files:

```text
plugin-loom.yaml
plugin-loom.lock
<rootAgentFile>                  # AGENTS.md by default
<scoped directories>/AGENTS.md
.plugin-loom/local-skills/
.plugin-loom/overrides/
```

Do not commit the generated `.plugin-loom/cache/` or `.plugin-loom/effective/` directories.

## 3. Enable catalogs in agent files

`plugin-loom enable` validates the requested catalog against the pinned source, then creates or updates a single `## Plugin Loom` section. `--when` is required: the command writes that inclusion context directly beside the catalog entry in the agent file. It preserves the rest of the target file and runs `sync` unless `--no-sync` is supplied.

The optional `rootAgentFile` configuration names the root target, relative to the project root. It defaults to `AGENTS.md`:

```yaml
rootAgentFile: .agents/project-guidance.md
```

Without `--path`, the command updates that configured root file:

```bash
plugin-loom enable reason-health/adlc \
  --when "Any task that plans, implements, reviews, or ships software changes."
```

If the root file previously contains ordinary project instructions:

```md
# Project guidance

Run the test suite before changing production behavior.
```

the command produces:

```md
# Project guidance

Run the test suite before changing production behavior.

## Plugin Loom

Enable catalogs:

- reason-health/adlc
  - When to include: Any task that plans, implements, reviews, or ships software changes.
```

For a domain catalog, choose a project-relative directory. This always updates that directory's `AGENTS.md` so nested guidance remains conventional and discoverable:

```bash
plugin-loom enable reason-health/release-process \
  --path ops/release \
  --when "Preparing, approving, or executing a release."
plugin-loom enable reason-health/qa \
  --path tests \
  --when "Writing, running, or investigating automated tests."
plugin-loom enable reason-health/frontend-design \
  --path apps/web \
  --when "Changing user-facing flows, components, or visual design."
```

For example, the resulting `tests/AGENTS.md` contains:

```md
## Plugin Loom

Enable catalogs:

- reason-health/qa
  - When to include: Writing, running, or investigating automated tests.
```

## 4. Always-on skills and root catalogs

There are two kinds of skills available from the project root:

1. **Core skills** are explicitly selected in `plugin-loom.yaml` or listed as `core` by a source plugin. They are always present in every directory of the project.
2. **Root catalogs** are enabled in the configured root agent file. Put the ADLC workflow catalog here so every task starts with the shared planning, implementation, review, and delivery process.

Use core skills for small, broadly applicable practices: Git hygiene, code review, or testing. Use the root catalog for the ADLC workflow. Keep release-process, QA, and frontend-design catalogs scoped to their domains so they do not bloat every agent context.

```bash
plugin-loom enable reason-health/adlc --when "Any software delivery task."
```

With this setup, a task from any subdirectory sees:

```text
source core + project core + reason-health/adlc
```

Inspect the result from the project root:

```bash
plugin-loom list --effective
```

## 5. Domain-specific catalogs

Domain-specific catalogs belong in a nested `AGENTS.md`, close to the code and instructions they govern. They are additive: a nested file does not turn off root core skills or the root ADLC catalog.

For example, scope release-process guidance to release operations, QA guidance to tests, and frontend-design guidance to the web application rather than loading all three everywhere:

```text
my-project/
├── AGENTS.md                 # ADLC catalog
├── ops/release/AGENTS.md     # release-process catalog
├── tests/AGENTS.md           # QA catalog
└── apps/web/AGENTS.md        # frontend-design catalog
```

```bash
plugin-loom enable reason-health/frontend-design \
  --path apps/web \
  --when "Changing user-facing flows, components, or visual design."
```

Resolve from that domain directory to include every applicable level:

```bash
plugin-loom list --effective --cwd apps/web
plugin-loom sync --cwd apps/web
```

The lock records the working-directory scope and active catalogs. If a different directory needs a different generated package, run `sync --cwd <directory>` for that scope before invoking the client that consumes `.plugin-loom/effective/`.

## 6. Overriding a shared skill

Never edit a cached source under `.plugin-loom/cache/`. Declare an override in `plugin-loom.yaml` instead. The override key is always `source-id/skill-name`.

### Extend: add local instructions

Use `extend` for project-specific guardrails, commands, or examples while retaining the shared skill.

```yaml
overrides:
  reason-health/code-review:
    mode: extend
    path: .plugin-loom/overrides/code-review
```

Put the added content in `.plugin-loom/overrides/code-review/SKILL.md`:

```md
## Release constraints

- Run the service's contract tests before approving a release change.
- Treat migration changes as requiring an explicit rollback note.
```

During sync, the resolver appends this content under `## Project overlay` in the generated effective skill. Extra files in the override directory are copied into that generated skill.

### Patch: make a version-bound change

Use `patch` when a local change needs to modify the shared skill itself and should fail if upstream changes make the patch unsafe.

```yaml
overrides:
  reason-health/deploy:
    mode: patch
    path: .plugin-loom/overrides/deploy.patch
```

The patch must be a unified Git patch relative to the individual skill root, for example `SKILL.md`. `sync` checks it with `git apply --check` against the exact pinned source commit, applies it in a temporary local workspace, and materializes only the result into `.plugin-loom/effective/`.

If the patch no longer applies after a source update, sync fails. Update the patch deliberately, then run:

```bash
plugin-loom update reason-health --to v1.9.0
plugin-loom sync
plugin-loom check
```

### Advanced: author a patch from the pinned skill

Author a patch from the exact cached source that Plugin Loom resolved. Do not edit the cache itself: copy the individual skill into a temporary Git repository, make the change there, and export its diff. This produces paths relative to the skill root, which is the format the resolver applies.

First, ensure the cache is current and declare the patch override:

```bash
plugin-loom sync
```

```yaml
overrides:
  reason-health/deploy:
    mode: patch
    path: .plugin-loom/overrides/deploy.patch
```

Then create the patch. Edit `SKILL.md` after the temporary repository is initialized, before running the final `git diff` command:

```bash
PATCH_ROOT="$PWD/.plugin-loom/overrides"
SCRATCH=$(mktemp -d)
mkdir -p "$PATCH_ROOT"
cp -R "$PWD/.plugin-loom/cache/reason-health/skills/deploy" "$SCRATCH/deploy"

git -C "$SCRATCH/deploy" init -q
git -C "$SCRATCH/deploy" add .
git -C "$SCRATCH/deploy" -c user.name="Plugin Loom" -c user.email="plugin-loom@local" commit -qm base

# Edit $SCRATCH/deploy/SKILL.md (and any other files in that skill) now.
git -C "$SCRATCH/deploy" diff --binary > "$PATCH_ROOT/deploy.patch"
```

Inspect the result before relying on it. A patch should contain only the intended changes and paths such as `a/SKILL.md` and `b/SKILL.md`, never an absolute cache path:

```bash
git -C "$SCRATCH/deploy" diff --check
plugin-loom sync
plugin-loom check
```

`sync` runs `git apply --check` against the same pinned source before it generates the effective package. Keep the patch in version control; the temporary directory is only a local authoring workspace.

### Replace: take ownership of a skill

Use `replace` only when the project must substitute the shared skill completely. A reason is required so the decision is reviewable.

```yaml
overrides:
  reason-health/deploy:
    mode: replace
    path: .plugin-loom/overrides/deploy
    reason: "This regulated service has a separate production-release control set."
```

The replacement directory must contain its own `SKILL.md`. The shared source remains cached and pinned, but its version of that skill is not included in the effective package.

## 7. Verify before relying on the effective package

Use these commands as a normal review loop:

```bash
# Validate source manifests, catalog names, overrides, and the current lock/output.
plugin-loom check

# Show which files and instructions would change before regenerating.
plugin-loom diff --effective

# Regenerate the effective Agent Plugin and lock file.
plugin-loom sync

# Inspect name, source, exact commit, and overlay mode per effective skill.
plugin-loom list --effective
```

The generated `.plugin-loom/effective/` directory is itself an Agent Plugin package. It gives a compatible client one standard `plugin.json` and one resolved `skills/` tree, while the project retains a versioned, inspectable record of how that package was assembled.
