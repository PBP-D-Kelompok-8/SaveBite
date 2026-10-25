from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import SavedRecipe
from .services import MealDBError


User = get_user_model()

SAMPLE_MEAL = {
    "idMeal": "52772",
    "strMeal": "Teriyaki Chicken Casserole",
    "strMealThumb": "https://www.themealdb.com/images/media/meals/wvpscp1511551460.jpg",
    "strCategory": "Chicken",
    "strArea": "Japanese",
    "strInstructions": "Mix the ingredients and cook.",
    "strIngredient1": "Chicken",
    "strMeasure1": "500g",
    "strIngredient2": "Soy Sauce",
    "strMeasure2": "2 tbsp",
}


class KatalogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="katalog-user", password="test-pass-123")
        self.other_user = User.objects.create_user(username="other-user", password="test-pass-123")

    def test_home_page_opens(self):
        response = self.client.get(reverse("katalog:home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "katalog/home.html")

    def test_home_page_can_receive_ingredients_from_url(self):
        response = self.client.get(reverse("katalog:home"), {"ingredients": "ayam, nasi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["initial_ingredients"], "ayam, nasi")

    @patch("katalog.views.services.search_meals")
    def test_search_returns_recipe_data(self, mock_search):
        mock_search.return_value = [SAMPLE_MEAL]
        response = self.client.get(reverse("katalog:api_search"), {"q": "teriyaki"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["strMeal"], "Teriyaki Chicken Casserole")

    def test_search_rejects_more_than_three_ingredients(self):
        response = self.client.get(
            reverse("katalog:api_search"),
            {"ingredients": "ayam, nasi, telur, tomat"},
        )
        self.assertEqual(response.status_code, 400)

    @patch("katalog.views.services.search_meals", side_effect=MealDBError("offline"))
    def test_search_shows_error_when_recipe_service_fails(self, _mock_search):
        response = self.client.get(reverse("katalog:api_search"), {"q": "soup"})
        self.assertEqual(response.status_code, 502)

    @patch("katalog.views.services.get_meal", return_value=SAMPLE_MEAL)
    def test_recipe_detail_opens(self, _mock_get_meal):
        response = self.client.get(reverse("katalog:meal_detail", args=[52772]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "katalog/meal_detail.html")
        self.assertEqual(response.context["ingredients"][0]["name"], "Chicken")

    @patch("katalog.views.services.get_meal", return_value=None)
    def test_unknown_recipe_returns_404(self, _mock_get_meal):
        response = self.client.get(reverse("katalog:meal_detail", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_guest_cannot_save_recipe(self):
        response = self.client.post(reverse("katalog:saved_create"), {"meal_id": "52772"})
        self.assertEqual(response.status_code, 401)

    @patch("katalog.views.services.get_meal", return_value=SAMPLE_MEAL)
    def test_logged_in_user_can_save_recipe(self, _mock_get_meal):
        self.client.force_login(self.user)
        response = self.client.post(reverse("katalog:saved_create"), {"meal_id": "52772"})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(SavedRecipe.objects.filter(user=self.user, meal_id="52772").exists())

    @patch("katalog.views.services.get_meal", return_value=SAMPLE_MEAL)
    def test_same_recipe_is_not_saved_twice_for_one_user(self, _mock_get_meal):
        self.client.force_login(self.user)
        url = reverse("katalog:saved_create")
        self.client.post(url, {"meal_id": "52772"})
        self.client.post(url, {"meal_id": "52772"})
        self.assertEqual(SavedRecipe.objects.filter(user=self.user, meal_id="52772").count(), 1)

    def test_saved_list_requires_login(self):
        response = self.client.get(reverse("katalog:saved_list"))
        self.assertEqual(response.status_code, 302)

    def test_saved_list_only_shows_current_users_recipes(self):
        SavedRecipe.objects.create(user=self.user, meal_id="52772", name="My recipe")
        SavedRecipe.objects.create(user=self.other_user, meal_id="53000", name="Other recipe")
        self.client.force_login(self.user)
        response = self.client.get(reverse("katalog:saved_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My recipe")
        self.assertNotContains(response, "Other recipe")

    def test_owner_can_open_edit_page(self):
        recipe = SavedRecipe.objects.create(user=self.user, meal_id="52772", name="My recipe")
        self.client.force_login(self.user)
        response = self.client.get(reverse("katalog:saved_update", args=[recipe.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "katalog/saved_edit.html")

    def test_user_cannot_edit_another_users_recipe(self):
        recipe = SavedRecipe.objects.create(user=self.other_user, meal_id="52772", name="Private recipe")
        self.client.force_login(self.user)
        response = self.client.get(reverse("katalog:saved_update", args=[recipe.pk]))
        self.assertEqual(response.status_code, 404)

    def test_owner_can_update_recipe_note(self):
        recipe = SavedRecipe.objects.create(user=self.user, meal_id="52772", name="My recipe")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("katalog:saved_update", args=[recipe.pk]),
            {"note": "Kurangi garam."},
        )
        self.assertEqual(response.status_code, 302)
        recipe.refresh_from_db()
        self.assertEqual(recipe.note, "Kurangi garam.")

    def test_too_long_note_is_rejected(self):
        recipe = SavedRecipe.objects.create(user=self.user, meal_id="52772", name="My recipe")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("katalog:saved_update", args=[recipe.pk]),
            {"note": "x" * 501},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_user_cannot_delete_another_users_recipe(self):
        recipe = SavedRecipe.objects.create(user=self.other_user, meal_id="52772", name="Private recipe")
        self.client.force_login(self.user)
        response = self.client.post(reverse("katalog:saved_delete", args=[recipe.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SavedRecipe.objects.filter(pk=recipe.pk).exists())

    def test_owner_can_delete_recipe(self):
        recipe = SavedRecipe.objects.create(user=self.user, meal_id="52772", name="My recipe")
        self.client.force_login(self.user)
        response = self.client.post(reverse("katalog:saved_delete", args=[recipe.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SavedRecipe.objects.filter(pk=recipe.pk).exists())
