from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/state/", views.api_state, name="api_state"),
    path("api/export/", views.api_export, name="api_export"),
]
