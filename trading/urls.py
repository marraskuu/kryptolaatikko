from django.urls import path

from . import views

urlpatterns = [
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("llms.txt", views.llms_txt, name="llms_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path(
        views.GOOGLE_SITE_VERIFICATION_FILE,
        views.google_site_verification,
        name="google_site_verification",
    ),
    path("", views.index, name="index"),
    path("eng/", views.index_en, name="index_en"),
    path("eng", views.index_en, name="index_en_noslash"),
    path("muutokset/", views.muutokset_page, name="muutokset"),
    path("changelog/", views.changelog_page, name="changelog"),
    path("changelog", views.changelog_page, name="changelog_noslash"),
    path("stats/login/", views.stats_login, name="stats_login"),
    path("stats/logout/", views.stats_logout, name="stats_logout"),
    path("stats", views.stats_page, name="stats"),
    path("stats/", views.stats_page, name="stats_slash"),
    path(
        "strategy-explorer/",
        views.strategy_explorer_page,
        name="strategy_explorer",
    ),
    path("api/health/", views.api_health, name="api_health"),
    path("api/state/", views.api_state, name="api_state"),
    path(
        "api/strategy-explorer/",
        views.api_strategy_explorer,
        name="api_strategy_explorer",
    ),
    path(
        "api/strategy-explorer/symbols/",
        views.api_strategy_explorer_symbols,
        name="api_strategy_explorer_symbols",
    ),
    path("api/visit-record/", views.api_visit_record, name="api_visit_record"),
    path("api/visit-duration/", views.api_visit_duration, name="api_visit_duration"),
    path("api/share-click/", views.api_share_click, name="api_share_click"),
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
