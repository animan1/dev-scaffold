from __future__ import annotations

import json
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

from backoffice_lib.json_dataclass import from_json_data
from django.conf import settings
from django.utils import timezone

from app.project.release_health import ReleaseHealth

REVISION_PATTERN = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)


class StagingReleaseError(Exception):
    pass


@dataclass(frozen=True)
class DeploymentAction:
    priority: int
    staged_at: datetime
    revision: str
    review_url: str


def _timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise StagingReleaseError(f"{label} is invalid") from error
    if timezone.is_naive(parsed):
        raise StagingReleaseError(f"{label} must include a timezone")
    return parsed


def deployment_action(
    release: ReleaseHealth,
    now: datetime | None = None,
    *,
    production_revision: str | None = None,
    production_deployed_at: str | None = None,
) -> DeploymentAction | None:
    if release.status != "ok":
        raise StagingReleaseError("staging health status is not ok")
    if release.environment != "staging":
        raise StagingReleaseError("staging health environment is invalid")
    if REVISION_PATTERN.fullmatch(release.revision) is None:
        raise StagingReleaseError("staging revision is invalid")

    staged_at = _timestamp(release.deployed_at, "staging deployment time")
    production_at = _timestamp(
        production_deployed_at or settings.RELEASE_DEPLOYED_AT,
        "production deployment time",
    )
    production_revision = production_revision or settings.RELEASE_REVISION
    is_current_revision = release.revision.lower() == production_revision.lower()
    if is_current_revision or staged_at <= production_at:
        return None

    age = (now or timezone.now()) - staged_at
    if age < timedelta(hours=settings.DEPLOYMENT_REMINDER_HOURS):
        return None
    if age >= timedelta(days=14):
        priority = 4
    elif age >= timedelta(days=7):
        priority = 3
    else:
        priority = 1
    return DeploymentAction(priority, staged_at, release.revision, str(settings.STAGING_REVIEW_URL))


def _release_health(review_url: str, ca_file: str, label: str) -> ReleaseHealth:
    review_url = review_url.rstrip("/")
    if not review_url:
        raise StagingReleaseError(f"{label} URL is not configured")
    context = None
    if ca_file:
        context = ssl.create_default_context(cafile=Path(ca_file))
        # Local deployments pin the peer's mkcert leaf certificate directly.
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    status_url = f"{review_url}/api/healthz"
    try:
        with urlopen(status_url, timeout=10, context=context) as response:  # noqa: S310
            release = from_json_data(ReleaseHealth, json.load(response))
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise StagingReleaseError(f"{label} health could not be read") from error
    return release


def current_deployment_action() -> DeploymentAction | None:
    environment = settings.DEPLOYMENT_ENVIRONMENT
    if environment == "production":
        staging = _release_health(
            str(settings.STAGING_REVIEW_URL),
            str(settings.STAGING_STATUS_CA_FILE),
            "staging",
        )
        return deployment_action(staging)
    if environment == "staging":
        production = _release_health(
            str(settings.PRODUCTION_REVIEW_URL),
            str(settings.PRODUCTION_STATUS_CA_FILE),
            "production",
        )
        production_is_invalid = (
            production.status != "ok"
            or production.environment != "production"
            or REVISION_PATTERN.fullmatch(production.revision) is None
        )
        if production_is_invalid:
            raise StagingReleaseError("production health is invalid")
        staging = ReleaseHealth(
            status="ok",
            environment="staging",
            revision=str(settings.RELEASE_REVISION),
            image_prefix=str(settings.RELEASE_IMAGE_PREFIX),
            deployed_at=str(settings.RELEASE_DEPLOYED_AT),
        )
        return deployment_action(
            staging,
            production_revision=production.revision,
            production_deployed_at=production.deployed_at,
        )
    return None
