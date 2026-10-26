
from django.urls import path

from . import views

app_name = "resep_komunitas"

urlpatterns = [
    path("", views.recipe_list, name="recipe_list"),
    path("<int:pk>/delete/", views.recipe_delete, name="recipe_delete"),
    path("<int:pk>/star/", views.recipe_toggle_star, name="recipe_toggle_star"),
]
