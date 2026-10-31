"""Validate portable release image archives before loading or deploying them.

The independently transferred bundle SHA256 remains the trust boundary;
content checks detect damage, not authorship. Uses only the Python standard library.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def environment():
    """Remove inherited Make/Compose selectors before invoking tenant workflows."""
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
    result = subprocess.run(
        (
            "make",
            "--no-print-directory",
            "-s",
            target,
            *(f"{key}={value}" for key, value in values.items()),
        ),
        cwd=root,
        env=env,
        check=True,
        stdout=subprocess.PIPE if output else None,
    )
    return result.stdout


def build_hotfix(root, source, rev, target, selected, values, env, spec):
    """Build and locally validate one clean committed hotfix for its target platform."""
    revision(rev)
    platform(target)
    require(
        not run("git", "status", "--porcelain", cwd=root, output=True),
        "commit all hotfix changes first",
    )
    require(
        run("git", "rev-parse", "HEAD", cwd=root, output=True).decode().strip() == rev,
        "hotfix revision must equal committed HEAD",
    )

    env["DOCKER_DEFAULT_PLATFORM"] = target
    prefix = "local/emergency-" + rev
    build_values = {
        **values,
        "SCAFFOLD_PROFILE": selected,
        "RELEASE_REVISION": rev,
        "RELEASE_PLATFORM": target,
        "RELEASE_IMAGE_PREFIX": prefix,
    }
    make(source, "build-release-images", build_values, env)
    roles = {
        name: prefix
        + "-"
        + name.removeprefix("RELEASE_").removesuffix("_IMAGE").lower()
        + ":"
        + rev
        for name in spec["images"].values()
    }
    manifest = source / "release.env"
    manifest.write_text(
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
    before = {name: image_id(inspect(tag, env)["Id"]) for name, tag in roles.items()}
    started = time.time()
    check_project = "emergency-check-" + rev[:16]
    check_values = {
        **build_values,
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
        == {name: image_id(inspect(tag, env)["Id"]) for name, tag in roles.items()},
        "checks changed the built application images",
    )
    evidence = {
        "source": "local-checks",
        "revision": rev,
        "platform": target,
        "targets": spec["check_targets"],
        "started_at": started,
        "passed_at": time.time(),
        "images": before,
        "follow_up": "Preserve this commit and reconcile through the normal PR workflow.",
    }
    return manifest, build_values, roles, evidence


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


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


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


def run(*args, cwd=None, env=None, output=False):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, stdout=subprocess.PIPE if output else None
    ).stdout


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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


def verify_source(work, rev):
    """Match the deployment archive to its exact preserved commit, entirely offline."""
    revision(rev)
    history = work / "verify.git"
    run(
        "git",
        "clone",
        "--bare",
        str(work / "history.bundle"),
        str(history),
        output=True,
    )
    committed = run("git", "archive", "--format=tar", rev, cwd=history, output=True)
    require(
        hashlib.sha256(committed).hexdigest() == digest(work / "source.tar"),
        "source archive differs from preserved commit",
    )


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


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
    verify_source(work, data["revision"])
    return data, spec, source


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
