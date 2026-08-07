# External Plugin Installation Design

> Proposal only. This describes a future `plugin-loom add` experience; it does not describe a command that exists today.

## Goal

Make adding a skill collection from an external Git repository feel as quick as the familiar `npx skills add <owner/repo>` flow, while retaining Plugin Loom's project-local policy, exact source pins, scoped catalogs, and protection against skill-context bloat. The reference experience is the [Skills CLI](https://www.skills.sh/docs/cli), which accepts an owner/repository identifier in a single add command.

For Plugin Loom, **install** means all of the following in one reviewable project change:

1. Register and pin an external Agent Plugin source.
2. Select only the skills the project wants.
3. Place those skills in the default `root` catalog unless a specialized catalog is requested.
4. Materialize the result at `.agents/skills` unless another path is requested.
5. Add `When to include` context only for a specialized, scoped catalog.
6. Generate the lock record.

It does not copy a skill into the project or activate every skill from a repository by default.

## Intended user experience

The short interactive flow is:

```bash
plugin-loom add example-org/engineering-skills
```

Plugin Loom expands the GitHub shorthand to a canonical HTTPS repository URL, fetches the default branch, validates its `plugin.json`, and lists the available `skills/*/SKILL.md` directories. For a multi-skill repository it then asks:

```text
Select skills: git-workflow
Catalog: root (default)
Install path: .agents/skills (default)

About to add source example-org/engineering-skills at main,
add git-workflow to the root catalog, and materialize it in .agents/skills.
Continue? [y/N]
```

The non-interactive equivalent is explicit and suitable for CI or a documented project setup:

```bash
plugin-loom add vercel-labs/agent-skills \
  --skill frontend-design \
  --catalog frontend-design \
  --scope apps/web \
  --when "Changing user-facing flows, components, or visual design." \
  --yes
```

For a source containing exactly one skill, `--skill` may be inferred. The catalog defaults to `root`; the install path defaults to `.agents/skills`. For a multi-skill source, skills must be selected interactively or supplied explicitly. There is deliberately no implicit "install everything" behavior.

## Resulting project state

The command adds source policy to the existing single `plugin-loom.yaml` file. It does not introduce another source-side catalog file.

```yaml
version: 1
sources:
  - id: example-org-engineering-skills
    repo: https://github.com/example-org/engineering-skills.git
    ref: main
    core: []
    catalogs:
      root:
        - git-workflow

rootAgentFile: AGENTS.md
core: []
overrides: {}
```

The root catalog is always active, so it does not need a `When to include` note or an `AGENTS.md` edit. The selected skills are materialized in the default install path:

```text
.agents/skills/
└── git-workflow/
    └── SKILL.md
```

For a specialized catalog, `--scope` names the project subtree whose `AGENTS.md` should enable it. In that case `--when` is required and is recorded beside the activation. `--path` optionally changes the materialization destination from `.agents/skills`.

Finally, the command records the resolved commit, catalog, scope, and materialized-file hashes in `plugin-loom.lock`.

## Decision rules

| Situation | Proposed behavior |
|---|---|
| GitHub shorthand | Expand `owner/repo` to `https://github.com/owner/repo.git`. |
| Full Git URL | Accept it as-is after normalization. |
| Source has one skill | Infer the selected skill unless overridden; use the `root` catalog. |
| Source has multiple skills | Require interactive selection or one or more `--skill` flags. |
| Catalog omitted | Use `root`, which is active for the whole project. |
| Multiple selected skills | Allow them in `root` only after an explicit preview; require `--catalog <name>` for a specialized group. |
| Existing matching source | Reuse it only when the normalized repository and requested ref match; otherwise require `--as <source-id>` or an explicit update. |
| Existing catalog name | Preview the added skills; never silently replace its membership. |
| Existing activation | Update the `When to include` metadata only after confirmation. |
| `--path` omitted | Materialize to `.agents/skills`. |
| `--path` supplied | Materialize to the requested project-relative directory. |
| `--scope` omitted | Use the root catalog; no agent-file update is needed. |
| `--scope` supplied | Update that subtree's `AGENTS.md`; require `--when`. |

The source identifier should default to a normalized `owner-repo` form and be overridable with `--as`. `root` is reserved for universally applicable skills. Specialized catalog names remain readable and domain-oriented rather than auto-generated hashes.

## Trust and safety boundary

The add flow should be deterministic and should not execute code from the external repository. Before it writes project files, it should:

1. Fetch the requested ref and resolve its immutable commit.
2. Validate the Agent Plugin manifest and enumerate only immediate skill directories.
3. Show the canonical repository URL, requested ref, resolved commit, selected skills, materialization path, any scoped agent-file change, and resulting catalog changes.
4. Require confirmation in an interactive terminal, or `--yes` in non-interactive use.

The default should be no telemetry. The lock file remains the reviewable record of the exact external content incorporated into the effective package.

## Catalogs prevent context bloat

The command must create or extend a catalog, not append selected skills to project `core`. This preserves the division of responsibility:

- The default `root` catalog holds genuinely universal workflow guidance, such as ADLC.
- Scoped catalogs carry specialized guidance, such as release-process, QA, or frontend-design.
- Local skills and overrides remain explicit project-authored changes.

For example, adding `frontend-design` to the root catalog is appropriate only if it is needed for every task. Otherwise the command should use `--catalog frontend-design --scope apps/web --when "..."`; that keeps it out of a backend test task. Adding a QA skill later should create or update a separate `qa` catalog enabled in `tests/AGENTS.md`.

## First milestone and later extensions

The first implementation should support GitHub shorthand and full Git URLs only. It should operate on repositories that already satisfy the Agent Plugins portable layout.

Later, Plugin Loom could add:

- `plugin-loom search <term>` backed by an opt-in directory or registry.
- `plugin-loom add <pack-url>` for curated packs, after defining a portable pack manifest and trust rules.
- `plugin-loom remove <source>/<catalog> --scope <directory>` to remove an activation and prune unreferenced catalog membership with a preview.
- Source trust policy, such as an allowlist or organization-only mode, for managed environments.

Those extensions should not change the basic contract: source selection and catalog policy stay in `plugin-loom.yaml`; activation context stays in agent files; the lock records the resolved result.

## Acceptance criteria for the future command

1. A one-command interactive add works for a single-skill external plugin, using the `root` catalog and `.agents/skills` by default.
2. A multi-skill plugin cannot activate all of its skills accidentally.
3. The non-interactive command produces only `plugin-loom.yaml`, an agent file when a scope is requested, the lock, and the requested materialization path.
4. Re-running the same command is idempotent and previews any changed metadata or catalog membership.
5. A reviewer can identify the external URL, requested ref, resolved commit, selected skills, activation scope, and inclusion rationale from committed project files.
