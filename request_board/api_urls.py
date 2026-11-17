from django.urls import path

from request_board import api_views

app_name = "request_board_api"

urlpatterns = [
    path("requests/", api_views.request_list, name="request_list"),
    path("requests/<int:request_id>/", api_views.request_detail, name="request_detail"),
    path(
        "requests/<int:request_id>/offers/",
        api_views.request_offer_list,
        name="request_offer_list",
    ),
    path(
        "requests/<int:request_id>/offers/<int:offer_id>/",
        api_views.request_offer_detail,
        name="request_offer_detail",
    ),
    path(
        "requests/<int:request_id>/selection/",
        api_views.request_selection,
        name="request_selection",
    ),
    path(
        "requests/<int:request_id>/status/",
        api_views.request_status,
        name="request_status",
    ),
    path(
        "request-creations/<uuid:key>/",
        api_views.request_creation_status,
        name="request_creation_status",
    ),
]
