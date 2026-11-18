
from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from . import views, api_views

app_name = "resep_komunitas"

urlpatterns = [
    path("", views.recipe_list, name="recipe_list"),
    path("create/", views.recipe_create, name="recipe_create"),
    path("<int:pk>/edit/", views.recipe_update, name="recipe_update"),
    path("<int:pk>/delete/", views.recipe_delete, name="recipe_delete"),
    path("<int:pk>/", views.recipe_detail, name="recipe_detail"),
    path("api/recipes/", views.get_recipes_json, name="get_recipes_json"),

    # API: Login untuk mendapatkan token
    path("api/v1/token/", obtain_auth_token, name="api-token"),
    # API: Daftar dan tambah resep
    path("api/v1/recipes/", api_views.recipe_list_api, name="recipe-list-api"),
    # API: Detail, update, dan delete resep
    path("api/v1/recipes/<int:pk>/", api_views.recipe_detail_api, name="recipe-detail-api"),
]
