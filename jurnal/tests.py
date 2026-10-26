from types import SimpleNamespace
# Used to create simple objects with dynamic attributes
# Used in unit test to create dummy containers

from django.contrib.auth import get_user_model
# Returns the user model that is currently active in the django project

from django.test import TestCase

from .models import FoodLog
from .services import sync_log_from_rescue_plan




class SyncLogFromRescuePlanTests(TestCase):

    # Menyiapkan data awal (dummy user) sebelum setiap tes baru
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="test-password",
        )

    # Menyiapkan data awal (dummy plan) untuk keperluan tes (tidak sebelum setiap tes baru tapi dipanggil)
    def make_plan(self, **changes):
        # Nilai default bisa ditimpa melalui keyword arguments (misal: jumlah=5).
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
        # Arrange
        plan = self.make_plan()

        # Act
        log = sync_log_from_rescue_plan(plan)

        # Assert
        self.assertEqual(log.food_name, "Bayam")
        self.assertEqual(log.status, "terselamatkan")
        self.assertEqual(log.quantity, 2)
        self.assertEqual(log.rescue_plan_id, 123)

    def test_syncing_same_plan_twice_does_not_create_duplicate(self):
        # Arrange
        plan = self.make_plan()

        # Act
        sync_log_from_rescue_plan(plan)
        sync_log_from_rescue_plan(plan)

        # Assert
        self.assertEqual(FoodLog.objects.count(), 1)

    def test_non_final_plan_deletes_existing_log(self):
        # Arrange
        plan = self.make_plan()
        sync_log_from_rescue_plan(plan)

        # Act
        result = sync_log_from_rescue_plan(
            self.make_plan(status="ongoing")
        )

        # Assert
        self.assertIsNone(result)
        self.assertFalse(
            FoodLog.objects.filter(rescue_plan_id=123).exists()
        )

    def test_unsaved_plan_does_nothing(self):
        # Arrange
        plan = self.make_plan(pk=None)

        # Act
        result = sync_log_from_rescue_plan(plan)

        # Assert
        self.assertIsNone(result)
        self.assertEqual(FoodLog.objects.count(), 0)

    