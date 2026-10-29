from django.shortcuts import render

from main.views import login_page, logout_page, register_page


def landing_page(request):
    return render(request, "index.html")
