# Emergency release bundles

Emergency deployment uses the same application build, release checks, migrations,
deployment and smoke workflows as normal GHCR deployment. It replaces the image
transport with a portable bundle. SSH, host selection and file transfer belong to
the deployment coordinator; this repository owns packaging and tenant-side
installation. Each child application owns its configuration and release checks.
Applications with a separate deployment adapter, such as Frappe, are outside this
implementation.

## Operator commands

From a child application's checkout, with its normal deployment configuration
already prepared:

```bash
# Before an outage: save the latest successful main release.
make release-bundle-capture

# During a CI outage: commit the hotfix on a feature branch, then build it locally.
make release-bundle-hotfix

# On the destination, after trusted transfer of the bundle and checksum file:
make release-bundle-validate
make release-bundle-install EMERGENCY=1
```

Capture and hotfix both print `PASS`, the selected full revision, bundle path and
checksum. Hotfix builds run in the current clean checkout; no clone or checkout
switch is needed. Validation prints `PASS: bundle, destination architecture and
required inputs; no deployment changes`. Installation repeats validation, loads
the saved images, and invokes the application's existing deployment and smoke
hooks. Success means the application reports the bundled full revision.

`EMERGENCY=1` is required on **each installation invocation**. It is not saved as
a standing authorization. Neither validation nor installation fetches Git,
queries GitHub, authenticates to a registry, pulls images or rebuilds them.

## Defaults and overrides

The Make commands reuse the selected profile's normal deployment settings:
`PROD_ENV_FILE`, `RELEASE_COMPOSE_PROJECT`, host/port settings, ingress and
monitoring options. There is no required emergency input JSON file or second
credentials file. A previously recorded deployment project is reused unless a
project override is explicit; conflicting project records still reject the
operation. Existing credentials, volumes and application data stay on the host.

Use the same Make overrides that normal deployment needs when the destination
deviates from the application's defaults. For example, a host with an alternate
env file uses `PROD_ENV_FILE=/absolute/existing.env` for both normal and emergency
deployment. Store recurring host settings in the application's existing Make
configuration or deployment wrapper rather than retyping them for emergencies.
The private env file must already exist and have mode 600.

| Input | Default | Override purpose |
| --- | --- | --- |
| `RELEASE_REVISION` | Capture: latest successful main run's SHA. Hotfix: current committed HEAD. | Select a particular captured revision; a hotfix override must still equal HEAD. |
| `RELEASE_PLATFORM` | Current Docker daemon's OS/architecture. | Build or capture for a different destination, e.g. `linux/amd64`. |
| `BUNDLE_REPO` | GitHub repository from Git's `origin` URL. | Capture from another repository. |
| `BUNDLE_WORKFLOW` | `ci.yml`, the scaffold release workflow. | Child application's differently named release workflow file. |
| `BUNDLE_RUN_ID` | Latest successful main push run of that workflow. | Select a particular successful run. |
| `BUNDLE_ARTIFACT` | Selected run's `release-<sha>-<arch>` artifact; legacy `release-<sha>` only on AMD64. | Explicit artifact selection, still checked against revision and platform. |
| `BUNDLE` | `.local/emergency-release/bundle.tar` in the checkout. | Alternate output or transferred bundle location. |
| `BUNDLE_SHA256` | Contents of `<BUNDLE>.sha256`. | A checksum supplied directly through a trusted channel. |
| `BUNDLE_INPUTS` | Normal Make deployment settings. | Optional existing host JSON configuration, described below. |
| `BUNDLE_SMOKE_URL` | Loopback HTTP origin using the normal production/release HTTP port. | An application's different smoke origin; custom routing/TLS should use its smoke hook. |

Capture prints the selected run, revision, platform and artifact before packaging.
It checks that the run succeeded on main and the revision remains on main. If
necessary it fetches that exact commit during this online preparation step,
without switching the working checkout. It never falls back to an older release
if the selected artifact/images are unavailable. A laptop's current architecture
may differ from its CI image architecture: override `RELEASE_PLATFORM` explicitly
in that case; there is no automatic architecture substitution.

The conventional bundle path is refreshed only after packaging and checks pass.
Starting a refresh invalidates the previous default checksum selection, so a
failed attempt cannot cause a later bare install to select the previous bundle.
An explicitly supplied `BUNDLE` path must not already exist. Installation reads
only the selected path; it does not scan for an older usable bundle.

