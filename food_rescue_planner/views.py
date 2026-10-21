from django.shortcuts import render

def index(request):
    return render(request, 'food_rescue_planner/index.html')