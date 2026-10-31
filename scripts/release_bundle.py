"""Validate portable release image archives before loading or deploying them.

The independently transferred bundle SHA256 remains the trust boundary;
content checks detect damage, not authorship. Uses only the Python standard library.
"""

import hashlib
import json
from pathlib import PurePosixPath
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
