from django.urls import path

from request_board import api_views

app_name = "request_board_api"

urlpatterns = [
    path("requests/", api_views.request_list, name="request_list"),
    path("requests/<int:request_id>/", api_views.request_detail, name="request_detail"),
    path(
        "request-creations/<uuid:key>/",
        api_views.request_creation_status,
        name="request_creation_status",
    ),
]
