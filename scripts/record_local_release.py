"""Record verified local image identities for the normal release deploy workflow."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def record(options):
    if not re.fullmatch("[0-9a-f]{40}", options.revision):
        raise ValueError("a full committed revision is required")
    images = {}
    for role in ("backend", "web"):
        info = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", getattr(options, role)], text=True
            )
        )[0]
        if (info["Config"].get("Labels") or {}).get(
            "org.opencontainers.image.revision"
        ) != options.revision:
            raise ValueError("image revision does not match source")
        actual = info["Os"] + "/" + info["Architecture"]
        if options.platform and actual != options.platform:
            raise ValueError("image architecture does not match destination")
        if not re.fullmatch("sha256:[0-9a-f]{64}", info["Id"]):
            raise ValueError("image has no immutable local identity")
        images[role] = info["Id"]
    values = {
        "SCAFFOLD_PROFILE": options.profile,
        "RELEASE_REVISION": options.revision,
        "RELEASE_IMAGE_PREFIX": options.prefix,
        "RELEASE_BACKEND_IMAGE": images["backend"],
        "RELEASE_WEB_IMAGE": images["web"],
    }
    if any(not re.fullmatch("[a-zA-Z0-9_./:@+-]+", value) for value in values.values()):
        raise ValueError("unsafe release manifest value")
    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=output.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    print(f"Recorded local content-pinned release: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("revision", "profile", "prefix", "backend", "web", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--platform")
    record(parser.parse_args())


if __name__ == "__main__":
    main()
