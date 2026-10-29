from functools import wraps

from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect

from .models import FoodLog


FOOD_LOG_WRITE_LIMIT = 20
FOOD_LOG_WRITE_WINDOW_SECONDS = 60
FOOD_LOG_WRITE_CACHE_KEY = "jurnal:food-log-write:{user_id}"


def require_food_log_owner(view):
    """Resolve an entry only when it belongs to the authenticated request user."""
    @wraps(view)
    def wrapped(request, pk, *args, **kwargs):
        food_log = get_object_or_404(FoodLog, pk=pk, user=request.user)
        return view(request, pk, *args, food_log=food_log, **kwargs)

    return wrapped


def limit_food_log_writes(view):
    """Limit authenticated users to 20 Jurnal write attempts per minute."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method != "POST":
            return view(request, *args, **kwargs)

        cache_key = FOOD_LOG_WRITE_CACHE_KEY.format(user_id=request.user.pk)
        if cache.add(cache_key, 1, timeout=FOOD_LOG_WRITE_WINDOW_SECONDS):
            request_count = 1
        else:
            try:
                request_count = cache.incr(cache_key)
            except ValueError:
                # The key may expire between add() and incr(). Start a fresh window.
                cache.set(cache_key, 1, timeout=FOOD_LOG_WRITE_WINDOW_SECONDS)
                request_count = 1

        if request_count > FOOD_LOG_WRITE_LIMIT:
            message = "Terlalu banyak perubahan dalam waktu singkat. Coba lagi sebentar."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                response = JsonResponse({"ok": False, "message": message}, status=429)
            else:
                messages.error(request, message)
                response = redirect("jurnal:index")
            response["Retry-After"] = str(FOOD_LOG_WRITE_WINDOW_SECONDS)
            return response

        return view(request, *args, **kwargs)

    return wrapped