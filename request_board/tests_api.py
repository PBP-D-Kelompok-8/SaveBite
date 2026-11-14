from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
import hashlib
import json
from types import ModuleType
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, InvalidPage, PageNotAnInteger
from django.db import IntegrityError, connection
from django.http import Http404, QueryDict
from django.middleware.csrf import get_token
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path, resolve, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from request_board import api_urls
from request_board.api_exceptions import (
    CONFLICT_ERROR_CODES,
    ConflictError,
    handle_domain_exceptions,
    map_domain_exception,
    request_board_exception_handler,
)
from request_board.forms import RequestForm
from request_board.models import Request, RequestCreationKey, RequestOffer
from request_board.serializers import RequestCreateSerializer, RequestSerializer
from request_board.services import (
    create_request,
    create_request_from_api_idempotent,
    create_request_idempotent,
    delete_request,
)


class DRFFoundationTests(APITestCase):
    """Verify DRF installation, local settings isolation, and routing foundation."""

    def test_rest_framework_installed_without_global_policy_overrides(self):
        self.assertIn("rest_framework", settings.INSTALLED_APPS)
        global_drf_settings = getattr(settings, "REST_FRAMEWORK", {})
        self.assertNotIn("DEFAULT_AUTHENTICATION_CLASSES", global_drf_settings)
        self.assertNotIn("DEFAULT_PERMISSION_CLASSES", global_drf_settings)
        self.assertNotIn("EXCEPTION_HANDLER", global_drf_settings)

    def test_api_urls_namespace_mounted_at_api_v1_with_t03_routes(self):
        self.assertEqual(api_urls.app_name, "request_board_api")
        self.assertEqual(
            [pattern.name for pattern in api_urls.urlpatterns],
            ["request_list", "request_detail", "request_creation_status"],
        )
        self.assertEqual(reverse("request_board_api:request_list"), "/api/v1/requests/")
        self.assertEqual(
            reverse("request_board_api:request_detail", args=[42]),
            "/api/v1/requests/42/",
        )
        sample_key = uuid4()
        self.assertEqual(
            reverse("request_board_api:request_creation_status", args=[sample_key]),
            f"/api/v1/request-creations/{sample_key}/",
        )
        self.assertEqual(resolve("/api/v1/requests/").view_name, "request_board_api:request_list")
        self.assertEqual(
            resolve("/api/v1/requests/42/").view_name,
            "request_board_api:request_detail",
        )
        self.assertEqual(
            resolve(f"/api/v1/request-creations/{sample_key}/").view_name,
            "request_board_api:request_creation_status",
        )

        root_resolver = resolve("/api-auth/login/")
        self.assertEqual(root_resolver.view_name, "rest_framework:login")
        self.assertEqual(reverse("rest_framework:login"), "/api-auth/login/")

        from SaveBite import urls as root_urls

        api_v1_patterns = [
            pattern for pattern in root_urls.urlpatterns
            if str(pattern.pattern) == "api/v1/"
        ]
        self.assertEqual(len(api_v1_patterns), 1)
        self.assertEqual(api_v1_patterns[0].namespace, "request_board_api")
        self.assertEqual(api_v1_patterns[0].urlconf_module, api_urls)

    def test_existing_routes_remain_intact(self):
        self.assertEqual(resolve("/requests").view_name, "request_board:api_request_collection")
        self.assertEqual(resolve("/board/").view_name, "request_board:request_board")
        self.assertEqual(resolve("/login/").view_name, "login")
        self.assertEqual(resolve("/logout/").view_name, "logout")
        self.assertEqual(resolve("/register/").view_name, "register")


