from django.urls import path

from . import views

app_name = "katalog"

urlpatterns = [
    path("", views.home, name="home"),
    path("api/search/", views.api_search, name="api_search"),
    path("meal/<int:meal_id>/", views.meal_detail, name="meal_detail"),
    path("saved/", views.saved_list, name="saved_list"),
    path("saved/add/", views.saved_create, name="saved_create"),
    path("saved/<int:pk>/edit/", views.saved_update, name="saved_update"),
    path("saved/<int:pk>/delete/", views.saved_delete, name="saved_delete"),
]
