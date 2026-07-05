from django.urls import path

from . import views

urlpatterns = [
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path(
        views.GOOGLE_SITE_VERIFICATION_FILE,
        views.google_site_verification,
        name="google_site_verification",
    ),
    path("", views.index, name="index"),
    path("stats/login/", views.stats_login, name="stats_login"),
    path("stats/logout/", views.stats_logout, name="stats_logout"),
    path("stats", views.stats_page, name="stats"),
    path("stats/", views.stats_page, name="stats_slash"),
    path("api/health/", views.api_health, name="api_health"),
    path("api/state/", views.api_state, name="api_state"),
    path("api/visit-duration/", views.api_visit_duration, name="api_visit_duration"),
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
