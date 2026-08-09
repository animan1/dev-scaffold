from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.utils import timezone

from .checks import OperationalCheckResult, operational_checks
from .heartbeats import (
    HeartbeatChannel,
    HeartbeatConfigurationError,
    HeartbeatEvent,
    load_heartbeat_policy,
    load_heartbeat_url,
    send_heartbeat,
)
from .models import OperationalCheckState

FAILED_TRANSITION: Final = "failed"
RECOVERED_TRANSITION: Final = "recovered"


@dataclass(frozen=True)
class OperationalCheckTransition:
    kind: Literal["failed", "recovered"]
    result: OperationalCheckResult


def validate_monitoring_configuration() -> None:
    if settings.MONITOR_NOTIFICATION_BACKEND == "external":
        try:
            for channel in HeartbeatChannel:
                load_heartbeat_url(channel)
                load_heartbeat_policy(channel)
        except HeartbeatConfigurationError as error:
            raise ImproperlyConfigured(str(error)) from error
        return
    if settings.MONITOR_NOTIFICATION_BACKEND != "email":
        raise ImproperlyConfigured("MONITOR_NOTIFICATION_BACKEND must be email or external.")
    if not settings.SITE_ADMIN_EMAIL:
        raise ImproperlyConfigured(
            "Set SITE_ADMIN_EMAIL in deploy/.env.prod "
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
            f"[{settings.MONITOR_SITE_NAME} {environment}] Operational check "
            f"{transition.kind}: {transition.result.label}"
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


def configured_transition_notifier(transition: OperationalCheckTransition) -> None:
    if settings.MONITOR_NOTIFICATION_BACKEND == "email":
        email_operational_transition(transition)


def run_operational_checks(
    notify: Callable[[OperationalCheckTransition], None] = configured_transition_notifier,
) -> tuple[OperationalCheckResult, ...]:
    results = operational_checks()
    for result in results:
        _record_result(result, notify)
    if settings.MONITOR_NOTIFICATION_BACKEND == "external":
        send_heartbeat(
            HeartbeatChannel.OPERATIONAL,
            HeartbeatEvent.SUCCESS
            if all(result.healthy for result in results)
            else HeartbeatEvent.FAILURE,
        )
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