## Build prerequisites and hotfix follow-up

Use Python 3.12+, Git, Make, Docker Engine and Compose v2 with JSON configuration
and `pull_policy: never` support. Prepare enough storage for the source/history
and all saved runtime images. Supported destinations are `linux/amd64` and
`linux/arm64`; destination emulation does not satisfy the architecture check.

Capture requires GitHub/artifact and registry access **before** an outage.
Hotfixes require cached or reachable build dependencies and all non-application
runtime images already in the build daemon's cache for the selected architecture.
The hotfix command does not pull those runtime images or publish application
images. Prewarm base images/dependency caches for a completely offline build.

For a hotfix, first commit the intended changes on a feature branch. Packaging
rejects tracked or untracked uncommitted changes and any revision other than
HEAD before building. It builds for the selected platform, runs the adapter's
local checks through the existing Make workflows, and verifies the checks did
not change committed source or the application image IDs. These normal checks
may start and stop their usual development/release verification services; run
build-side validation in the development environment, not on production data.

Keep the original feature branch and bundle. After recovery, push the preserved
commit and reconcile it with current main through the normal PR/CI workflow.
Emergency installation does not waive that follow-up. `history.bundle` preserves
the commit if the original checkout is lost; restore the preserved tip from that
Git bundle before opening the normal PR.

## Child application integration

New applications forked from this scaffold already contain the common script,
Make includes and supplied profile adapters. Existing derived applications get
them by merging the scaffold update through their normal PR workflow. Resolve
application-owned Make/Compose customizations as part of that update; there is
no separate scaffold installation step.

Keep `.local/` and host-owned credentials excluded from each application's
Docker build context, as in the scaffold's `.dockerignore`.

An unchanged scaffold profile needs no `release-bundle.json` configuration.
`.scaffold-profile` selects its supplied adapter under
`profiles/emergency-release/`. The selected release commit must contain these
hooks; releases predating the update cannot be retroactively packaged by this
contract. Begin routine capture with the first successful adopted release.

For a customized topology, commit `release-bundle.json` in the application root:

```json
{
  "schema": 1,
  "profile": "server-rendered-django",
  "images": {"app": "RELEASE_BACKEND_IMAGE", "web": "RELEASE_WEB_IMAGE"},
  "check_targets": ["release-bundle-check"]
}
```

These values come from the application's existing configuration:

- `schema` is the bundle adapter format version, currently the literal `1`.
- `profile` is the `SCAFFOLD_PROFILE` value in committed `.scaffold-profile`.
- `images` keys are application service names from the release Compose files
  used by `COMPOSE_RELEASE`. Values are the manifest variables referenced by
  those services' `image:` definitions. The supplied profiles use
  `RELEASE_BACKEND_IMAGE` and `RELEASE_WEB_IMAGE`. Include workers/monitors that
  share application images; multiple services can map to the same variable.
- `check_targets` lists existing application Make targets that perform its local
  release validation. The supplied `release-bundle-check` hook calls the selected
  profile's quality, test, migration-drift and exact-image release checks.

Every runtime service must declare an image, including databases, workers,
initializers and optional monitors. Runtime inventory includes all Compose
profiles. The application-role map excludes external infrastructure and stock
database images, which are still captured automatically as runtime dependencies.
Application images must carry `org.opencontainers.image.revision=<full SHA>`.

Keep application-specific hook overrides included after the scaffold defaults.
Use the existing workflows rather than copying migration/deployment logic:

| Hook | Contract |
| --- | --- |
| `build-release-images` | Build locally for `RELEASE_PLATFORM`, label the revision, and respect `RELEASE_FILE`/`RELEASE_IMAGE_PREFIX`. Role suffixes correspond to the manifest variables, e.g. `RELEASE_WEB_IMAGE` uses `-web`. Do not publish. |
| `release-bundle-config` | Print only `$(COMPOSE_RELEASE) --profile '*' config --format json`. Respect the supplied env file, release manifest, project and host/port settings; no fetch/pull/build. |
| `release-bundle-check` | Invoke the application's normal local quality and exact-image release checks. Respect the selected build/project settings and use the normal disposable validation databases. Fail on any gate failure and do not rebuild release images. |
| `release-bundle-deploy` | Invoke the normal production initialization/deployment target with its existing migration ordering. All Compose actions use the supplied `COMPOSE_RELEASE`; pulls go through `RELEASE_PULL`, which the installer supplies as `true`. No build/fetch/login. |
| `release-bundle-smoke` | Invoke normal application smoke checks and verify the reported full revision, respecting `COMPOSE_RELEASE` and host/port settings. |