class DomainExceptionMappingTests(APITestCase):
    """Verify local 400/403/404/409 exception mapping and CSRF-enforced SessionAuthentication."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="foundation_tester",
            password="test-password-123",
        )

    def _dispatch_error(self, exc_to_raise):
        @api_view(["GET"])
        @authentication_classes([SessionAuthentication])
        @permission_classes([AllowAny])
        @handle_domain_exceptions
        def sample_view(request):
            raise exc_to_raise

        factory = APIRequestFactory()
        request = factory.get("/api/v1/_test-only/", HTTP_ACCEPT="application/json")
        return sample_view(request)

    def test_validation_field_errors_map_to_400(self):
        exc = ValidationError({
            "title": ["Kolom ini wajib diisi."],
            "quantity": ["Pastikan nilai minimal 0.01."],
        })
        response = self._dispatch_error(exc)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response.render()
        self.assertEqual(
            response.data,
            {
                "title": ["Kolom ini wajib diisi."],
                "quantity": ["Pastikan nilai minimal 0.01."],
            },
        )

    def test_validation_non_field_and_pagination_errors_map_to_400(self):
        for exc, expected_detail in (
            (ValidationError("Input tidak valid."), "Input tidak valid."),
            (ValidationError({"__all__": ["Kombinasi filter tidak valid."]}), "Kombinasi filter tidak valid."),
            (PageNotAnInteger("Nomor halaman harus berupa bilangan bulat positif."), "Nomor halaman harus berupa bilangan bulat positif."),
            (InvalidPage("Nomor halaman tidak valid."), "Nomor halaman tidak valid."),
        ):
            with self.subTest(exc=type(exc).__name__):
                response = self._dispatch_error(exc)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data, {"detail": expected_detail})

    def test_permission_denied_maps_to_403_with_message(self):
        response = self._dispatch_error(
            PermissionDenied("Hanya pemilik request yang dapat menghapus request.")
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.data,
            {"detail": "Hanya pemilik request yang dapat menghapus request."},
        )

    def test_not_found_exceptions_map_to_404(self):
        for exc, expected_detail in (
            (Request.DoesNotExist(), "Request tidak ditemukan."),
            (RequestOffer.DoesNotExist(), "Penawaran tidak ditemukan."),
            (ObjectDoesNotExist(), "Data tidak ditemukan."),
            (EmptyPage("Halaman yang diminta tidak ditemukan."), "Halaman yang diminta tidak ditemukan."),
            (Http404("Objek tidak ditemukan."), "Objek tidak ditemukan."),
        ):
            with self.subTest(exc=type(exc).__name__):
                response = self._dispatch_error(exc)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(response.data, {"detail": expected_detail})

    def test_domain_conflict_codes_map_to_409_with_detail_and_code(self):
        expected_codes = {
            "request_expired",
            "request_fulfilled",
            "offers_closed",
            "offer_selection_required",
            "idempotency_conflict",
            "idempotency_deleted",
        }
        self.assertEqual(CONFLICT_ERROR_CODES, expected_codes)

        for code in sorted(CONFLICT_ERROR_CODES):
            with self.subTest(code=code):
                response = self._dispatch_error(
                    ValidationError(f"Konflik untuk {code}.", code=code)
                )
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(
                    response.data,
                    {"detail": f"Konflik untuk {code}.", "code": code},
                )

    def test_conflict_error_direct_handler_returns_409(self):
        exc = ConflictError("Permintaan sudah kedaluwarsa.", code="request_expired")
        response = request_board_exception_handler(exc, context={})
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data,
            {"detail": "Permintaan sudah kedaluwarsa.", "code": "request_expired"},
        )

    def test_unmapped_unexpected_exceptions_are_not_swallowed(self):
        unexpected = RuntimeError("Unexpected failure")
        self.assertIsNone(map_domain_exception(unexpected))
        self.assertIsNone(request_board_exception_handler(unexpected, context={}))
        with self.assertRaises(RuntimeError):
            self._dispatch_error(unexpected)

    def test_session_authentication_enforces_csrf_and_returns_403_for_anonymous(self):
        @api_view(["POST"])
        @authentication_classes([SessionAuthentication])
        @permission_classes([IsAuthenticated])
        @handle_domain_exceptions
        def protected_mutation(request):
            return Response({"ok": True}, status=status.HTTP_200_OK)

        ephemeral_urlconf = ModuleType("_ephemeral_api_urls")
        ephemeral_urlconf.urlpatterns = [
            path("api/v1/_ephemeral-check/", protected_mutation),
        ]

        with override_settings(ROOT_URLCONF=ephemeral_urlconf):
            client = APIClient(enforce_csrf_checks=True)

            anon_response = client.post(
                "/api/v1/_ephemeral-check/",
                data={},
                format="json",
            )
            self.assertEqual(anon_response.status_code, status.HTTP_403_FORBIDDEN)

            self.assertTrue(client.login(username="foundation_tester", password="test-password-123"))

            missing_csrf_response = client.post(
                "/api/v1/_ephemeral-check/",
                data={},
                format="json",
            )
            self.assertEqual(missing_csrf_response.status_code, status.HTTP_403_FORBIDDEN)
            self.assertIn("CSRF", str(missing_csrf_response.json().get("detail", "")))

            csrf_request = RequestFactory().get("/")
            csrf_token = get_token(csrf_request)
            client.cookies["csrftoken"] = csrf_request.META["CSRF_COOKIE"]

            valid_csrf_response = client.post(
                "/api/v1/_ephemeral-check/",
                data={},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.assertEqual(valid_csrf_response.status_code, status.HTTP_200_OK)
            self.assertEqual(valid_csrf_response.json(), {"ok": True})



class RequestReadAndPrivacyAPITests(APITestCase):
    """Verify public read, privacy guarantees, effective_status, and constant query count."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.password = "api-test-password-123"
        cls.owner = User.objects.create_user(
            username="api_owner",
            password=cls.password,
            email="owner-secret@example.invalid",
            first_name="OwnerSecretFirst",
            last_name="OwnerSecretLast",
        )
        cls.donor = User.objects.create_user(
            username="api_donor_one",
            password=cls.password,
            email="donor-secret@example.invalid",
            first_name="DonorSecretFirst",
            last_name="DonorSecretLast",
        )
        cls.other_donor = User.objects.create_user(
            username="api_donor_two",
            password=cls.password,
            email="donor-two@example.invalid",
        )
        cls.outsider = User.objects.create_user(
            username="api_outsider",
            password=cls.password,
            email="outsider@example.invalid",
        )
        cls.superuser = User.objects.create_superuser(
            username="api_superuser",
            password=cls.password,
            email="superuser@example.invalid",
        )

    def setUp(self):
        self.list_url = reverse("request_board_api:request_list")

    def make_request(self, **overrides):
        values = {
            "owner": self.owner,
            "title": "Butuh beras",
            "food_item": "Beras",
            "quantity": Decimal("2.50"),
            "unit": "kg",
            "description": "Untuk makan bersama",
            "location": "Depok",
            "status": Request.STATUS_PENDING,
            "expires_at": timezone.now() + timedelta(hours=24),
        }
        values.update(overrides)
        return Request.objects.create(**values)

    def assert_safe_request_payload(
        self,
        item: dict,
        food_request: Request,
        *,
        expected_offer_count: int,
        expected_has_offered: bool,
        expected_selected_offer_id: int | None,
        expected_effective_status: str,
    ):
        self.assertEqual(
            set(item.keys()),
            {
                "id",
                "owner",
                "title",
                "food_item",
                "quantity",
                "unit",
                "description",
                "location",
                "status",
                "effective_status",
                "expires_at",
                "offer_count",
                "has_offered",
                "selected_offer_id",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(item["id"], food_request.pk)
        self.assertEqual(
            item["owner"],
            {"id": food_request.owner_id, "display_name": food_request.owner.get_username()},
        )
        self.assertEqual(item["title"], food_request.title)
        self.assertEqual(item["food_item"], food_request.food_item)
        self.assertEqual(item["quantity"], format(food_request.quantity, ".2f"))
        self.assertEqual(item["unit"], food_request.unit)
        self.assertEqual(item["description"], food_request.description)
        self.assertEqual(item["location"], food_request.location)
        self.assertEqual(item["status"], food_request.status)
        self.assertEqual(item["effective_status"], expected_effective_status)
        self.assertEqual(item["offer_count"], expected_offer_count)
        self.assertIs(item["has_offered"], expected_has_offered)
        self.assertEqual(item["selected_offer_id"], expected_selected_offer_id)

        for field in ("created_at", "updated_at"):
            parsed = parse_datetime(item[field])
            self.assertIsNotNone(parsed)
            self.assertIsNotNone(parsed.utcoffset())
            self.assertEqual(parsed, getattr(food_request, field))

        if food_request.expires_at is None:
            self.assertIsNone(item["expires_at"])
        else:
            parsed_exp = parse_datetime(item["expires_at"])
            self.assertIsNotNone(parsed_exp)
            self.assertIsNotNone(parsed_exp.utcoffset())
            self.assertEqual(parsed_exp, food_request.expires_at)

    def assert_no_sensitive_leaks(self, raw_body: str):
        for secret in (
            self.owner.email,
            self.owner.first_name,
            self.owner.last_name,
            self.owner.password,
            self.donor.get_username(),
            self.donor.email,
            self.donor.first_name,
            self.donor.last_name,
            self.other_donor.get_username(),
            self.other_donor.email,
        ):
            self.assertNotIn(secret, raw_body)

    def test_public_list_and_detail_with_privacy_and_computed_fields(self):
        offered_req = self.make_request(status=Request.STATUS_OFFERED)
        offer_one = RequestOffer.objects.create(request=offered_req, offered_by=self.donor)
        RequestOffer.objects.create(request=offered_req, offered_by=self.other_donor)
        offered_req.selected_offer = offer_one
        offered_req.save(update_fields=["selected_offer"])

        expired_req = self.make_request(
            title="Telat",
            status=Request.STATUS_OFFERED,
            expires_at=timezone.now() - timedelta(minutes=5),
        )
        fulfilled_past_req = self.make_request(
            title="Selesai",
            status=Request.STATUS_FULFILLED,
            expires_at=timezone.now() - timedelta(days=1),
        )
        legacy_no_expiry_req = self.make_request(
            title="Legacy tanpa tenggat",
            expires_at=None,
        )

        detail_url = reverse("request_board_api:request_detail", args=[offered_req.pk])

        viewer_cases = (
            ("anonymous", None, False, None),
            ("donor", self.donor, True, None),
            ("outsider", self.outsider, False, None),
            ("superuser_non_owner", self.superuser, False, None),
            ("owner", self.owner, False, offer_one.pk),
        )

        for label, user, expected_has_offered, expected_selected_id in viewer_cases:
            with self.subTest(viewer=label):
                client = APIClient()
                if user is not None:
                    client.force_authenticate(user=user)

                list_resp = client.get(self.list_url)
                self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
                list_payload = list_resp.json()
                self.assertEqual(
                    set(list_payload.keys()),
                    {"count", "page", "page_size", "total_pages", "results"},
                )
                self.assertEqual(list_payload["count"], 4)
                self.assertEqual(list_payload["page"], 1)
                self.assertEqual(list_payload["page_size"], 20)
                self.assertEqual(list_payload["total_pages"], 1)

                by_id = {item["id"]: item for item in list_payload["results"]}
                self.assert_safe_request_payload(
                    by_id[offered_req.pk],
                    offered_req,
                    expected_offer_count=2,
                    expected_has_offered=expected_has_offered,
                    expected_selected_offer_id=expected_selected_id,
                    expected_effective_status=Request.STATUS_OFFERED,
                )
                self.assert_safe_request_payload(
                    by_id[expired_req.pk],
                    expired_req,
                    expected_offer_count=0,
                    expected_has_offered=False,
                    expected_selected_offer_id=None,
                    expected_effective_status=Request.STATUS_EXPIRED,
                )
                self.assert_safe_request_payload(
                    by_id[fulfilled_past_req.pk],
                    fulfilled_past_req,
                    expected_offer_count=0,
                    expected_has_offered=False,
                    expected_selected_offer_id=None,
                    expected_effective_status=Request.STATUS_FULFILLED,
                )
                self.assert_safe_request_payload(
                    by_id[legacy_no_expiry_req.pk],
                    legacy_no_expiry_req,
                    expected_offer_count=0,
                    expected_has_offered=False,
                    expected_selected_offer_id=None,
                    expected_effective_status=Request.STATUS_PENDING,
                )
                self.assert_no_sensitive_leaks(list_resp.content.decode("utf-8"))

                detail_resp = client.get(detail_url)
                self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
                self.assert_safe_request_payload(
                    detail_resp.json(),
                    offered_req,
                    expected_offer_count=2,
                    expected_has_offered=expected_has_offered,
                    expected_selected_offer_id=expected_selected_id,
                    expected_effective_status=Request.STATUS_OFFERED,
                )
                self.assert_no_sensitive_leaks(detail_resp.content.decode("utf-8"))

    def test_detail_missing_returns_404(self):
        response = self.client.get(reverse("request_board_api:request_detail", args=[999999]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json(), {"detail": "Request tidak ditemukan."})

    def test_list_query_count_stays_constant_from_1_to_20_cards(self):
        first = self.make_request(status=Request.STATUS_OFFERED)
        RequestOffer.objects.create(request=first, offered_by=self.donor)
        RequestOffer.objects.create(request=first, offered_by=self.other_donor)

        clients = {
            "anonymous": APIClient(),
            "donor": APIClient(),
        }
        clients["donor"].force_authenticate(user=self.donor)

        query_counts = {}
        for size in (1, 20):
            if size == 20:
                for idx in range(19):
                    item = self.make_request(
                        title=f"Kartu {idx}",
                        owner=self.outsider if idx % 2 == 0 else self.owner,
                        status=Request.STATUS_OFFERED if idx < 5 else Request.STATUS_PENDING,
                    )
                    if idx < 5:
                        RequestOffer.objects.create(request=item, offered_by=self.donor)
                        RequestOffer.objects.create(request=item, offered_by=self.other_donor)
            for label, client in clients.items():
                with self.subTest(size=size, caller=label):
                    with CaptureQueriesContext(connection) as queries:
                        response = client.get(self.list_url)
                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.assertEqual(len(response.json()["results"]), size)
                    query_counts[(label, size)] = len(queries)

        for label in clients:
            self.assertEqual(query_counts[(label, 1)], query_counts[(label, 20)])


class RequestListFilterAndPaginationAPITests(APITestCase):
    """Verify filters, sorting, pagination, and error codes on GET /api/v1/requests/."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(username="filter_api_owner")
        cls.other = User.objects.create_user(username="filter_api_other")

    def setUp(self):
        self.list_url = reverse("request_board_api:request_list")

    def item(self, **overrides):
        values = {
            "owner": self.owner,
            "title": "Permintaan",
            "food_item": "Roti",
            "quantity": Decimal("1.00"),
            "unit": "porsi",
            "location": "Jakarta",
            "status": Request.STATUS_PENDING,
            "expires_at": timezone.now() + timedelta(days=1),
        }
        values.update(overrides)
        return Request.objects.create(**values)

    def test_combined_filters_and_stable_ordering(self):
        first = self.item(title="Bantuan Nasi satu", food_item="Nasi", status="offered", location="Depok")
        second = self.item(title="Bantuan Nasi dua", food_item="nAsI", status="offered", location="DEPOK")
        self.item(title="Bantuan Nasi", food_item="Nasi putih", status="offered", location="Depok")
        self.item(title="Bantuan lain", food_item="Nasi", status="offered", location="Depok")
        self.item(title="Bantuan Nasi", food_item="Nasi", status="pending", location="Depok")
        self.item(title="Bantuan Nasi", food_item="Nasi", status="offered", location="Bogor")
        self.item(owner=self.other, title="Bantuan Nasi", food_item="Nasi", status="offered", location="Depok")

        self.client.force_authenticate(user=self.owner)
        params = {
            "q": "nasi",
            "title": "bantuan nasi",
            "food_item": " NASI ",
            "location": "depok",
            "status": "offered",
            "mine": "true",
        }
        newest = self.client.get(self.list_url, {**params, "sort": "newest"})
        self.assertEqual(newest.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in newest.json()["results"]], [second.pk, first.pk])

        oldest = self.client.get(self.list_url, {**params, "sort": "oldest"})
        self.assertEqual(oldest.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in oldest.json()["results"]], [first.pk, second.pk])

    def test_mine_requires_login_with_403(self):
        self.item()
        response = self.client.get(self.list_url, {"mine": "true"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", response.json())

    def test_status_expired_filter_matches_only_non_fulfilled_expired(self):
        expired = self.item(status="offered", expires_at=timezone.now() - timedelta(minutes=1))
        self.item(status="offered", expires_at=timezone.now() + timedelta(days=1))
        self.item(status="fulfilled", expires_at=timezone.now() - timedelta(days=1))

        resp_expired = self.client.get(self.list_url, {"status": "expired"})
        self.assertEqual(resp_expired.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in resp_expired.json()["results"]], [expired.pk])

        resp_offered = self.client.get(self.list_url, {"status": "offered"})
        self.assertEqual(resp_offered.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_offered.json()["count"], 1)

    def test_pagination_splits_21_items_into_20_and_1(self):
        rows = [self.item(title=f"Item {idx}") for idx in range(21)]
        page1 = self.client.get(self.list_url)
        page2 = self.client.get(self.list_url, {"page": "2"})

        self.assertEqual(page1.status_code, status.HTTP_200_OK)
        self.assertEqual(page1.json()["count"], 21)
        self.assertEqual(page1.json()["page"], 1)
        self.assertEqual(page1.json()["page_size"], 20)
        self.assertEqual(page1.json()["total_pages"], 2)
        self.assertEqual([row["id"] for row in page1.json()["results"]], [r.pk for r in reversed(rows[1:])])

        self.assertEqual(page2.status_code, status.HTTP_200_OK)
        self.assertEqual(page2.json()["page"], 2)
        self.assertEqual([row["id"] for row in page2.json()["results"]], [rows[0].pk])

        leading_zero = self.client.get(self.list_url, {"page": "0" * 5000 + "1"})
        self.assertEqual(leading_zero.status_code, status.HTTP_200_OK)
        self.assertEqual(leading_zero.json()["page"], 1)

    def test_invalid_filters_and_pagination_errors(self):
        self.item()
        for invalid_params, field_name in (
            ({"status": "unknown"}, "status"),
            ({"sort": "random"}, "sort"),
            ({"q": "x" * 121}, "q"),
            ({"title": "x" * 121}, "title"),
            ({"food_item": "x" * 121}, "food_item"),
            ({"location": "x" * 121}, "location"),
        ):
            with self.subTest(invalid_params=invalid_params):
                resp = self.client.get(self.list_url, invalid_params)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field_name, resp.json())

        for invalid_page in ("", "0", "000", "-1", "abc", "1.0", "١", " 1"):
            with self.subTest(invalid_page=repr(invalid_page)):
                resp = self.client.get(self.list_url, {"page": invalid_page})
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", resp.json())

        for repeated_query in ("page=1&page=1", "page=1&page=2"):
            with self.subTest(repeated_query=repeated_query):
                resp = self.client.get(f"{self.list_url}?{repeated_query}")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", resp.json())

        for out_of_range in ("2", "9" * 5000):
            with self.subTest(out_of_range=out_of_range):
                resp = self.client.get(self.list_url, {"page": out_of_range})
                self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
                self.assertIn("detail", resp.json())


class RequestCreateSerializerAndAlignmentTests(APITestCase):
    """Verify RequestCreateSerializer contracts for T03 and RequestForm strip_tags alignment."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="serializer_actor")

    def valid_payload(self, **overrides):
        data = {
            "title": "Butuh beras",
            "food_item": "Beras",
            "quantity": "2.50",
            "unit": "kg",
            "description": "Untuk makan bersama",
            "location": "Depok",
            "expires_at": (timezone.now() + timedelta(hours=12)).isoformat(),
        }
        data.update(overrides)
        return data

    def test_valid_payload_returns_clean_validated_data(self):
        payload = self.valid_payload(
            title="  <b>Butuh</b> beras  ",
            food_item=" <i>Beras</i> merah ",
            description=" <p>Untuk kegiatan</p> ",
            location=" <span>Depok</span> ",
            expires_at="2030-01-01T10:00:00+07:00",
        )
        serializer = RequestCreateSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["title"], "Butuh beras")
        self.assertEqual(serializer.validated_data["food_item"], "Beras merah")
        self.assertEqual(serializer.validated_data["description"], "Untuk kegiatan")
        self.assertEqual(serializer.validated_data["location"], "Depok")
        self.assertEqual(serializer.validated_data["quantity"], Decimal("2.50"))
        self.assertEqual(serializer.validated_data["unit"], "kg")
        self.assertEqual(
            serializer.validated_data["expires_at"].astimezone(dt_timezone.utc),
            datetime(2030, 1, 1, 3, 0, tzinfo=dt_timezone.utc),
        )

    def test_omitted_optional_fields_and_expires_at_default_24_hours(self):
        payload = {
            "title": "Beras",
            "food_item": "Beras",
            "quantity": "0.01",
            "unit": "porsi",
        }
        before = timezone.now()
        serializer = RequestCreateSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        after = timezone.now()
        self.assertEqual(serializer.validated_data["description"], "")
        self.assertEqual(serializer.validated_data["location"], "")
        self.assertEqual(serializer.validated_data["quantity"], Decimal("0.01"))
        self.assertGreaterEqual(serializer.validated_data["expires_at"], before + timedelta(hours=24))
        self.assertLessEqual(serializer.validated_data["expires_at"], after + timedelta(hours=24))

    def test_rejects_unknown_or_spoofed_fields(self):
        for bad_field, value in (
            ("owner", 99),
            ("owner_id", 99),
            ("status", "fulfilled"),
            ("effective_status", "fulfilled"),
            ("selected_offer", 1),
            ("selected_offer_id", 1),
            ("offer_count", 10),
            ("has_offered", True),
            ("id", 999),
            ("created_at", "2020-01-01T00:00:00Z"),
            ("updated_at", "2020-01-01T00:00:00Z"),
            ("unexpected", "extra"),
        ):
            with self.subTest(bad_field=bad_field):
                payload = self.valid_payload(**{bad_field: value})
                serializer = RequestCreateSerializer(data=payload)
                self.assertFalse(serializer.is_valid())
                self.assertIn(bad_field, serializer.errors)

    def test_rejects_non_object_payload(self):
        for bad_data in ([], "text", 123, None, True):
            with self.subTest(bad_data=bad_data):
                serializer = RequestCreateSerializer(data=bad_data)
                self.assertFalse(serializer.is_valid())

    def test_rejects_invalid_text_quantity_unit_and_expires_at(self):
        invalid_cases = [
            ("blank_title", {"title": "   "}, "title"),
            ("html_only_title", {"title": "  <b> </b>  "}, "title"),
            ("html_only_food_item", {"food_item": "<script></script>"}, "food_item"),
            ("null_char_title", {"title": "Beras\x00"}, "title"),
            ("non_string_title", {"title": 123}, "title"),
            ("long_title", {"title": "a" * 121}, "title"),
            ("long_food_item", {"food_item": "a" * 121}, "food_item"),
            ("long_location", {"location": "a" * 121}, "location"),
            ("long_description", {"description": "a" * 1001}, "description"),
            ("zero_quantity", {"quantity": "0"}, "quantity"),
            ("negative_quantity", {"quantity": "-0.01"}, "quantity"),
            ("too_many_decimals", {"quantity": "0.001"}, "quantity"),
            ("too_many_digits", {"quantity": "1000000.00"}, "quantity"),
            ("bool_quantity", {"quantity": True}, "quantity"),
            ("invalid_unit", {"unit": "karung"}, "unit"),
            ("uppercase_unit", {"unit": "KG"}, "unit"),
            ("explicit_null_expires_at", {"expires_at": None}, "expires_at"),
            ("explicit_empty_expires_at", {"expires_at": ""}, "expires_at"),
            ("past_expires_at", {"expires_at": (timezone.now() - timedelta(minutes=1)).isoformat()}, "expires_at"),
        ]
        for label, override, expected_field in invalid_cases:
            with self.subTest(case=label):
                serializer = RequestCreateSerializer(data=self.valid_payload(**override))
                self.assertFalse(serializer.is_valid())
                self.assertIn(expected_field, serializer.errors)

    def test_form_and_service_align_strip_tags_without_breaking_idempotent_retry(self):
        form = RequestForm(
            data={
                "title": "<b></b>",
                "food_item": "Beras",
                "quantity": "1.00",
                "unit": "kg",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

        qd = QueryDict(mutable=True)
        qd.update(
            {
                "title": "  <b>Beras</b>   segar  ",
                "food_item": " <i>Beras</i> ",
                "quantity": "1.00",
                "unit": "kg",
                "description": " <p>Detail</p> ",
                "location": " <span>Depok</span> ",
            }
        )
        key = uuid4()
        first = create_request_idempotent(actor=self.actor, data=qd, key=key)
        second = create_request_idempotent(actor=self.actor, data=qd, key=key)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.title, "Beras   segar")
        self.assertEqual(first.food_item, "Beras")
        self.assertEqual(first.description, "Detail")
        self.assertEqual(first.location, "Depok")


class RequestMethodAndDeleteAPITests(APITestCase):
    """Verify disallowed methods and DELETE owner-only + CSRF + cascade enforcement."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.password = "delete-test-password-123"
        cls.owner = User.objects.create_user(username="del_owner", password=cls.password)
        cls.other_user = User.objects.create_user(username="del_other", password=cls.password)
        cls.donor = User.objects.create_user(username="del_donor", password=cls.password)
        cls.superuser = User.objects.create_superuser(
            username="del_superuser",
            password=cls.password,
            email="del-su@example.invalid",
        )

    def setUp(self):
        self.list_url = reverse("request_board_api:request_list")

    def database_snapshot(self):
        return {
            "requests": list(Request.objects.order_by("pk").values()),
            "offers": list(RequestOffer.objects.order_by("pk").values()),
            "keys": list(RequestCreationKey.objects.order_by("pk").values()),
        }

    def make_target(self, owner=None, **overrides):
        values = {
            "owner": owner or self.owner,
            "title": "Target",
            "food_item": "Beras",
            "quantity": Decimal("1.50"),
            "unit": "kg",
            "status": Request.STATUS_OFFERED,
        }
        values.update(overrides)
        return Request.objects.create(**values)

    def test_disallowed_methods_return_403_for_anon_and_405_for_auth_without_db_change(self):
        target = self.make_target()
        detail_url = reverse("request_board_api:request_detail", args=[target.pk])
        status_url = reverse("request_board_api:request_creation_status", args=[uuid4()])

        for actor in (None, self.owner):
            client = APIClient()
            if actor is not None:
                client.force_authenticate(user=actor)

            before = self.database_snapshot()
            for method in ("put", "patch", "delete"):
                with self.subTest(endpoint="list", actor=actor, method=method):
                    resp = getattr(client, method)(self.list_url, data={}, format="json")
                    expected_status = (
                        status.HTTP_403_FORBIDDEN
                        if actor is None
                        else status.HTTP_405_METHOD_NOT_ALLOWED
                    )
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertEqual(self.database_snapshot(), before)

            for method in ("post", "put", "patch"):
                with self.subTest(endpoint="detail", actor=actor, method=method):
                    resp = getattr(client, method)(detail_url, data={}, format="json")
                    # DRF checks has_permission before method dispatch: anonymous unsafe -> 403, authenticated -> 405.
                    expected_status = (
                        status.HTTP_403_FORBIDDEN
                        if actor is None
                        else status.HTTP_405_METHOD_NOT_ALLOWED
                    )
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertEqual(self.database_snapshot(), before)

            for method in ("post", "put", "patch", "delete"):
                with self.subTest(endpoint="creation_status", actor=actor, method=method):
                    resp = getattr(client, method)(status_url, data={}, format="json")
                    expected_status = (
                        status.HTTP_403_FORBIDDEN
                        if actor is None
                        else status.HTTP_405_METHOD_NOT_ALLOWED
                    )
                    self.assertEqual(resp.status_code, expected_status)
                    self.assertEqual(self.database_snapshot(), before)

    def test_delete_rejects_anonymous_other_user_and_superuser_without_db_change(self):
        target = self.make_target()
        RequestOffer.objects.create(request=target, offered_by=self.donor)
        detail_url = reverse("request_board_api:request_detail", args=[target.pk])
        missing_url = reverse("request_board_api:request_detail", args=[target.pk + 999])

        before = self.database_snapshot()
        anon_client = APIClient()
        for url in (detail_url, missing_url):
            resp = anon_client.delete(url)
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.database_snapshot(), before)

        for non_owner in (self.other_user, self.donor, self.superuser):
            with self.subTest(non_owner=non_owner.username):
                client = APIClient()
                client.force_authenticate(user=non_owner)
                resp = client.delete(detail_url)
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(self.database_snapshot(), before)

                with self.assertRaises(PermissionDenied):
                    delete_request(actor=non_owner, request_id=target.pk)
                self.assertEqual(self.database_snapshot(), before)

    def test_owner_delete_enforces_csrf_and_returns_204_empty_body(self):
        target = self.make_target()
        unrelated = self.make_target(owner=self.other_user, title="Milik lain")
        RequestOffer.objects.create(request=target, offered_by=self.donor)
        unrelated_offer = RequestOffer.objects.create(request=unrelated, offered_by=self.donor)
        key_record = RequestCreationKey.objects.create(
            owner=self.owner,
            key=uuid4(),
            payload_digest=hashlib.sha256(b"test").hexdigest(),
            request=target,
        )
        detail_url = reverse("request_board_api:request_detail", args=[target.pk])

        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=self.owner.username, password=self.password))

        before = self.database_snapshot()
        no_csrf = client.delete(detail_url)
        self.assertEqual(no_csrf.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.database_snapshot(), before)

        csrf_request = RequestFactory().get("/")
        csrf_token = get_token(csrf_request)
        client.cookies["csrftoken"] = csrf_request.META["CSRF_COOKIE"]

        non_empty_body = client.generic(
            "DELETE",
            detail_url,
            data=json.dumps({"unexpected": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(non_empty_body.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.database_snapshot(), before)

        response = client.delete(detail_url, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(response.content, b"")
        self.assertFalse(Request.objects.filter(pk=target.pk).exists())
        self.assertFalse(RequestOffer.objects.filter(request_id=target.pk).exists())

        key_record.refresh_from_db()
        self.assertIsNone(key_record.request_id)

        self.assertTrue(Request.objects.filter(pk=unrelated.pk).exists())
        self.assertTrue(RequestOffer.objects.filter(pk=unrelated_offer.pk).exists())

        repeat = client.delete(detail_url, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(repeat.status_code, status.HTTP_404_NOT_FOUND)


_UNSET = object()


class RequestIdempotentCreateAPITests(APITestCase):
    """Verify POST /api/v1/requests/ and GET /api/v1/request-creations/<key>/ contracts."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.password = "create-api-password-123"
        cls.owner = User.objects.create_user(username="create_owner", password=cls.password)
        cls.other_user = User.objects.create_user(username="create_other", password=cls.password)
        cls.donor = User.objects.create_user(username="create_donor", password=cls.password)
        cls.superuser = User.objects.create_superuser(
            username="create_superuser",
            password=cls.password,
            email="create-su@example.invalid",
        )

    def setUp(self):
        self.list_url = reverse("request_board_api:request_list")

    def status_url(self, key):
        return reverse("request_board_api:request_creation_status", args=[key])

    def valid_payload(self, **overrides):
        data = {
            "title": "Butuh beras",
            "food_item": "Beras",
            "quantity": "2.50",
            "unit": "kg",
            "description": "Untuk makan bersama",
            "location": "Depok",
            "expires_at": (timezone.now() + timedelta(hours=12)).isoformat(),
        }
        data.update(overrides)
        return data

    def auth_client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def post_create(self, client, payload=_UNSET, *, key=None, **extra):
        headers = {}
        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = str(key)
        headers.update(extra)
        return client.post(
            self.list_url,
            data=self.valid_payload() if payload is _UNSET else payload,
            format="json",
            **headers,
        )

    def test_create_and_replay_return_201_then_200_for_same_record(self):
        key = uuid4()
        client = self.auth_client(self.owner)
        payload = self.valid_payload()

        first = self.post_create(client, payload, key=key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        first_body = first.json()
        self.assertEqual(first_body["owner"], {"id": self.owner.pk, "display_name": self.owner.username})
        self.assertEqual(first_body["status"], Request.STATUS_PENDING)
        self.assertEqual(first_body["effective_status"], Request.STATUS_PENDING)
        self.assertEqual(first_body["quantity"], "2.50")
        self.assertEqual(first_body["offer_count"], 0)
        self.assertIs(first_body["has_offered"], False)
        self.assertIsNone(first_body["selected_offer_id"])
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestCreationKey.objects.count(), 1)

        RequestOffer.objects.create(request_id=first_body["id"], offered_by=self.donor)
        Request.objects.filter(pk=first_body["id"]).update(status=Request.STATUS_OFFERED)

        second = self.post_create(client, payload, key=key)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        second_body = second.json()
        self.assertEqual(second_body["id"], first_body["id"])
        self.assertEqual(second_body["status"], Request.STATUS_OFFERED)
        self.assertEqual(second_body["offer_count"], 1)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestCreationKey.objects.count(), 1)

    def test_same_key_with_different_payload_returns_409_conflict(self):
        key = uuid4()
        client = self.auth_client(self.owner)
        payload = self.valid_payload()

        first = self.post_create(client, payload, key=key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        for changed in (
            {**payload, "title": "Butuh roti"},
            {**payload, "quantity": "3.00"},
            {**payload, "description": "Deskripsi berbeda"},
        ):
            with self.subTest(changed=changed["title"]):
                conflict = self.post_create(client, changed, key=key)
                self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(conflict.json()["code"], "idempotency_conflict")
                self.assertIn("detail", conflict.json())
                self.assertEqual(Request.objects.count(), 1)

        omitted_expiry_key = uuid4()
        minimal = {"title": "Beras", "food_item": "Beras", "quantity": "1", "unit": "kg"}
        self.assertEqual(self.post_create(client, minimal, key=omitted_expiry_key).status_code, status.HTTP_201_CREATED)
        mismatch_expiry = self.post_create(
            client,
            {**minimal, "expires_at": (timezone.now() + timedelta(hours=6)).isoformat()},
            key=omitted_expiry_key,
        )
        self.assertEqual(mismatch_expiry.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(mismatch_expiry.json()["code"], "idempotency_conflict")

    def test_deleted_record_replay_returns_409_and_does_not_recreate(self):
        key = uuid4()
        client = self.auth_client(self.owner)
        payload = self.valid_payload()

        created = self.post_create(client, payload, key=key)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        request_id = created.json()["id"]

        deleted = client.delete(reverse("request_board_api:request_detail", args=[request_id]))
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Request.objects.count(), 0)

        replay_same = self.post_create(client, payload, key=key)
        self.assertEqual(replay_same.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(replay_same.json()["code"], "idempotency_deleted")
        self.assertEqual(Request.objects.count(), 0)

        replay_different = self.post_create(client, {**payload, "title": "Baru"}, key=key)
        self.assertEqual(replay_different.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(replay_different.json()["code"], "idempotency_conflict")
        self.assertEqual(Request.objects.count(), 0)

    def test_missing_or_invalid_idempotency_key_returns_400_without_db_writes(self):
        client = self.auth_client(self.owner)
        payload = self.valid_payload()

        for bad_key in (None, "", "   ", "not-a-uuid", "12345"):
            with self.subTest(bad_key=bad_key):
                resp = self.post_create(client, payload, key=bad_key)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("detail", resp.json())
                self.assertEqual(Request.objects.count(), 0)
                self.assertEqual(RequestCreationKey.objects.count(), 0)

    def test_owner_isolation_on_key_namespace_and_private_status_endpoint(self):
        shared_key = uuid4()
        owner_client = self.auth_client(self.owner)
        other_client = self.auth_client(self.other_user)
        superuser_client = self.auth_client(self.superuser)

        anon_status = APIClient().get(self.status_url(shared_key))
        self.assertEqual(anon_status.status_code, status.HTTP_403_FORBIDDEN)

        first = self.post_create(owner_client, self.valid_payload(title="Milik A"), key=shared_key)
        second = self.post_create(other_client, self.valid_payload(title="Milik B"), key=shared_key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(first.json()["id"], second.json()["id"])

        owner_status = owner_client.get(self.status_url(shared_key))
        self.assertEqual(owner_status.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_status.json(), {"state": "created", "request_id": first.json()["id"]})
        self.assertNotIn("digest", owner_status.content.decode("utf-8"))
        self.assertIn("no-store", owner_status.headers.get("Cache-Control", ""))

        other_status = other_client.get(self.status_url(shared_key))
        self.assertEqual(other_status.status_code, status.HTTP_200_OK)
        self.assertEqual(other_status.json(), {"state": "created", "request_id": second.json()["id"]})

        outsider_only_key = uuid4()
        self.assertEqual(
            self.post_create(owner_client, self.valid_payload(), key=outsider_only_key).status_code,
            status.HTTP_201_CREATED,
        )
        foreign_resp = other_client.get(self.status_url(outsider_only_key))
        superuser_resp = superuser_client.get(self.status_url(outsider_only_key))
        unknown_resp = owner_client.get(self.status_url(uuid4()))
        self.assertEqual(foreign_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(superuser_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(unknown_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign_resp.json(), unknown_resp.json())

        owner_client.delete(reverse("request_board_api:request_detail", args=[first.json()["id"]]))
        deleted_status = owner_client.get(self.status_url(shared_key))
        self.assertEqual(deleted_status.status_code, status.HTTP_200_OK)
        self.assertEqual(deleted_status.json(), {"state": "deleted", "request_id": None})
        self.assertEqual(
            other_client.get(self.status_url(shared_key)).json(),
            {"state": "created", "request_id": second.json()["id"]},
        )

    def test_canonical_payload_omission_sanitization_decimal_and_timezone_stability(self):
        client = self.auth_client(self.owner)
        omission_key = uuid4()
        t0 = datetime(2030, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)

        with patch("request_board.validators.timezone.now", return_value=t0):
            first = self.post_create(
                client,
                {
                    "title": "  <b>Beras</b>  ",
                    "food_item": " <i>Beras</i> ",
                    "quantity": "2",
                    "unit": "kg",
                },
                key=omission_key,
            )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        first_body = first.json()
        self.assertEqual(first_body["title"], "Beras")
        self.assertEqual(first_body["food_item"], "Beras")
        self.assertEqual(first_body["quantity"], "2.00")
        self.assertEqual(
            parse_datetime(first_body["expires_at"]),
            t0 + timedelta(hours=24),
        )

        # Advancing clock + equivalent canonical inputs must still replay 200 without conflict.
        with patch("request_board.validators.timezone.now", return_value=t0 + timedelta(minutes=15)):
            replay = self.post_create(
                client,
                {
                    "title": "Beras",
                    "food_item": "Beras",
                    "quantity": 2.0,
                    "unit": "kg",
                    "description": "  <p></p> ",
                    "location": "",
                },
                key=omission_key,
            )
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.json()["id"], first_body["id"])

        tz_key = uuid4()
        with patch("request_board.validators.timezone.now", return_value=t0):
            tz_first = self.post_create(
                client,
                self.valid_payload(
                    quantity=123456.78,
                    expires_at="2030-01-02T10:00:00+07:00",
                ),
                key=tz_key,
            )
            tz_replay = self.post_create(
                client,
                self.valid_payload(
                    quantity="123456.78",
                    expires_at="2030-01-02T03:00:00Z",
                ),
                key=tz_key,
            )
        self.assertEqual(tz_first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(tz_replay.status_code, status.HTTP_200_OK)
        self.assertEqual(tz_first.json()["id"], tz_replay.json()["id"])
        self.assertEqual(tz_first.json()["quantity"], "123456.78")

    def test_replay_after_deadline_succeeds_while_new_past_deadline_and_conflict_fail(self):
        client = self.auth_client(self.owner)
        t0 = datetime(2030, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
        deadline = t0 + timedelta(hours=1)
        payload = self.valid_payload(expires_at=deadline.isoformat())
        key = uuid4()

        with patch("request_board.validators.timezone.now", return_value=t0):
            first = self.post_create(client, payload, key=key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        t_after = t0 + timedelta(hours=2)
        with (
            patch("request_board.validators.timezone.now", return_value=t_after),
            patch("request_board.models.timezone.now", return_value=t_after),
        ):
            replay = self.post_create(client, payload, key=key)
            self.assertEqual(replay.status_code, status.HTTP_200_OK)
            self.assertEqual(replay.json()["id"], first.json()["id"])
            self.assertEqual(replay.json()["effective_status"], Request.STATUS_EXPIRED)

            conflict = self.post_create(client, {**payload, "title": "Berubah"}, key=key)
            self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(conflict.json()["code"], "idempotency_conflict")

            fresh_key = uuid4()
            past_new = self.post_create(client, payload, key=fresh_key)
            self.assertEqual(past_new.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("expires_at", past_new.json())
            self.assertEqual(Request.objects.count(), 1)
            self.assertEqual(RequestCreationKey.objects.count(), 1)

            # Unused fresh_key remains valid once deadline is corrected.
            recovered = self.post_create(
                client,
                self.valid_payload(expires_at=(t_after + timedelta(hours=1)).isoformat()),
                key=fresh_key,
            )
            self.assertEqual(recovered.status_code, status.HTTP_201_CREATED)

    def test_validation_transport_and_transaction_rollback_leave_no_orphan_records(self):
        client = self.auth_client(self.owner)
        reusable_key = uuid4()

        bad_payloads = [
            self.valid_payload(owner=self.other_user.pk),
            self.valid_payload(status="fulfilled"),
            self.valid_payload(selected_offer_id=1),
            self.valid_payload(unknown_field="x"),
            self.valid_payload(title="  <b></b>  "),
            {"title": "incomplete"},
            [],
            "text",
            123,
            None,
        ]
        for bad_payload in bad_payloads:
            with self.subTest(bad_payload=bad_payload):
                resp = self.post_create(client, bad_payload, key=reusable_key)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(Request.objects.count(), 0)
                self.assertEqual(RequestCreationKey.objects.count(), 0)

        for raw_body in (b"", b"null", b"{invalid", b'{"title":"a","title":"b"}', b'{"quantity":NaN}', b"\xff"):
            with self.subTest(raw_body=raw_body):
                resp = client.post(
                    self.list_url,
                    data=raw_body,
                    content_type="application/json",
                    HTTP_IDEMPOTENCY_KEY=str(reusable_key),
                )
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(Request.objects.count(), 0)

        for bad_type in ("text/plain", "application/x-www-form-urlencoded"):
            resp = client.post(
                self.list_url,
                data=json.dumps(self.valid_payload()),
                content_type=bad_type,
                HTTP_IDEMPOTENCY_KEY=str(reusable_key),
            )
            self.assertEqual(resp.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            self.assertEqual(Request.objects.count(), 0)

        with patch(
            "request_board.services.RequestCreationKey.objects.create",
            side_effect=IntegrityError("simulated key insert failure"),
        ):
            with self.assertRaises(IntegrityError):
                create_request_from_api_idempotent(
                    actor=self.owner,
                    validated_data={
                        "title": "Rollback test",
                        "food_item": "Beras",
                        "quantity": Decimal("1.00"),
                        "unit": "kg",
                        "description": "",
                        "location": "",
                    },
                    key=reusable_key,
                )
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(RequestCreationKey.objects.count(), 0)

        # Key was never consumed by failures and succeeds on valid retry.
        recovered = self.post_create(client, self.valid_payload(), key=reusable_key)
        self.assertEqual(recovered.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Request.objects.count(), 1)
        self.assertEqual(RequestCreationKey.objects.count(), 1)

    def test_post_create_enforces_session_auth_and_real_csrf(self):
        key = uuid4()
        payload = self.valid_payload()

        anon = APIClient(enforce_csrf_checks=True)
        anon_resp = self.post_create(anon, payload, key=key)
        self.assertEqual(anon_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Request.objects.count(), 0)

        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=self.owner.username, password=self.password))

        no_csrf = self.post_create(client, payload, key=key)
        self.assertEqual(no_csrf.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Request.objects.count(), 0)

        csrf_request = RequestFactory().get("/")
        csrf_token = get_token(csrf_request)
        client.cookies["csrftoken"] = csrf_request.META["CSRF_COOKIE"]

        created = self.post_create(client, payload, key=key, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        replayed = self.post_create(client, payload, key=key, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(replayed.status_code, status.HTTP_200_OK)
        self.assertEqual(created.json()["id"], replayed.json()["id"])
        self.assertEqual(Request.objects.count(), 1)
