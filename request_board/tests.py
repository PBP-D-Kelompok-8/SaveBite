from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from request_board import models


class RequestCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.other_user = User.objects.create_user(username="other_requester")
        cls.owner = User.objects.create_user(username="request_owner")

    def create_request(self):
        request_model = getattr(models, "Request", None)
        self.assertIsNotNone(
            request_model,
            "Model Request belum tersedia.",
        )
        return request_model.objects.create(
            owner=self.owner,
            title="Butuh beras untuk makan malam",
            food_item="Beras",
            quantity=Decimal("0.50"),
            unit="kg",
        )

    def test_new_request_is_pending(self):
        food_request = self.create_request()

        food_request.refresh_from_db()

        self.assertEqual(food_request.status, "pending")

    def test_new_request_has_timestamps(self):
        before_creation = timezone.now()
        food_request = self.create_request()
        after_creation = timezone.now()

        food_request.refresh_from_db()

        for field in ("created_at", "updated_at"):
            with self.subTest(field=field):
                timestamp = getattr(food_request, field)
                self.assertIsNotNone(timestamp)
                self.assertGreaterEqual(timestamp, before_creation)
                self.assertLessEqual(timestamp, after_creation)

    def test_request_belongs_to_its_owner(self):
        food_request = self.create_request()

        food_request.refresh_from_db()

        self.assertEqual(food_request.owner, self.owner)
        self.assertEqual(food_request.owner_id, self.owner.pk)
        self.assertNotEqual(food_request.owner_id, self.other_user.pk)

    def test_request_string_contains_its_title(self):
        food_request = self.create_request()

        self.assertIn(food_request.title, str(food_request))


class RequestValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(
            username="validation_request_owner"
        )

    def build_request(self, **overrides):
        data = {
            "owner": self.owner,
            "title": "Butuh telur untuk sarapan",
            "food_item": "Telur",
            "quantity": Decimal("2.00"),
            "unit": "butir",
        }
        data.update(overrides)
        return models.Request(**data)

    def test_small_positive_quantity_is_valid(self):
        food_request = self.build_request(quantity=Decimal("0.01"))

        food_request.full_clean()
        food_request.save()
        food_request.refresh_from_db()

        self.assertEqual(food_request.quantity, Decimal("0.01"))

    def test_validation_rejects_nonpositive_quantity(self):
        for quantity in (Decimal("0.00"), Decimal("-0.01")):
            with self.subTest(quantity=quantity):
                food_request = self.build_request(quantity=quantity)

                with self.assertRaises(ValidationError):
                    food_request.full_clean()

    def test_database_rejects_nonpositive_quantity(self):
        for quantity in (Decimal("0.00"), Decimal("-0.01")):
            with self.subTest(quantity=quantity):
                food_request = self.build_request(quantity=quantity)

                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        food_request.save()

                self.assertEqual(models.Request.objects.count(), 0)

    def test_valid_statuses_have_correct_labels(self):
        for status, label in (
            ("pending", "Pending"),
            ("offered", "Ditawarkan"),
            ("fulfilled", "Terpenuhi"),
        ):
            with self.subTest(status=status):
                food_request = self.build_request(status=status)

                food_request.full_clean()
                food_request.save()
                food_request.refresh_from_db()

                self.assertEqual(food_request.status, status)
                self.assertEqual(food_request.get_status_display(), label)

    def test_validation_rejects_invalid_status(self):
        for status in ("unknown", "Pending", ""):
            with self.subTest(status=status):
                food_request = self.build_request(status=status)

                with self.assertRaises(ValidationError):
                    food_request.full_clean()

    def test_database_rejects_invalid_status(self):
        for status in ("unknown", "Pending", ""):
            with self.subTest(status=status):
                food_request = self.build_request(status=status)

                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        food_request.save()

                self.assertEqual(models.Request.objects.count(), 0)


class RequestOfferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="offer_request_owner")
        cls.donor = User.objects.create_user(username="first_donor")
        cls.other_donor = User.objects.create_user(username="second_donor")
        cls.food_request = models.Request.objects.create(
            owner=cls.owner,
            title="Butuh tomat untuk memasak",
            food_item="Tomat",
            quantity=Decimal("3.00"),
            unit="buah",
        )
        cls.other_request = models.Request.objects.create(
            owner=cls.owner,
            title="Butuh bawang untuk memasak",
            food_item="Bawang",
            quantity=Decimal("0.25"),
            unit="kg",
        )

    def offer_model(self):
        offer_model = getattr(models, "RequestOffer", None)
        self.assertIsNotNone(
            offer_model,
            "Model RequestOffer belum tersedia.",
        )
        return offer_model

    def create_offer(self, food_request=None, donor=None):
        return self.offer_model().objects.create(
            request=self.food_request if food_request is None else food_request,
            offered_by=self.donor if donor is None else donor,
        )

    def test_offer_belongs_to_request_and_donor(self):
        offer = self.create_offer()

        offer.refresh_from_db()

        self.assertEqual(offer.request, self.food_request)
        self.assertEqual(offer.offered_by, self.donor)
        self.assertNotEqual(offer.offered_by_id, self.owner.pk)

    def test_new_offer_has_creation_time(self):
        before_creation = timezone.now()
        offer = self.create_offer()
        after_creation = timezone.now()

        offer.refresh_from_db()

        self.assertIsNotNone(offer.created_at)
        self.assertGreaterEqual(offer.created_at, before_creation)
        self.assertLessEqual(offer.created_at, after_creation)

    def test_duplicate_offer_is_rejected(self):
        offer = self.create_offer()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_offer()

        self.assertEqual(self.offer_model().objects.count(), 1)
        self.assertTrue(self.offer_model().objects.filter(pk=offer.pk).exists())

    def test_donor_can_offer_for_different_requests(self):
        self.create_offer()
        self.create_offer(food_request=self.other_request)

        offered_request_ids = set(
            self.offer_model().objects.filter(offered_by=self.donor).values_list(
                "request_id", flat=True
            )
        )
        self.assertEqual(
            offered_request_ids, {self.food_request.pk, self.other_request.pk}
        )

    def test_request_can_receive_offers_from_multiple_donors(self):
        self.create_offer()
        self.create_offer(donor=self.other_donor)

        donor_ids = set(
            self.offer_model().objects.filter(request=self.food_request).values_list(
                "offered_by_id", flat=True
            )
        )
        self.assertEqual(donor_ids, {self.donor.pk, self.other_donor.pk})

    def test_deleting_request_removes_its_offers_only(self):
        self.create_offer()
        self.create_offer(donor=self.other_donor)
        unrelated_offer = self.create_offer(food_request=self.other_request)
        deleted_request_id = self.food_request.pk

        self.food_request.delete()

        self.assertFalse(models.Request.objects.filter(pk=deleted_request_id).exists())
        self.assertFalse(
            self.offer_model().objects.filter(request_id=deleted_request_id).exists()
        )
        self.assertEqual(self.offer_model().objects.count(), 1)
        self.assertTrue(
            self.offer_model().objects.filter(pk=unrelated_offer.pk).exists()
        )
        self.assertTrue(models.Request.objects.filter(pk=self.other_request.pk).exists())
        self.assertEqual(
            get_user_model().objects.filter(
                pk__in=[self.owner.pk, self.donor.pk, self.other_donor.pk]
            ).count(),
            3,
        )
