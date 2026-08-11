from typing import TYPE_CHECKING

from django.contrib import admin

from .models import OperationalCheckState

# django-stubs models ModelAdmin as generic, but Django's runtime class is not
# subscriptable, so apply the model type only while static type checking.
if TYPE_CHECKING:
    _OperationalCheckStateAdminBase = admin.ModelAdmin[OperationalCheckState]
else:
    _OperationalCheckStateAdminBase = admin.ModelAdmin


@admin.register(OperationalCheckState)
class OperationalCheckStateAdmin(_OperationalCheckStateAdminBase):
    list_display = ("label", "status", "message", "checked_at", "changed_at")
    readonly_fields = ("key", "label", "status", "message", "checked_at", "changed_at")
