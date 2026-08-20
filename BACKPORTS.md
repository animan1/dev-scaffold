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
  - Status: Monitoring foundation backported in
    [dev-scaffold#11](https://github.com/animan1/dev-scaffold/pull/11);
    compatible greenfield extension tracked separately in
    [dev-scaffold#12](https://github.com/animan1/dev-scaffold/pull/12).

- [x] Add an optional server-rendered Django application profile.
  - Keep the React/Vite profile intact and select behavior with one committed
    switch rather than copying or deleting scaffold files.
  - Use full-Docker Django and PostgreSQL development, the same aggregate Make
    verification contract in CI, safe state-preserving teardown, a non-root
    production image, and routed smoke tests.
  - Keep React, Vite, pnpm, frontend services, and frontend quality gates
    inactive under this profile.
  - Destination: `dev-scaffold`
  - Status: Implemented across
    [dev-scaffold#19](https://github.com/animan1/dev-scaffold/pull/19) through
    [dev-scaffold#23](https://github.com/animan1/dev-scaffold/pull/23).

- [ ] Add a Dockerized dependency-lock workflow.
  - Provide `make deps.lock` for the selected profile using the pinned uv image
    so developers do not need host-installed uv.
  - Destination: `dev-scaffold`
  - Status: Requested; implementation in progress.

- [ ] Use the selected profile's Python tool configuration.
  - Make Ruff, MyPy, pytest, and coverage explicitly load
    `profiles/server-rendered-django/pyproject.toml` when that profile is
    selected instead of discovering the inactive backend configuration.
  - Destination: `dev-scaffold`
  - Status: Requested; implementation in progress.

- [x] Make operational notifications identify their deployment context.
  - Include the environment, release revision, check, and transition in email
    notifications.
  - Validate SMTP configuration only when SMTP is the selected notification
    transport.
  - Destination: `dev-scaffold`
  - Status: Proven in
    [Cuplr#104](https://github.com/animan1/cuplr/pull/104) and included in the
    monitoring backport in
    [dev-scaffold#11](https://github.com/animan1/dev-scaffold/pull/11).

- [ ] Add an optional release-promotion safety profile.
  - Compare immutable release metadata between staging and production.
  - Derive each health endpoint from its operator-facing review URL instead of
    maintaining duplicate URLs that can drift.
  - Surface promotion reminders in both environments without implying that
    staging can promote itself.
  - Escalate an overdue promotion once at bounded thresholds and send one
    recovery notification when production catches up.
  - Provide development-only notification previews without mutating monitoring
    state or contacting external recipients.
  - Destination: `dev-scaffold`
  - Status: Backport requested from
    [Cuplr#105](https://github.com/animan1/cuplr/pull/105),
    [Cuplr#106](https://github.com/animan1/cuplr/pull/106), and
    [Cuplr#107](https://github.com/animan1/cuplr/pull/107). The underlying
    staged-release comparison was proven in
    [Cuplr#99](https://github.com/animan1/cuplr/pull/99) through
    [Cuplr#101](https://github.com/animan1/cuplr/pull/101).

- [ ] Serialize shared development dependency synchronization.
  - When multiple development services share one dependency environment, make
    secondary services wait for the backend's dependency sync and health check
    instead of racing their own sync.
  - Destination: `dev-scaffold`
  - Status: Backport requested from the dependency-sync fix in
    [Cuplr#107](https://github.com/animan1/cuplr/pull/107); apply when an
    optional development monitor or another shared-environment service is
    enabled.

## Consider on next major release

- [ ] Reconsider a standalone external dead-man implementation.
  - Evaluate whether a separate `app.deadman` module offers material benefits
    over the integrated monitoring and heartbeat architecture.
  - Reference implementation preserved in the closed draft
    [dev-scaffold#10](https://github.com/animan1/dev-scaffold/pull/10).

## Track now, implement upstream after website proof

- [ ] Reduce development/production database drift.
  - Provide an optional full-Docker PostgreSQL development and CI path.
  - Destination: `dev-scaffold`
  - Status: Implemented by the server-rendered Django profile across
    [dev-scaffold#19](https://github.com/animan1/dev-scaffold/pull/19) through
    [dev-scaffold#23](https://github.com/animan1/dev-scaffold/pull/23); keep
    unchecked until the complete stack lands.

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
  - Derive freshness from the newest recoverable repository snapshot rather
    than trusting a last-success marker that can outlive a deleted snapshot.
  - Refresh repository-derived status independently of the backup schedule and
    report unreadable or empty repository state as unavailable.
  - Destination: Cuplr now; `dev-scaffold` after website proof.
  - Status: Backup profile requested; repository-derived freshness proven in
    [Cuplr#103](https://github.com/animan1/cuplr/pull/103), with bounded
    two-interval alert grace proven in
    [Cuplr#104](https://github.com/animan1/cuplr/pull/104).

## Investigation candidate

- [ ] Make Redis and Celery demand-driven optional services.
  - They should not be direct baseline dependencies without a demonstrated use.
  - Destination: `dev-scaffold`
  - Before requesting: Confirm no hidden scaffold contract requires them.
