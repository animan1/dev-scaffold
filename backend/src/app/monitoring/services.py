from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.utils import timezone

from .checks import OperationalCheckResult, operational_checks
from .models import OperationalCheckState

FAILED_TRANSITION: Final = "failed"
RECOVERED_TRANSITION: Final = "recovered"


@dataclass(frozen=True)
class OperationalCheckTransition:
    kind: Literal["failed", "recovered"]
    result: OperationalCheckResult


def validate_monitoring_configuration() -> None:
    if not settings.SITE_ADMIN_EMAIL:
        raise ImproperlyConfigured(
            "Set CUPLR_SITE_ADMIN_EMAIL in deploy/.env.prod "
            "(or the production process environment) for operational monitoring."
        )
    uses_console_backend = settings.EMAIL_BACKEND.endswith(".console.EmailBackend")
    if not settings.DEBUG and uses_console_backend:
        message = "Configure a production email backend for monitoring."
        raise ImproperlyConfigured(message)
    uses_smtp_backend = settings.EMAIL_BACKEND.endswith(".smtp.EmailBackend")
    if uses_smtp_backend and not settings.EMAIL_HOST:
        raise ImproperlyConfigured(
            "Set DJANGO_EMAIL_HOST in deploy/.env.prod "
            "(or the production process environment) for SMTP monitoring notifications."
        )


def email_operational_transition(transition: OperationalCheckTransition) -> None:
    validate_monitoring_configuration()
    environment = settings.DEPLOYMENT_ENVIRONMENT
    send_mail(
        subject=(
            f"[Cuplr {environment}] Operational check {transition.kind}: {transition.result.label}"
        ),
        message=(
            f"Environment: {environment}\n"
            f"Release: {settings.RELEASE_REVISION}\n"
            f"Check: {transition.result.label}\n"
            f"Transition: {transition.kind}\n\n"
            f"{transition.result.message}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.SITE_ADMIN_EMAIL],
    )


def run_operational_checks(
    notify: Callable[[OperationalCheckTransition], None] = email_operational_transition,
) -> tuple[OperationalCheckResult, ...]:
    results = operational_checks()
    for result in results:
        _record_result(result, notify)
    return results


def _record_result(
    result: OperationalCheckResult,
    notify: Callable[[OperationalCheckTransition], None],
) -> None:
    now = timezone.now()
    next_status = (
        OperationalCheckState.Status.HEALTHY
        if result.healthy
        else OperationalCheckState.Status.FAILED
    )
    state = OperationalCheckState.objects.filter(key=result.key).first()
    transition: OperationalCheckTransition | None = None
    if state is None and not result.healthy:
        transition = OperationalCheckTransition(FAILED_TRANSITION, result)
    elif state is not None and state.status != next_status:
        transition = OperationalCheckTransition(
            RECOVERED_TRANSITION if result.healthy else FAILED_TRANSITION,
            result,
        )
    if transition is not None:
        notify(transition)

    if state is None:
        OperationalCheckState.objects.create(
            key=result.key,
            label=result.label,
            status=next_status,
            message=result.message,
            checked_at=now,
            changed_at=now,
        )
        return
    changed = state.status != next_status
    state.label = result.label
    state.status = next_status
    state.message = result.message
    state.checked_at = now
    if changed:
        state.changed_at = now
    state.save()
