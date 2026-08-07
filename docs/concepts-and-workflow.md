# Agent Skills Concepts and Workflow

This guide explains how `agent-skills` turns shared Agent Plugin packages into the effective skill set for one project and one working directory.

## 1. The portable boundary: a source plugin

Each shared source is an [Agent Plugin](https://agent-plugins.org/specification): a Git repository with a root `plugin.json` and skills in immediate child directories of `skills/`.

```text
reason-health-skills/
├── plugin.json
├── agent-skills.catalogs.yaml  # optional resolver metadata
└── skills/
    ├── git-workflow/
    │   └── SKILL.md
    ├── fhir/
    │   └── SKILL.md
    └── clinical-safety/
        └── SKILL.md
```

The source plugin remains portable: an Agent Plugins-compatible client can discover `plugin.json` and `skills/*/SKILL.md` without knowing anything about `agent-skills`.

`agent-skills.catalogs.yaml` is optional resolver metadata. It groups a source plugin's existing skills for convenient activation; it does not add fields to `plugin.json`.

```yaml
# reason-health-skills/agent-skills.catalogs.yaml
version: 1

# Source-level core skills are always selected whenever this source is used.
core:
  - git-workflow

catalogs:
  healthcare:
    - clinical-safety
  fhir:
    - fhir
  release:
    - deploy
```

## 2. Install the CLI and register a source plugin

Install the CLI into an isolated environment:

```bash
pipx install agent-skills
```

At a project root, create a starter project configuration:

```bash
cd my-project
agent-skills init \
  --source-id reason-health \
  --repo https://github.com/reason-healthcare/agent-skills.git \
  --ref v1.8.0
```

Or write `agent-skills.yaml` directly:

```yaml
version: 1
sources:
  - id: reason-health
    repo: https://github.com/reason-healthcare/agent-skills.git
    ref: v1.8.0

# Project-level core selections are always available, regardless of directory.
core:
  - reason-health/code-review
  - reason-health/testing

overrides: {}
```

`sync` fetches the declared Git ref, resolves it to an exact commit, and writes that commit into `agent-skills.lock`.

```bash
agent-skills sync
agent-skills check
```

Commit these project-owned files:

```text
agent-skills.yaml
agent-skills.lock
AGENTS.md
.agent-skills/local-skills/
.agent-skills/overrides/
```

Do not commit the generated `.agent-skills/cache/` or `.agent-skills/effective/` directories.

## 3. Always-on skills and root catalogs

There are two kinds of skills available from the project root:

1. **Core skills** are explicitly selected in `agent-skills.yaml` or listed as `core` by a source plugin. They are always present in every directory of the project.
2. **Root catalogs** are enabled in the project-root `AGENTS.md`. They apply throughout the project unless a tool is resolved from outside the project root.

Use core skills for small, broadly applicable practices: Git hygiene, code review, or testing. Use a root catalog for a larger shared domain that should apply everywhere in this repository, such as release operations.

```md
<!-- AGENTS.md at the project root -->
## Agent skills

Enable catalogs:

- reason-health/release
```

With this setup, a task from any subdirectory sees:

```text
source core + project core + reason-health/release
```

Inspect the result from the project root:

```bash
agent-skills list --effective
```

## 4. Domain-specific catalogs

Domain-specific catalogs belong in a nested `AGENTS.md`, close to the code and instructions they govern. They are additive: a nested file does not turn off root core skills or root catalogs.

For example, a FHIR service can enable healthcare and FHIR-specific guidance without loading it for the rest of the project:

```text
my-project/
├── AGENTS.md
└── services/
    └── fhir/
        ├── AGENTS.md
        └── src/
```

```md
<!-- services/fhir/AGENTS.md -->
## Agent skills

Additionally enable:

- reason-health/healthcare
- reason-health/fhir
```

Resolve from that domain directory to include every applicable level:

```bash
agent-skills list --effective --cwd services/fhir
agent-skills sync --cwd services/fhir
```

The lock records the working-directory scope and active catalogs. If a different directory needs a different generated package, run `sync --cwd <directory>` for that scope before invoking the client that consumes `.agent-skills/effective/`.

## 5. Overriding a shared skill

Never edit a cached source under `.agent-skills/cache/`. Declare an override in `agent-skills.yaml` instead. The override key is always `source-id/skill-name`.

### Extend: add local instructions

Use `extend` for project-specific guardrails, commands, or examples while retaining the shared skill.

```yaml
overrides:
  reason-health/code-review:
    mode: extend
    path: .agent-skills/overrides/code-review
```

Put the added content in `.agent-skills/overrides/code-review/SKILL.md`:

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
    path: .agent-skills/overrides/deploy.patch
```

The patch must be a unified Git patch relative to the individual skill root, for example `SKILL.md`. `sync` checks it with `git apply --check` against the exact pinned source commit, applies it in a temporary local workspace, and materializes only the result into `.agent-skills/effective/`.

If the patch no longer applies after a source update, sync fails. Update the patch deliberately, then run:

```bash
agent-skills update reason-health --to v1.9.0
agent-skills sync
agent-skills check
```

### Replace: take ownership of a skill

Use `replace` only when the project must substitute the shared skill completely. A reason is required so the decision is reviewable.

```yaml
overrides:
  reason-health/deploy:
    mode: replace
    path: .agent-skills/overrides/deploy
    reason: "This regulated service has a separate production-release control set."
```

The replacement directory must contain its own `SKILL.md`. The shared source remains cached and pinned, but its version of that skill is not included in the effective package.

## 6. Verify before relying on the effective package

Use these commands as a normal review loop:

```bash
# Validate source manifests, catalog names, overrides, and the current lock/output.
agent-skills check

# Show which files and instructions would change before regenerating.
agent-skills diff --effective

# Regenerate the effective Agent Plugin and lock file.
agent-skills sync

# Inspect name, source, exact commit, and overlay mode per effective skill.
agent-skills list --effective
```

The generated `.agent-skills/effective/` directory is itself an Agent Plugin package. It gives a compatible client one standard `plugin.json` and one resolved `skills/` tree, while the project retains a versioned, inspectable record of how that package was assembled.
