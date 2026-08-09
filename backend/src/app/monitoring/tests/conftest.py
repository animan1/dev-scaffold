from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def configured_google_calendar(settings: object) -> None:
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"  # type: ignore[attr-defined]
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"  # type: ignore[attr-defined]
