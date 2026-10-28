
from django.urls import path

from . import views

app_name = "resep_komunitas"

urlpatterns = [
    path("", views.recipe_list, name="recipe_list"),
    path("create/", views.recipe_create, name="recipe_create"),
    path("<int:pk>/edit/", views.recipe_update, name="recipe_update"),
    path("<int:pk>/delete/", views.recipe_delete, name="recipe_delete"),
    path("<int:pk>/", views.recipe_detail, name="recipe_detail"),
    path("api/recipes/", views.get_recipes_json, name="get_recipes_json"),
]
