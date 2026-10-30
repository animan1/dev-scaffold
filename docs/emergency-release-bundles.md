# Emergency release bundles

This extends the existing image build, release verification, migration and smoke
workflows. Normal deployment still selects a successful GitHub main release.
Nothing here deploys through SSH or changes ingress. Frappe is excluded.

## Ownership and prerequisites

- **dev-scaffold** owns the portable format, packaging, preflight, and installer.
- **cranescastle-infra** owns explicit release selection, trusted bundle/checksum
  transfer, operator/host selection, and invoking the tenant's installer.
- **Applications** own their committed adapter, local checks, required runtime
  configuration, migration compatibility, and production/smoke hooks.

Use Python 3.11+, Git, Make, Docker Engine, and current Compose v2 with JSON
configuration and `pull_policy: never` support. Packaging additionally needs space
for a complete source/history bundle and a Docker save archive. Capture needs
`gh`, repository/artifact read access and registry read access **before** an outage.
Hotfix builds need cached or reachable build dependencies, but no GitHub Actions
minutes, GitHub API, registry login, or image publication. An outage does not make
uncached base images or dependency downloads magically available: prepare those
caches ahead of time. Destination Docker architecture must match `linux/amd64`
or `linux/arm64`; emulation does not satisfy the destination architecture check.

## Adopt into an application

1. Adopt `scripts/release_bundle.py`, `profiles/release-transport.mk`,
   `profiles/emergency-release/`, and the Make includes. Apply the small existing
   profile changes: `RELEASE_PLATFORM`, revision labels on **both** builds,
   `PROD_ENV_FILE`, and the `RELEASE_PULL` seam. Merge through a normal PR first.
   Older releases without the committed adapter/hooks cannot be packaged by this
   contract. Start routine capture with the first adopted successful release.
2. Keep the production Compose project name and volumes exactly as deployed.
   Make every runtime service declare an `image`, including PostgreSQL, workers,
   monitors, static initializers and any application-owned sidecar. The inventory
   hook resolves **all** profiles so optional services are captured too. External
   infrastructure (such as the separately owned Caddy ingress) stays external.
3. For an unmodified scaffold profile, the supplied adapter works directly. For a
   tenant topology, commit `release-bundle.json` with the following shape (example
   for a server-rendered tenant; include monitor if the tenant defines it):

   ```json
   {
     "schema": 1,
     "profile": "server-rendered-django",
     "images": {
       "app": "RELEASE_BACKEND_IMAGE",
       "web": "RELEASE_WEB_IMAGE"
     },
     "check_targets": ["release-bundle-check"]
   }
   ```

   Image roles map Compose services to manifest variables. Multiple services may
   use the same variable. A custom adapter can declare another profile name;
   the same name must select the tenant Make implementation. Every application
   image must carry `org.opencontainers.image.revision=<full commit SHA>`.
4. Implement these hooks using the existing application workflows. Do not copy
   migrations into a new installer:

   | Hook | Contract |
   | --- | --- |
   | `build-release-images` | Build `<RELEASE_IMAGE_PREFIX>-backend:<RELEASE_REVISION>` and `-web:<RELEASE_REVISION>` for `RELEASE_PLATFORM`; no publication. Other role variables follow the corresponding lower-case suffix. |
   | `release-bundle-config` | Print only `$(COMPOSE_RELEASE) --profile '*' config --format json` to stdout. Use the supplied absolute `PROD_ENV_FILE` and `RELEASE_FILE`, explicit project and port inputs. Never fetch, pull or build. |
   | `release-bundle-check` | Required local quality, test, migration-drift and exact-image smoke checks. Respect `PROJECT_NAME`, `COMPOSE_PROJECT_NAME`, `RELEASE_COMPOSE_PROJECT`, and supplied image prefix. Use disposable databases. Fail on any gate failure; do not rebuild verified images. |
   | `release-bundle-deploy` | Invoke existing production initialization/deployment, preserving migration ordering. All Compose actions must use the supplied `COMPOSE_RELEASE`. Pull steps must go through `RELEASE_PULL` (normal default pulls, installer supplies `true`). Never build/fetch/login. |
   | `release-bundle-smoke` | Invoke existing application smoke checks **and verify the reported full revision**. Respect `COMPOSE_RELEASE` and production host/port inputs. Fail on mismatch. |

   Keep an application-specific hook file included **after** scaffold's defaults
   when overriding recipes. Audit all target prerequisites for hidden fetches,
   builds, alternate Compose commands or implicit pulls. The installer pins and
   disables pulls for `COMPOSE_RELEASE`; it cannot enforce offline behavior in
   arbitrary application shell code that ignores that interface.

5. **cranescastle-web:** preserve its `PRODUCTION_COMPOSE_PROJECT`, production env,
   loopback port, and prelaunch host. Wire the deploy hook to `deploy-production`
   and smoke hook to the existing prelaunch/release smoke target plus revision
   comparison. Ensure those wrappers forward the supplied Compose command and
   production env rather than independently reconstructing it.
