from django.contrib import admin

from .models import BotState, PageVisit


@admin.register(BotState)
class BotStateAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
    readonly_fields = ("id", "data", "updated_at")


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    list_display = ("visited_at", "referer_source", "referer_host", "path", "is_bot")
    list_filter = ("referer_source", "is_bot", "visited_at")
    search_fields = ("referer_host", "referer", "user_agent", "ip_hash")
    readonly_fields = (
        "visited_at",
        "path",
        "referer",
        "referer_source",
        "referer_host",
        "user_agent",
        "ip_hash",
        "is_bot",
    )
    date_hierarchy = "visited_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
