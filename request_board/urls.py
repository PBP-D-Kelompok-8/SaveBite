from django.urls import path
from django.views.generic import RedirectView

from request_board import views

app_name = "request_board"

urlpatterns = [
    path("requests", views.api_request_collection, name="api_request_collection"),
    path(
        "requests/<int:request_id>/offers",
        views.api_request_offer,
        name="api_request_offer",
    ),
    path(
        "requests/<int:request_id>/status",
        views.api_request_status,
        name="api_request_status",
    ),
    path(
        "requests/<int:request_id>",
        views.api_request_delete,
        name="api_request_delete",
    ),
    path("board/", views.request_board, name="request_board"),
    path("board/new/", views.request_create, name="request_create"),
    path(
        "board/<int:request_id>/offer/",
        views.request_action,
        {"action": "offer"},
        name="request_offer",
    ),
    path(
        "board/<int:request_id>/fulfill/",
        views.request_action,
        {"action": "fulfill"},
        name="request_fulfill",
    ),
    path(
        "board/<int:request_id>/delete/",
        views.request_action,
        {"action": "delete"},
        name="request_delete",
    ),
    path(
        "board/login/",
        RedirectView.as_view(pattern_name="login", query_string=True),
        name="login",
    ),
]
