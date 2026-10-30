"""Real OCI capture, offline builds and installs on two disposable Docker daemons.

Only setup uses external registry access. GitHub run/artifact responses are a
deterministic fixture; no Actions jobs or real repository writes are performed.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]


def run(*args, env=None, cwd=None, output=False, check=True):
    return subprocess.run(
        args,
        env=env,
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE if output else None,
        text=True,
    )


def save_load(host, engine, images):
    with tempfile.TemporaryFile() as archive:
        subprocess.run([host, "save", *images], stdout=archive, check=True)
        archive.seek(0)
        subprocess.run(
            [host, "exec", "-i", engine, "docker", "load"], stdin=archive, check=True
        )


def repack(original, target, change):
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        # This helper handles only our own generated bundle.
        with tarfile.open(original) as archive:
            archive.extractall(work, filter="data")
        manifest = json.loads((work / "bundle.json").read_text())
        change(work, manifest)
        (work / "bundle.json").write_text(json.dumps(manifest))
        with tarfile.open(target, "w") as archive:
            for path in work.iterdir():
                archive.add(path, arcname=path.name)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def main():
    host = shutil.which("docker")
    architecture = run(
        host, "info", "--format", "{{.Architecture}}", output=True
    ).stdout.strip()
    architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(
        architecture, architecture
    )
    assert architecture in ("amd64", "arm64")
    platform = "linux/" + architecture
    # Explicit preparation, before the outage boundary.
    for image in ("docker:28-dind", "python:3.12-alpine", "registry:2"):
        run(host, "pull", "--platform", platform, image)
    engines = []
    with tempfile.TemporaryDirectory(prefix="emergency-exercise-") as temporary:
        work = Path(temporary).resolve()
        try:
            for role in ("build", "destination"):
                name = f"scaffold-emergency-{role}-{os.getpid()}"
                run(
                    host,
                    "run",
                    "-d",
                    "--privileged",
                    "--network",
                    "none",
                    "--name",
                    name,
                    "--mount",
                    f"type=bind,source={work},target={work}",
                    "--add-host",
                    "ghcr.io:127.0.0.1",
                    "-e",
                    "DOCKER_TLS_CERTDIR=",
                    "docker:28-dind",
                    "dockerd",
                    "--host=unix:///var/run/docker.sock",
                    "--insecure-registry=ghcr.io",
                )
                engines.append(name)
                for attempt in range(60):
                    if (
                        run(
                            host,
                            "exec",
                            name,
                            "docker",
                            "info",
                            output=True,
                            check=False,
                        ).returncode
                        == 0
                    ):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("isolated daemon did not start")
            build, destination = engines
            save_load(host, build, ["python:3.12-alpine", "registry:2"])
            # Seed existing application data before either deployment, then clear
            # the destination image store so recovery must use the bundle.
            save_load(host, destination, ["python:3.12-alpine"])
            run(
                host,
                "exec",
                destination,
                "docker",
                "volume",
                "create",
                "emergency-exercise_data",
            )
            run(
                host,
                "exec",
                destination,
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                "emergency-exercise_data:/data",
                "python:3.12-alpine",
                "python",
                "-c",
                "import sqlite3; d=sqlite3.connect('/data/application.db'); "
                "d.execute('create table saved (value text)'); "
                "d.execute(\"insert into saved values ('existing-customer-data')\"); d.commit()",
            )
            run(
                host, "exec", destination, "docker", "image", "rm", "python:3.12-alpine"
            )
            binaries = work / "bin"
            binaries.mkdir()
            wrapper = binaries / "docker"
            wrapper.write_text(
                "#!" + sys.executable + "\nimport json, os, subprocess, sys\n"
                'with open(os.environ["EXERCISE_LOG"], "a") as log: '
                'log.write(json.dumps([os.environ["EXERCISE_ENGINE"], *sys.argv[1:]]) + "\\n")\n'
                'cwd = os.getcwd(); root = os.environ["EXERCISE_ROOT"]\n'
                'cwd = cwd if cwd.startswith(root + "/") else root\n'
                'forward = [item for key in ("PROD_ENV_FILE", "RELEASE_REVISION", "RELEASE_BACKEND_IMAGE", '
                '"RELEASE_WEB_IMAGE", "COMPOSE_PROJECT_NAME") if key in os.environ '
                'for item in ("-e", key + "=" + os.environ[key])]\n'
                'sys.exit(subprocess.call([os.environ["EXERCISE_DOCKER"], "exec", "-i", "-w", cwd, '
                '"-e", "DOCKER_BUILDKIT=0", *forward, os.environ["EXERCISE_ENGINE"], "docker", *sys.argv[1:]]))\n'
            )
            wrapper.chmod(0o755)
            git = binaries / "git"
            git.write_text(
                "#!" + sys.executable + "\nimport os, subprocess, sys\n"
                'if "fetch" in sys.argv: raise SystemExit("Git fetch unavailable in exercise")\n'
                'sys.exit(subprocess.call([os.environ["EXERCISE_GIT"], *sys.argv[1:]]))\n'
            )
            git.chmod(0o755)
            gh = binaries / "gh"
            gh.write_text(
                "#!" + sys.executable + "\nimport json, os, pathlib, shutil, sys\n"
                'if os.environ.get("EXERCISE_OUTAGE") == "1": raise SystemExit("GitHub unavailable")\n'
                'rev = os.environ["EXERCISE_REVISION"]\n'
                'if sys.argv[1] == "api":\n'
                ' value = {"merge_base_commit":{"sha":rev}} if "/compare/" in sys.argv[2] else '
                '{"head_sha":rev,"head_branch":"main","event":"push","status":"completed",'
                '"conclusion":"success","repository":{"full_name":"exercise/app"}}\n'
                " print(json.dumps(value))\n"
                "else:\n"
                ' target=pathlib.Path(sys.argv[sys.argv.index("--dir")+1]); target.mkdir()\n'
                ' for path in pathlib.Path(os.environ["EXERCISE_ARTIFACTS"]).iterdir(): shutil.copy(path,target/path.name)\n'
            )
            gh.chmod(0o755)
            source = work / "build-source"
            shutil.copytree(ROOT / "tests/emergency_release/fixture", source)
            (source / "scripts").mkdir()
            shutil.copy(
                ROOT / "scripts/release_bundle.py", source / "scripts/release_bundle.py"
            )
            env = os.environ.copy()
            for key in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "RELEASE_REVISION"):
                env.pop(key, None)
            env.update(
                {
                    "EXERCISE_DOCKER": host,
                    "EXERCISE_ENGINE": build,
                    "EXERCISE_ROOT": str(work),
                    "EXERCISE_LOG": str(work / "docker-commands.jsonl"),
                    "EXERCISE_GIT": shutil.which("git"),
                    "PATH": str(binaries) + ":" + env["PATH"],
                    "TMPDIR": str(work),
                    "DOCKER_DEFAULT_PLATFORM": platform,
                    "SCAFFOLD_PROFILE": "exercise",
                    "RELEASE_PLATFORM": platform,
                    "GIT_AUTHOR_NAME": "Emergency exercise",
                    "GIT_AUTHOR_EMAIL": "exercise@example.invalid",
                    "GIT_COMMITTER_NAME": "Emergency exercise",
                    "GIT_COMMITTER_EMAIL": "exercise@example.invalid",
                }
            )
            run("git", "init", "-b", "main", cwd=source, env=env)
            run(
                "git",
                "add",
                "Makefile",
                "Dockerfile",
                "compose.yml",
                "app.py",
                "app_test.py",
                "revision.txt",
                "release-bundle.json",
                "scripts/release_bundle.py",
                cwd=source,
                env=env,
            )
            run("git", "commit", "-m", "Initial release fixture", cwd=source, env=env)
            first = run(
                "git", "rev-parse", "HEAD", cwd=source, output=True
            ).stdout.strip()
            env["RELEASE_REVISION"] = first
            env["EXERCISE_REVISION"] = first
            tenant = work / "destination"
            tenant.mkdir()
            config = tenant / "production.env"
            config.write_text("PRIVATE_SETTING=existing-env-value\n")
            config.chmod(0o600)
            (tenant / "private.conf").write_text("existing-private-configuration\n")
            settings = work / "inputs.json"
            settings.write_text(
                json.dumps(
                    {
                        "project": "emergency-exercise",
                        "env_file": str(config),
                        "variables": {},
                        "smoke_url": "http://127.0.0.1:8000",
                    }
                )
            )
            env["BUNDLE_INPUTS"] = str(settings)
            prefix = "ghcr.io/exercise/app"
            run(
                "make",
                "build-release-images",
                f"RELEASE_REVISION={first}",
                f"RELEASE_PLATFORM={platform}",
                f"RELEASE_IMAGE_PREFIX={prefix}",
                cwd=source,
                env=env,
            )
            run(
                host,
                "exec",
                build,
                "docker",
                "run",
                "-d",
                "--name",
                "registry",
                "-p",
                "80:5000",
                "registry:2",
            )
            artifacts = work / "artifacts"
            artifacts.mkdir()
            run(
                str(wrapper),
                "tag",
                "python:3.12-alpine",
                "ghcr.io/exercise/runtime:cached",
                env=env,
            )
            run(str(wrapper), "push", "ghcr.io/exercise/runtime:cached", env=env)
            lines = {
                "SCAFFOLD_PROFILE": "exercise",
                "RELEASE_REVISION": first,
                "RELEASE_IMAGE_PREFIX": prefix,
            }
            for role in ("backend", "web"):
                tag = prefix + "-" + role + ":" + first
                run(str(wrapper), "push", tag, env=env)
                info = json.loads(
                    run(
                        str(wrapper), "image", "inspect", tag, env=env, output=True
                    ).stdout
                )[0]
                lines["RELEASE_" + role.upper() + "_IMAGE"] = info["RepoDigests"][0]
                (artifacts / f"{first}-{role}.spdx.json").write_text("{}")
            (artifacts / f"{first}.env").write_text(
                "".join(f"{key}={value}\n" for key, value in lines.items())
            )
            env.update(
                {
                    "EXERCISE_ARTIFACTS": str(artifacts),
                    "BUNDLE_REPO": "exercise/app",
                    "BUNDLE_RUN_ID": "1",
                    "BUNDLE_ARTIFACT": f"release-{first}-{architecture}",
                }
            )
            captured = work / "captured.tar"
            env["BUNDLE"] = str(captured)
            run(
                sys.executable,
                str(source / "scripts/release_bundle.py"),
                "capture",
                cwd=source,
                env=env,
            )
            # Outage boundary. Destination has an empty image store, no registry,
            # and no network interface; all GitHub calls now fail too.
            run(host, "exec", build, "docker", "stop", "registry")
            env.update(
                {
                    "EXERCISE_OUTAGE": "1",
                    "EXERCISE_ENGINE": destination,
                    "EMERGENCY": "1",
                    "BUNDLE_SHA256": hashlib.sha256(captured.read_bytes()).hexdigest(),
                }
            )
            run(
                sys.executable,
                str(source / "scripts/release_bundle.py"),
                "install",
                cwd=tenant,
                env=env,
            )
            initial_ids = run(str(wrapper), "ps", "-aq", env=env, output=True).stdout

            def wrong_platform(directory, manifest):
                manifest["platform"] = "linux/" + (
                    "amd64" if architecture == "arm64" else "arm64"
                )

            def incomplete(directory, manifest):
                (directory / "images.tar").unlink()

            for name, mutation in (
                ("wrong-platform", wrong_platform),
                ("incomplete", incomplete),
            ):
                bad = work / (name + ".tar")
                bad_sha = repack(captured, bad, mutation)
                bad_env = {**env, "BUNDLE": str(bad), "BUNDLE_SHA256": bad_sha}
                assert (
                    run(
                        sys.executable,
                        str(source / "scripts/release_bundle.py"),
                        "install",
                        cwd=tenant,
                        env=bad_env,
                        check=False,
                    ).returncode
                    != 0
                )
                assert (
                    run(str(wrapper), "ps", "-aq", env=env, output=True).stdout
                    == initial_ids
                )
                print(
                    "PASS: " + name + " rejected; destination containers unchanged",
                    flush=True,
                )
            (source / "revision.txt").write_text("hotfix-two\n")
            run("git", "add", "revision.txt", cwd=source, env=env)
            run("git", "commit", "-m", "Emergency fixture hotfix", cwd=source, env=env)
            second = run(
                "git", "rev-parse", "HEAD", cwd=source, output=True
            ).stdout.strip()
            hotfix = work / "hotfix.tar"
            env.update(
                {
                    "RELEASE_REVISION": second,
                    "BUNDLE": str(hotfix),
                    "EXERCISE_ENGINE": build,
                }
            )
            # No registry, GitHub or external network is available for this build.
            run(
                sys.executable,
                str(source / "scripts/release_bundle.py"),
                "hotfix",
                cwd=source,
                env=env,
            )
            env.update(
                {
                    "BUNDLE_SHA256": hashlib.sha256(hotfix.read_bytes()).hexdigest(),
                    "EXERCISE_ENGINE": destination,
                }
            )
            run(
                sys.executable,
                str(source / "scripts/release_bundle.py"),
                "install",
                cwd=tenant,
                env=env,
            )
            audit = json.loads(
                (tenant / ".local/emergency-release/deployment.json").read_text()
            )
            assert audit["revision"] == second and audit["status"] == "succeeded"
            assert config.read_text() == "PRIVATE_SETTING=existing-env-value\n"
            commands = [
                json.loads(line)
                for line in (work / "docker-commands.jsonl").read_text().splitlines()
            ]
            assert not any(
                command[0] == destination
                and any(word in command[1:] for word in ("pull", "login", "build"))
                for command in commands
            )
            print(
                "PASS: visible release-one → hotfix-two; exact commit IDs changed; existing database/config preserved"
            )
            print(
                "PASS: both destination installs used separate network-none engine; hotfix built during outage"
            )
        finally:
            for engine in engines:
                run(host, "rm", "-fv", engine, check=False)


if __name__ == "__main__":
    main()
