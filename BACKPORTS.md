# Cuplr Backport Queue

This document tracks improvements to bring back from Cuplr so future projects
created from dev-scaffold inherit them. Checked items have landed at their
destination; unchecked items still require implementation, proof, or
investigation.

## Ready to track or request now

- [x] Make production teardown non-destructive by default.
  - `down-prod` must not remove production volumes.
  - Provide a separately named, guarded destructive operation.
  - Destination: `dev-scaffold`
  - Status: Landed in
    [dev-scaffold#2](https://github.com/animan1/dev-scaffold/pull/2).

- [x] Remove generated and editor artifacts from Git.
  - Remove tracked `db.sqlite3` and `*.un~` files.
  - Strengthen ignore and repository-hygiene checks.
  - Destination: `dev-scaffold`
  - Status: Landed in
    [dev-scaffold#3](https://github.com/animan1/dev-scaffold/pull/3).

- [x] Use one enforced Python formatter.
  - Select Ruff Format or Black, not both.
  - Include its non-mutating check in the shared local/CI verification command.
  - Destination: `dev-scaffold`
  - Status: Landed in
    [dev-scaffold#4](https://github.com/animan1/dev-scaffold/pull/4).

- [x] Avoid executing the backend tests twice.
  - One coverage-enabled pytest run should satisfy both testing and coverage
    gates.
  - Destination: `dev-scaffold`
  - Status: Landed in
    [dev-scaffold#4](https://github.com/animan1/dev-scaffold/pull/4).

- [x] Add an optional immutable-release profile.
  - Build once in CI.
  - Test the exact image.
  - Publish a commit-addressed GHCR image with digest and SBOM.
  - Deploy and roll back by digest.
  - Destination: `dev-scaffold`; Cuplr should evaluate the same convention.
  - Status: Base profile landed in
    [dev-scaffold#5](https://github.com/animan1/dev-scaffold/pull/5);
    digest publication landed in
    [dev-scaffold#6](https://github.com/animan1/dev-scaffold/pull/6); SBOM
    publication is tracked in
    [dev-scaffold#8](https://github.com/animan1/dev-scaffold/pull/8).

- [x] Support deployment behind separately owned host ingress.
  - Bind application origins to configurable loopback ports.
  - Trust forwarded headers only across the documented proxy boundary.
  - Do not make the application own public TLS.
  - Include routed smoke checks without prescribing Caddy.
  - Destination: `dev-scaffold` and Cuplr.
  - Status: Tracked in
    [dev-scaffold#9](https://github.com/animan1/dev-scaffold/pull/9).

- [x] Add optional external dead-man monitoring.
  - Support content-free operational, backup, and restore heartbeats.
  - Alerts must use an external provider instead of application SMTP.
  - Include secret URL handling, bounded grace periods, and recovery notices.
  - Destination: `dev-scaffold`
  - Status: Backported from the Cuplr operational-monitoring pattern; pull
    request link pending.

## Track now, implement upstream after website proof

- [ ] Reduce development/production database drift.
  - Provide an optional full-Docker PostgreSQL development and CI path.
  - Destination: `dev-scaffold`
  - Proof required: Phase 4 website implementation.

- [ ] Add an optional server-rendered web quality profile.
  - Playwright customer journeys, axe accessibility checks, redirect/link
    validation, visual checks, and Lighthouse performance budgets.
  - Destination: `dev-scaffold`
  - Proof required: Phase 4 website implementation.

- [ ] Add an optional immutable backup profile.
  - Use file-mounted backup secrets.
  - Publish digest-pinned PostgreSQL, Restic, and rclone tooling as an immutable
    backup image.
  - Separate least-privilege read-only backup credentials from destructive
    restore credentials.
  - Support combined database/media recovery points, freshness evidence, and
    isolated end-to-end restore verification.
  - Destination: Cuplr now; `dev-scaffold` after website proof.
  - Status: New request.

## Investigation candidate

- [ ] Make Redis and Celery demand-driven optional services.
  - They should not be direct baseline dependencies without a demonstrated use.
  - Destination: `dev-scaffold`
  - Before requesting: Confirm no hidden scaffold contract requires them.
