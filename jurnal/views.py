import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from .forms import FoodLogFilterForm, FoodLogForm
from .models import FoodLog


def _food_log_summary(user):
    counts = {
        row["status"]: row["total"]
        for row in FoodLog.objects.filter(user=user)
        .values("status")
        .annotate(total=Count("pk"))
    }
    saved_count = counts.get("terselamatkan", 0)
    wasted_count = counts.get("terbuang", 0)
    total_count = saved_count + wasted_count
    saved_percentage = round(saved_count * 100 / total_count) if total_count else 0
    wasted_percentage = 100 - saved_percentage if total_count else 0
    discarded_angle = wasted_count * 360 / total_count if total_count else 0
    return {
        "saved_count": saved_count,
        "wasted_count": wasted_count,
        "total_count": total_count,
        "saved_percentage": saved_percentage,
        "wasted_percentage": wasted_percentage,
        "discarded_angle": discarded_angle,
        "chart_start_angle": -(discarded_angle / 2),
    }


def _prepare_food_log_card(log):
    completed_at = timezone.localtime(log.completed_at)
    log.card_text = f"{completed_at:%d %b %Y} · {log.quantity} {log.get_unit_display()}"
    if log.notes:
        log.card_text += f"\n{log.notes}"
    log.card_badge_status = "success" if log.status == "terselamatkan" else "failed"
    log.edit_values_json = json.dumps({
        "food_name": log.food_name,
        "quantity": log.quantity,
        "unit": log.unit,
        "status": log.status,
        "notes": log.notes,
    })
    return log


def _food_logs_for_user(user, filters=None):
    """Return only this user's logs, applying each validated filter together."""
    logs = FoodLog.objects.filter(user=user)
    if filters:
        query = filters.get("query", "").strip()
        if query:
            logs = logs.filter(Q(food_name__icontains=query) | Q(notes__icontains=query))
        if filters.get("status"):
            logs = logs.filter(status=filters["status"])
        if filters.get("date_from"):
            logs = logs.filter(completed_at__date__gte=filters["date_from"])
        if filters.get("date_to"):
            logs = logs.filter(completed_at__date__lte=filters["date_to"])
    return [_prepare_food_log_card(log) for log in logs]


def _journal_context(request, form=None):
    filter_form = FoodLogFilterForm(request.GET or None)
    filters_valid = filter_form.is_valid()
    filters = filter_form.cleaned_data if filters_valid else {}
    return {
        "food_logs": _food_logs_for_user(request.user, filters),
        "form": form if form is not None else FoodLogForm(),
        "filter_form": filter_form,
        "filters_invalid": bool(request.GET and not filters_valid),
        "filters_applied": any(value.strip() for value in request.GET.values()),
        **_food_log_summary(request.user),
    }


def _ajax_journal_response(request, message=None):
    """Return fresh list and summary markup for a single AJAX round trip."""
    context = _journal_context(request)
    payload = {
        "ok": True,
        "list_html": render_to_string(
            "jurnal/foodlog_list.html", context, request=request
        ),
        "summary_html": render_to_string(
            "jurnal/foodlog_summary.html", context, request=request
        ),
    }
    if message:
        payload["message"] = message
    return JsonResponse(payload)


@login_required(login_url="login")
def food_log_list(request):
    context = _journal_context(request)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return _ajax_journal_response(request)
    return render(request, "jurnal/index.html", context)


@login_required(login_url="login")
def food_log_create(request):
    if request.method != "POST":
        return redirect("jurnal:index")

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    form = FoodLogForm(request.POST)
    if form.is_valid():
        food_log = form.save(commit=False)
        food_log.user = request.user
        food_log.save()
        if is_ajax:
            return _ajax_journal_response(
                request, "Catatan makanan berhasil ditambahkan."
            )
        messages.success(request, "Catatan makanan berhasil ditambahkan.")
        return redirect("jurnal:index")

    if is_ajax:
        return JsonResponse({
            "ok": False,
            "form_html": render_to_string(
                "jurnal/foodlog_form_fields.html", {"form": form}, request=request
            ),
        }, status=400)

    return render(
        request,
        "jurnal/index.html",
        _journal_context(request, form=form),
        status=400,
    )


@login_required(login_url="login")
def food_log_update(request, pk):
    food_log = get_object_or_404(FoodLog, pk=pk, user=request.user)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == "POST":
        form = FoodLogForm(request.POST, instance=food_log)
        if form.is_valid():
            form.save()
            if is_ajax:
                return _ajax_journal_response(
                    request, "Catatan makanan berhasil diperbarui."
                )
            messages.success(request, "Catatan makanan berhasil diperbarui.")
            return redirect("jurnal:index")
        if is_ajax:
            return JsonResponse({
                "ok": False,
                "form_html": render_to_string(
                    "jurnal/foodlog_form_fields.html", {"form": form}, request=request
                ),
            }, status=400)
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
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method == "POST":
        food_log.delete()
        if is_ajax:
            return _ajax_journal_response(
                request, "Catatan makanan berhasil dihapus."
            )
        messages.success(request, "Catatan makanan berhasil dihapus.")
        return redirect("jurnal:index")

    return render(request, "jurnal/delete_confirm.html", {"food_log": food_log})