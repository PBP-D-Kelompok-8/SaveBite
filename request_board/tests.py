from decimal import Decimal
from importlib import import_module
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.query import QuerySet
from django.forms import ModelForm
from django.http import QueryDict
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


class RequestFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="form_request_owner")
        cls.other_user = User.objects.create_user(username="form_other_user")

    def form_class(self):
        try:
            form_module = import_module("request_board.forms")
        except ModuleNotFoundError as exc:
            if exc.name != "request_board.forms":
                raise
            self.fail("Modul request_board.forms belum tersedia.")
        form_class = getattr(form_module, "RequestForm", None)
        self.assertIsNotNone(form_class, "RequestForm belum tersedia.")
        return form_class

    def valid_data(self, **overrides):
        data = {
            "title": "Butuh beras untuk makan malam",
            "food_item": "Beras",
            "quantity": "0.50",
            "unit": "kg",
            "description": "Untuk memasak dua porsi makan malam.",
            "location": "Depok",
        }
        data.update(overrides)
        return data

    def test_valid_form_preserves_submitted_values(self):
        data = self.valid_data()
        form = self.form_class()(data=data)
        self.assertTrue(form.is_valid(), form.errors)

        food_request = form.save(commit=False)
        self.assertIsInstance(food_request, models.Request)
        self.assertIsNone(food_request.pk)
        self.assertEqual(models.Request.objects.count(), 0)
        food_request.owner = self.owner
        food_request.save()
        food_request.refresh_from_db()

        for field in ("title", "food_item", "unit", "description", "location"):
            with self.subTest(field=field):
                self.assertEqual(getattr(food_request, field), data[field])
        self.assertEqual(food_request.quantity, Decimal("0.50"))
        self.assertEqual(food_request.owner, self.owner)
        self.assertEqual(food_request.status, "pending")

    def test_required_fields_reject_empty_values(self):
        form_class = self.form_class()
        for field in ("title", "food_item", "quantity", "unit"):
            values = ("", "   ", None) if field in ("title", "food_item") else ("", None)
            for value in values:
                with self.subTest(field=field, value=value):
                    data = self.valid_data()
                    if value is None:
                        data.pop(field)
                    else:
                        data[field] = value
                    form = form_class(data=data)
                    self.assertFalse(form.is_valid())
                    self.assertIn(field, form.errors)

    def test_quantity_obeys_positive_decimal_limits(self):
        form_class = self.form_class()
        for value in ("0", "-0.01", "bukan angka", "0.001", "1000000"):
            with self.subTest(invalid_quantity=value):
                form = form_class(data=self.valid_data(quantity=value))
                self.assertFalse(form.is_valid())
                self.assertIn("quantity", form.errors)
        for value in ("0.01", "999999.99"):
            with self.subTest(valid_quantity=value):
                form = form_class(data=self.valid_data(quantity=value))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["quantity"], Decimal(value))

    def test_unit_rejects_unknown_choices(self):
        form_class = self.form_class()
        for value in ("karung", "KG"):
            with self.subTest(invalid_unit=value):
                form = form_class(data=self.valid_data(unit=value))
                self.assertFalse(form.is_valid())
                self.assertIn("unit", form.errors)
        for value, _ in models.Request.UNIT_CHOICES:
            with self.subTest(valid_unit=value):
                form = form_class(data=self.valid_data(unit=value))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["unit"], value)

    def test_optional_fields_accept_empty_or_missing_values(self):
        form_class = self.form_class()
        for missing in (False, True):
            with self.subTest(missing=missing):
                data = self.valid_data(description="", location="")
                if missing:
                    data.pop("description")
                    data.pop("location")
                form = form_class(data=data)
                self.assertTrue(form.is_valid(), form.errors)
                food_request = form.save(commit=False)
                self.assertEqual(food_request.description, "")
                self.assertEqual(food_request.location, "")

    def test_text_fields_enforce_length_limits(self):
        form_class = self.form_class()
        for field, limit in (
            ("title", 120),
            ("food_item", 120),
            ("location", 120),
            ("description", 1000),
        ):
            with self.subTest(field=field, length=limit):
                form = form_class(data=self.valid_data(**{field: "a" * limit}))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data[field], "a" * limit)
            with self.subTest(field=field, length=limit + 1):
                form = form_class(data=self.valid_data(**{field: "a" * (limit + 1)}))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_form_exposes_only_create_fields(self):
        form = self.form_class()()
        self.assertIsInstance(form, ModelForm)
        self.assertIs(form._meta.model, models.Request)
        self.assertEqual(
            set(form.fields),
            {"title", "food_item", "quantity", "unit", "description", "location"},
        )

    def test_internal_payload_cannot_override_request_metadata(self):
        forged_timestamp = "2000-01-01T00:00:00Z"
        data = self.valid_data(
            owner=str(self.other_user.pk),
            status="fulfilled",
            offered_by=str(self.other_user.pk),
            id="999999",
            created_at=forged_timestamp,
            updated_at=forged_timestamp,
        )
        form = self.form_class()(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        food_request = form.save(commit=False)

        self.assertIsNone(food_request.owner_id)
        self.assertEqual(food_request.status, "pending")
        self.assertIsNone(food_request.pk)
        self.assertIsNone(food_request.created_at)
        self.assertIsNone(food_request.updated_at)
        self.assertEqual(models.Request.objects.count(), 0)
        self.assertEqual(models.RequestOffer.objects.count(), 0)

        food_request.owner = self.owner
        before_save = timezone.now()
        food_request.save()
        after_save = timezone.now()
        food_request.refresh_from_db()

        self.assertEqual(food_request.owner_id, self.owner.pk)
        self.assertEqual(food_request.status, "pending")
        self.assertNotEqual(food_request.pk, 999999)
        for field in ("created_at", "updated_at"):
            with self.subTest(field=field):
                self.assertGreaterEqual(getattr(food_request, field), before_save)
                self.assertLessEqual(getattr(food_request, field), after_save)
        self.assertEqual(models.Request.objects.count(), 1)
        self.assertEqual(models.RequestOffer.objects.count(), 0)


class RequestCreateServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="create_service_actor")
        cls.other_user = User.objects.create_user(username="create_service_other")

    def service(self):
        try:
            service_module = import_module("request_board.services")
        except ModuleNotFoundError as exc:
            if exc.name != "request_board.services":
                raise
            self.fail("Modul request_board.services belum tersedia.")
        create_request = getattr(service_module, "create_request", None)
        self.assertTrue(callable(create_request), "create_request belum callable.")
        return create_request

    def valid_data(self, **overrides):
        data = {
            "title": "Butuh beras untuk makan malam",
            "food_item": "Beras",
            "quantity": "0.50",
            "unit": "kg",
            "description": "Untuk dua orang",
            "location": "Area kampus",
        }
        data.update(overrides)
        return data

    def counts(self):
        return models.Request.objects.count(), models.RequestOffer.objects.count()

    def test_authenticated_actor_creates_pending_request(self):
        create_request = self.service()
        data = self.valid_data()
        request_count, offer_count = self.counts()
        before = timezone.now()
        food_request = create_request(actor=self.actor, data=data)
        after = timezone.now()

        self.assertIsInstance(food_request, models.Request)
        self.assertIsNotNone(food_request.pk)
        self.assertEqual(self.counts(), (request_count + 1, offer_count))
        food_request.refresh_from_db()
        for field in ("title", "food_item", "unit", "description", "location"):
            self.assertEqual(getattr(food_request, field), data[field])
        self.assertEqual(food_request.quantity, Decimal("0.50"))
        self.assertEqual(food_request.owner_id, self.actor.pk)
        self.assertEqual(food_request.status, "pending")
        for field in ("created_at", "updated_at"):
            self.assertGreaterEqual(getattr(food_request, field), before)
            self.assertLessEqual(getattr(food_request, field), after)

    def test_optional_fields_accept_empty_or_missing_values(self):
        create_request = self.service()
        for missing in (False, True):
            with self.subTest(missing=missing):
                data = self.valid_data(description="", location="")
                if missing:
                    data.pop("description")
                    data.pop("location")
                request_count, offer_count = self.counts()
                food_request = create_request(actor=self.actor, data=data)
                food_request.refresh_from_db()
                self.assertEqual(food_request.description, "")
                self.assertEqual(food_request.location, "")
                self.assertEqual(food_request.owner_id, self.actor.pk)
                self.assertEqual(food_request.status, "pending")
                self.assertEqual(self.counts(), (request_count + 1, offer_count))

    def test_rejects_unauthenticated_actor_without_writes(self):
        create_request = self.service()
        for actor in (None, AnonymousUser()):
            for invalid in (False, True):
                with self.subTest(actor=actor, invalid=invalid):
                    data = self.valid_data()
                    if invalid:
                        data["title"] = ""
                    counts = self.counts()
                    with self.assertRaises(PermissionDenied):
                        create_request(actor=actor, data=data)
                    self.assertEqual(self.counts(), counts)

    def test_rejects_unsaved_actor_without_writes(self):
        create_request = self.service()
        for pk in (None, 999999):
            with self.subTest(pk=pk):
                actor = get_user_model()(username="unsaved_actor", pk=pk)
                self.assertTrue(actor.is_authenticated)
                self.assertTrue(actor._state.adding)
                self.assertFalse(get_user_model().objects.filter(pk=pk).exists())
                counts = self.counts()
                with self.assertRaises(PermissionDenied):
                    create_request(actor=actor, data=self.valid_data())
                self.assertEqual(self.counts(), counts)

    def test_invalid_form_reports_field_errors_without_writes(self):
        create_request = self.service()
        cases = [
            ("title", ""), ("title", "   "), ("title", None),
            ("food_item", ""),
            ("quantity", "0"), ("quantity", "-0.01"),
            ("quantity", "abc"), ("quantity", "0.001"),
            ("quantity", "1000000"), ("unit", "karung"),
            ("title", "a" * 121), ("food_item", "a" * 121),
            ("location", "a" * 121), ("description", "a" * 1001),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = self.valid_data()
                if value is None:
                    data.pop(field)
                else:
                    data[field] = value
                counts = self.counts()
                with self.assertRaises(ValidationError) as raised:
                    create_request(actor=self.actor, data=data)
                self.assertIn(field, raised.exception.error_dict)
                self.assertEqual(self.counts(), counts)
        counts = self.counts()
        with self.assertRaises(ValidationError) as raised:
            create_request(actor=self.actor, data=self.valid_data(title="", quantity="0"))
        self.assertTrue({"title", "quantity"}.issubset(raised.exception.error_dict))
        self.assertEqual(self.counts(), counts)

    def test_internal_payload_cannot_override_metadata_or_update_existing_request(self):
        create_request = self.service()
        for actor, other_user in (
            (self.actor, self.other_user), (self.other_user, self.actor),
        ):
            with self.subTest(actor=actor.pk):
                existing = models.Request.objects.create(
                    owner=other_user, status="offered", **self.valid_data()
                )
                snapshot = models.Request.objects.values().get(pk=existing.pk)
                data = self.valid_data(
                    owner=str(other_user.pk), owner_id=str(other_user.pk),
                    status="fulfilled", offered_by=str(other_user.pk),
                    offered_by_id=str(other_user.pk), id=str(existing.pk),
                    pk=str(existing.pk), created_at="2000-01-01T00:00:00Z",
                    updated_at="2000-01-01T00:00:00Z", unknown="ignored",
                )
                request_count, offer_count = self.counts()
                before = timezone.now()
                food_request = create_request(actor=actor, data=data)
                after = timezone.now()
                food_request.refresh_from_db()
                self.assertIsNotNone(food_request.pk)
                self.assertNotEqual(food_request.pk, existing.pk)
                self.assertEqual(food_request.owner_id, actor.pk)
                self.assertEqual(food_request.status, "pending")
                for field in ("created_at", "updated_at"):
                    self.assertGreaterEqual(getattr(food_request, field), before)
                    self.assertLessEqual(getattr(food_request, field), after)
                existing.refresh_from_db()
                self.assertEqual(
                    models.Request.objects.values().get(pk=existing.pk), snapshot
                )
                for field, value in snapshot.items():
                    self.assertEqual(getattr(existing, field), value)
                self.assertEqual(self.counts(), (request_count + 1, offer_count))

    def test_mapping_inputs_share_validation_without_mutation(self):
        create_request = self.service()
        expected = self.valid_data()
        for mapping_type in (dict, QueryDict):
            for invalid in (False, True):
                with self.subTest(mapping_type=mapping_type.__name__, invalid=invalid):
                    values = self.valid_data()
                    for field in ("title", "food_item", "description", "location"):
                        values[field] = "  " + values[field] + "  "
                    if invalid:
                        values["quantity"] = "abc"
                    if mapping_type is QueryDict:
                        data = QueryDict("", mutable=True)
                        data.update(values)
                        data = QueryDict(data.urlencode())
                    else:
                        data = dict(values)
                    snapshot = data.copy()
                    request_count, offer_count = self.counts()
                    if invalid:
                        with self.assertRaises(ValidationError) as raised:
                            create_request(actor=self.actor, data=data)
                        self.assertIn("quantity", raised.exception.error_dict)
                        self.assertEqual(self.counts(), (request_count, offer_count))
                    else:
                        food_request = create_request(actor=self.actor, data=data)
                        food_request.refresh_from_db()
                        for field in ("title", "food_item", "unit", "description", "location"):
                            self.assertEqual(getattr(food_request, field), expected[field])
                        self.assertEqual(food_request.quantity, Decimal("0.50"))
                        self.assertEqual(food_request.owner_id, self.actor.pk)
                        self.assertEqual(food_request.status, "pending")
                        self.assertEqual(self.counts(), (request_count + 1, offer_count))
                    self.assertEqual(data, snapshot)

    def test_repeated_valid_calls_create_distinct_requests(self):
        create_request = self.service()
        request_count, offer_count = self.counts()
        first = create_request(actor=self.actor, data=self.valid_data())
        second = create_request(actor=self.actor, data=self.valid_data())
        self.assertIsNotNone(first.pk)
        self.assertIsNotNone(second.pk)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(self.counts(), (request_count + 2, offer_count))
        for food_request in (first, second):
            food_request.refresh_from_db()
            self.assertEqual(food_request.owner_id, self.actor.pk)
            self.assertEqual(food_request.status, "pending")


class RequestOfferServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="offer_service_owner")
        cls.donor = User.objects.create_user(username="offer_service_donor")
        cls.other_donor = User.objects.create_user(username="offer_service_other")

    def setUp(self):
        self.food_request = self.make_request()
        self.other_request = self.make_request(title="Posting lain")

    def service(self):
        service = getattr(import_module("request_board.services"), "offer_request", None)
        self.assertTrue(callable(service), "offer_request belum callable")
        return service

    def make_request(self, **overrides):
        data = dict(
            owner=self.owner, title="Butuh nasi", food_item="Nasi",
            quantity=Decimal("2.50"), unit="porsi", description="Untuk makan",
            location="Depok",
        )
        data.update(overrides)
        food_request = models.Request.objects.create(**data)
        old_time = timezone.now() - timedelta(days=2)
        models.Request.objects.filter(pk=food_request.pk).update(
            created_at=old_time, updated_at=old_time,
        )
        food_request.refresh_from_db()
        return food_request

    def snapshot(self):
        return (
            list(models.Request.objects.order_by("pk").values()),
            list(models.RequestOffer.objects.order_by("pk").values()),
        )

    def prepare_offered(self, status="offered"):
        offer = models.RequestOffer.objects.create(
            request=self.food_request, offered_by=self.donor,
        )
        models.Request.objects.filter(pk=self.food_request.pk).update(status=status)
        return offer

    def assert_result(self, result, *, created, food_request=None, donor=None):
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        offer, actual_created = result
        self.assertIsInstance(offer, models.RequestOffer)
        self.assertIs(actual_created, created)
        self.assertIsNotNone(offer.pk)
        offer.refresh_from_db()
        self.assertEqual(offer.request_id, (food_request or self.food_request).pk)
        self.assertEqual(offer.offered_by_id, (donor or self.donor).pk)
        return offer

    def assert_transition(self, before, *, target_ids, start, end, offers):
        after_requests, after_offers = self.snapshot()
        expected_requests = []
        for row in before[0]:
            expected = row.copy()
            if row["id"] in target_ids:
                actual = next(item for item in after_requests if item["id"] == row["id"])
                self.assertLessEqual(start, actual["updated_at"])
                self.assertLessEqual(actual["updated_at"], end)
                self.assertGreater(actual["updated_at"], row["updated_at"])
                expected.update(status="offered", updated_at=actual["updated_at"])
            expected_requests.append(expected)
        self.assertEqual(after_requests, expected_requests)
        new_rows = []
        for offer in offers:
            self.assertLessEqual(start, offer.created_at)
            self.assertLessEqual(offer.created_at, end)
            new_rows.append(dict(
                id=offer.pk, request_id=offer.request_id,
                offered_by_id=offer.offered_by_id, created_at=offer.created_at,
            ))
        self.assertEqual(after_offers, sorted(before[1] + new_rows, key=lambda row: row["id"]))

    def test_first_offer_creates_offer_and_changes_pending_request(self):
        offer_request = self.service()
        before = self.snapshot()
        start = timezone.now()
        offer = self.assert_result(
            offer_request(actor=self.donor, request_id=self.food_request.pk), created=True,
        )
        end = timezone.now()
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "offered")

        self.assert_transition(before, target_ids={self.food_request.pk}, start=start, end=end, offers=[offer])

    def test_different_donor_can_offer_an_offered_request(self):
        offer_request = self.service()
        original = self.prepare_offered()
        before = self.snapshot()
        start = timezone.now()
        offer = self.assert_result(
            offer_request(actor=self.other_donor, request_id=self.food_request.pk),
            created=True, donor=self.other_donor,
        )
        end = timezone.now()
        self.assertNotEqual(offer.pk, original.pk)
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "offered")
        self.assert_transition(before, target_ids=set(), start=start, end=end, offers=[offer])

    def test_repeated_offer_returns_existing_offer_without_writes(self):
        offer_request = self.service()
        original = self.prepare_offered()
        before = self.snapshot()
        writes = []

        def record_writes(execute, sql, params, many, context):
            if sql.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(sql)
            return execute(sql, params, many, context)

        for _ in range(2):
            with self.subTest(attempt=_):
                with connection.execute_wrapper(record_writes):
                    result = offer_request(actor=self.donor, request_id=self.food_request.pk)
                offer = self.assert_result(result, created=False)
                self.assertEqual(offer.pk, original.pk)
                self.assertEqual(offer.created_at, original.created_at)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(writes, [])

    def test_rejects_unauthenticated_actor_before_lookup_without_writes(self):
        offer_request = self.service()
        missing_id = models.Request.objects.order_by("-pk").first().pk + 100
        for actor in (None, AnonymousUser()):
            for request_id in (self.food_request.pk, missing_id):
                with self.subTest(actor=actor, request_id=request_id):
                    before = self.snapshot()
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        offer_request(actor=actor, request_id=request_id)
                    self.assertEqual(self.snapshot(), before)

    def test_rejects_unsaved_actor_before_lookup_without_writes(self):
        offer_request = self.service()
        User = get_user_model()
        missing_id = models.Request.objects.order_by("-pk").first().pk + 100
        for pk in (None, self.donor.pk + 100, self.donor.pk):
            for request_id in (self.food_request.pk, missing_id):
                with self.subTest(pk=pk, request_id=request_id):
                    actor = User(username="unsaved_offer", pk=pk)
                    self.assertTrue(actor.is_authenticated)
                    self.assertTrue(actor._state.adding)
                    before = self.snapshot()
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        offer_request(actor=actor, request_id=request_id)
                    self.assertEqual(self.snapshot(), before)

    def test_owner_cannot_offer_in_any_state(self):
        offer_request = self.service()
        for status in ("pending", "offered", "fulfilled"):
            with self.subTest(status=status):
                if status != "pending":
                    models.RequestOffer.objects.get_or_create(
                        request=self.food_request, offered_by=self.donor,
                    )
                models.Request.objects.filter(pk=self.food_request.pk).update(status=status)
                before = self.snapshot()
                with self.assertRaises(PermissionDenied):
                    offer_request(actor=self.owner, request_id=self.food_request.pk)
                self.assertEqual(self.snapshot(), before)

    def test_fulfilled_request_rejects_new_and_repeated_offers(self):
        offer_request = self.service()
        self.prepare_offered(status="fulfilled")
        for actor in (self.donor, self.other_donor):
            with self.subTest(actor=actor.pk):
                before = self.snapshot()
                with self.assertRaises(ValidationError) as caught:
                    offer_request(actor=actor, request_id=self.food_request.pk)
                self.assertEqual(caught.exception.code, "request_fulfilled")
                self.assertEqual(self.snapshot(), before)

    def test_missing_request_raises_does_not_exist_without_writes(self):
        offer_request = self.service()
        missing_id = models.Request.objects.order_by("-pk").first().pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        before = self.snapshot()
        with self.assertRaises(models.Request.DoesNotExist):
            offer_request(actor=self.donor, request_id=missing_id)
        self.assertEqual(self.snapshot(), before)

    def test_reads_current_owner_and_status_from_database(self):
        offer_request = self.service()
        for change in ("owner", "status"):
            with self.subTest(change=change):
                stale = self.make_request()
                if change == "owner":
                    models.Request.objects.filter(pk=stale.pk).update(owner=self.donor)
                    exception = PermissionDenied
                else:
                    models.RequestOffer.objects.create(request=stale, offered_by=self.other_donor)
                    models.Request.objects.filter(pk=stale.pk).update(status="fulfilled")
                    exception = ValidationError
                self.assertEqual(stale.owner_id, self.owner.pk)
                self.assertEqual(stale.status, "pending")
                before = self.snapshot()
                with self.assertRaises(exception) as caught:
                    offer_request(actor=self.donor, request_id=stale.pk)
                if change == "status":
                    self.assertEqual(caught.exception.code, "request_fulfilled")
                self.assertEqual(self.snapshot(), before)

    def test_same_donor_can_offer_different_requests(self):
        offer_request = self.service()
        before = self.snapshot()
        start = timezone.now()
        offers = [self.assert_result(
            offer_request(actor=self.donor, request_id=target.pk),
            created=True, food_request=target,
        ) for target in (self.food_request, self.other_request)]
        end = timezone.now()
        self.assertNotEqual(offers[0].pk, offers[1].pk)
        self.assert_transition(
            before, target_ids={self.food_request.pk, self.other_request.pk},
            start=start, end=end, offers=offers,
        )

    def test_locks_request_inside_atomic_before_offer_access(self):
        offer_request = self.service()
        manager = models.Request.objects
        real_select = manager.select_for_update
        real_get = QuerySet.get
        real_get_or_create = QuerySet.get_or_create
        events = []

        def select(*args, **kwargs):
            events.append(("select", connection.in_atomic_block))
            return real_select(*args, **kwargs)

        def get(queryset, *args, **kwargs):
            if queryset.model is models.Request:
                self.assertTrue(queryset.query.select_for_update)
                self.assertTrue(connection.in_atomic_block)
                result = real_get(queryset, *args, **kwargs)
                events.append(("locked", result.pk))
                return result
            if queryset.model is models.RequestOffer:
                self.assertIn(("locked", self.food_request.pk), events)
                events.append(("offer", connection.in_atomic_block))
            return real_get(queryset, *args, **kwargs)

        def get_or_create(queryset, *args, **kwargs):
            if queryset.model is models.RequestOffer:
                self.assertIn(("locked", self.food_request.pk), events)
                self.assertTrue(connection.in_atomic_block)
                events.append(("offer", connection.in_atomic_block))
            return real_get_or_create(queryset, *args, **kwargs)

        with patch.object(manager, "select_for_update", side_effect=select), \
                patch.object(QuerySet, "get", new=get), \
                patch.object(QuerySet, "get_or_create", new=get_or_create):
            self.assert_result(
                offer_request(actor=self.donor, request_id=self.food_request.pk), created=True,
            )
        self.assertIn(("select", True), events)
        self.assertIn(("locked", self.food_request.pk), events)
        self.assertIn(("offer", True), events)
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "offered")

    def test_insert_failure_rolls_back_entire_offer_operation(self):
        offer_request = self.service()
        before = self.snapshot()
        real_save = models.RequestOffer.save
        sentinel = IntegrityError("offer insert sentinel")
        inserted = []

        def save(offer, *args, **kwargs):
            inserting = offer._state.adding
            result = real_save(offer, *args, **kwargs)
            if inserting and offer.request_id == self.food_request.pk:
                inserted.append(offer.pk)
                raise sentinel
            return result

        with patch.object(models.RequestOffer, "save", new=save):
            with self.assertRaises(IntegrityError) as caught:
                offer_request(actor=self.donor, request_id=self.food_request.pk)
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(len(inserted), 1)
        self.assertEqual(self.snapshot(), before)
        self.assert_result(
            offer_request(actor=self.donor, request_id=self.food_request.pk), created=True,
        )
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "offered")

    def test_status_save_failure_rolls_back_inserted_offer(self):
        offer_request = self.service()
        before = self.snapshot()
        real_save = models.Request.save
        sentinel = RuntimeError("request status sentinel")
        saved = []

        def save(food_request, *args, **kwargs):
            result = real_save(food_request, *args, **kwargs)
            if food_request.pk == self.food_request.pk and food_request.status == "offered":
                saved.append(food_request.pk)
                self.assertTrue(models.RequestOffer.objects.filter(
                    request=food_request, offered_by=self.donor,
                ).exists())
                raise sentinel
            return result

        with patch.object(models.Request, "save", new=save):
            with self.assertRaises(RuntimeError) as caught:
                offer_request(actor=self.donor, request_id=self.food_request.pk)
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(saved, [self.food_request.pk])
        self.assertEqual(self.snapshot(), before)
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "pending")
        self.assert_result(
            offer_request(actor=self.donor, request_id=self.food_request.pk), created=True,
        )
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "offered")


class RequestFulfillServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="fulfill_service_owner")
        cls.donor = User.objects.create_user(username="fulfill_service_donor")
        cls.other_user = User.objects.create_user(username="fulfill_service_other")

    def setUp(self):
        self.food_request = self.make_request()
        self.other_request = self.make_request(title="Posting lain")

    def service(self):
        service = getattr(import_module("request_board.services"), "fulfill_request", None)
        self.assertTrue(callable(service), "fulfill_request belum callable")
        return service

    def make_request(self, **overrides):
        data = dict(
            owner=self.owner, title="Butuh nasi", food_item="Nasi",
            quantity=Decimal("2.50"), unit="porsi", description="Untuk makan",
            location="Depok",
        )
        data.update(overrides)
        food_request = models.Request.objects.create(**data)
        old_time = timezone.now() - timedelta(days=2)
        models.Request.objects.filter(pk=food_request.pk).update(
            created_at=old_time, updated_at=old_time,
        )
        food_request.refresh_from_db()
        return food_request

    def prepare_offered(self, food_request=None, status="offered"):
        target = self.food_request if food_request is None else food_request
        offer = models.RequestOffer.objects.create(request=target, offered_by=self.donor)
        models.RequestOffer.objects.filter(pk=offer.pk).update(
            created_at=target.created_at,
        )
        models.Request.objects.filter(pk=target.pk).update(
            status=status, selected_offer=offer if status == "offered" else None,
        )
        return offer

    def snapshot(self):
        return (
            list(models.Request.objects.order_by("pk").values()),
            list(models.RequestOffer.objects.order_by("pk").values()),
        )

    def assert_result(self, result, target=None):
        target = self.food_request if target is None else target
        self.assertIsInstance(result, models.Request)
        self.assertEqual(result.pk, target.pk)
        self.assertFalse(result._state.adding)
        self.assertEqual(result.status, "fulfilled")
        returned = {field.attname: getattr(result, field.attname)
                    for field in models.Request._meta.concrete_fields}
        result.refresh_from_db()
        self.assertEqual(returned, models.Request.objects.values().get(pk=target.pk))
        self.assertEqual(result.status, "fulfilled")
        return result

    def assert_transition(self, before, *, target, start, end):
        after_requests, after_offers = self.snapshot()
        expected_requests = []
        for row in before[0]:
            expected = row.copy()
            if row["id"] == target.pk:
                actual = next(item for item in after_requests if item["id"] == target.pk)
                self.assertGreater(actual["updated_at"], row["updated_at"])
                self.assertLessEqual(start, actual["updated_at"])
                self.assertLessEqual(actual["updated_at"], end)
                expected.update(status="fulfilled", updated_at=actual["updated_at"])
            expected_requests.append(expected)
        self.assertEqual(after_requests, expected_requests)
        self.assertEqual(after_offers, before[1])

    def record_writes(self, writes):
        def record(execute, sql, params, many, context):
            if sql.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(sql)
            return execute(sql, params, many, context)
        return record

    def test_owner_fulfills_offered_request(self):
        fulfill_request = self.service()
        self.prepare_offered()
        models.RequestOffer.objects.create(
            request=self.food_request, offered_by=self.other_user,
        )
        self.prepare_offered(self.other_request)
        before = self.snapshot()
        start = timezone.now()
        result = fulfill_request(actor=self.owner, request_id=self.food_request.pk)
        end = timezone.now()
        self.assert_result(result)
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "fulfilled")
        self.assert_transition(before, target=self.food_request, start=start, end=end)

    def test_repeated_fulfillment_returns_unchanged_request_without_writes(self):
        fulfill_request = self.service()
        self.prepare_offered(status="fulfilled")
        before = self.snapshot()
        writes = []
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                with connection.execute_wrapper(self.record_writes(writes)):
                    result = fulfill_request(actor=self.owner, request_id=self.food_request.pk)
                self.assert_result(result)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(writes, [])

    def test_rejects_unauthenticated_actor_before_lookup_without_writes(self):
        fulfill_request = self.service()
        missing_id = self.other_request.pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        for actor in (None, AnonymousUser()):
            for request_id in (self.food_request.pk, missing_id):
                with self.subTest(actor=actor, request_id=request_id):
                    before = self.snapshot()
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        fulfill_request(actor=actor, request_id=request_id)
                    self.assertEqual(self.snapshot(), before)

    def test_rejects_unsaved_actor_before_lookup_without_writes(self):
        fulfill_request = self.service()
        User = get_user_model()
        missing_id = self.other_request.pk + 100
        missing_user_id = self.other_user.pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        self.assertFalse(User.objects.filter(pk=missing_user_id).exists())
        for pk in (None, missing_user_id, self.owner.pk):
            for request_id in (self.food_request.pk, missing_id):
                with self.subTest(pk=pk, request_id=request_id):
                    actor = User(username="unsaved_fulfill", pk=pk)
                    self.assertTrue(actor.is_authenticated)
                    self.assertTrue(actor._state.adding)
                    before = self.snapshot()
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        fulfill_request(actor=actor, request_id=request_id)
                    self.assertEqual(self.snapshot(), before)

    def test_nonowner_cannot_fulfill_in_any_state(self):
        fulfill_request = self.service()
        for status in ("pending", "offered", "fulfilled"):
            if status != "pending":
                models.RequestOffer.objects.get_or_create(
                    request=self.food_request, offered_by=self.donor,
                )
            models.Request.objects.filter(pk=self.food_request.pk).update(status=status)
            for actor in (self.donor, self.other_user):
                with self.subTest(status=status, actor=actor.pk):
                    before = self.snapshot()
                    with self.assertRaises(PermissionDenied):
                        fulfill_request(actor=actor, request_id=self.food_request.pk)
                    self.assertEqual(self.snapshot(), before)

    def test_pending_request_cannot_be_fulfilled(self):
        fulfill_request = self.service()
        self.assertEqual(self.food_request.status, "pending")
        self.assertFalse(self.food_request.offers.exists())
        before = self.snapshot()
        with self.assertRaises(ValidationError) as caught:
            fulfill_request(actor=self.owner, request_id=self.food_request.pk)
        self.assertEqual(caught.exception.code, "offer_selection_required")
        self.assertEqual(self.snapshot(), before)

    def test_missing_request_raises_does_not_exist_without_writes(self):
        fulfill_request = self.service()
        missing_id = self.other_request.pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        before = self.snapshot()
        with self.assertRaises(models.Request.DoesNotExist):
            fulfill_request(actor=self.owner, request_id=missing_id)
        self.assertEqual(self.snapshot(), before)

    def test_reads_current_owner_and_status_from_database(self):
        fulfill_request = self.service()
        stale = self.make_request()
        self.prepare_offered(stale)
        stale.refresh_from_db()
        models.Request.objects.filter(pk=stale.pk).update(owner=self.donor)
        self.assertEqual(stale.owner_id, self.owner.pk)
        self.assertEqual(stale.status, "offered")
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            fulfill_request(actor=self.owner, request_id=stale.pk)
        self.assertEqual(self.snapshot(), before)

        stale = self.make_request()
        self.prepare_offered(stale)
        self.assertEqual(stale.status, "pending")
        before = self.snapshot()
        start = timezone.now()
        result = fulfill_request(actor=self.owner, request_id=stale.pk)
        end = timezone.now()
        self.assert_result(result, stale)
        self.assert_transition(before, target=stale, start=start, end=end)

        stale = self.make_request()
        self.prepare_offered(stale)
        stale.refresh_from_db()
        models.Request.objects.filter(pk=stale.pk).update(status="fulfilled")
        self.assertEqual(stale.status, "offered")
        before = self.snapshot()
        writes = []
        with connection.execute_wrapper(self.record_writes(writes)):
            result = fulfill_request(actor=self.owner, request_id=stale.pk)
        self.assert_result(result, stale)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(writes, [])

    def test_locks_request_inside_atomic_before_status_save(self):
        fulfill_request = self.service()
        self.prepare_offered()
        before = self.snapshot()
        manager = models.Request.objects
        real_select = manager.select_for_update
        real_get = QuerySet.get
        real_save = models.Request.save
        events = []

        def select(*args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            events.append("select")
            return real_select(*args, **kwargs)

        def get(queryset, *args, **kwargs):
            if queryset.model is models.Request:
                self.assertIn("select", events)
                self.assertTrue(queryset.query.select_for_update)
                self.assertTrue(connection.in_atomic_block)
                result = real_get(queryset, *args, **kwargs)
                self.assertEqual(result.pk, self.food_request.pk)
                events.append("locked")
                return result
            return real_get(queryset, *args, **kwargs)

        def save(food_request, *args, **kwargs):
            self.assertIn("locked", events)
            self.assertTrue(connection.in_atomic_block)
            self.assertEqual(food_request.pk, self.food_request.pk)
            self.assertEqual(food_request.status, "fulfilled")
            self.assertEqual(set(kwargs.get("update_fields", ())), {"status", "updated_at"})
            events.append("save")
            return real_save(food_request, *args, **kwargs)

        def check_offer_access(execute, sql, params, many, context):
            if models.RequestOffer._meta.db_table in sql:
                self.assertIn("locked", events)
            return execute(sql, params, many, context)

        start = timezone.now()
        with patch.object(manager, "select_for_update", side_effect=select), \
                patch.object(QuerySet, "get", new=get), \
                patch.object(models.Request, "save", new=save), \
                connection.execute_wrapper(check_offer_access):
            result = fulfill_request(actor=self.owner, request_id=self.food_request.pk)
        end = timezone.now()
        self.assertEqual(events, ["select", "locked", "save"])
        self.assert_result(result)
        self.assert_transition(before, target=self.food_request, start=start, end=end)

    def test_status_save_failure_rolls_back_entire_fulfillment(self):
        fulfill_request = self.service()
        self.prepare_offered()
        before = self.snapshot()
        real_save = models.Request.save
        sentinel = RuntimeError("fulfillment status sentinel")
        saved = []

        def save(food_request, *args, **kwargs):
            result = real_save(food_request, *args, **kwargs)
            if food_request.pk == self.food_request.pk and food_request.status == "fulfilled":
                self.assertEqual(models.Request.objects.get(pk=food_request.pk).status, "fulfilled")
                saved.append(food_request.pk)
                raise sentinel
            return result

        with patch.object(models.Request, "save", new=save):
            with self.assertRaises(RuntimeError) as caught:
                fulfill_request(actor=self.owner, request_id=self.food_request.pk)
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(saved, [self.food_request.pk])
        self.assertEqual(self.snapshot(), before)
        self.food_request.refresh_from_db()
        self.assertEqual(self.food_request.status, "offered")
        start = timezone.now()
        result = fulfill_request(actor=self.owner, request_id=self.food_request.pk)
        end = timezone.now()
        self.assert_result(result)
        self.assert_transition(before, target=self.food_request, start=start, end=end)

    def test_offer_after_fulfillment_rejects_new_and_repeated_donors(self):
        fulfill_request = self.service()
        self.prepare_offered()
        models.Request.objects.filter(pk=self.food_request.pk).update(selected_offer_id=self.food_request.offers.get(offered_by=self.donor).pk)
        self.assert_result(fulfill_request(actor=self.owner, request_id=self.food_request.pk))
        offer_request = import_module("request_board.services").offer_request
        for actor in (self.donor, self.other_user):
            with self.subTest(actor=actor.pk):
                before = self.snapshot()
                with self.assertRaises(ValidationError) as caught:
                    offer_request(actor=actor, request_id=self.food_request.pk)
                self.assertEqual(caught.exception.code, "request_fulfilled")
                self.assertEqual(self.snapshot(), before)


class RequestDeleteServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="delete_service_owner")
        cls.donor = User.objects.create_user(username="delete_service_donor")
        cls.other_user = User.objects.create_user(username="delete_service_other")

    def setUp(self):
        self.food_request = self.make_request()
        self.other_request = self.make_request(title="Posting lain", status="offered")

    def service(self):
        service = getattr(import_module("request_board.services"), "delete_request", None)
        self.assertTrue(callable(service), "delete_request belum callable")
        return service

    def make_request(self, **overrides):
        data = dict(
            owner=self.owner, title="Butuh nasi", food_item="Nasi",
            quantity=Decimal("2.50"), unit="porsi", description="Untuk makan",
            location="Depok", status="pending",
        )
        data.update(overrides)
        target = models.Request.objects.create(**data)
        old_time = timezone.datetime(2020, 1, 2, tzinfo=timezone.get_default_timezone())
        models.Request.objects.filter(pk=target.pk).update(
            created_at=old_time, updated_at=old_time,
        )
        if data["status"] != "pending":
            for donor in (self.donor, self.other_user):
                offer = models.RequestOffer.objects.create(request=target, offered_by=donor)
                models.RequestOffer.objects.filter(pk=offer.pk).update(created_at=old_time)
        target.refresh_from_db()
        return target

    def snapshot(self):
        return (
            list(models.Request.objects.order_by("pk").values()),
            list(models.RequestOffer.objects.order_by("pk").values()),
            list(get_user_model().objects.order_by("pk").values()),
        )

    def assert_deleted_only(self, before, request_id):
        expected = (
            [row for row in before[0] if row["id"] != request_id],
            [row for row in before[1] if row["request_id"] != request_id],
            before[2],
        )
        self.assertEqual(self.snapshot(), expected)

    def record_writes(self, writes):
        def record(execute, sql, params, many, context):
            if sql.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(sql)
            return execute(sql, params, many, context)
        return record

    def test_owner_deletes_request_and_its_offers_in_any_state(self):
        delete_request = self.service()
        for status in ("pending", "offered", "fulfilled"):
            with self.subTest(status=status):
                target = self.make_request(status=status)
                request_id = target.pk
                before = self.snapshot()
                self.assertIsNone(delete_request(actor=self.owner, request_id=request_id))
                self.assert_deleted_only(before, request_id)

    def test_nonowner_cannot_delete_in_any_state(self):
        delete_request = self.service()
        for status in ("pending", "offered", "fulfilled"):
            target = self.make_request(status=status)
            # The other user has no offer on this target; the donor does except pending.
            models.RequestOffer.objects.filter(request=target, offered_by=self.other_user).delete()
            for actor in (self.donor, self.other_user):
                with self.subTest(status=status, actor=actor.pk):
                    before = self.snapshot()
                    with self.assertRaises(PermissionDenied):
                        delete_request(actor=actor, request_id=target.pk)
                    self.assertEqual(self.snapshot(), before)

    def test_rejects_unauthenticated_actor_before_lookup_without_writes(self):
        delete_request = self.service()
        missing_id = self.other_request.pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        for actor in (None, AnonymousUser()):
            for request_id in (self.food_request.pk, missing_id):
                with self.subTest(actor=actor, request_id=request_id):
                    before = self.snapshot()
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        delete_request(actor=actor, request_id=request_id)
                    self.assertEqual(self.snapshot(), before)

    def test_rejects_unsaved_actor_before_lookup_without_writes(self):
        delete_request = self.service()
        User = get_user_model()
        missing_id = self.other_request.pk + 100
        missing_user_id = self.other_user.pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        self.assertFalse(User.objects.filter(pk=missing_user_id).exists())
        for pk in (None, missing_user_id, self.owner.pk):
            actor = User(username="unsaved_delete", pk=pk)
            self.assertTrue(actor.is_authenticated)
            self.assertTrue(actor._state.adding)
            for request_id in (self.food_request.pk, missing_id):
                with self.subTest(pk=pk, request_id=request_id):
                    before = self.snapshot()
                    with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
                        delete_request(actor=actor, request_id=request_id)
                    self.assertEqual(self.snapshot(), before)

    def test_missing_request_raises_does_not_exist_without_writes(self):
        delete_request = self.service()
        missing_id = self.other_request.pk + 100
        self.assertFalse(models.Request.objects.filter(pk=missing_id).exists())
        before = self.snapshot()
        writes = []
        with connection.execute_wrapper(self.record_writes(writes)):
            with self.assertRaises(models.Request.DoesNotExist):
                delete_request(actor=self.owner, request_id=missing_id)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(writes, [])

    def test_repeated_delete_raises_does_not_exist_without_further_writes(self):
        delete_request = self.service()
        target = self.make_request(status="offered")
        request_id = target.pk
        before = self.snapshot()
        self.assertIsNone(delete_request(actor=self.owner, request_id=request_id))
        self.assert_deleted_only(before, request_id)
        after_first = self.snapshot()
        writes = []
        with connection.execute_wrapper(self.record_writes(writes)):
            with self.assertRaises(models.Request.DoesNotExist):
                delete_request(actor=self.owner, request_id=request_id)
        self.assertEqual(self.snapshot(), after_first)
        self.assertEqual(writes, [])

    def test_reads_current_owner_from_database(self):
        delete_request = self.service()
        stale = self.make_request(status="offered")
        request_id = stale.pk
        models.Request.objects.filter(pk=request_id).update(owner=self.other_user)
        self.assertEqual(stale.owner_id, self.owner.pk)
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            delete_request(actor=self.owner, request_id=request_id)
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(delete_request(actor=self.other_user, request_id=request_id))
        self.assert_deleted_only(before, request_id)

    def test_locks_request_inside_atomic_before_deletion(self):
        delete_request = self.service()
        target = self.make_request(status="offered")
        request_id = target.pk
        before = self.snapshot()
        manager = models.Request.objects
        real_select = manager.select_for_update
        real_get = QuerySet.get
        real_delete = models.Request.delete
        events = []
        offer_accesses = []

        def select(*args, **kwargs):
            self.assertTrue(connection.in_atomic_block)
            events.append("select")
            return real_select(*args, **kwargs)

        def get(queryset, *args, **kwargs):
            if queryset.model is models.Request:
                self.assertIn("select", events)
                self.assertTrue(queryset.query.select_for_update)
                self.assertTrue(connection.in_atomic_block)
                result = real_get(queryset, *args, **kwargs)
                self.assertEqual(result.pk, request_id)
                events.append("locked")
                return result
            return real_get(queryset, *args, **kwargs)

        def delete(food_request, *args, **kwargs):
            self.assertIn("locked", events)
            self.assertTrue(connection.in_atomic_block)
            self.assertEqual(food_request.pk, request_id)
            events.append("delete")
            return real_delete(food_request, *args, **kwargs)

        def check_offer_access(execute, sql, params, many, context):
            if models.RequestOffer._meta.db_table in sql:
                self.assertIn("locked", events)
                self.assertTrue(connection.in_atomic_block)
                offer_accesses.append(sql)
            return execute(sql, params, many, context)

        with patch.object(manager, "select_for_update", side_effect=select), \
                patch.object(QuerySet, "get", new=get), \
                patch.object(models.Request, "delete", new=delete), \
                connection.execute_wrapper(check_offer_access):
            result = delete_request(actor=self.owner, request_id=request_id)
        self.assertIsNone(result)
        self.assertEqual(events, ["select", "locked", "delete"])
        self.assertTrue(offer_accesses)
        self.assert_deleted_only(before, request_id)

    def test_delete_failure_restores_request_and_cascaded_offers(self):
        delete_request = self.service()
        target = self.make_request(status="offered")
        request_id = target.pk
        before = self.snapshot()
        self.assertEqual(models.RequestOffer.objects.filter(request_id=request_id).count(), 2)
        real_delete = models.Request.delete
        sentinel = RuntimeError("delete cascade sentinel")
        deleted = []

        def delete(food_request, *args, **kwargs):
            self.assertEqual(food_request.pk, request_id)
            result = real_delete(food_request, *args, **kwargs)
            self.assertFalse(models.Request.objects.filter(pk=request_id).exists())
            self.assertFalse(models.RequestOffer.objects.filter(request_id=request_id).exists())
            deleted.append(request_id)
            raise sentinel

        with patch.object(models.Request, "delete", new=delete):
            with self.assertRaises(RuntimeError) as caught:
                delete_request(actor=self.owner, request_id=request_id)
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(deleted, [request_id])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(models.Request.objects.get(pk=request_id).status, "offered")
        self.assertIsNone(delete_request(actor=self.owner, request_id=request_id))
        self.assert_deleted_only(before, request_id)
