from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/state/", views.api_state, name="api_state"),
    path("api/bot/start/", views.api_start, name="api_start"),
    path("api/bot/stop/", views.api_stop, name="api_stop"),
    path("api/bot/reset/", views.api_reset, name="api_reset"),
    path("api/prices/", views.api_prices, name="api_prices"),
    path("api/trade/", views.api_trade, name="api_trade"),
    path("api/export/", views.api_export, name="api_export"),
]