If a child app uses a different frontend suffix, map its actual manifest variable
and image build suffix consistently. Keep application-owned static, migration,
integration and prelaunch checks. Audit wrappers for hidden network calls or
alternate Compose commands; an installer cannot enforce offline behavior in
arbitrary application code that ignores the supplied interface.

## Destination and deployment-coordinator contract

Transfer the complete `.local/emergency-release/bundle.tar` to that same path
under the destination's existing application checkout. Install its matching
`.local/emergency-release/bundle.tar.sha256` through an independently trusted
channel. The checksum file is created by packaging; operators do not need to
look up or type SHA values. Internal checksums detect damage but do not prove
authorship, so an untrusted tar and checksum arriving together are insufficient.

The coordinator then runs, as the existing application operator:

```bash
make release-bundle-validate
make release-bundle-install EMERGENCY=1
```

It must bypass normal GitHub selection, Git fetch, registry authentication and
pull steps for emergency installation. To transfer elsewhere, override `BUNDLE`
and keep the checksum at `<BUNDLE>.sha256`, or supply a trusted `BUNDLE_SHA256`.
No transfer/SSH behavior is implemented here.

Validation checks the outer checksum, complete payload checksums, safe archive
members, exact source/history/manifest revision, validation evidence, image
config/layer digests and platform. It also checks the destination architecture,
project records, env file, bind files and external Compose resources before
loading images or changing services. Installation repeats preflight under a
local lock. It freezes all services to content IDs with `pull_policy: never` and
no build definitions, then calls the existing deploy/migration/smoke hooks.

Host credentials are never packed. Named volumes retain their normal identity;
untracked bind configuration resolves against the destination checkout and
committed deployment source comes from the bundle. The installer leaves the
checkout HEAD unchanged. Private state in `.local/emergency-release/` contains
resolved Compose configuration and retained source paths; do not print or delete
those paths while containers may bind files from them.

The private `deployment.json` audit records status, revision, image identities,
validation source, emergency mode, checksum, project and retained directory.
It does not record credentials. A failure after deployment starts can leave
migrations applied; inspect service/schema state before attempting recovery.

An optional existing `BUNDLE_INPUTS` JSON file has fields `project`, `env_file`,
`variables` and `smoke_url`. `env_file` is an absolute private existing file;
`variables` contains only supported non-secret Make overrides. This compatibility
override is useful for a coordinator already managing host input records; it is
not needed for ordinary operators and does not duplicate credentials.

## Bundle v1 and database recovery

The portable tar contains `bundle.json` (mode/revision/platform/profile, every
runtime image identity/reference, payload SHA256s), `source.tar` (exact committed
source), `history.bundle` (preserved Git history), `release.env` (manifest),
`images.tar` (all distinct runtime image IDs) and `evidence.json` (successful main
run or locally passing checks). Capture also includes `artifacts.tar` with the
original manifest and SPDX SBOMs. V1 is single-architecture and rejects source
symlinks/hardlinks/devices; applications requiring those need a format extension.

Release recovery runs the selected release's normal migrations. It does not
reverse an already applied schema migration or recover deleted data. Assess
migration compatibility and backups using the application's existing procedure;
use the separately tested database restore workflow when schema/data recovery
is necessary. There is no automatic rollback or older-release fallback.

## Development review

```bash
make be.sync
make be.verify
```

Use the existing dev-scaffold dummy application, its normal release deployment
recipe in the root README, and its established project/configuration/data for
manual review. Development `make up` alone does not exercise release transport.
Use the capture/hotfix/validate/install operator commands above; do not introduce
another review application or Compose project. Confirm each deployed revision,
preserved data/configuration and fail-before-mutation rejection for incomplete
or wrong-platform bundles. Do not use production.
