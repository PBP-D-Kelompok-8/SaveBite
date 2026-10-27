from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import CommunityRecipe


User = get_user_model()


class CommunityRecipeTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(
            username="author",
            password="test-password-123",
        )
        self.other_user = User.objects.create_user(
            username="other",
            password="test-password-123",
        )
        self.recipe = CommunityRecipe.objects.create(
            author=self.author,
            title="Nasi Goreng Sisa",
            main_ingred="Nasi",
            image_url="",
            description="Memanfaatkan nasi sisa.",
            ingredients="Nasi\nTelur",
            steps="Masak semua bahan.",
        )

    def test_feed_is_public(self):
        response = self.client.get(
            reverse("community_recipes:recipe_list")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nasi Goreng Sisa")

    def test_search_by_main_ingredient(self):
        response = self.client.get(
            reverse("community_recipes:recipe_list"),
            {"q": "nasi"},
        )
        self.assertContains(response, "Nasi Goreng Sisa")

    def test_create_requires_login(self):
        response = self.client.get(
            reverse("community_recipes:recipe_create")
        )
        self.assertEqual(response.status_code, 302)

    def test_author_can_edit_recipe(self):
        self.client.force_login(self.author)

        response = self.client.post(
            reverse(
                "community_recipes:recipe_update",
                args=[self.recipe.pk],
            ),
            {
                "title": "Nasi Goreng Telur",
                "main_ingred": "Nasi",
                "image_url": "",
                "description": "Resep yang diperbarui.",
                "ingredients": "Nasi\nTelur",
                "steps": "Masak sampai matang.",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "community_recipes:recipe_detail",
                args=[self.recipe.pk],
            ),
        )
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.title, "Nasi Goreng Telur")

    def test_other_user_cannot_edit_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse(
                "community_recipes:recipe_update",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_author_can_delete_recipe(self):
        self.client.force_login(self.author)

        response = self.client.post(
            reverse(
                "community_recipes:recipe_delete",
                args=[self.recipe.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse("community_recipes:recipe_list"),
        )
        self.assertFalse(
            CommunityRecipe.objects.filter(pk=self.recipe.pk).exists()
        )

    def test_star_toggle(self):
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse(
                "community_recipes:recipe_toggle_star",
                args=[self.recipe.pk],
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["star_count"], 1)

        response = self.client.post(
            reverse(
                "community_recipes:recipe_toggle_star",
                args=[self.recipe.pk],
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["star_count"], 0)
