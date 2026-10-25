from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import services
from .forms import SaveRecipeForm, SavedRecipeNoteForm, SearchForm
from .models import SavedRecipe


ERROR_API = "TheMealDB sedang tidak bisa dihubungi. Periksa koneksi lalu coba lagi."


def _wants_json(request):
    """Bedakan submit biasa dengan submit Fetch API berdasarkan header Accept."""
    return "application/json" in request.headers.get("Accept", "")


def home(request):
    # Mendukung tautan dari Planner, misalnya /katalog/?ingredients=ayam%2C+nasi.
    search_form = SearchForm(request.GET)
    initial_query = ""
    initial_ingredients = ""
    if request.GET and search_form.is_valid():
        initial_query = search_form.cleaned_data["q"]
        initial_ingredients = search_form.cleaned_data["ingredients"]
    return render(
        request,
        "katalog/home.html",
        {
            "initial_query": initial_query,
            "initial_ingredients": initial_ingredients,
        },
    )


@require_GET
def api_search(request):
    """Endpoint JSON yang dipanggil oleh JavaScript Fetch API."""
    form = SearchForm(request.GET)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0]
        return JsonResponse({"error": str(first_error)}, status=400)

    query = form.cleaned_data["q"]
    ingredients = form.cleaned_data["ingredients"]
    if not query and not ingredients:
        return JsonResponse({"results": [], "count": 0})

    try:
        meals = services.search_meals(query=query, ingredients=ingredients)
    except services.MealDBError:
        return JsonResponse({"error": ERROR_API}, status=502)

    results = []
    for meal in meals:
        meal_id = meal.get("idMeal")
        name = meal.get("strMeal")
        if not isinstance(meal_id, (str, int)) or not str(meal_id).isdigit():
            continue
        if not isinstance(name, str) or not name.strip():
            continue

        item = {
            "idMeal": str(meal_id),
            "strMeal": name.strip(),
            "strMealThumb": meal.get("strMealThumb") if isinstance(meal.get("strMealThumb"), str) else "",
            "strCategory": meal.get("strCategory") if isinstance(meal.get("strCategory"), str) else "",
            "strArea": meal.get("strArea") if isinstance(meal.get("strArea"), str) else "",
        }
        matches = meal.get("matches")
        if isinstance(matches, int) and matches > 0:
            item["matches"] = matches
        results.append(item)

    return JsonResponse({"results": results, "count": len(results)})


def meal_detail(request, meal_id):
    try:
        meal = services.get_meal(meal_id)
    except services.MealDBError:
        return render(
            request,
            "katalog/meal_detail.html",
            {"meal": None, "error": ERROR_API},
            status=503,
        )

    if meal is None:
        raise Http404("Resep tidak ditemukan.")

    meal_id_from_api = str(meal.get("idMeal") or "")
    if meal_id_from_api != str(meal_id):
        raise Http404("Resep tidak ditemukan.")

    is_saved = request.user.is_authenticated and SavedRecipe.objects.filter(
        user=request.user, meal_id=meal_id_from_api
    ).exists()
    return render(
        request,
        "katalog/meal_detail.html",
        {
            "meal": meal,
            "ingredients": services.meal_ingredients(meal),
            "is_saved": is_saved,
        },
    )


@require_POST
def saved_create(request):
    """Verifikasi ID resep ke TheMealDB sebelum menyimpannya untuk user aktif."""
    if not request.user.is_authenticated:
        return JsonResponse(
            {
                "error": "Silakan login terlebih dahulu untuk menyimpan resep.",
                "login_url": reverse("login"),
            },
            status=401,
        )

    form = SaveRecipeForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "ID resep tidak valid."}, status=400)

    requested_meal_id = form.cleaned_data["meal_id"]
    try:
        meal = services.get_meal(requested_meal_id)
    except services.MealDBError:
        return JsonResponse({"error": ERROR_API}, status=502)

    if meal is None:
        return JsonResponse({"error": "Resep tidak ditemukan di TheMealDB."}, status=404)

    meal_id = str(meal.get("idMeal") or "")
    name = meal.get("strMeal")
    if meal_id != requested_meal_id or not isinstance(name, str) or not name.strip():
        return JsonResponse({"error": "Respons resep dari TheMealDB tidak valid."}, status=502)

    # Hanya URL gambar HTTPS dari TheMealDB yang disimpan.
    thumbnail = meal.get("strMealThumb")
    if not isinstance(thumbnail, str) or not thumbnail.startswith("https://www.themealdb.com/"):
        thumbnail = ""

    category = meal.get("strCategory") if isinstance(meal.get("strCategory"), str) else ""
    area = meal.get("strArea") if isinstance(meal.get("strArea"), str) else ""
    saved_recipe, created = SavedRecipe.objects.get_or_create(
        user=request.user,
        meal_id=meal_id,
        defaults={
            "name": name.strip()[:200],
            "thumbnail": thumbnail,
            "category": category[:100],
            "area": area[:100],
        },
    )
    return JsonResponse(
        {
            "message": "Resep berhasil disimpan." if created else "Resep ini sudah ada di daftar tersimpan.",
            "created": created,
            "saved_id": saved_recipe.pk,
            "saved_url": reverse("katalog:saved_list"),
        },
        status=201 if created else 200,
    )


@login_required(login_url="login")
def saved_list(request):
    recipes = SavedRecipe.objects.filter(user=request.user)
    return render(request, "katalog/saved_list.html", {"recipes": recipes})


@login_required(login_url="login")
def saved_update(request, pk):
    recipe = get_object_or_404(SavedRecipe, pk=pk, user=request.user)
    if request.method == "POST":
        form = SavedRecipeNoteForm(request.POST, instance=recipe)
        if form.is_valid():
            form.save()
            success_message = "Catatan resep berhasil diperbarui."
            if _wants_json(request):
                return JsonResponse({"message": success_message, "note": recipe.note})
            messages.success(request, success_message)
            return redirect("katalog:saved_list")

        if _wants_json(request):
            errors = form.errors.get("note") or ["Catatan tidak valid."]
            return JsonResponse({"error": str(errors[0])}, status=400)
    else:
        form = SavedRecipeNoteForm(instance=recipe)

    return render(request, "katalog/saved_edit.html", {"form": form, "recipe": recipe})


@login_required(login_url="login")
@require_POST
def saved_delete(request, pk):
    recipe = get_object_or_404(SavedRecipe, pk=pk, user=request.user)
    recipe.delete()
    success_message = "Resep dihapus dari daftar tersimpan."
    if _wants_json(request):
        return JsonResponse({"message": success_message, "deleted_id": pk})
    messages.success(request, success_message)
    return redirect("katalog:saved_list")
