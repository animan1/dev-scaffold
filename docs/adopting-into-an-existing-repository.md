# Adopt the scaffold before application development

Use this process when a repository already contains planning, identity, or
license files but does not yet contain meaningful application code. It preserves
the destination repository's history while importing an exact, reviewable
`dev-scaffold` snapshot.

The recommended review shape is one mechanical snapshot commit followed by one
repository-reconciliation commit. Application implementation starts only after
that baseline merges.

## Prepare the import

Start from clean scaffold and destination repositories. From a local
`dev-scaffold` checkout, run:

```bash
scripts/prepare-scaffold-adoption
```

The helper prompts for the destination and an exact scaffold commit. It then:

- creates a project-scoped sibling `.tmp` directory outside the destination's
  Docker build context;
- archives the pinned commit and records its repository, tree, and SHA-256;
- inventories every upstream path already present at the destination;
- writes separate collision and non-collision path lists; and
- asks before copying and staging any non-collision path.

It refuses a dirty destination, an existing import workspace, or a destination
that is the scaffold checkout. It never overwrites collision paths. The archive,
extracted tree, reports, and prepared provenance values remain outside the
destination repository for review.

For the easiest mechanical review, accept non-collision files byte-for-byte from
the pinned scaffold tree. A project may instead apply them manually when its
workflow requires that, but should still record which paths differ and why.

## Record provenance and collisions

Copy [the blank provenance template](scaffold-provenance.env.example) to the
destination as `docs/scaffold-provenance.env`, fill it with the values prepared
by the helper, and commit it. It is tracked metadata, not a secret file.

Review every collision and record its intended result. Common collisions are
`README.md`, `LICENSE`, `.editorconfig`, and `.gitignore`. Preserve repository
identity and licensing; merge compatible editor and ignore rules deliberately.
Do not overlay collision files merely to restore them later.

## Commit the mechanical snapshot

The helper can copy, byte-verify, and stage every non-collision path. Review the
staged paths before committing them as the mechanical scaffold snapshot. Do not
customize names, settings, profiles, or dependencies in this commit.

Generated lock files remain committed; mark them as generated in
`.gitattributes` when that improves review presentation.

## Reconcile the repository explicitly

Use a second commit to:

- merge each collision according to the recorded decision;
- restore the repository's name, purpose, and license;
- select the active `SCAFFOLD_PROFILE` and `CI_PROFILES`;
- complete the provenance record and collision inventory; and
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
collisions, and give reviewers the comparison result. Merge this baseline before
application work so later reviews show only application-specific changes.
