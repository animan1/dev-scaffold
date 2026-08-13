from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path

from .release_health import ReleaseHealth


def health(_request: HttpRequest) -> JsonResponse:
    release = ReleaseHealth(
        status="ok",
        environment=settings.DEPLOYMENT_ENVIRONMENT,
        revision=settings.RELEASE_REVISION,
        image_prefix=settings.RELEASE_IMAGE_PREFIX,
        deployed_at=settings.RELEASE_DEPLOYED_AT,
    )
    return JsonResponse(release.payload())


def home(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("<!doctype html><html><body><main>Django application</main></body></html>")


urlpatterns = [
    path("", home, name="home"),
    path("api/healthz", health, name="health"),
]
