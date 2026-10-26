from django.contrib.auth.views import LoginView
from django.shortcuts import render

from SaveBite.forms import SaveBiteAuthenticationForm


def landing_page(request):
    return render(request, "index.html")


login_page = LoginView.as_view(
    authentication_form=SaveBiteAuthenticationForm,
    template_name="login.html",
)


def register_page(request):
    return render(request, "register.html")
