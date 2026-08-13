from __future__ import annotations

import json
import ssl
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import patch

import pytest
from django.test.utils import override_settings

from app.monitoring.releases import (
    StagingReleaseError,
    current_deployment_action,
    deployment_action,
)
from app.project.release_health import ReleaseHealth


def _health(staged_at: datetime, revision: str = "b" * 40) -> ReleaseHealth:
    return ReleaseHealth(
        status="ok",
        environment="staging",
        revision=revision,
        image_prefix="example/app",
        deployed_at=staged_at.isoformat(),
    )


@override_settings(
    DEPLOYMENT_ENVIRONMENT="production",
    RELEASE_REVISION="a" * 40,
    RELEASE_DEPLOYED_AT="2026-08-01T00:00:00+00:00",
    STAGING_REVIEW_URL="https://staging.example.com",
    DEPLOYMENT_REMINDER_HOURS=48,
)
@pytest.mark.parametrize(
    ("age", "expected_priority"),
    [
        (timedelta(hours=47), None),
        (timedelta(hours=48), 1),
        (timedelta(days=7), 3),
        (timedelta(days=14), 4),
    ],
)
def test_deployment_action_ages_into_priority(
    age: timedelta, expected_priority: int | None
) -> None:
    now = datetime(2026, 8, 20, tzinfo=UTC)

    action = deployment_action(_health(now - age), now)

    assert (action.priority if action else None) == expected_priority


@override_settings(
    RELEASE_REVISION="a" * 40,
    RELEASE_DEPLOYED_AT="2026-08-10T00:00:00+00:00",
)
def test_deployment_action_requires_staging_to_be_ahead() -> None:
    now = datetime(2026, 8, 20, tzinfo=UTC)

    same_revision = deployment_action(_health(now - timedelta(days=3), "a" * 40), now)
    older_staging = deployment_action(_health(datetime(2026, 8, 9, tzinfo=UTC)), now)

    assert same_revision is None
    assert older_staging is None


@override_settings(RELEASE_DEPLOYED_AT="2026-08-01T00:00:00+00:00")
def test_deployment_action_rejects_invalid_staging_health() -> None:
    with pytest.raises(StagingReleaseError, match="environment"):
        deployment_action(
            ReleaseHealth(
                status="ok",
                environment="production",
                revision="b" * 40,
                image_prefix="example/app",
                deployed_at="2026-08-10T00:00:00+00:00",
            )
        )


@override_settings(
    DEPLOYMENT_ENVIRONMENT="production",
    RELEASE_REVISION="a" * 40,
    RELEASE_DEPLOYED_AT="2026-08-01T00:00:00+00:00",
    STAGING_STATUS_CA_FILE="/staging.crt",
    STAGING_REVIEW_URL="https://staging.example.com/",
    DEPLOYMENT_REMINDER_HOURS=48,
)
def test_current_deployment_action_derives_health_url_and_trusts_certificate() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    response = BytesIO(json.dumps(_health(datetime(2026, 8, 10, tzinfo=UTC)).payload()).encode())

    with (
        patch("app.monitoring.releases.ssl.create_default_context", return_value=context),
        patch("app.monitoring.releases.urlopen", return_value=response) as open_status,
    ):
        current_deployment_action()

    assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    open_status.assert_called_once_with(
        "https://staging.example.com/api/healthz", timeout=10, context=context
    )


@override_settings(
    DEPLOYMENT_ENVIRONMENT="staging",
    RELEASE_REVISION="b" * 40,
    RELEASE_IMAGE_PREFIX="example/app",
    RELEASE_DEPLOYED_AT="2026-08-10T00:00:00+00:00",
    STAGING_REVIEW_URL="https://staging.example.com",
    PRODUCTION_REVIEW_URL="https://production.example.com/",
    PRODUCTION_STATUS_CA_FILE="",
    DEPLOYMENT_REMINDER_HOURS=48,
)
def test_current_deployment_action_compares_staging_to_production() -> None:
    production = ReleaseHealth(
        status="ok",
        environment="production",
        revision="a" * 40,
        image_prefix="example/app",
        deployed_at="2026-08-01T00:00:00+00:00",
    )
    response = BytesIO(json.dumps(production.payload()).encode())

    with (
        patch("app.monitoring.releases.urlopen", return_value=response) as open_status,
        patch(
            "app.monitoring.releases.timezone.now",
            return_value=datetime(2026, 8, 13, tzinfo=UTC),
        ),
    ):
        action = current_deployment_action()

    assert action is not None
    assert action.priority == 1
    assert action.revision == "b" * 40
    open_status.assert_called_once_with(
        "https://production.example.com/api/healthz", timeout=10, context=None
    )


@override_settings(DEPLOYMENT_ENVIRONMENT="development")
def test_development_has_no_deployment_action() -> None:
    assert current_deployment_action() is None
