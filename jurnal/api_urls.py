from django.urls import path

from . import api_auth, api_views

app_name = "jurnal_api"

urlpatterns = [
    # POST {"username": ..., "password": ...} -> {"token": "..."}
    path("auth/token/", api_auth.login, name="token"),
    path("food-logs/", api_views.food_log_collection, name="food-log-list"),
    # "summary/" must come before the <int:pk> route only for readability; ints never match it.
    path("food-logs/summary/", api_views.food_log_summary, name="food-log-summary"),
    path("food-logs/<int:pk>/", api_views.food_log_item, name="food-log-detail"),
]