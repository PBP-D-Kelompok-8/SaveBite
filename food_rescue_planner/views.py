from django.shortcuts import render
from .models import FoodRescuePlan


def index(request):
    plans = FoodRescuePlan.objects.all().order_by('-created_at')

    context = {
        'plans': plans,
    }

    return render(
        request,
        'food_rescue_planner/index.html',
        context,
    )