from django.urls import include, path

from . import views

app_name = "jurnal"

urlpatterns = [
    path("", views.food_log_list, name="index"),
    path("tambah/", views.food_log_create, name="create"),
    path("<int:pk>/edit/", views.food_log_update, name="update"),
    path("<int:pk>/hapus/", views.food_log_delete, name="delete"),
    path("api/v1/", include("jurnal.api_urls")),


]

