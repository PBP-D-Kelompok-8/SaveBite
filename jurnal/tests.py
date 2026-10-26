from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import FoodLog
from .services import sync_log_from_rescue_plan


class SyncLogFromRescuePlanTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="test-password",
        )

    def make_plan(self, **changes):
        values = {
            "pk": 123,
            "user": self.user,
            "nama_bahan": "Bayam",
            "jumlah": 2,
            "satuan": "kg",
            "status": "success",
            "metode_aksi": "dimasak",
        }
        values.update(changes)
        return SimpleNamespace(**values)

    def test_success_plan_creates_saved_food_log(self):
        log = sync_log_from_rescue_plan(self.make_plan())

        self.assertEqual(log.food_name, "Bayam")
        self.assertEqual(log.status, "terselamatkan")
        self.assertEqual(log.quantity, 2)
        self.assertEqual(log.rescue_plan_id, 123)

    def test_syncing_same_plan_twice_does_not_create_duplicate(self):
        plan = self.make_plan()

        sync_log_from_rescue_plan(plan)
        sync_log_from_rescue_plan(plan)

        self.assertEqual(FoodLog.objects.count(), 1)

    def test_non_final_plan_deletes_existing_log(self):
        sync_log_from_rescue_plan(self.make_plan())

        result = sync_log_from_rescue_plan(self.make_plan(status="ongoing"))

        self.assertIsNone(result)
        self.assertFalse(FoodLog.objects.filter(rescue_plan_id=123).exists())

    def test_unsaved_plan_does_nothing(self):
        result = sync_log_from_rescue_plan(self.make_plan(pk=None))

        self.assertIsNone(result)
        self.assertEqual(FoodLog.objects.count(), 0)


class FoodLogCreateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="journal-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.create_url = reverse("jurnal:create")
        self.form_data = {
            "food_name": "Nasi sisa",
            "quantity": "2",
            "unit": "pcs",
            "status": "terselamatkan",
            "notes": "Dibuat nasi goreng",
        }

    def test_valid_post_creates_food_log_for_current_user(self):
        response = self.client.post(self.create_url, self.form_data)

        self.assertRedirects(response, reverse("jurnal:index"))
        log = FoodLog.objects.get(food_name="Nasi sisa")
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.quantity, 2)
        self.assertEqual(log.unit, "pcs")
        self.assertEqual(log.status, "terselamatkan")
        self.assertEqual(log.notes, "Dibuat nasi goreng")
        self.assertIsNone(log.rescue_plan_id)

    def test_invalid_quantity_does_not_create_food_log(self):
        response = self.client.post(
            self.create_url,
            {**self.form_data, "quantity": "0"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["form"].errors["quantity"])
        self.assertEqual(FoodLog.objects.count(), 0)

    def test_anonymous_user_cannot_create_food_log(self):
        self.client.logout()

        response = self.client.post(self.create_url, self.form_data)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
        self.assertEqual(FoodLog.objects.count(), 0)


class FoodLogUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="journal-owner",
            password="test-password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="another-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.food_log = FoodLog.objects.create(
            user=self.user,
            food_name="Bayam",
            quantity=1,
            unit="pcs",
            status="terselamatkan",
            notes="Catatan awal",
        )
        self.update_url = reverse("jurnal:update", args=[self.food_log.pk])

    def test_get_edit_page_shows_existing_food_log(self):
        response = self.client.get(self.update_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].instance, self.food_log)
        self.assertContains(response, "Bayam")
        self.assertContains(response, "Catatan awal")

    def test_valid_post_updates_food_log(self):
        response = self.client.post(
            self.update_url,
            {
                "food_name": "Bayam tumis",
                "quantity": "3",
                "unit": "gr",
                "status": "terbuang",
                "notes": "Catatan diperbarui",
            },
        )

        self.assertRedirects(response, reverse("jurnal:index"))
        self.food_log.refresh_from_db()
        self.assertEqual(self.food_log.food_name, "Bayam tumis")
        self.assertEqual(self.food_log.quantity, 3)
        self.assertEqual(self.food_log.unit, "gr")
        self.assertEqual(self.food_log.status, "terbuang")
        self.assertEqual(self.food_log.notes, "Catatan diperbarui")

    def test_invalid_post_keeps_original_food_log(self):
        response = self.client.post(
            self.update_url,
            {
                "food_name": "Bayam tumis",
                "quantity": "0",
                "unit": "gr",
                "status": "terbuang",
                "notes": "Perubahan tidak valid",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["form"].errors["quantity"])
        self.food_log.refresh_from_db()
        self.assertEqual(self.food_log.food_name, "Bayam")
        self.assertEqual(self.food_log.quantity, 1)
        self.assertEqual(self.food_log.status, "terselamatkan")

    def test_user_cannot_update_another_users_food_log(self):
        other_log = FoodLog.objects.create(
            user=self.other_user,
            food_name="Milik pengguna lain",
            quantity=1,
            unit="pcs",
            status="terselamatkan",
        )
        other_update_url = reverse("jurnal:update", args=[other_log.pk])

        response = self.client.post(
            other_update_url,
            {
                "food_name": "Diubah tanpa izin",
                "quantity": "2",
                "unit": "pcs",
                "status": "terbuang",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 404)
        other_log.refresh_from_db()
        self.assertEqual(other_log.food_name, "Milik pengguna lain")


class FoodLogDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="delete-owner",
            password="test-password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="delete-other",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.food_log = FoodLog.objects.create(
            user=self.user,
            food_name="Sup sayur",
            quantity=1,
            unit="porsi",
            status="terbuang",
            notes="Sudah basi",
        )
        self.delete_url = reverse("jurnal:delete", args=[self.food_log.pk])

    def test_get_shows_confirmation_without_deleting(self):
        response = self.client.get(self.delete_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sup sayur")
        self.assertContains(response, "Ya, Hapus Catatan")
        self.assertTrue(FoodLog.objects.filter(pk=self.food_log.pk).exists())

    def test_post_deletes_food_log_and_redirects(self):
        response = self.client.post(self.delete_url)

        self.assertRedirects(response, reverse("jurnal:index"))
        self.assertFalse(FoodLog.objects.filter(pk=self.food_log.pk).exists())

    def test_user_cannot_delete_another_users_food_log(self):
        other_log = FoodLog.objects.create(
            user=self.other_user,
            food_name="Makanan pribadi orang lain",
            quantity=1,
            unit="pcs",
            status="terselamatkan",
        )
        other_delete_url = reverse("jurnal:delete", args=[other_log.pk])

        response = self.client.post(other_delete_url)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(FoodLog.objects.filter(pk=other_log.pk).exists())

