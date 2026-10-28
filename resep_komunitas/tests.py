from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from .models import CommunityRecipe


User = get_user_model()


class CommunityRecipeTest(TestCase):
    def setUp(self):
        # Membuat dua user untuk menguji kepemilikan resep.
        self.user = User.objects.create_user(
            username="lalita",
            password="testpassword123",
        )

        self.other_user = User.objects.create_user(
            username="otheruser",
            password="testpassword123",
        )

        # Membuat resep milik user pertama.
        self.recipe = CommunityRecipe.objects.create(
            author=self.user,
            title="Nasi Goreng Telur",
            main_ingred="Telur",
            image_url="",
            description="Nasi goreng sederhana dengan telur.",
            ingredients="1 piring nasi\n1 butir telur",
            steps="1. Goreng telur\n2. Masukkan nasi",
        )

    # --- MODEL --- #

    def test_recipe_model(self):
        self.assertEqual(str(self.recipe), "Nasi Goreng Telur")
        self.assertEqual(self.recipe.author, self.user)
        self.assertEqual(self.recipe.main_ingred, "Telur")

    # --- RECIPE LIST --- #

    def test_recipe_list_page(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_list.html")
        self.assertContains(response, self.recipe.title)
        self.assertContains(response, self.recipe.description)
        self.assertContains(response, self.user.username)

    def test_empty_recipe_list(self):
        CommunityRecipe.objects.all().delete()

        response = self.client.get(
            reverse("resep_komunitas:recipe_list")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Belum ada resep yang ditemukan",
        )

    def test_recipe_search_by_title(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"q": "Nasi Goreng"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nasi Goreng Telur")

    def test_recipe_search_by_main_ingredient(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"q": "Telur"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nasi Goreng Telur")

    def test_recipe_search_no_result(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"q": "Cokelat"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Belum ada resep yang ditemukan",
        )

    # --- HTMX --- #

    def test_recipe_list_htmx_request(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_cards.html")
        self.assertContains(response, self.recipe.title)

    def test_recipe_list_htmx_search(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"q": "Nasi Goreng"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_cards.html")
        self.assertContains(response, "Nasi Goreng Telur")

    # --- FILTER RESEP MILIK USER --- #

    def test_filter_my_recipes(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"filter": "mine"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.recipe.title)

    def test_filter_my_recipes_anonymous(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"filter": "mine"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.recipe.title)
        self.assertContains(
            response,
            "Belum ada resep yang ditemukan",
        )

    def test_filter_my_recipes_excludes_other_users(self):
        other_recipe = CommunityRecipe.objects.create(
            author=self.other_user,
            title="Puding Cokelat",
            main_ingred="Cokelat",
            description="Puding cokelat sederhana.",
            ingredients="Susu\nCokelat",
            steps="1. Campur bahan\n2. Masak",
        )

        self.client.force_login(self.user)

        response = self.client.get(
            reverse("resep_komunitas:recipe_list"),
            {"filter": "mine"},
        )

        self.assertContains(response, self.recipe.title)
        self.assertNotContains(response, other_recipe.title)

    # --- RECIPE DETAIL --- #

    def test_recipe_detail_page(self):
        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_detail",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_detail.html")
        self.assertContains(response, self.recipe.title)
        self.assertContains(response, self.recipe.description)
        self.assertContains(response, self.recipe.ingredients)
        self.assertContains(response, self.recipe.steps)
        self.assertContains(response, self.user.username)

    def test_nonexistent_recipe_detail(self):
        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_detail",
                args=[99999],
            )
        )

        self.assertEqual(response.status_code, 404)

    # --- CREATE RECIPE --- #

    def test_recipe_create_requires_login(self):
        response = self.client.get(
            reverse("resep_komunitas:recipe_create")
        )

        self.assertEqual(response.status_code, 302)

    def test_recipe_create_page(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("resep_komunitas:recipe_create")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_form.html")
        self.assertContains(response, "Bagikan Resep")

    def test_recipe_create_success(self):
        self.client.force_login(self.user)

        data = {
            "title": "Omelet Sayur",
            "main_ingred": "Telur",
            "image_url": "",
            "description": "Omelet sederhana dengan sayuran.",
            "ingredients": "2 butir telur\nWortel",
            "steps": "1. Campur bahan\n2. Masak hingga matang",
        }

        response = self.client.post(
            reverse("resep_komunitas:recipe_create"),
            data,
        )

        self.assertEqual(response.status_code, 302)

        recipe = CommunityRecipe.objects.get(
            title="Omelet Sayur"
        )

        self.assertEqual(recipe.author, self.user)
        self.assertEqual(recipe.main_ingred, "Telur")

        self.assertRedirects(
            response,
            reverse(
                "resep_komunitas:recipe_detail",
                args=[recipe.pk],
            ),
        )

    def test_recipe_create_invalid_data(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("resep_komunitas:recipe_create"),
            {
                "title": "",
                "main_ingred": "Telur",
                "image_url": "",
                "description": "Deskripsi resep",
                "ingredients": "Telur",
                "steps": "Masak",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_form.html")
        self.assertEqual(CommunityRecipe.objects.count(), 1)

    # --- UPDATE RECIPE --- #

    def test_recipe_update_requires_login(self):
        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_update",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 302)

    def test_recipe_update_page(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_update",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "recipe_form.html")
        self.assertContains(response, "Edit Resep")
        self.assertContains(response, self.recipe.title)

    def test_recipe_update_success(self):
        self.client.force_login(self.user)

        data = {
            "title": "Nasi Goreng Spesial",
            "main_ingred": "Telur dan nasi",
            "image_url": "",
            "description": "Nasi goreng dengan telur spesial.",
            "ingredients": "Nasi\nTelur\nKecap",
            "steps": "1. Goreng telur\n2. Masukkan nasi",
        }

        response = self.client.post(
            reverse(
                "resep_komunitas:recipe_update",
                args=[self.recipe.pk],
            ),
            data,
        )

        self.assertEqual(response.status_code, 302)

        self.recipe.refresh_from_db()

        self.assertEqual(
            self.recipe.title,
            "Nasi Goreng Spesial",
        )

        self.assertRedirects(
            response,
            reverse(
                "resep_komunitas:recipe_detail",
                args=[self.recipe.pk],
            ),
        )

    def test_other_user_cannot_update_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_update",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 404)

    # --- DELETE RECIPE --- #

    def test_recipe_delete_requires_login(self):
        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_delete",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 302)

    def test_recipe_delete_confirmation_page(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "resep_komunitas:recipe_delete",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "recipe_confirm_delete.html",
        )
        self.assertContains(response, self.recipe.title)

    def test_recipe_delete_success(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "resep_komunitas:recipe_delete",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityRecipe.objects.filter(
                pk=self.recipe.pk
            ).exists()
        )

        self.assertRedirects(
            response,
            reverse("resep_komunitas:recipe_list"),
        )

    def test_other_user_cannot_delete_recipe(self):
        self.client.force_login(self.other_user)

        response = self.client.post(
            reverse(
                "resep_komunitas:recipe_delete",
                args=[self.recipe.pk],
            )
        )

        self.assertEqual(response.status_code, 404)

        self.assertTrue(
            CommunityRecipe.objects.filter(
                pk=self.recipe.pk
            ).exists()
        )

    # --- JSON --- #

    def test_get_recipes_json(self):
        response = self.client.get(
            reverse("resep_komunitas:get_recipes_json")
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(len(data), 1)
        self.assertEqual(
            data[0]["pk"],
            str(self.recipe.pk),
        )
        self.assertEqual(
            data[0]["fields"]["title"],
            self.recipe.title,
        )
        self.assertEqual(
            data[0]["fields"]["main_ingred"],
            self.recipe.main_ingred,
        )
        self.assertEqual(
            data[0]["fields"]["author"],
            self.user.username,
        )

    def test_get_recipes_json_search(self):
        response = self.client.get(
            reverse("resep_komunitas:get_recipes_json"),
            {"q": "Telur"},
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(len(data), 1)
        self.assertEqual(
            data[0]["fields"]["title"],
            "Nasi Goreng Telur",
        )

    def test_get_recipes_json_no_result(self):
        response = self.client.get(
            reverse("resep_komunitas:get_recipes_json"),
            {"q": "Cokelat"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])