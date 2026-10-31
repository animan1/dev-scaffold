"""Portable emergency releases. No third-party Python dependencies.

The bundle is trusted executable deployment source. Its independently transferred
SHA256 is the trust boundary; internal checksums detect damage, not authorship.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def run(*args, cwd=None, env=None, output=False):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, stdout=subprocess.PIPE if output else None
    ).stdout


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def revision(value):
    require(
        re.fullmatch("[0-9a-f]{40}", value or ""), "require full 40-character revision"
    )
    return value


def platform(value):
    require(
        value in ("linux/amd64", "linux/arm64"),
        "supported platforms: linux/amd64, linux/arm64",
    )
    return value


def image_id(value):
    require(
        re.fullmatch("sha256:[0-9a-f]{64}", value or ""), "invalid image content ID"
    )
    return value


def members(archive):
    seen = set()
    for item in archive.getmembers():
        path = PurePosixPath(item.name)
        require(
            not path.is_absolute() and ".." not in path.parts and item.name not in seen,
            "unsafe or duplicate archive path",
        )
        require(item.isfile() or item.isdir(), "archive links/devices are unsupported")
        seen.add(item.name)
    return archive.getmembers()


def extract(path, destination):
    with tarfile.open(path) as archive:
        for item in members(archive):
            target = destination / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, target.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                target.chmod(item.mode & 0o755)


def adapter(root, profile_name):
    path = root / "release-bundle.json"
    if not path.exists():
        path = root / "profiles/emergency-release" / (profile_name + ".json")
    value = read_json(path)
    require(
        set(value) == {"schema", "profile", "images", "check_targets"},
        "invalid adapter fields",
    )
    require(
        value["schema"] == 1 and value["profile"] == profile_name,
        "adapter/profile mismatch",
    )
    require(
        value["images"]
        and all(
            re.fullmatch("[a-z][a-z0-9_-]*", key)
            and re.fullmatch("RELEASE_[A-Z_]+_IMAGE", name)
            for key, name in value["images"].items()
        ),
        "invalid image role mapping",
    )
    require(
        value["check_targets"]
        and all(
            re.fullmatch("[a-z][a-z0-9_.-]*", name) for name in value["check_targets"]
        ),
        "invalid validation targets",
    )
    return value


def inputs(path):
    value = read_json(path)
    require(
        set(value) == {"project", "env_file", "variables", "smoke_url"},
        "invalid inputs fields",
    )
    require(
        re.fullmatch("[a-z0-9][a-z0-9_-]+", value["project"]),
        "explicit Compose project required",
    )
    config = Path(value["env_file"])
    require(
        config.is_absolute() and config.is_file(), "absolute existing env_file required"
    )
    require(
        re.fullmatch("[a-zA-Z0-9_./-]+", str(config)),
        "env_file path must be shell/Make safe",
    )
    require(config.stat().st_mode & 0o077 == 0, "env_file must be private (mode 600)")
    # Make command-line values execute Make expansion and later shell recipes.
    for key, content in value["variables"].items():
        require(re.fullmatch("[A-Z][A-Z0-9_]*", key), "invalid input variable name")
        require(
            key
            not in {
                "COMPOSE_RELEASE",
                "RELEASE_PULL",
                "SHELL",
                "MAKE",
                "MAKEFLAGS",
                "MAKEFILES",
                "SCAFFOLD_ROOT",
                "SCAFFOLD_PROFILE",
                "RELEASE_REVISION",
                "RELEASE_FILE",
                "RELEASE_PLATFORM",
                "PROD_ENV_FILE",
                "RELEASE_COMPOSE_PROJECT",
            },
            "reserved input variable",
        )
        require(
            key
            in {
                "HOST_INGRESS",
                "HOST_INGRESS_HOST",
                "HOST_INGRESS_PORT",
                "MONITORING",
                "RELEASE_HTTP_PORT",
                "RELEASE_BIND_ADDRESS",
                "RELEASE_HTTPS_PORT",
                "RELEASE_HOST",
                "PRODUCTION_COMPOSE_PROJECT",
                "PRODUCTION_HTTP_PORT",
                "PRODUCTION_SMOKE_HOST",
            },
            "unsupported input variable",
        )
        require(
            isinstance(content, str) and re.fullmatch("[a-zA-Z0-9_./:@,+-]*", content),
            "input variables must be non-secret simple Make values",
        )
    require(
        re.fullmatch(r"https?://(?:localhost|127\.0\.0\.1):[0-9]+", value["smoke_url"]),
        "smoke_url must be a loopback HTTP(S) origin",
    )
    return value


def environment():
    env = os.environ.copy()
    for key in (
        "MAKEFLAGS",
        "MFLAGS",
        "MAKEOVERRIDES",
        "MAKEFILES",
        "COMPOSE_FILE",
        "COMPOSE_PROFILES",
        "COMPOSE_PROJECT_NAME",
        "RELEASE_BACKEND_IMAGE",
        "RELEASE_WEB_IMAGE",
    ):
        env.pop(key, None)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def make(root, target, values, env, output=False):
    return run(
        "make",
        "--no-print-directory",
        "-s",
        target,
        *(f"{key}={value}" for key, value in values.items()),
        cwd=root,
        env=env,
        output=output,
    )


def options(data, settings, manifest):
    require(
        "PRODUCTION_COMPOSE_PROJECT" not in settings["variables"]
        or settings["variables"]["PRODUCTION_COMPOSE_PROJECT"] == settings["project"],
        "production wrapper project must equal explicit project",
    )
    return {
        **settings["variables"],
        "SCAFFOLD_PROFILE": data["profile"],
        "RELEASE_REVISION": data["revision"],
        "RELEASE_PLATFORM": data["platform"],
        "RELEASE_FILE": str(manifest),
        "PROD_ENV_FILE": settings["env_file"],
        "RELEASE_COMPOSE_PROJECT": settings["project"],
    }


def compose(root, values, env):
    # Never print resolved configuration: Compose embeds credentials here.
    config = json.loads(make(root, "release-bundle-config", values, env, output=True))
    require(
        config.get("name") == values["RELEASE_COMPOSE_PROJECT"],
        "Compose project mismatch",
    )
    require(config.get("services"), "empty runtime topology")
    return config


def inspect(reference, env):
    return json.loads(
        run("docker", "image", "inspect", reference, env=env, output=True)
    )[0]


def inspect_roles(config, spec, rev, target, env, require_labels):
    require(
        set(spec["images"]) <= set(config["services"]), "adapter image service missing"
    )
    images = {}
    for service, definition in config["services"].items():
        reference = definition.get("image")
        require(reference, "every runtime service must declare an image")
        info = inspect(reference, env)
        require(
            info["Os"] + "/" + info["Architecture"] == target,
            "runtime image architecture mismatch",
        )
        if service in spec["images"] and require_labels:
            require(
                (info["Config"].get("Labels") or {}).get(
                    "org.opencontainers.image.revision"
                )
                == rev,
                "application image revision label mismatch",
            )
        images[service] = {
            "id": image_id(info["Id"]),
            "reference": reference,
            "repository_digests": info.get("RepoDigests", []),
            "platform": target,
        }
    return images


def validate_images(path, images, target):
    """Verify config identities, platform and uncompressed layer digests before load."""
    with tarfile.open(path) as archive:
        files = {item.name: item for item in members(archive) if item.isfile()}
        require("manifest.json" in files, "Docker save manifest missing")
        saved = json.load(archive.extractfile(files["manifest.json"]))
        configs = {}
        checked_layers = {}
        for item in saved:
            config_bytes = archive.extractfile(files[item["Config"]]).read()
            identity = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
            config = json.loads(config_bytes)
            require(
                config["os"] + "/" + config["architecture"] == target,
                "archive architecture mismatch",
            )
            diffs = config["rootfs"]["diff_ids"]
            require(len(diffs) == len(item["Layers"]), "image layer count mismatch")
            for layer, expected in zip(item["Layers"], diffs):
                if layer not in checked_layers:
                    with archive.extractfile(files[layer]) as stream:
                        checked_layers[layer] = (
                            "sha256:"
                            + hashlib.file_digest(stream, "sha256").hexdigest()
                        )
                require(
                    checked_layers[layer] == expected, "image layer digest mismatch"
                )
            configs[identity] = config
        require(
            set(configs) == {item["id"] for item in images.values()},
            "incomplete/extra image archive",
        )
        return configs


def manifest_values(path):
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        require(
            separator and key not in values and re.fullmatch("[A-Z_]+", key),
            "malformed release manifest",
        )
        require(re.fullmatch("[a-zA-Z0-9_./:@+-]+", value), "unsafe manifest value")
        values[key] = value
    return values


def capture_evidence(work, rev, target, env, spec):
    repo = os.environ.get("BUNDLE_REPO", "")
    run_id = os.environ.get("BUNDLE_RUN_ID", "")
    artifact = os.environ.get("BUNDLE_ARTIFACT", "")
    require(
        re.fullmatch("[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "BUNDLE_REPO required"
    )
    require(
        run_id.isdigit()
        and artifact in (f"release-{rev}", f"release-{rev}-{target.split('/')[1]}"),
        "explicit BUNDLE_RUN_ID and BUNDLE_ARTIFACT required",
    )
    require(
        artifact != f"release-{rev}" or target == "linux/amd64",
        "legacy artifacts are AMD64 only",
    )
    evidence = json.loads(
        run("gh", "api", f"repos/{repo}/actions/runs/{run_id}", env=env, output=True)
    )
    require(
        evidence["head_sha"] == rev
        and evidence["head_branch"] == "main"
        and evidence["event"] == "push"
        and evidence["status"] == "completed"
        and evidence["conclusion"] == "success"
        and evidence["repository"]["full_name"] == repo,
        "specified run is not a successful main release of this revision/repository",
    )
    comparison = json.loads(
        run("gh", "api", f"repos/{repo}/compare/{rev}...main", env=env, output=True)
    )
    require(
        comparison["merge_base_commit"]["sha"] == rev, "release is no longer on main"
    )
    artifacts = work / "artifacts"
    run(
        "gh",
        "run",
        "download",
        run_id,
        "--repo",
        repo,
        "--name",
        artifact,
        "--dir",
        str(artifacts),
        env=env,
    )
    manifest = artifacts / (rev + ".env")
    require(manifest.is_file(), "selected artifact has no release manifest")
    roles = {
        name.removeprefix("RELEASE_").removesuffix("_IMAGE").lower()
        for name in spec["images"].values()
    }
    for role in roles:
        read_json(artifacts / f"{rev}-{role}.spdx.json")
    with tarfile.open(work / "artifacts.tar", "w") as archive:
        for path in sorted(artifacts.iterdir()):
            require(
                path.is_file() and not path.is_symlink(), "unexpected artifact entry"
            )
            archive.add(path, arcname=path.name)
    return manifest, {
        "source": "github-main",
        "repository": repo,
        "run_id": run_id,
        "artifact": artifact,
        "run": evidence,
    }


def package(mode):
    env = environment()
    root = Path.cwd()
    rev = revision(os.environ.get("RELEASE_REVISION"))
    target = platform(os.environ.get("RELEASE_PLATFORM"))
    selected = os.environ.get("SCAFFOLD_PROFILE", "")
    settings = inputs(os.environ["BUNDLE_INPUTS"])
    output = Path(os.environ["BUNDLE"]).resolve()
    require(not output.exists(), "refusing to replace existing bundle")
    if mode == "hotfix":
        require(
            not run("git", "status", "--porcelain", cwd=root, output=True),
            "commit all hotfix changes first",
        )
        require(
            run("git", "rev-parse", "HEAD", cwd=root, output=True).decode().strip()
            == rev,
            "hotfix revision must equal committed HEAD",
        )
    with tempfile.TemporaryDirectory(prefix="release-bundle-") as temporary:
        work = Path(temporary)
        # Bundle HEAD in an isolated bare repository so no refs/checkouts are changed.
        bare = work / "history.git"
        run(
            "git",
            "clone",
            "--bare",
            "--no-hardlinks",
            str(root),
            str(bare),
            output=True,
        )
        run("git", "update-ref", "refs/heads/emergency-release", rev, cwd=bare)
        run(
            "git",
            "bundle",
            "create",
            str(work / "history.bundle"),
            "refs/heads/emergency-release",
            cwd=bare,
        )
        source_tar = run("git", "archive", "--format=tar", rev, cwd=root, output=True)
        (work / "source.tar").write_bytes(source_tar)
        with tarfile.open(work / "source.tar") as archive:
            members(archive)
        source = work / "source"
        run("git", "clone", str(work / "history.bundle"), str(source), output=True)
        run("git", "checkout", "--detach", rev, cwd=source, output=True)
        spec = adapter(source, selected)
        data = {
            "schema": 1,
            "mode": mode,
            "revision": rev,
            "platform": target,
            "profile": selected,
        }
        release = work / "release.env"
        values = options(data, settings, release)
        env["DOCKER_DEFAULT_PLATFORM"] = target
        if mode == "capture":
            captured, evidence = capture_evidence(work, rev, target, env, spec)
            release.write_bytes(captured.read_bytes())
            recorded = manifest_values(release)
            require(
                recorded.get("RELEASE_REVISION") == rev,
                "release artifact revision mismatch",
            )
            require(
                recorded.get("SCAFFOLD_PROFILE", "react-vite") == selected,
                "release profile mismatch",
            )
            require(
                recorded.get("RELEASE_IMAGE_PREFIX")
                == "ghcr.io/" + evidence["repository"].lower(),
                "release repository mismatch",
            )
            for name in set(spec["images"].values()):
                require(
                    re.fullmatch(
                        r"ghcr\.io/"
                        + re.escape(evidence["repository"].lower())
                        + r"-[a-z-]+@sha256:[0-9a-f]{64}",
                        recorded.get(name, ""),
                    ),
                    "release application reference must pin this repository by digest",
                )
        else:
            prefix = "local/emergency-" + rev
            values["RELEASE_IMAGE_PREFIX"] = prefix
            make(source, "build-release-images", values, env)
            roles = {
                name: prefix
                + "-"
                + name.removeprefix("RELEASE_").removesuffix("_IMAGE").lower()
                + ":"
                + rev
                for name in spec["images"].values()
            }
            # Adapters with different tag naming override build-release-images consistently.
            release.write_text(
                "".join(
                    f"{key}={value}\n"
                    for key, value in {
                        "RELEASE_REVISION": rev,
                        "SCAFFOLD_PROFILE": selected,
                        "RELEASE_IMAGE_PREFIX": prefix,
                        **roles,
                    }.items()
                )
            )
            before = {name: inspect(tag, env)["Id"] for name, tag in roles.items()}
            started = time.time()
            check_project = "emergency-check-" + rev[:16]
            check_values = {
                **values,
                "PROJECT_NAME": check_project,
                "COMPOSE_PROJECT_NAME": check_project,
                "RELEASE_COMPOSE_PROJECT": check_project + "-release",
            }
            for check in spec["check_targets"]:
                make(source, check, check_values, env)
            require(
                not run("git", "diff", "--exit-code", rev, cwd=source, output=True),
                "checks changed committed source",
            )
            require(
                before
                == {name: inspect(tag, env)["Id"] for name, tag in roles.items()},
                "checks changed the built application images",
            )
            evidence = {
                "source": "local-checks",
                "targets": spec["check_targets"],
                "started_at": started,
                "passed_at": time.time(),
                "images": before,
                "follow_up": "Preserve this commit and reconcile through the normal PR workflow.",
            }
        config = compose(source, values, env)
        for definition in config["services"].values():
            reference = definition.get("image")
            require(reference, "runtime service has no image")
            if mode == "capture":
                run("docker", "pull", "--platform", target, reference, env=env)
        data["images"] = inspect_roles(config, spec, rev, target, env, mode == "hotfix")
        # Local content IDs survive docker save/load without any repository tag lookup.
        if mode == "hotfix":
            recorded = manifest_values(release)
            for service, name in spec["images"].items():
                recorded[name] = data["images"][service]["id"]
            release.write_text(
                "".join(f"{key}={value}\n" for key, value in recorded.items())
            )
        run(
            "docker",
            "image",
            "save",
            "--output",
            str(work / "images.tar"),
            *sorted({item["id"] for item in data["images"].values()}),
            env=env,
        )
        validate_images(work / "images.tar", data["images"], target)
        write_json(work / "evidence.json", evidence)
        names = [
            "source.tar",
            "history.bundle",
            "release.env",
            "images.tar",
            "evidence.json",
        ]
        if mode == "capture":
            names.append("artifacts.tar")
        data["files"] = {name: digest(work / name) for name in names}
        write_json(work / "bundle.json", data)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output, "x") as archive:
            for name in ["bundle.json", *names]:
                archive.add(work / name, arcname=name)
        print(f"PASS: {mode} bundle {output}\nBUNDLE_SHA256={digest(output)}")


def unpack(bundle, expected, work):
    require(
        re.fullmatch("[0-9a-f]{64}", expected or ""),
        "independently supplied BUNDLE_SHA256 required",
    )
    require(digest(bundle) == expected, "bundle SHA256 mismatch")
    extract(bundle, work)
    data = read_json(work / "bundle.json")
    require(
        set(data)
        == {"schema", "mode", "revision", "platform", "profile", "images", "files"},
        "unexpected bundle fields",
    )
    require(
        data["schema"] == 1 and data["mode"] in ("capture", "hotfix"),
        "unsupported bundle schema/mode",
    )
    revision(data["revision"])
    platform(data["platform"])
    names = {
        "source.tar",
        "history.bundle",
        "release.env",
        "images.tar",
        "evidence.json",
    }
    if data["mode"] == "capture":
        names.add("artifacts.tar")
    require(
        set(data["files"]) == names
        and {p.name for p in work.iterdir()} == names | {"bundle.json"},
        "incomplete or unexpected bundle contents",
    )
    for name, expected_digest in data["files"].items():
        require(
            digest(work / name) == expected_digest, "bundle payload checksum mismatch"
        )
    source = work / "source"
    source.mkdir()
    extract(work / "source.tar", source)
    spec = adapter(source, data["profile"])
    saved = validate_images(work / "images.tar", data["images"], data["platform"])
    evidence = read_json(work / "evidence.json")
    manifest = manifest_values(work / "release.env")
    require(
        manifest.get("RELEASE_REVISION") == data["revision"],
        "manifest revision mismatch",
    )
    require(
        manifest.get("SCAFFOLD_PROFILE", "react-vite") == data["profile"],
        "manifest profile mismatch",
    )
    for service, name in spec["images"].items():
        item = data["images"][service]
        if data["mode"] == "hotfix":
            require(manifest[name] == item["id"], "hotfix manifest image mismatch")
            require(
                (saved[item["id"]].get("config", {}).get("Labels") or {}).get(
                    "org.opencontainers.image.revision"
                )
                == data["revision"],
                "saved image revision mismatch",
            )
        else:
            require(
                manifest[name] == item["reference"] and "@sha256:" in item["reference"],
                "captured manifest image mismatch",
            )
    if data["mode"] == "hotfix":
        require(
            evidence["source"] == "local-checks"
            and evidence["targets"] == spec["check_targets"]
            and evidence["passed_at"] >= evidence["started_at"],
            "missing local validation evidence",
        )
        require(
            set(evidence["images"].values())
            == {data["images"][s]["id"] for s in spec["images"]},
            "validation evidence images mismatch",
        )
    else:
        require(
            evidence["source"] == "github-main"
            and evidence["run"]["head_sha"] == data["revision"]
            and evidence["run"]["head_branch"] == "main"
            and evidence["run"]["conclusion"] == "success",
            "missing successful main evidence",
        )
    # Verify the preserved commit object and exact source independently of the working checkout.
    history = work / "verify.git"
    run(
        "git",
        "clone",
        "--bare",
        str(work / "history.bundle"),
        str(history),
        output=True,
    )
    committed = run(
        "git", "archive", "--format=tar", data["revision"], cwd=history, output=True
    )
    require(
        hashlib.sha256(committed).hexdigest() == data["files"]["source.tar"],
        "source archive differs from preserved commit",
    )
    return data, spec, source


def frozen_config(config, data, spec, destination, source):
    require(
        set(config["services"]) == set(data["images"]),
        "runtime service set differs from bundle",
    )
    for service, definition in config["services"].items():
        item = data["images"][service]
        require(item["platform"] == data["platform"], "service platform mismatch")
        expected = (
            item["id"]
            if data["mode"] == "hotfix" and service in spec["images"]
            else item["reference"]
        )
        require(
            definition.get("image") == expected,
            "runtime image reference differs from bundle",
        )
        definition["image"] = image_id(item["id"])
        definition["platform"] = data["platform"]
        definition["pull_policy"] = "never"
        definition.pop("build", None)
        for volume in definition.get("volumes", []):
            if volume["type"] == "bind":
                path = Path(volume["source"])
                if path.is_relative_to(source):
                    if not path.exists():
                        path = destination / path.relative_to(source)
                require(path.exists(), "required bind input missing: " + str(path))
                volume["source"] = str(path)
                volume.setdefault("bind", {})["create_host_path"] = False
    # configs/secrets file paths are also host-owned inputs, never bundle material.
    for group in ("configs", "secrets"):
        for value in config.get(group, {}).values():
            if "file" in value:
                path = Path(value["file"])
                if path.is_relative_to(source):
                    if not path.exists():
                        path = destination / path.relative_to(source)
                require(path.is_file(), "required config/secret missing")
                value["file"] = str(path)
    return config


@contextlib.contextmanager
def staging(state):
    work = Path(tempfile.mkdtemp(prefix="release-", dir=state)).resolve()
    try:
        yield work
    finally:
        # Running containers may bind committed deployment files from this source.
        if not (work / ".retain").exists():
            shutil.rmtree(work)


def destination(operation):
    require(
        operation != "install" or os.environ.get("EMERGENCY") == "1",
        "installation requires EMERGENCY=1 on each invocation",
    )
    env = environment()
    settings = inputs(os.environ["BUNDLE_INPUTS"])
    bundle = Path(os.environ["BUNDLE"]).resolve()
    expected = os.environ.get("BUNDLE_SHA256")
    root = Path.cwd()
    state = root / ".local/emergency-release"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with staging(state) as work:
            data, spec, source = unpack(bundle, expected, work)
            actual = run(
                "docker",
                "info",
                "--format",
                "{{.OSType}}/{{.Architecture}}",
                env=env,
                output=True,
            )
            host = (
                actual.decode()
                .strip()
                .replace("x86_64", "amd64")
                .replace("aarch64", "arm64")
            )
            require(
                host == data["platform"], "bundle architecture differs from destination"
            )
            record = state / "deployment.json"
            if record.exists():
                require(
                    read_json(record)["project"] == settings["project"],
                    "recorded Compose project differs",
                )
            tenant_record = root / ".git/tenant-deployment.json"
            if tenant_record.exists():
                require(
                    read_json(tenant_record)["project"] == settings["project"],
                    "infra Compose project differs",
                )
            values = options(data, settings, work / "release.env")
            config = frozen_config(
                compose(source, values, env), data, spec, root, source
            )
            for group, resource in (("volumes", "volume"), ("networks", "network")):
                for definition in config.get(group, {}).values():
                    if definition.get("external"):
                        run(
                            "docker",
                            resource,
                            "inspect",
                            definition["name"],
                            env=env,
                            output=True,
                        )
            for group, resource in (("secrets", "secret"), ("configs", "config")):
                for name, definition in config.get(group, {}).items():
                    if definition.get("external"):
                        run(
                            "docker",
                            resource,
                            "inspect",
                            definition.get("name", name),
                            env=env,
                            output=True,
                        )
            write_json(work / "compose.json", config)
            # Validate with the engine's Compose implementation before loading or changing services.
            command = [
                "docker",
                "compose",
                "--project-directory",
                str(root),
                "-p",
                settings["project"],
                "-f",
                str(work / "compose.json"),
            ]
            if settings["variables"].get("MONITORING") == "1":
                command += ["--profile", "monitoring"]
            run(*command, "--profile", "*", "config", "--quiet", env=env)
            if operation == "validate":
                print(
                    "PASS: bundle, destination architecture and required inputs; no deployment changes"
                )
                return
            audit = {
                "schema": 1,
                "revision": data["revision"],
                "project": settings["project"],
                "images": data["images"],
                "validation_source": read_json(work / "evidence.json")["source"],
                "emergency_mode": data["mode"],
                "bundle_sha256": expected,
                "started_at": time.time(),
                "status": "installing",
            }
            (work / ".retain").touch()
            audit["deployment_directory"] = str(work)
            write_json(record, audit)
            try:
                run(
                    "docker",
                    "image",
                    "load",
                    "--input",
                    str(work / "images.tar"),
                    env=env,
                    output=True,
                )
                for item in data["images"].values():
                    require(
                        inspect(item["id"], env)["Id"] == item["id"],
                        "loaded image identity mismatch",
                    )
                values.update(
                    {
                        "COMPOSE_RELEASE": shlex.join(command),
                        "RELEASE_PULL": "true",
                        "BUNDLE_SMOKE_URL": settings["smoke_url"],
                    }
                )
                make(source, "release-bundle-deploy", values, env)
                make(source, "release-bundle-smoke", values, env)
                audit["status"] = "succeeded"
            except BaseException:
                audit["status"] = (
                    "failed; inspect deployment and migration state before recovery"
                )
                raise
            finally:
                audit["finished_at"] = time.time()
                write_json(work / "audit.json", audit)
                write_json(record, audit)
            print(
                "PASS: emergency deployment and smoke; audit .local/emergency-release/deployment.json"
            )
            if data["mode"] == "hotfix":
                print(
                    "Hotfix history remains in the original bundle; reconcile this revision through a normal PR."
                )


def smoke():
    url = os.environ["BUNDLE_SMOKE_URL"]
    expected = os.environ["RELEASE_REVISION"]
    headers = {
        "Host": os.environ.get(
            "PRODUCTION_SMOKE_HOST", os.environ.get("RELEASE_HOST", "localhost")
        ),
        "X-Forwarded-Proto": "https",
    }
    for attempt in range(60):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url + "/api/healthz", headers=headers), timeout=2
            ) as response:
                health = json.load(response)
            require(
                health["revision"] == expected, "deployed revision differs from bundle"
            )
            for path in ("/", "/static/smoketest.txt"):
                with urllib.request.urlopen(
                    urllib.request.Request(url + path, headers=headers), timeout=2
                ) as response:
                    require(response.status == 200, "smoke request failed")
            print(
                "PASS: application reports exact bundled revision; root/static respond"
            )
            return
        except (OSError, Rejected, KeyError):
            if attempt == 59:
                raise
            time.sleep(1)


def main():
    os.umask(0o077)
    operation = sys.argv[1]
    if operation in ("capture", "hotfix"):
        package(operation)
    elif operation in ("validate", "install"):
        destination(operation)
    elif operation == "smoke":
        smoke()
    else:
        raise Rejected("unknown operation")


if __name__ == "__main__":
    try:
        main()
    except (
        Rejected,
        OSError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as error:
        # Subprocess output may contain tool diagnostics; never echo resolved Compose/env objects.
        print("FAIL: emergency release rejected: " + str(error), file=sys.stderr)
        sys.exit(1)
