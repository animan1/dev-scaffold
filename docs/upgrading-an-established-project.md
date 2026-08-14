# Upgrade an established scaffold-derived project

Use this process after application code, deployment configuration, and project
conventions have diverged from `dev-scaffold`. The goal is to adopt selected
generic improvements without replacing product behavior or disguising a rewrite
as an upstream update.

## Identify repository lineage

First determine how the scaffold entered the project:

- **Shared Git ancestry:** the project was forked from the scaffold or retains a
  common ancestor. Cuplr is an example and keeps `dev-scaffold` as an `upstream`
  remote. Use a normal Git merge from an exact upstream commit.
- **Snapshot lineage:** the project imported source without scaffold history.
  Use the recorded source commit and the path-based snapshot procedure below.
  Do not use `--allow-unrelated-histories` merely to manufacture ancestry.

In either case, start from a clean project `main`, create a dedicated upgrade
branch, and pin the target scaffold commit rather than merging a moving branch.

## Inventory the upgrade before applying it

Find the last adopted scaffold commit from the project's provenance record. For
shared ancestry, confirm it with the merge base. Then record:

```bash
baseline_commit="<last-adopted-scaffold-commit>"
target_commit="<new-scaffold-commit>"

git diff --name-status "$baseline_commit" "$target_commit"
git diff --name-only "$baseline_commit"...HEAD
```

The intersection is the collision set: paths changed both upstream and in the
application. Classify every changed upstream path before integration:

- unchanged downstream: accept the upstream file exactly;
- application-owned collision: merge only the applicable generic behavior;
- upstream deletion: delete only if the application no longer uses the path;
- generated lock or artifact: follow the owning profile's deterministic update
  command; and
- deferred change: record why it is not applicable instead of silently omitting
  it.

Review release notes, migrations, environment variables, Compose resources,
ports, secrets, and destructive Make targets before running anything.

## Shared-ancestry upgrade, such as Cuplr

Fetch the scaffold remote and resolve the reviewed target SHA:

```bash
git fetch upstream main
target_commit="$(git rev-parse upstream/main)"
git switch -c codex/upgrade-dev-scaffold
git merge --no-ff --no-commit "$target_commit"
```

Resolve conflicts using the collision decisions. Preserve application models,
URLs, settings, deployment values, credentials, and domain behavior while
bringing across the generic contract. Complete the merge as the upstream-update
commit, then put necessary application adaptations in a separate commit. Do not
rewrite upstream changes merely to match local style.

If the full target contains unrelated or unproven profiles, integrate a reviewed
commit range or individual landed feature instead and record exactly what was
selected. Do not label greenfield application work as a scaffold upgrade.

## Snapshot-lineage upgrade

Archive the exact target outside the repository as described in
[the initial adoption guide](adopting-into-an-existing-repository.md). Compare
the baseline and target scaffold trees to obtain added, modified, and deleted
upstream paths. Copy changed non-collision files byte-for-byte in one mechanical
commit; handle collisions and applicable deletions in a second adaptation
commit. Append the target commit, tree, date, and archive checksum to the
project's provenance history.

Never overlay the complete target and then restore application files. That makes
commit-by-commit review noisy and can briefly stage unsafe licenses, settings,
or credentials.

## Validate application behavior

Use the project's committed profile selection, not a one-off environment
override:

```bash
make verify
make precommit
git diff --check
```

Also run any application-owned migration, backup/restore, security, browser,
deployment, and rollback checks affected by the upgrade. Confirm routine
teardown preserves data, project-scoped ports and Compose names still isolate
concurrent applications, and no scaffold example values replaced production
configuration.

## Structure the review

Target project `main`. Present commits in this order:

1. exact upstream merge or byte-identical snapshot delta;
2. application-specific conflict resolution and compatibility adaptations; and
3. provenance and upgrade notes, when they are not already part of step 2.

The pull-request description should identify the baseline and target commits,
list collisions and deferred changes, distinguish copied upstream work from
local edits, and provide verification evidence. Keep later review fixes
append-only. After merge, update the recorded adopted scaffold commit so the
next upgrade has an unambiguous baseline.
