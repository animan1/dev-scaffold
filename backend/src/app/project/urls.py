from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.urls import path


def health(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", health, name="health"),
]
