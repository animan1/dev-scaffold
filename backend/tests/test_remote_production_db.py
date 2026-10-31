from pathlib import Path

ROOT = Path(__file__).parents[2]
PROFILE = ROOT / "profiles" / "remote-production-db"


def _read(relative: str) -> str:
    return (PROFILE / relative).read_text()


def test_production_database_is_published_on_remote_loopback_only() -> None:
    compose = _read("production.compose.yml")

    assert '"127.0.0.1:${REMOTE_DB_LOOPBACK_PORT:-15432}:5432"' in compose
    assert "0.0.0.0" not in compose


def test_laptop_tunnel_publishes_no_host_port_and_mounts_ssh_inputs_read_only() -> None:
    compose = _read("staging.compose.yml")

    assert "production-db-tunnel:" in compose
    assert "ports:" not in compose
    assert "STAGING_DB_SSH_IDENTITY_FILE" in compose
    assert "STAGING_DB_SSH_KNOWN_HOSTS_FILE" in compose
    assert compose.count("read_only: true") == 3
    assert "cap_drop:" in compose
    assert "no-new-privileges:true" in compose
    assert "read_only: true" in compose
    assert "pg_isready --host 127.0.0.1 --port 5432" in compose
    assert "restart: unless-stopped" in compose


def test_tunnel_fails_closed_and_uses_strict_host_verification() -> None:
    script = _read("tunnel.sh")

    assert "ExitOnForwardFailure=yes" in script
    assert "StrictHostKeyChecking=yes" in script
    assert "UserKnownHostsFile=" in script
    assert "BatchMode=yes" in script
    assert "IdentitiesOnly=yes" in script
    assert '-L "0.0.0.0:${TUNNEL_PORT}:${REMOTE_DB_HOST}:${REMOTE_DB_PORT}"' in script


def test_make_contract_operates_only_the_tunnel_service() -> None:
    makefile = _read("profile.mk")

    for target in (
        "up-staging-db-tunnel",
        "status-staging-db-tunnel",
        "logs-staging-db-tunnel",
        "down-staging-db-tunnel",
    ):
        assert f"{target}:" in makefile

    down_recipe = makefile.split("down-staging-db-tunnel:", 1)[1].split(".PHONY:", 1)[0]
    assert " stop $(REMOTE_DB_TUNNEL_SERVICE)" in down_recipe
    assert " rm -f $(REMOTE_DB_TUNNEL_SERVICE)" in down_recipe
    assert " down" not in down_recipe


def test_profile_documents_live_database_risk_and_no_staging_migrations() -> None:
    readme = _read("README.md")

    assert "transport privacy, not staging isolation" in readme
    assert "Never run candidate migrations from the laptop" in readme
    assert "expand/contract migrations" in readme
    assert "production continues normally" in readme.lower()
