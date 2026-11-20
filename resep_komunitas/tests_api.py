from django.contrib.auth import get_user_model
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APITestCase

from .models import CommunityRecipe


User = get_user_model()


class CommunityRecipeAPITest(APITestCase):

    def setUp(self):
        self.user1 = User.objects.create_user(
            username="user1",
            password="password123",
        )

        self.user2 = User.objects.create_user(
            username="user2",
            password="password123",
        )

        self.recipe = CommunityRecipe.objects.create(
            author=self.user1,
            title="Nasi Goreng",
            main_ingred="Nasi",
            image_url="",
            description="Nasi goreng sederhana",
            ingredients="Nasi\nTelur\nKecap",
            steps="1. Siapkan nasi\n2. Goreng nasi",
        )

        self.list_url = reverse(
            "resep_komunitas_api:recipe-list"
        )

        self.detail_url = reverse(
            "resep_komunitas_api:recipe-detail",
            args=[self.recipe.pk],
        )

    def test_get_recipe_list(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_search_recipe(self):
        response = self.client.get(
            self.list_url,
            {"q": "Nasi"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_create_recipe_requires_login(self):
        response = self.client.post(
            self.list_url,
            {
                "title": "Mie Goreng",
                "main_ingred": "Mie",
                "image_url": "",
                "description": "Mie goreng sederhana",
                "ingredients": "Mie\nTelur",
                "steps": "1. Rebus mie",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_create_recipe_success(self):
        self.client.force_authenticate(user=self.user1)

        response = self.client.post(
            self.list_url,
            {
                "title": "Mie Goreng",
                "main_ingred": "Mie",
                "image_url": "",
                "description": "Mie goreng sederhana",
                "ingredients": "Mie\nTelur",
                "steps": "1. Rebus mie",
            },
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        self.assertEqual(
            CommunityRecipe.objects.count(),
            2,
        )

        self.assertEqual(
            CommunityRecipe.objects.latest("id").author,
            self.user1,
        )

    def test_get_recipe_detail(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Nasi Goreng")

    def test_owner_can_update_recipe(self):
        self.client.force_authenticate(user=self.user1)

        response = self.client.patch(
            self.detail_url,
            {"title": "Nasi Goreng Spesial"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.recipe.refresh_from_db()

        self.assertEqual(
            self.recipe.title,
            "Nasi Goreng Spesial",
        )

    def test_other_user_cannot_update_recipe(self):
        self.client.force_authenticate(user=self.user2)

        response = self.client.patch(
            self.detail_url,
            {"title": "Resep Diubah"},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_owner_can_delete_recipe(self):
        self.client.force_authenticate(user=self.user1)

        response = self.client.delete(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

        self.assertFalse(
            CommunityRecipe.objects.filter(pk=self.recipe.pk).exists()
        )

    def test_other_user_cannot_delete_recipe(self):
        self.client.force_authenticate(user=self.user2)

        response = self.client.delete(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )