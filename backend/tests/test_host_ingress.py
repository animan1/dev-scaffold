from __future__ import annotations

import errno
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    candidates = (
        Path(os.environ.get("REPO_DIR", "")),
        Path(__file__).resolve().parents[2],
        Path("/workspace"),
    )
    for candidate in candidates:
        if (candidate / "Makefile").is_file():
            return candidate
    raise FileNotFoundError("Could not find the repository root")


def _production_checkout(tmp_path: Path) -> str:
    for name in (
        "deploy/docker-compose.prod.yml",
        "deploy/docker-compose.host-ingress.yml",
        "profiles/server-rendered-django/release.compose.yml",
        "profiles/immutable-backup/compose.yml",
    ):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_repo_root() / name, destination)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_repo_root(), text=True
    ).strip()


def _prompted_prepare(
    command: list[str],
    directory: Path,
    value: str,
    redirect_input: bool = False,
) -> tuple[int, str]:
    process, terminal = pty.fork()
    if process == 0:
        os.chdir(directory)
        if redirect_input:
            with open(os.devnull) as empty:
                os.dup2(empty.fileno(), 0)
        os.execvp(command[0], command)
    output = b""
    supplied = False
    ownership_cancelled = False
    try:
        while True:
            if not select.select([terminal], [], [], 20)[0]:
                os.kill(process, signal.SIGKILL)
                os.waitpid(process, 0)
                pytest.fail("Preparation did not finish its terminal prompt")
            try:
                chunk = os.read(terminal, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output += chunk
            if b"BACKUP_PASSWORD: " in output and not supplied:
                os.write(terminal, (value + "\n").encode())
                supplied = True
            if b"+ sudo " in output and not ownership_cancelled:
                os.write(terminal, b"\x03")
                ownership_cancelled = True
        _, status = os.waitpid(process, 0)
    finally:
        os.close(terminal)
    return os.waitstatus_to_exitcode(status), output.decode(errors="replace")


def test_host_ingress_origin_is_loopback_http_only() -> None:
    compose = (_repo_root() / "deploy/docker-compose.host-ingress.yml").read_text()
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert '"127.0.0.1:${HOST_INGRESS_PORT:-18080}:80"' in compose
    assert "/etc/nginx/certs" not in compose
    assert "listen 80;" in nginx
    assert "listen 443" not in nginx
    assert "ssl_certificate" not in nginx


def test_host_ingress_templates_only_the_selected_backend() -> None:
    compose = (_repo_root() / "deploy/docker-compose.host-ingress.yml").read_text()
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "${BACKEND_HOST:-backend}" in compose
    assert 'NGINX_ENVSUBST_FILTER: "^BACKEND_HOST$"' in compose
    assert "/etc/nginx/templates/default.conf.template:ro" in compose
    assert nginx.count("proxy_pass http://${BACKEND_HOST}:8000;") == 2
    assert "$host" in nginx
    assert "$trusted_forwarded_proto" in nginx


def test_host_ingress_routes_django_admin_without_capturing_frontend_routes() -> None:
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "location = /admin {" in nginx
    assert "absolute_redirect off;" in nginx
    assert "return 301 /admin/;" in nginx
    assert "location /admin/ {" in nginx
    assert "location /api/ {" in nginx
    assert "location /static/ {" in nginx
    assert "location /assets/ {" in nginx
    assert "location / {" in nginx


def test_production_workflows_refresh_nginx_after_backend_replacement() -> None:
    makefile = (_repo_root() / "Makefile").read_text()

    refresh = "$(COMPOSE_PROD) up -d --no-deps --force-recreate nginx"
    assert makefile.count(refresh) == 1
    bootstrap = makefile.split("bootstrap-prod: prepare-prod", 1)[1].split(".PHONY:", 1)[0]
    assert "$(MAKE) up-prod" in bootstrap
    assert "$(COMPOSE_RELEASE) up -d --no-deps --force-recreate nginx" in makefile


def test_host_ingress_normalizes_forwarding_headers_at_the_proxy_boundary() -> None:
    nginx = (_repo_root() / "deploy/nginx/default.host-ingress.conf").read_text()

    assert "map $http_x_forwarded_proto $trusted_forwarded_proto" in nginx
    assert "proxy_set_header X-Forwarded-Host $host;" in nginx
    assert "proxy_set_header X-Forwarded-For $trusted_forwarded_for;" in nginx
    assert "proxy_set_header X-Forwarded-Proto $trusted_forwarded_proto;" in nginx


@pytest.mark.skipif(shutil.which("docker") is None, reason="Needs the Docker Compose client")
@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_local_tls_preparation_requires_opt_in_and_covers_browser_hosts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    monkeypatch.setenv("SCAFFOLD_PROFILE", "server-rendered-django")
    revision = _production_checkout(tmp_path)
    private_env = tmp_path / "deploy/.env.prod"
    existing = (
        "DJANGO_SECRET_KEY=existing-private-configuration\n"
        "DJANGO_ALLOWED_HOSTS=localhost\nPOSTGRES_USER=app\n"
        "POSTGRES_PASSWORD=existing-password\nPOSTGRES_DB=app\n"
        "DATABASE_URL=postgresql://app:existing-password@db:5432/app\n"
    )
    private_env.write_text(existing)
    command = [
        "make",
        "--no-print-directory",
        "-f",
        str(_repo_root() / "Makefile"),
        f"SCAFFOLD_PROFILE={profile}",
        f"RELEASE_REVISION={revision}",
        "SCAFFOLD_BACKUP_PROFILE=none",
        "DOCKER_HOST=unix:///nonexistent",
        "HOST_INGRESS=0",
        "prepare-prod",
    ]
    rejected = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert not (tmp_path / "deploy/nginx/certs/server.key").exists()
    subprocess.run([*command, "LOCAL_TLS=1"], cwd=tmp_path, capture_output=True, check=True)
    certificate = tmp_path / "deploy/nginx/certs/server.crt"
    inspected = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(certificate),
            "-noout",
            "-ext",
            "subjectAltName",
            "-checkend",
            "86400",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "DNS:localhost" in inspected.stdout
    assert "IP Address:127.0.0.1" in inspected.stdout
    subprocess.run(command, cwd=tmp_path, capture_output=True, check=True)
    assert private_env.read_text() == existing
    assert private_env.stat().st_mode & 0o077 == 0
    assert (tmp_path / "deploy/nginx/certs/server.key").stat().st_mode & 0o077 == 0


@pytest.mark.skipif(shutil.which("docker") is None, reason="Needs the Docker Compose client")
@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_prepare_prod_generates_private_inputs_and_revalidates_without_overwriting(
    tmp_path: Path,
    profile: str,
) -> None:
    revision = _production_checkout(tmp_path)
    command = [
        "make",
        "--no-print-directory",
        "-f",
        str(_repo_root() / "Makefile"),
        f"SCAFFOLD_PROFILE={profile}",
        f"RELEASE_REVISION={revision}",
        "SCAFFOLD_BACKUP_PROFILE=none",
        "DOCKER_HOST=unix:///nonexistent",
        "prepare-prod",
    ]
    subprocess.run(command, cwd=tmp_path, capture_output=True, check=True)
    private_env = tmp_path / "deploy/.env.prod"
    original = private_env.read_bytes()
    assert private_env.stat().st_mode & 0o077 == 0
    assert not (tmp_path / "deploy/nginx/certs").exists()
    assert not (tmp_path / "deploy/releases").exists()
    mismatch = subprocess.run(
        [*command, "HOST_INGRESS_HOST=unaccepted.example"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mismatch.returncode != 0
    assert "not accepted by DJANGO_ALLOWED_HOSTS" in mismatch.stderr
    assert private_env.read_bytes() == original
    private_env.chmod(0o644)
    subprocess.run(command, cwd=tmp_path, capture_output=True, check=True)
    assert private_env.read_bytes() == original
    assert private_env.stat().st_mode & 0o077 == 0
    incomplete = b"\n".join(
        line for line in original.split(b"\n") if not line.startswith(b"DJANGO_SECRET_KEY=")
    )
    private_env.write_bytes(incomplete)
    rejected = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "Missing required production input: DJANGO_SECRET_KEY" in rejected.stderr
    assert "POSTGRES_PASSWORD=" not in rejected.stdout + rejected.stderr
    assert private_env.read_bytes() == incomplete
    private_env.write_bytes(original + b"\nDATABASE_URL=postgresql://app:wrong@db/app\n")
    mismatched = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert mismatched.returncode != 0
    assert "DATABASE_URL does not match" in mismatched.stderr
    private_env.write_text('DJANGO_SECRET_KEY="private-value-with-unclosed-quote\n')
    malformed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert malformed.returncode != 0
    assert "private-value-with-unclosed-quote" not in malformed.stdout + malformed.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="Needs the Docker Compose client")
@pytest.mark.parametrize("profile", ["react-vite", "server-rendered-django"])
def test_preparation_prompts_for_missing_backup_key_and_preserves_it_on_repeat(
    tmp_path: Path,
    profile: str,
) -> None:
    revision = _production_checkout(tmp_path)
    command = [
        "make",
        "--no-print-directory",
        "-f",
        str(_repo_root() / "Makefile"),
        f"SCAFFOLD_PROFILE={profile}",
        "PROJECT_NAME=dev-scaffold",
        f"RELEASE_REVISION={revision}",
        f"BACKUP_IMAGE=local/dev-scaffold-backup:{revision}",
        f"BACKUP_LOCAL_PATH={tmp_path}/repository",
        "DOCKER_HOST=unix:///nonexistent",
        "prepare-prod",
    ]
    missing = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert missing.returncode != 0
    assert "Missing required backup input: BACKUP_PASSWORD" in missing.stderr
    assert "Traceback" not in missing.stderr
    private_env = tmp_path / "deploy/.env.prod"
    original = private_env.read_bytes()
    redirected_status, redirected_output = _prompted_prepare(
        command,
        tmp_path,
        "unused",
        redirect_input=True,
    )
    assert redirected_status != 0
    assert "Missing required backup input: BACKUP_PASSWORD" in redirected_output
    assert "BACKUP_PASSWORD: " not in redirected_output
    assert private_env.read_bytes() == original
    supplied = subprocess.run(
        [*command, "BACKUP_PASSWORD=environment-supplied-key"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert supplied.returncode == 0 or "host ownership privileges" in supplied.stderr
    assert private_env.read_bytes() == original
    assert "environment-supplied-key" not in supplied.stdout + supplied.stderr
    empty_status, empty_output = _prompted_prepare(command, tmp_path, "")
    assert empty_status != 0
    assert "BACKUP_PASSWORD cannot be empty" in empty_output
    assert private_env.read_bytes() == original
    key = "review-$TOKEN#'\"\\ key\\"
    status, output = _prompted_prepare(command, tmp_path, key)
    assert status == 0 or any(
        message in output for message in ("host ownership privileges", "+ sudo ")
    ), output
    assert output.count("BACKUP_PASSWORD: ") == 1
    assert key not in output
    assert private_env.read_bytes().startswith(original)
    assert private_env.stat().st_mode & 0o077 == 0
    configured = private_env.read_bytes()
    ready = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert ready.returncode == 0 or "host ownership privileges" in ready.stderr
    assert "BACKUP_PASSWORD: " not in ready.stdout + ready.stderr
    assert key not in ready.stdout + ready.stderr
    assert private_env.read_bytes() == configured
    base = (
        "deploy/docker-compose.prod.yml"
        if profile == "react-vite"
        else ("profiles/server-rendered-django/release.compose.yml")
    )
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            ".",
            "-f",
            base,
            "-f",
            "profiles/immutable-backup/compose.yml",
            "--env-file",
            str(private_env),
            "config",
            "--environment",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "PROD_ENV_FILE": str(private_env),
            "COMPOSE_PROJECT_NAME": "dev-scaffold",
            "BACKUP_IMAGE": f"local/dev-scaffold-backup:{revision}",
            "BACKUP_LOCAL_PATH": str(tmp_path / "repository"),
            "RELEASE_BACKEND_IMAGE": f"local/dev-scaffold-backend:{revision}",
            "RELEASE_WEB_IMAGE": f"local/dev-scaffold-web:{revision}",
            "DOCKER_HOST": "unix:///nonexistent",
        },
    )
    resolved = dict(line.split("=", 1) for line in rendered.stdout.splitlines() if "=" in line)
    assert resolved["BACKUP_PASSWORD"] == key
    directory = tmp_path / "repository"
    if directory.exists():
        assert directory.stat().st_uid == 10001
        assert directory.stat().st_mode & 0o7777 == 0o700
        directory.rmdir()
    assert not (tmp_path / "rclone").exists()


@pytest.mark.parametrize(
    "database_url,accepted",
    [
        ("postgresql://app:private-password@db/app", True),
        ("postgresql://app:wrong@db/app", False),
        ("postgresql://app:private-password@db:invalid/app", False),
    ],
)
@pytest.mark.parametrize("application_service", ["backend", "app"])
def test_production_input_validation_checks_resolved_credentials_without_leaking_values(
    database_url: str,
    accepted: bool,
    application_service: str,
) -> None:
    environment = {
        "DJANGO_SECRET_KEY": "private-key",
        "DJANGO_ALLOWED_HOSTS": "localhost",
        "POSTGRES_USER": "app",
        "POSTGRES_PASSWORD": "private-password",
        "POSTGRES_DB": "app",
        "DATABASE_URL": database_url,
    }
    result = subprocess.run(
        [
            sys.executable,
            str(_repo_root() / "scripts/validate_prod_config.py"),
            application_service,
        ],
        input=json.dumps(
            {
                "services": {
                    name: {"environment": environment} for name in (application_service, "db")
                }
            }
        ),
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is accepted
    assert "private-password" not in result.stdout + result.stderr
    assert "private-key" not in result.stdout + result.stderr
