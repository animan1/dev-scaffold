"""Validate portable release image archives before loading or deploying them.

The independently transferred bundle SHA256 remains the trust boundary;
content checks detect damage, not authorship. Uses only the Python standard library.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


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
