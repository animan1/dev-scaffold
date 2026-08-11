from __future__ import annotations

from datetime import datetime

from django.db import models


class OperationalCheckState(models.Model):
    class Status(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        FAILED = "failed", "Failed"

    key: models.CharField[str, str] = models.CharField(max_length=100, unique=True)
    label: models.CharField[str, str] = models.CharField(max_length=200)
    status: models.CharField[str, str] = models.CharField(max_length=10, choices=Status.choices)
    message: models.TextField[str, str] = models.TextField(blank=True)
    checked_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    changed_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.label}: {self.status}"
