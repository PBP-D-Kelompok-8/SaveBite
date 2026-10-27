from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .decorators import FOOD_LOG_WRITE_CACHE_KEY
from .forms import FoodLogForm
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


    def test_ajax_update_returns_updated_card_and_summary(self):
        response = self.client.post(
            self.update_url,
            {
                "food_name": "Bayam tumis",
                "quantity": "3",
                "unit": "gr",
                "status": "terbuang",
                "notes": "Sudah dimasak",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("Bayam tumis", payload["list_html"])
        self.assertIn("summary_html", payload)
        self.assertIn("0 terselamatkan · 1 terbuang · 1 total", payload["summary_html"])
        self.food_log.refresh_from_db()
        self.assertEqual(self.food_log.status, "terbuang")

    def test_ajax_update_rejects_invalid_data_and_preserves_record(self):
        response = self.client.post(
            self.update_url,
            {
                "food_name": "Bayam tumis",
                "quantity": "0",
                "unit": "gr",
                "status": "terbuang",
                "notes": "Invalid",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("quantity", response.json()["form_html"])
        self.food_log.refresh_from_db()
        self.assertEqual(self.food_log.food_name, "Bayam")
        self.assertEqual(self.food_log.quantity, 1)

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


class FoodLogFilteringTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="filter-user",
            password="test-password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="filter-other",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.saved_log = FoodLog.objects.create(
            user=self.user,
            food_name="Nasi sisa",
            quantity=2,
            unit="pcs",
            status="terselamatkan",
            notes="Dibuat untuk makan malam",
        )
        self.wasted_log = FoodLog.objects.create(
            user=self.user,
            food_name="Sup basi",
            quantity=1,
            unit="porsi",
            status="terbuang",
            notes="Ditemukan di kulkas",
        )
        FoodLog.objects.create(
            user=self.other_user,
            food_name="Nasi rahasia",
            quantity=1,
            unit="pcs",
            status="terselamatkan",
        )

    def test_combines_search_status_and_inclusive_date_range(self):
        response = self.client.get(
            reverse("jurnal:index"),
            {
                "query": "nasi",
                "status": "terselamatkan",
                "date_from": self.today.isoformat(),
                "date_to": self.today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([log.pk for log in response.context["food_logs"]], [self.saved_log.pk])
        self.assertContains(response, "Nasi sisa")
        self.assertNotContains(response, "Sup basi")
        self.assertNotContains(response, "Nasi rahasia")

    def test_search_checks_notes_as_well_as_food_name(self):
        response = self.client.get(reverse("jurnal:index"), {"query": "kulkas"})

        self.assertEqual([log.pk for log in response.context["food_logs"]], [self.wasted_log.pk])

    def test_invalid_reversed_date_range_shows_error_without_crashing(self):
        response = self.client.get(
            reverse("jurnal:index"),
            {
                "date_from": (self.today + timedelta(days=1)).isoformat(),
                "date_to": self.today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filters_invalid"])
        self.assertTrue(response.context["filter_form"].errors["date_to"])
        self.assertEqual(len(response.context["food_logs"]), 2)

    def test_invalid_status_is_rejected_as_a_filter_value(self):
        response = self.client.get(reverse("jurnal:index"), {"status": "made-up"})

        self.assertTrue(response.context["filters_invalid"])
        self.assertTrue(response.context["filter_form"].errors["status"])
        self.assertEqual(len(response.context["food_logs"]), 2)

class FoodLogModelValidationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="model-validation-user",
            password="test-password",
        )

    def test_form_rejects_unknown_status_and_unit(self):
        form = FoodLogForm(data={
            "food_name": "Makanan",
            "quantity": "1",
            "unit": "unknown",
            "status": "unknown",
            "notes": "",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("unit", form.errors)
        self.assertIn("status", form.errors)

    def test_database_rejects_unknown_status(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FoodLog.objects.create(
                    user=self.user,
                    food_name="Status invalid",
                    status="unknown",
                )

    def test_database_rejects_unknown_unit(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FoodLog.objects.create(
                    user=self.user,
                    food_name="Satuan invalid",
                    unit="unknown",
                )

class FoodLogAjaxCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ajax-journal-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.create_url = reverse("jurnal:create")
        self.index_url = reverse("jurnal:index")
        self.form_data = {
            "food_name": "Nasi sisa",
            "quantity": "2",
            "unit": "pcs",
            "status": "terselamatkan",
            "notes": "Untuk makan malam",
        }

    def test_ajax_create_returns_json_and_saves_entry(self):
        response = self.client.post(
            self.create_url,
            self.form_data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["message"], "Catatan makanan berhasil ditambahkan.")
        self.assertIn("Nasi sisa", response.json()["list_html"])
        self.assertIn("1 total", response.json()["summary_html"])
        self.assertTrue(FoodLog.objects.filter(user=self.user, food_name="Nasi sisa").exists())

    def test_ajax_create_returns_validation_errors_without_saving(self):
        response = self.client.post(
            self.create_url,
            {**self.form_data, "quantity": "0"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("quantity", response.json()["form_html"])
        self.assertEqual(FoodLog.objects.count(), 0)

    def test_ajax_list_returns_filtered_results_and_summary(self):
        FoodLog.objects.create(
            user=self.user,
            food_name="Nasi sisa",
            quantity=1,
            unit="pcs",
            status="terselamatkan",
        )
        FoodLog.objects.create(
            user=self.user,
            food_name="Sup basi",
            quantity=1,
            unit="porsi",
            status="terbuang",
        )

        response = self.client.get(
            self.index_url,
            {"status": "terselamatkan"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("Nasi sisa", response.json()["list_html"])
        self.assertNotIn("Sup basi", response.json()["list_html"])
        self.assertIn("2 total", response.json()["summary_html"])

    def test_ajax_delete_returns_json_and_removes_entry(self):
        log = FoodLog.objects.create(
            user=self.user,
            food_name="Sup basi",
            quantity=1,
            unit="porsi",
            status="terbuang",
        )

        response = self.client.post(
            reverse("jurnal:delete", args=[log.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertFalse(FoodLog.objects.filter(pk=log.pk).exists())

class FoodLogWriteRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="rate-limit-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.create_url = reverse("jurnal:create")

    def tearDown(self):
        cache.clear()

    def test_write_endpoint_returns_429_after_per_user_limit(self):
        for index in range(20):
            response = self.client.post(
                self.create_url,
                {
                    "food_name": f"Log {index}",
                    "quantity": "1",
                    "unit": "pcs",
                    "status": "terselamatkan",
                    "notes": "",
                },
            )
            self.assertEqual(response.status_code, 302)

        self.assertEqual(cache.get(f"jurnal:food-log-write:{self.user.pk}"), 20)
        response = self.client.post(
            self.create_url,
            {
                "food_name": "Log kelebihan",
                "quantity": "1",
                "unit": "pcs",
                "status": "terselamatkan",
                "notes": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")
        self.assertFalse(FoodLog.objects.filter(food_name="Log kelebihan").exists())

    def test_rate_limit_also_protects_update_and_delete(self):
        log = FoodLog.objects.create(
            user=self.user,
            food_name="Tersimpan",
            quantity=1,
            unit="pcs",
            status="terselamatkan",
        )
        key = FOOD_LOG_WRITE_CACHE_KEY.format(user_id=self.user.pk)
        update_url = reverse("jurnal:update", args=[log.pk])
        delete_url = reverse("jurnal:delete", args=[log.pk])
        form_data = {
            "food_name": "Diubah",
            "quantity": "2",
            "unit": "pcs",
            "status": "terbuang",
            "notes": "",
        }

        cache.set(key, 20, timeout=60)
        update_response = self.client.post(
            update_url,
            form_data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(update_response.status_code, 429)
        log.refresh_from_db()
        self.assertEqual(log.food_name, "Tersimpan")

        cache.set(key, 20, timeout=60)
        delete_response = self.client.post(
            delete_url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(delete_response.status_code, 429)
        self.assertTrue(FoodLog.objects.filter(pk=log.pk).exists())