from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from SaveBite.forms import SaveBiteAuthenticationForm


def login_page(request):
    next_url = request.POST.get("next") or request.GET.get("next", "")

    if request.method == "POST":
        form = SaveBiteAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())

            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)

            return redirect("landing_page")
    else:
        form = SaveBiteAuthenticationForm(request)

    return TemplateResponse(request, "login.html", {"form": form, "next": next_url})


def register_page(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Akun berhasil dibuat. Silakan login.")
            return redirect("login")
    else:
        form = UserCreationForm()

    return render(request, "register.html", {"form": form})


@require_POST
def logout_page(request):
    logout(request)
    return redirect("landing_page")
