from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import services
from .forms import SaveRecipeForm, SavedRecipeNoteForm, SearchForm
from .models import SavedRecipe


ERROR_API = "TheMealDB sedang tidak bisa dihubungi. Periksa koneksi lalu coba lagi."


def _search_context(request):
    """Siapkan hasil pencarian untuk halaman biasa maupun permintaan Fetch."""
    search_form = SearchForm(request.GET or None)
    initial_query = request.GET.get("q", "")
    initial_ingredients = request.GET.get("ingredients", "")
    meals = []
    search_error = ""
    search_performed = "q" in request.GET or "ingredients" in request.GET
    has_search = bool(initial_query.strip() or initial_ingredients.strip())

    if search_performed and search_form.is_valid():
        initial_query = search_form.cleaned_data["q"]
        initial_ingredients = search_form.cleaned_data["ingredients"]
        has_search = bool(initial_query or initial_ingredients)

        if has_search:
            try:
                found_meals = services.search_meals(
                    query=initial_query,
                    ingredients=initial_ingredients,
                )
                for found_meal in found_meals:
                    meal = found_meal.copy()
                    meal_id = str(meal.get("idMeal") or "")
                    name = meal.get("strMeal")
                    if not meal_id.isdigit() or not isinstance(name, str) or not name.strip():
                        continue

                    meal["idMeal"] = meal_id
                    meal["strMeal"] = name.strip()
                    thumbnail = meal.get("strMealThumb")
                    if not isinstance(thumbnail, str) or not thumbnail.startswith("https://www.themealdb.com/"):
                        meal["strMealThumb"] = ""
                    meals.append(meal)
            except services.MealDBError:
                search_error = ERROR_API
    elif search_performed:
        search_error = next(iter(search_form.errors.values()))[0]

    return {
        "initial_query": initial_query,
        "initial_ingredients": initial_ingredients,
        "meals": meals,
        "search_error": search_error,
        "has_search": has_search,
    }


def home(request):
    """Tampilkan katalog; form GET tetap berfungsi saat JavaScript tidak aktif."""
    return render(request, "katalog/home.html", _search_context(request))


@require_GET
def api_search(request):
    """Kirim potongan HTML hasil pencarian untuk ditampilkan oleh Fetch API."""
    return render(
        request,
        "katalog/partials/recipe_results.html",
        _search_context(request),
    )


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
    """Simpan resep yang dikirim dari halaman detail."""
    if not request.user.is_authenticated:
        meal_id = request.POST.get("meal_id", "")
        if meal_id.isdigit():
            next_url = reverse("katalog:meal_detail", args=[meal_id])
            return redirect(f"{reverse('login')}?next={next_url}")
        return redirect("login")

    form = SaveRecipeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "ID resep tidak valid.")
        return redirect("katalog:home")

    requested_meal_id = form.cleaned_data["meal_id"]
    if not requested_meal_id.isdigit():
        messages.error(request, "ID resep tidak valid.")
        return redirect("katalog:home")

    try:
        meal = services.get_meal(requested_meal_id)
    except services.MealDBError:
        messages.error(request, ERROR_API)
        return redirect("katalog:home")

    if meal is None:
        messages.error(request, "Resep tidak ditemukan di TheMealDB.")
        return redirect("katalog:home")

    meal_id = str(meal.get("idMeal") or "")
    name = meal.get("strMeal")
    if meal_id != requested_meal_id or not isinstance(name, str) or not name.strip():
        messages.error(request, "Data resep dari TheMealDB tidak valid.")
        return redirect("katalog:home")

    thumbnail = meal.get("strMealThumb")
    if not isinstance(thumbnail, str) or not thumbnail.startswith("https://www.themealdb.com/"):
        thumbnail = ""

    category = meal.get("strCategory") if isinstance(meal.get("strCategory"), str) else ""
    area = meal.get("strArea") if isinstance(meal.get("strArea"), str) else ""
    _saved_recipe, created = SavedRecipe.objects.get_or_create(
        user=request.user,
        meal_id=meal_id,
        defaults={
            "name": name.strip()[:200],
            "thumbnail": thumbnail,
            "category": category[:100],
            "area": area[:100],
        },
    )

    if created:
        messages.success(request, "Resep berhasil disimpan.")
    else:
        messages.info(request, "Resep ini sudah ada di daftar tersimpan.")
    return redirect("katalog:saved_list")


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
            messages.success(request, "Catatan resep berhasil diperbarui.")
            return redirect("katalog:saved_list")
    else:
        form = SavedRecipeNoteForm(instance=recipe)

    return render(request, "katalog/saved_edit.html", {"form": form, "recipe": recipe})


@require_POST
def saved_delete(request, pk):
    if not request.user.is_authenticated:
        next_url = reverse("katalog:saved_list")
        return redirect(f"{reverse('login')}?next={next_url}")

    recipe = get_object_or_404(SavedRecipe, pk=pk, user=request.user)
    recipe.delete()
    messages.success(request, "Resep dihapus dari daftar tersimpan.")
    return redirect("katalog:saved_list")