6. **cuplr:** map its actual backend/frontend/static-init services explicitly.
   Its existing image suffix is `-frontend`, whereas the scaffold default is
   `-web`. Use `RELEASE_FRONTEND_IMAGE` consistently in the tenant adapter, build,
   capture manifest and production Compose. Retain application-owned integration,
   static-file, migration and prelaunch checks; do not replace them with generic
   scaffold smoke. These are adoption instructions, not a claim either tenant
   has already adopted the changes.

## Inputs and routine capture

Create a host-local JSON file outside the source repository (or under ignored
`.local/`). It contains paths and non-secret options, **not credentials**:

```json
{
  "project": "cranescastle-production",
  "env_file": "/home/operator/services/cranescastle-web/deploy/.env.prod",
  "variables": {
    "RELEASE_HTTP_PORT": "18080",
    "PRODUCTION_COMPOSE_PROJECT": "cranescastle-production",
    "PRODUCTION_HTTP_PORT": "18080",
    "PRODUCTION_SMOKE_HOST": "preview.example.com"
  },
  "smoke_url": "http://127.0.0.1:18080"
}
```

`env_file` must exist, be absolute, and have mode 600. Supported non-secret
variables are `HOST_INGRESS`, `HOST_INGRESS_HOST`, `HOST_INGRESS_PORT`, `MONITORING`,
`RELEASE_HTTP_PORT`, `RELEASE_HTTPS_PORT`, `RELEASE_HOST`,
`PRODUCTION_COMPOSE_PROJECT`, `PRODUCTION_HTTP_PORT`, `PRODUCTION_SMOKE_HOST`.
Paths and Make values must use the simple safe characters accepted by preflight.
Custom adapters needing additional variables require an explicit installer
contract update; arbitrary Make command overrides are deliberately rejected.

Use a separate build-input file with existing **non-production** validation env
and available loopback ports. Packaging does not deploy; it only renders the
runtime inventory. Local hotfix gates use `emergency-check-<revision>` projects,
never the destination project's volumes. Do not use production credentials for
build-side validation. The default smoke hook uses trusted TLS or HTTP loopback;
applications with a private CA or special routing should supply their existing
application smoke hook rather than disable certificate verification.

Capture the specifically selected successful main run while GitHub/GHCR are
available. Source revision must be present locally. Use the exact artifact name;
there is no latest-release selection or fallback:

```bash
make release-bundle-capture \
  RELEASE_REVISION=<full-commit-sha> RELEASE_PLATFORM=linux/arm64 \
  BUNDLE_REPO=animan1/cranescastle-web BUNDLE_RUN_ID=<successful-main-run-id> \
  BUNDLE_ARTIFACT=release-<full-commit-sha>-arm64 \
  BUNDLE_INPUTS=/absolute/build-inputs.json BUNDLE=/absolute/captured-release.tar
```

Expected: the specified run is confirmed successful and on main, its manifest
and SBOMs are downloaded, every runtime image is pulled for that architecture,
and the command prints `PASS: capture bundle` and `BUNDLE_SHA256=<64 hex>`.
Historical unsuffixed artifacts are accepted only for AMD64. No older run is
selected if this run/artifact is unavailable. Copy the printed checksum through
an independently trusted channel; internal checksums alone do not establish
authorship. A bundle contains executable deployment source and must be trusted.

## Hotfix when CI is unavailable

Create and preserve a feature-branch commit with the intended fix. Do not build
from an uncommitted working tree. Run from that exact clean HEAD:

```bash
make release-bundle-hotfix \
  RELEASE_REVISION=<full-hotfix-commit-sha> RELEASE_PLATFORM=linux/arm64 \
  BUNDLE_INPUTS=/absolute/build-inputs.json BUNDLE=/absolute/hotfix-release.tar
```

Expected: the committed source is cloned locally into an isolated build tree,
images are built for the destination architecture with revision labels, all
adapter-declared checks pass against the same application image IDs, and the
command prints `PASS: hotfix bundle` plus the independent SHA256. Any failed
check, changed source/image, missing runtime image or wrong architecture fails
packaging. Full check evidence identifies the targets, passing time and image
IDs; it is local validation, not a GitHub attestation. Application-specific
checks must be substantive: the adapter determines their coverage.

The bundle preserves reachable commit history in `history.bundle`. Keep the
original branch and bundle until reconciliation is complete. After service
recovery, push the preserved hotfix branch and open a normal PR; resolve changes
against current main and run ordinary CI once capacity returns. If the original
checkout is lost, restore the branch locally from the trusted bundle's
`history.bundle` before doing that PR work. Do not rewrite it as an untraceable
working-tree patch. Emergency installation does not waive this follow-up.

## Destination commands and infra handoff

