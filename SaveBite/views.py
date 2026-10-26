from django.shortcuts import render


def landing_page(request):
    return render(request, "index.html")


# Baru tampilan (HTML/CSS). Logika login & register ditambahkan nanti.
def login_page(request):
    return render(request, "login.html")


def register_page(request):
    return render(request, "register.html")
