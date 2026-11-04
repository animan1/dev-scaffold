from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from app.monitoring.models import OperationalCheckState


@pytest.mark.django_db
def test_deployment_check_removes_its_temporary_record() -> None:
    assert call_command("test_deployment") == "Application deployment checks passed"
    assert not OperationalCheckState.objects.exists()


@pytest.mark.django_db
def test_failed_deployment_check_preserves_existing_data(monkeypatch: pytest.MonkeyPatch) -> None:
    now = timezone.now()
    original = OperationalCheckState.objects.create(
        key="existing-state",
        label="Existing state",
        status="healthy",
        message="preserve",
        checked_at=now,
        changed_at=now,
    )
    monkeypatch.setattr(OperationalCheckState.objects, "get", lambda *a, **k: original)
    with pytest.raises(CommandError, match="write/read failed"):
        call_command("test_deployment")
    assert list(OperationalCheckState.objects.values_list("key", "message")) == [
        ("existing-state", "preserve")
    ]
