from django.urls import path

from . import api_views

app_name = "food_rescue_planner_api"

urlpatterns = [
    path(
        "rescue-plans/",
        api_views.rescue_plan_list,
        name="rescue_plan_list",
    ),
    path(
        "rescue-plans/<int:plan_id>/",
        api_views.rescue_plan_detail,
        name="rescue_plan_detail",
    ),
]