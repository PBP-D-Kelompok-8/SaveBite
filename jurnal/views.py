from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import FoodLogForm
from .models import FoodLog


@login_required(login_url="login")
def food_log_list(request):
    logs = FoodLog.objects.filter(user=request.user)
    return render(
        request,
        "jurnal/index.html",
        {"food_logs": logs, "form": FoodLogForm()},
    )


@login_required(login_url="login")
def food_log_create(request):
    if request.method != "POST":
        return redirect("jurnal:index")

    form = FoodLogForm(request.POST)
    if form.is_valid():
        food_log = form.save(commit=False)
        food_log.user = request.user
        food_log.save()
        messages.success(request, "Catatan makanan berhasil ditambahkan.")
        return redirect("jurnal:index")

    logs = FoodLog.objects.filter(user=request.user)
    return render(
        request,
        "jurnal/index.html",
        {"food_logs": logs, "form": form},
        status=400,
    )


@login_required(login_url="login")
def food_log_update(request, pk):
    food_log = get_object_or_404(FoodLog, pk=pk, user=request.user)

    if request.method == "POST":
        form = FoodLogForm(request.POST, instance=food_log)
        if form.is_valid():
            form.save()
            messages.success(request, "Catatan makanan berhasil diperbarui.")
            return redirect("jurnal:index")
    else:
        form = FoodLogForm(instance=food_log)

    return render(
        request,
        "jurnal/edit.html",
        {"form": form, "food_log": food_log},
        status=400 if request.method == "POST" else 200,
    )


@login_required(login_url="login")
def food_log_delete(request, pk):
    food_log = get_object_or_404(FoodLog, pk=pk, user=request.user)

    if request.method == "POST":
        food_log.delete()
        messages.success(request, "Catatan makanan berhasil dihapus.")
        return redirect("jurnal:index")

    return render(request, "jurnal/delete_confirm.html", {"food_log": food_log})
