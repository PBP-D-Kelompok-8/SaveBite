from django.urls import path
from . import views

app_name = 'food_rescue_planner'

urlpatterns = [
    path('', views.index, name='index'),
]
