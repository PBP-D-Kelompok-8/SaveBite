"""Fungsi kecil untuk mengambil data dari TheMealDB."""

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://www.themealdb.com/api/json/v1/1/"
REQUEST_TIMEOUT = 5
MAX_RESULTS = 24


class MealDBError(Exception):
    """Terjadi masalah saat menghubungi atau membaca respons TheMealDB."""


# Alias umum agar beberapa bahan yang ditulis dalam bahasa Indonesia tetap berguna.
INGREDIENT_ALIASES = {
    "ayam": "chicken",
    "daging ayam": "chicken",
    "telur": "egg",
    "nasi": "rice",
    "beras": "rice",
    "bawang": "onion",
    "bawang merah": "shallot",
    "bawang putih": "garlic",
    "tomat": "tomato",
    "kentang": "potato",
    "wortel": "carrot",
    "susu": "milk",
    "keju": "cheese",
    "tahu": "tofu",
    "udang": "prawn",
    "ikan": "fish",
    "daging sapi": "beef",
    "pisang": "banana",
    "apel": "apple",
    "roti": "bread",
    "jamur": "mushroom",
    "cabai": "chilli",
    "cabe": "chilli",
    "mie": "noodles",
}


def _get_json(endpoint, params=None):
    """Kirim GET ke TheMealDB dan pastikan respons berbentuk JSON."""
    query = urlencode(params or {})
    url = f"{API_BASE_URL}{endpoint}"
    if query:
        url = f"{url}?{query}"

    request = Request(url, headers={"User-Agent": "SaveBite-Katalog/1.0"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
    except (HTTPError, URLError, HTTPException, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MealDBError("TheMealDB tidak dapat dihubungi atau mengirim respons tidak valid.") from exc

    if not isinstance(data, dict):
        raise MealDBError("Respons TheMealDB tidak sesuai format yang diharapkan.")
    return data


def _meal_list(data):
    """Kembalikan daftar resep; perlakukan respons API yang rusak sebagai error terkontrol."""
    meals = data.get("meals")
    if meals is None:
        return []
    if not isinstance(meals, list) or any(not isinstance(meal, dict) for meal in meals):
        raise MealDBError("Data resep dari TheMealDB tidak sesuai format yang diharapkan.")
    return meals


def _ingredient_terms(value):
    terms = []
    for part in value.split(","):
        term = " ".join(part.lower().split())
        if term:
            translated = INGREDIENT_ALIASES.get(term, term).replace(" ", "_")
            if translated not in terms:
                terms.append(translated)
    return terms[:3]


def _search_ingredients(ingredients):
    """Gabungkan hasil filter bahan dan hitung berapa input bahan yang cocok."""
    ingredient_terms = _ingredient_terms(ingredients)
    meals_by_id = {}
    match_counts = {}

    for ingredient in ingredient_terms:
        data = _get_json("filter.php", {"i": ingredient})
        seen_for_ingredient = set()
        for meal in _meal_list(data):
            meal_id = str(meal.get("idMeal") or "")
            if not meal_id or meal_id in seen_for_ingredient:
                continue
            seen_for_ingredient.add(meal_id)
            meals_by_id.setdefault(meal_id, meal)
            match_counts[meal_id] = match_counts.get(meal_id, 0) + 1

    return meals_by_id, match_counts


def search_meals(query="", ingredients=""):
    """Cari berdasarkan nama dan/atau bahan, lalu prioritaskan kecocokan bahan terbanyak."""
    query = (query or "").strip()
    ingredients = (ingredients or "").strip()

    if not query and not ingredients:
        return []

    named_meals = None
    if query:
        data = _get_json("search.php", {"s": query})
        named_meals = _meal_list(data)
        if not named_meals:
            return []

    if ingredients:
        ingredient_meals, match_counts = _search_ingredients(ingredients)

        if named_meals is not None:
            # Bila nama resep dan bahan diisi bersama, hasil harus cocok dengan keduanya.
            results = [
                {**meal, "matches": match_counts[str(meal.get("idMeal") or "")]}
                for meal in named_meals
                if str(meal.get("idMeal") or "") in match_counts
            ]
        else:
            results = [
                {**meal, "matches": match_counts[meal_id]}
                for meal_id, meal in ingredient_meals.items()
            ]

        # sorted bersifat stabil, sehingga urutan asal tetap dipakai untuk nilai yang sama.
        results.sort(key=lambda meal: -meal["matches"])
        return results[:MAX_RESULTS]

    return named_meals[:MAX_RESULTS]


def get_meal(meal_id):
    """Ambil detail resep berdasarkan ID; ID divalidasi sebelum request."""
    if not re.fullmatch(r"[0-9]{1,10}", str(meal_id or "")):
        return None
    data = _get_json("lookup.php", {"i": str(meal_id)})
    meals = _meal_list(data)
    return meals[0] if meals else None


def meal_ingredients(meal):
    """Ubah kolom ingredient/measure TheMealDB menjadi daftar yang mudah ditampilkan."""
    ingredients = []
    for number in range(1, 21):
        ingredient_value = meal.get(f"strIngredient{number}")
        measure_value = meal.get(f"strMeasure{number}")
        ingredient = ingredient_value.strip() if isinstance(ingredient_value, str) else ""
        measure = measure_value.strip() if isinstance(measure_value, str) else ""
        if ingredient:
            ingredients.append({"name": ingredient, "measure": measure})
    return ingredients
