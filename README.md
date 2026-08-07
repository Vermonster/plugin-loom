# agent-skills

`agent-skills` resolves versioned Agent Plugin skill packages into one reproducible, project-local effective plugin.

It uses the [Agent Plugins v1](https://agent-plugins.org/specification) portable boundary without redefining it:

```text
shared-plugin/
├── plugin.json
└── skills/
    └── deploy/
        └── SKILL.md
```

`plugin.json` and `skills/*/SKILL.md` remain portable. Source pinning, catalog activation, and project overlays are resolver policy, not additions to the portable manifest.

## Project layout

```text
project/
├── AGENTS.md                       # enables catalogs for the project or subtree
├── agent-skills.yaml               # committed source, selection, and override policy
├── agent-skills.lock               # committed exact Git commits and effective inventory
└── .agent-skills/
    ├── local-skills/                # committed project-owned skills
    ├── overrides/                   # committed extends, patches, and replacements
    ├── cache/                       # ignored Git checkouts
    └── effective/                   # ignored generated Agent Plugin package
        ├── plugin.json
        └── skills/
```

Never edit `.agent-skills/cache/` or `.agent-skills/effective/` directly.

## Install

```bash
pipx install agent-skills
```

For development from a clone:

```bash
python -m pip install -e .
```

## Configure a project

Create `agent-skills.yaml`:

```yaml
version: 1
sources:
  - id: reason-health
    repo: https://github.com/reason-healthcare/agent-skills.git
    ref: v1.8.0

# Explicitly selected skills are always available.
core:
  - reason-health/git-workflow
  - reason-health/code-review

overrides:
  reason-health/code-review:
    mode: extend
    path: .agent-skills/overrides/code-review
```

Enable a catalog at the repository root or in a more-specific `AGENTS.md`:

```md
## Agent skills

Enable catalogs:

- reason-health/healthcare
- reason-health/release
```

The resolver reads all applicable `AGENTS.md` files from the project root to the current working directory. A scoped `services/fhir/AGENTS.md` can enable another catalog just for that subtree.

## Shared plugin catalogs

Catalogs are optional resolver metadata. A source plugin can publish this sidecar file without adding non-standard fields to its `plugin.json`:

```yaml
# agent-skills.catalogs.yaml
version: 1
core:
  - testing
catalogs:
  healthcare:
    - clinical-safety
    - fhir
  release:
    - deploy
    - incident-response
```

Each named skill must be present in the source plugin's immediate `skills/<skill>/SKILL.md` directory. Clients that only understand Agent Plugins can ignore this sidecar and still discover the portable skills.

## Local skills and overrides

Project-owned standalone skills live in `.agent-skills/local-skills/<skill>/SKILL.md`.

Shared skills may be customized only through an explicit `overrides` declaration:

| Mode | Path shape | Behavior |
|---|---|---|
| `extend` | directory containing `SKILL.md` | Appends project instructions under `## Project overlay`; extra files are added to the generated skill. |
| `patch` | unified diff file | Applies the diff against the pinned source skill; sync fails if it no longer applies. |
| `replace` | directory containing `SKILL.md` | Replaces the shared skill completely; a `reason` is required. |

An unintentional duplicate effective skill name is an error. This prevents one source or local skill from silently shadowing another.

## Commands

```bash
# Resolve refs, validate plugins, apply local overlays, and write the effective package.
agent-skills sync

# Validate without writing generated output.
agent-skills check

# Inspect the source, commit, and override mode for each resolved skill.
agent-skills list --effective

# Compare the current generated package with a newly resolved package.
agent-skills diff --effective

# Change one source ref and refresh the lock file and generated package.
agent-skills update reason-health --to v1.9.0
```

`sync` writes the exact source commits and every generated-file hash to `agent-skills.lock`. Commit that lock file along with `agent-skills.yaml`, local skills, overrides, and applicable `AGENTS.md` files.

## Standards boundary

Agent Plugins v1 currently standardizes a root `plugin.json`, skills in `skills/`, and optional `mcp.json`; it does not standardize dependency manifests, Git update policy, catalogs, or overlays. `agent-skills` deliberately keeps those concerns in its own YAML files and generated package, so a shared source remains usable by any compatible Agent Plugins client.

This project is not affiliated with the Agent Plugins specification or its maintainers.
