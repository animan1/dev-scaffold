# Release transport and tenant adoption

`build` and `download` produce a portable directory at
`.local/releases/<full revision>/<application profile>/<amd64|arm64>/`.
Transfer the whole directory; an image tag or manifest alone is insufficient.

| File | Contract |
| --- | --- |
| `manifest.json` | Schema `1`; full `revision`, selected `profile`, `backup_profile`, Linux `platform`, image `prefix`, and role-to-immutable-reference `images` map. Publication additionally records role-to-content-ID `image_ids` and the originating main workflow run. Required runtime roles are `backend`, `web`, `db`, `backup`; captured tooling and tenant runtime roles are included too. |
| `source.tar` | Exact `git archive` of the committed revision, including deployment configuration and commands; excludes private host inputs. |
| `source.bundle` | That revision's reachable Git history, preserving local hotfix commits and allowing an offline checkout. |
| `images.tar.gz` | Docker image archive containing the complete recorded image set for the destination architecture. |
| `source-check.json`, `image-check.json`, `runtime-check.json` | Successful source, image and ephemeral runtime evidence for this revision and image set. |
| `integrity.json` | SHA-256 hashes checked before loading or deploying the captured artifacts. |

The published GitHub Release asset is named
`release-<profile>-linux-<architecture>.tar` under `release-<full revision>`.
It contains deployment source, Git history, manifest, evidence (including any
SPDX SBOMs), and metadata checksums. `download` pulls only its recorded digests
and captures the resulting complete portable directory. Workflow artifacts with
an expiration date are not the release store.

## Infra handoff

Infra owns SSH, transfer, host preparation and traffic cutover. Scaffold commands
operate on the current host. Before an outage, retain tested release directories
off-host; a locally built hotfix can be captured during an outage using the same
local build workflow. Build for the destination with
`RELEASE_PLATFORM=linux/arm64` or `linux/amd64` on the build host.

For a replacement checkout without network access:

```sh
git clone /transferred-release/source.bundle /deployment/dev-scaffold
```

Use the tenant's established checkout basename or configured `PROJECT_NAME`.
Copy the transferred release directory into the path above in that checkout.
Restore its original private configuration, credentials and durable host state;
keep these separate from the release bundle. Then run:

```sh
cd /deployment/dev-scaffold
make download
make setup
make prepare-prod
make config-prod
make up-prod
make verify-prod
```

With a complete transferred bundle, these commands need no GitHub API, Git
fetch, registry login, image pull or rebuild. `download` validates and loads the
captured images; `setup` reuses captured tooling. Stop at the first failure.
For database restoration on a new host, use the disaster-recovery workflow
instead of starting the application before restore.

## Adoption boundary

Applications retain their Compose services, Django migrations and private
configuration. Declare additional runtime service image roles in
`deploy/dependencies.json` (`runtime` maps service names to pinned dependencies
or existing image roles), and extend application-owned `test_deployment` checks.
Supply host ingress/TLS, provider credentials, non-database data recovery and
notification configuration through the tenant/infra contracts. Frappe is outside
this adapter.

The three namespaces are `<project>-dev`, `<project>-prod` and
`<project>-ephemeral`. Do not start an existing tenant under a renamed project:
first plan and verify its database/volume migration and preserve its original
credentials. A legacy database without recorded release compatibility is
rejected; establish its known compatible baseline explicitly before adoption.
Renaming resources or choosing the checkout revision cannot establish that fact.
