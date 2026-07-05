from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("stats", views.stats_page, name="stats"),
    path("stats/", views.stats_page, name="stats_slash"),
    path("api/health/", views.api_health, name="api_health"),
    path("api/state/", views.api_state, name="api_state"),
    path("api/export/", views.api_export, name="api_export"),
    path(
        "api/admin/historical-backfill/",
        views.api_historical_backfill,
        name="api_historical_backfill",
    ),
    path(
        "api/admin/visitor-stats/",
        views.api_visitor_stats,
        name="api_visitor_stats",
    ),
]
