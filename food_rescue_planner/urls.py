from django.urls import path
from . import views

app_name = 'food_rescue_planner'

urlpatterns = [
    path('', views.index, name='index'),
    path('create/', views.create_plan, name='create'),
    path('<int:plan_id>/edit/', views.edit_plan, name='edit'),
    path('<int:plan_id>/delete/', views.delete_plan, name='delete'),
]