Infra transfers the complete tar file without extraction, verifies the trusted
checksum, and invokes these Make targets **as the tenant operator from its
existing checkout**, with an explicit destination inputs file. It must bypass
its normal GitHub selection, Git fetch, registry-auth and pull sequence for this
mode. No SSH behavior belongs in this repository.

```bash
make release-bundle-validate \
  BUNDLE=/absolute/transferred-release.tar BUNDLE_SHA256=<trusted-64-hex> \
  BUNDLE_INPUTS=/absolute/destination-inputs.json

make release-bundle-install EMERGENCY=1 \
  BUNDLE=/absolute/transferred-release.tar BUNDLE_SHA256=<trusted-64-hex> \
  BUNDLE_INPUTS=/absolute/destination-inputs.json
```

Validation must print `PASS: bundle, destination architecture and required
inputs; no deployment changes`. Installation repeats preflight under a
tenant-local lock. Missing `EMERGENCY=1` rejects that invocation. It checks:

- Trusted outer checksum, complete payload checksums, safe archive members,
  exact source/history/manifest revision, and applicable validation evidence.
- Image config identities, uncompressed layer digests and target architecture
  before any image load; destination daemon architecture must match.
- Every runtime service, every manifest role and reference, env file, required
  bind inputs, external secrets/configs/networks/volumes, and project identity
  against existing scaffold/infra deployment records where present.

Then it loads images by content ID, freezes the runtime configuration to those
IDs with `pull_policy: never` and no build definitions, and calls the existing
deployment/migration and smoke hooks. Neither the installer nor bundled history
verification calls GitHub, Git fetch, registry login or image pull. No mutable
tag selects an installed image. Registry manifest digests remain recorded in
captured release metadata; locally built hotfixes are pinned by config content
ID because they have not been published to a registry.

The inputs must name the **existing** project; guessing a new project can select
a different dataset. Existing named volumes/data are retained, untracked bind
configuration resolves against the tenant checkout, and committed deployment
files come from the exact bundled source. Credentials are resolved on the host,
never packed. Resolved Compose configuration is private local state, not printed
or included in the audit. Keep `.local/emergency-release/` private and backed up
as appropriate. Do not delete retained deployment directories while containers
may bind their committed files. The installer does not change checkout HEAD.

Expected success: the existing app reports the bundled full revision and its
normal smoke passes. `.local/emergency-release/deployment.json` records status,
revision, image identities, validation source, emergency mode, checksum, project,
time and retained directory. Each retained directory also has its own audit.
A failure after deployment starts is recorded and may leave migrations applied;
inspect the actual service/schema state before another attempt. There is no
automatic rollback or silent fallback.

## Bundle v1 transfer contract

One uncompressed portable tar file, with these regular-file members:

| File | Purpose |
| --- | --- |
| `bundle.json` | Schema 1; mode `capture`/`hotfix`; full revision; profile; platform; service-to-image identities/references; SHA256 of each payload. |
| `source.tar` | Exact `git archive` of the selected commit. |
| `history.bundle` | Self-contained Git history reachable from `emergency-release`; preserves hotfix reconciliation. |
| `release.env` | Original captured release manifest, or hotfix manifest with application content IDs. |
| `images.tar` | Docker save of every distinct runtime image ID, including data services and optional profiles. |
| `evidence.json` | Selected successful main run/artifact identity, or locally passing declared checks and exact image IDs. |
| `artifacts.tar` | Capture only: original release manifest and SPDX SBOM artifacts. |

Reject incomplete bundles rather than trying the registry. Integrity checks are
not signatures; the independent outer checksum must arrive through a trusted
channel. Source/archive symlinks, hardlinks and device entries are rejected in
v1 rather than followed. Applications using committed symlinks need a deliberate
format extension before adoption. Format v1 is intentionally single-architecture.

## Release recovery is not database rollback

This restores an application release and runs that release's normal migration
workflow. Selecting an older bundle does not reverse an already-applied schema
migration, recover deleted data, or prove compatibility. Before an emergency
installation, assess application migration compatibility and available database
backups through the existing tenant operational procedure. If schema/data
recovery is required, use the separately tested database restore procedure.

## Local acceptance

```bash
make release-bundle-test
make release-transport-test
make release-bundle-exercise
```

The exercise prepares three test images, then starts two disposable privileged
Docker-in-Docker engines with `--network none`. It seeds only the build engine,
captures a release through a real local OCI registry, stops that registry, and
installs into the empty destination engine. GitHub run/artifact responses are
deterministic synthetic fixtures; the exercise creates no Actions runs. All
GitHub calls fail after the outage boundary. It rejects incomplete and
wrong-platform bundles without changing destination container IDs, builds and
checks an exact committed hotfix without external access, and installs it on the
same destination project. The app visibly changes from `release-one` to
`hotfix-two`; both revision SHAs are checked. An existing SQLite row, private
bind configuration and env value must survive. Test engines/volumes are removed
afterward; no production daemon resources are selected. This tests the reusable
adapter contract, not either tenant's application-specific acceptance suite.
