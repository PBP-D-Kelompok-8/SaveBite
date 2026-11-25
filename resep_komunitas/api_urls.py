from django.urls import path
from . import api_views

app_name = "resep_komunitas_api"

urlpatterns = [
    path("recipes/", api_views.recipe_list_api, name="recipe-list",),
    path("recipes/<int:pk>/", api_views.recipe_detail_api, name="recipe-detail",),
]