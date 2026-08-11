from django.contrib import admin

from .models import OperationalCheckState


@admin.register(OperationalCheckState)
class OperationalCheckStateAdmin(admin.ModelAdmin[OperationalCheckState]):
    list_display = ("label", "status", "message", "checked_at", "changed_at")
    readonly_fields = ("key", "label", "status", "message", "checked_at", "changed_at")
