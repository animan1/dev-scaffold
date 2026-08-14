# Adopt the scaffold before application development

Use this process when a repository already contains planning, identity, or
license files but does not yet contain meaningful application code. It preserves
the destination repository's history while importing an exact, reviewable
`dev-scaffold` snapshot.

[Crane's Castle PR #16](https://github.com/animan1/cranescastle-web/pull/16)
demonstrates the resulting review shape: one mechanical snapshot commit followed
by one repository-reconciliation commit. Application implementation starts only
after that baseline merges.

## Choose and record the source

Pin a full commit SHA from `dev-scaffold`; never import a moving branch name.
Record the source repository, commit, tree, import date, and archive checksum in
the destination repository. Keep the archive and extracted verification tree
outside the destination's Docker build context. Start from a clean destination;
untracked files can be collisions too and must not be overwritten.

For example, from the destination repository:

```bash
destination_root="$(pwd -P)"
scaffold_source="../dev-scaffold"
scaffold_commit="<full-dev-scaffold-commit>"
import_tmp="$(dirname "$destination_root")/.tmp/$(basename "$destination_root")-scaffold-import"

mkdir -p "$import_tmp"
git -C "$scaffold_source" archive --format=tar \
  --output="$import_tmp/scaffold.tar" "$scaffold_commit"
tar -xf "$import_tmp/scaffold.tar" -C "$import_tmp"
git -C "$scaffold_source" rev-parse "$scaffold_commit^{tree}"
shasum -a 256 "$import_tmp/scaffold.tar"
```

The sibling `.tmp` location is project-scoped but outside the destination
repository, so Docker cannot accidentally send the archive as build context.

## Inventory collisions before copying

Create the complete upstream path list and compare it with paths already owned
by the destination:

```bash
git -C "$scaffold_source" ls-tree -r --name-only "$scaffold_commit" \
  > "$import_tmp/upstream-paths"
git ls-files > "$import_tmp/destination-paths"
comm -12 \
  <(sort "$import_tmp/upstream-paths") \
  <(sort "$import_tmp/destination-paths") \
  > "$import_tmp/collision-paths"
```

Review every collision and record its intended result. Common collisions are
`README.md`, `LICENSE`, `.editorconfig`, and `.gitignore`. Preserve repository
identity and licensing; merge compatible editor and ignore rules deliberately.
Do not overlay collision files merely to restore them later.

## Commit the mechanical snapshot

Copy every non-collision path from the extracted snapshot. The result must be
byte-identical to the pinned scaffold tree. Do not customize names, settings,
profiles, or dependencies in this commit.

```bash
comm -23 \
  <(sort "$import_tmp/upstream-paths") \
  <(sort "$import_tmp/collision-paths") \
  > "$import_tmp/import-paths"
rsync -a --files-from="$import_tmp/import-paths" \
  "$import_tmp/" "$destination_root/"

while IFS= read -r path; do
  cmp --silent "$import_tmp/$path" "$destination_root/$path" || exit 1
done < "$import_tmp/import-paths"

git add --pathspec-from-file="$import_tmp/import-paths"
git diff --cached --stat
```

Commit the staged paths as the mechanical scaffold snapshot. Generated lock
files remain committed; mark them as generated in `.gitattributes` when that
improves review presentation.

## Reconcile the repository explicitly

Use a second commit to:

- merge each collision according to the recorded decision;
- restore the repository's name, purpose, and license;
- select the active `SCAFFOLD_PROFILE` and `CI_PROFILES`;
- record source provenance and the collision inventory; and
- retain inactive scaffold profiles so future upgrades remain mergeable.

Do not add framework, domain, content, credential, hostname, or deployment
adaptations here. Those belong in later application commits or pull requests.

## Verify and review

Run the selected profile's documented contracts from the reconciled tree:

```bash
make verify
make precommit
git diff --check
```

Open one pull request with the mechanical snapshot commit first and the narrow
reconciliation commit second. State the pinned source commit and tree, list
collisions, and give reviewers the byte-comparison result. Merge this baseline
before application work so later reviews show only application-specific changes.
