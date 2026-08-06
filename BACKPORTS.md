# Cuplr Backport Queue

This document tracks improvements to bring back from Cuplr so future projects
created from dev-scaffold inherit them.

## Statuses

- **Queued**: accepted for future backport work.
- **In progress**: implementation or review is underway.
- **Landed**: merged into `main`.

## Queued

### Harden backup and restore delivery

Backport the related backup and restore improvements as one cohesive change:

- Use file-mounted secrets for backup operations.
- Separate read-only backup credentials from destructive restore credentials.
- Publish an immutable backup image.
- Verify backup and restore together in an isolated restore environment.
