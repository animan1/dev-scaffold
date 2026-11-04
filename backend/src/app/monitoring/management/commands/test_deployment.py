from __future__ import annotations

from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.monitoring.models import OperationalCheckState


class Command(BaseCommand):
    help = "Check application database writes and reads using a temporary monitoring record."

    def handle(self, *args: object, **options: object) -> str:
        now = timezone.now()
        record = OperationalCheckState.objects.create(
            key="deployment-acceptance-" + uuid4().hex,
            label="Deployment acceptance",
            status=OperationalCheckState.Status.HEALTHY,
            message="database write/read",
            checked_at=now,
            changed_at=now,
        )
        try:
            retrieved = OperationalCheckState.objects.get(pk=record.pk)
            if retrieved.message != "database write/read":
                raise CommandError("Application database write/read failed")
        finally:
            record.delete()
        return "Application deployment checks passed"
