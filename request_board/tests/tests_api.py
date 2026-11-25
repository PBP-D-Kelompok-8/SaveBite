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

    def test_api_urls_namespace_mounted_at_api_v1_with_t04_routes(self):
        self.assertEqual(api_urls.app_name, "request_board_api")
        self.assertEqual(
            [pattern.name for pattern in api_urls.urlpatterns],
            [
                "request_list",
                "request_detail",
                "request_offer_list",
                "request_offer_detail",
                "request_selection",
                "request_status",
                "request_creation_status",
            ],
        )
        self.assertEqual(reverse("request_board_api:request_list"), "/api/v1/requests/")
        self.assertEqual(
            reverse("request_board_api:request_detail", args=[42]),
            "/api/v1/requests/42/",
        )
        self.assertEqual(
            reverse("request_board_api:request_offer_list", args=[42]),
            "/api/v1/requests/42/offers/",
        )
        self.assertEqual(
            reverse("request_board_api:request_offer_detail", args=[42, 7]),
            "/api/v1/requests/42/offers/7/",
        )
        self.assertEqual(
            reverse("request_board_api:request_selection", args=[42]),
            "/api/v1/requests/42/selection/",
        )
        self.assertEqual(
            reverse("request_board_api:request_status", args=[42]),
            "/api/v1/requests/42/status/",
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
            resolve("/api/v1/requests/42/offers/").view_name,
            "request_board_api:request_offer_list",
        )
        self.assertEqual(
            resolve("/api/v1/requests/42/offers/7/").view_name,
            "request_board_api:request_offer_detail",
        )
        self.assertEqual(
            resolve("/api/v1/requests/42/selection/").view_name,
            "request_board_api:request_selection",
        )
        self.assertEqual(
            resolve("/api/v1/requests/42/status/").view_name,
            "request_board_api:request_status",
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
                list_cache_control = list_resp.headers.get("Cache-Control", "")
                self.assertIn("private", list_cache_control)
                self.assertIn("no-store", list_cache_control)
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
                detail_cache_control = detail_resp.headers.get("Cache-Control", "")
                self.assertIn("private", detail_cache_control)
                self.assertIn("no-store", detail_cache_control)
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


class RequestOfferAndFulfillmentAPITests(APITestCase):
    """Verify T04 offer create/list/detail, selection, fulfillment, scoping, and privacy."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.password = "offer-api-password-123"
        cls.owner = User.objects.create_user(
            username="t04_owner",
            password=cls.password,
            email="t04-owner@example.invalid",
            first_name="OwnerFirst",
            last_name="OwnerLast",
        )
        cls.donor = User.objects.create_user(
            username="t04_donor",
            password=cls.password,
            email="t04-donor@example.invalid",
            first_name="DonorFirst",
            last_name="DonorLast",
        )
        cls.second_donor = User.objects.create_user(
            username="t04_second_donor",
            password=cls.password,
            email="t04-second@example.invalid",
            first_name="SecondFirst",
            last_name="SecondLast",
        )
        cls.outsider = User.objects.create_user(
            username="t04_outsider",
            password=cls.password,
            email="t04-outsider@example.invalid",
        )
        cls.superuser = User.objects.create_superuser(
            username="t04_superuser",
            password=cls.password,
            email="t04-su@example.invalid",
        )

    def make_request(self, owner=None, **overrides):
        values = {
            "owner": owner or self.owner,
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

    def auth_client(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def database_snapshot(self):
        return {
            "requests": list(Request.objects.order_by("pk").values()),
            "offers": list(RequestOffer.objects.order_by("pk").values()),
        }

    def offers_url(self, request_id):
        return reverse("request_board_api:request_offer_list", args=[request_id])

    def offer_detail_url(self, request_id, offer_id):
        return reverse("request_board_api:request_offer_detail", args=[request_id, offer_id])

    def selection_url(self, request_id):
        return reverse("request_board_api:request_selection", args=[request_id])

    def status_url(self, request_id):
        return reverse("request_board_api:request_status", args=[request_id])

    def assert_offer_payload(self, item: dict, offer: RequestOffer, *, is_selected: bool):
        self.assertEqual(
            set(item.keys()),
            {"id", "request_id", "offered_by", "created_at", "is_selected"},
        )
        self.assertEqual(item["id"], offer.pk)
        self.assertEqual(item["request_id"], offer.request_id)
        self.assertEqual(
            item["offered_by"],
            {"id": offer.offered_by_id, "display_name": offer.offered_by.get_username()},
        )
        parsed_created = parse_datetime(item["created_at"])
        self.assertIsNotNone(parsed_created)
        self.assertIsNotNone(parsed_created.utcoffset())
        self.assertEqual(parsed_created, offer.created_at)
        self.assertIs(item["is_selected"], is_selected)

    def assert_no_user_secrets(self, raw_body: str):
        for secret in (
            self.owner.email,
            self.owner.first_name,
            self.owner.last_name,
            self.owner.password,
            self.donor.email,
            self.donor.first_name,
            self.donor.last_name,
            self.donor.password,
            self.second_donor.email,
            self.second_donor.first_name,
            self.second_donor.last_name,
        ):
            self.assertNotIn(secret, raw_body)

    def test_offer_create_first_and_duplicate_transitions_and_refreshes_request(self):
        target = self.make_request()
        donor_client = self.auth_client(self.donor)
        url = self.offers_url(target.pk)

        first = donor_client.post(url, data={}, format="json")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        target.refresh_from_db()
        offer = RequestOffer.objects.get(request=target, offered_by=self.donor)
        self.assertEqual(target.status, Request.STATUS_OFFERED)
        self.assertEqual(RequestOffer.objects.filter(request=target).count(), 1)
        self.assert_offer_payload(first.json(), offer, is_selected=False)
        self.assert_no_user_secrets(first.content.decode("utf-8"))

        # Public Request representation reflects refreshed offer_count & has_offered without leaking donor identity.
        detail_url = reverse("request_board_api:request_detail", args=[target.pk])
        donor_detail = donor_client.get(detail_url)
        self.assertEqual(donor_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(donor_detail.json()["offer_count"], 1)
        self.assertIs(donor_detail.json()["has_offered"], True)
        self.assertIsNone(donor_detail.json()["selected_offer_id"])
        self.assertNotIn(self.donor.username, donor_detail.content.decode("utf-8"))

        outsider_detail = self.auth_client(self.outsider).get(detail_url)
        self.assertEqual(outsider_detail.json()["offer_count"], 1)
        self.assertIs(outsider_detail.json()["has_offered"], False)
        self.assertNotIn(self.donor.username, outsider_detail.content.decode("utf-8"))

        # Replay by same donor returns 200 without creating a duplicate row.
        before = self.database_snapshot()
        replay = donor_client.post(url, data={}, format="json")
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_offer_payload(replay.json(), offer, is_selected=False)

    def test_offer_create_rejects_anonymous_self_offer_and_spoofed_or_invalid_payloads(self):
        target = self.make_request()
        url = self.offers_url(target.pk)
        before = self.database_snapshot()

        anon_resp = self.auth_client().post(url, data={}, format="json")
        self.assertEqual(anon_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.database_snapshot(), before)

        owner_resp = self.auth_client(self.owner).post(url, data={}, format="json")
        self.assertEqual(owner_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.database_snapshot(), before)

        donor_client = self.auth_client(self.donor)
        for bad_payload in (
            {"offered_by": self.outsider.pk},
            {"offered_by_id": self.outsider.pk},
            {"request": target.pk},
            {"request_id": target.pk},
            {"created_at": "2020-01-01T00:00:00Z"},
            {"is_selected": True},
            {"unexpected": "value"},
            [],
            "text",
            123,
        ):
            with self.subTest(bad_payload=bad_payload):
                resp = donor_client.post(url, data=bad_payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self.database_snapshot(), before)

        for raw_bad in (b"", b"null", b"{bad"):
            with self.subTest(raw_bad=raw_bad):
                raw_resp = donor_client.post(url, data=raw_bad, content_type="application/json")
                self.assertEqual(raw_resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self.database_snapshot(), before)

        wrong_type = donor_client.post(url, data="{}", content_type="text/plain")
        self.assertEqual(wrong_type.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(self.database_snapshot(), before)

        missing_resp = donor_client.post(self.offers_url(target.pk + 999), data={}, format="json")
        self.assertEqual(missing_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.database_snapshot(), before)

    def test_offer_conflicts_for_fulfilled_expired_and_selected_offer(self):
        # Fulfilled request rejects both new offers and replays of prior offers.
        fulfilled_req = self.make_request(status=Request.STATUS_OFFERED)
        existing_fulfilled_offer = RequestOffer.objects.create(
            request=fulfilled_req,
            offered_by=self.donor,
        )
        fulfilled_req.selected_offer = existing_fulfilled_offer
        fulfilled_req.status = Request.STATUS_FULFILLED
        fulfilled_req.save(update_fields=["selected_offer", "status"])

        before_fulfilled = self.database_snapshot()
        for actor in (self.donor, self.second_donor):
            resp = self.auth_client(actor).post(
                self.offers_url(fulfilled_req.pk),
                data={},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(resp.json()["code"], "request_fulfilled")
            self.assertEqual(self.database_snapshot(), before_fulfilled)

        # Expired request rejects both new offers and replays of prior offers.
        expired_req = self.make_request(
            status=Request.STATUS_OFFERED,
            expires_at=timezone.now() - timedelta(minutes=5),
        )
        RequestOffer.objects.create(request=expired_req, offered_by=self.donor)

        before_expired = self.database_snapshot()
        for actor in (self.donor, self.second_donor):
            resp = self.auth_client(actor).post(
                self.offers_url(expired_req.pk),
                data={},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(resp.json()["code"], "request_expired")
            self.assertEqual(self.database_snapshot(), before_expired)

        # Selected offer closes new donors (409 offers_closed) while allowing prior donors to replay (200).
        selected_req = self.make_request(status=Request.STATUS_OFFERED)
        chosen_offer = RequestOffer.objects.create(request=selected_req, offered_by=self.donor)
        other_prior_offer = RequestOffer.objects.create(
            request=selected_req,
            offered_by=self.second_donor,
        )
        selected_req.selected_offer = chosen_offer
        selected_req.save(update_fields=["selected_offer"])

        before_selected = self.database_snapshot()
        new_donor_resp = self.auth_client(self.outsider).post(
            self.offers_url(selected_req.pk),
            data={},
            format="json",
        )
        self.assertEqual(new_donor_resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(new_donor_resp.json()["code"], "offers_closed")
        self.assertEqual(self.database_snapshot(), before_selected)

        chosen_replay = self.auth_client(self.donor).post(
            self.offers_url(selected_req.pk),
            data={},
            format="json",
        )
        self.assertEqual(chosen_replay.status_code, status.HTTP_200_OK)
        self.assert_offer_payload(chosen_replay.json(), chosen_offer, is_selected=True)

        other_replay = self.auth_client(self.second_donor).post(
            self.offers_url(selected_req.pk),
            data={},
            format="json",
        )
        self.assertEqual(other_replay.status_code, status.HTTP_200_OK)
        self.assert_offer_payload(other_replay.json(), other_prior_offer, is_selected=False)
        self.assertEqual(self.database_snapshot(), before_selected)

    def test_offer_list_and_detail_are_private_paginated_and_scoped_to_request(self):
        target = self.make_request(status=Request.STATUS_OFFERED)
        other_request = self.make_request(owner=self.owner, title="Permintaan Lain", status=Request.STATUS_OFFERED)
        offer_one = RequestOffer.objects.create(request=target, offered_by=self.donor)
        offer_two = RequestOffer.objects.create(request=target, offered_by=self.second_donor)
        foreign_offer = RequestOffer.objects.create(request=other_request, offered_by=self.donor)
        target.selected_offer = offer_one
        target.save(update_fields=["selected_offer"])

        list_url = self.offers_url(target.pk)
        detail_url = self.offer_detail_url(target.pk, offer_one.pk)
        foreign_detail_url = self.offer_detail_url(target.pk, foreign_offer.pk)

        # Anonymous, donor, outsider, and superuser are all rejected with 403 before serializing offers.
        for actor in (None, self.donor, self.outsider, self.superuser):
            client = self.auth_client(actor)
            for url in (
                list_url,
                f"{list_url}?page=invalid",
                detail_url,
                foreign_detail_url,
            ):
                with self.subTest(actor=getattr(actor, "username", "anon"), url=url):
                    resp = client.get(url)
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                    cache_control = resp.headers.get("Cache-Control", "")
                    self.assertIn("private", cache_control)
                    self.assertIn("no-store", cache_control)
                    self.assertNotIn(self.donor.username, resp.content.decode("utf-8"))
                    self.assertNotIn(self.second_donor.username, resp.content.decode("utf-8"))

        owner_client = self.auth_client(self.owner)
        # Foreign offer scoped to wrong Request returns 404.
        scoped_miss = owner_client.get(foreign_detail_url)
        self.assertEqual(scoped_miss.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(scoped_miss.json(), {"detail": "Penawaran tidak ditemukan."})

        missing_req_list = owner_client.get(self.offers_url(target.pk + 9999))
        self.assertEqual(missing_req_list.status_code, status.HTTP_404_NOT_FOUND)

        detail_resp = owner_client.get(detail_url)
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertIn("private", detail_resp.headers.get("Cache-Control", ""))
        self.assertIn("no-store", detail_resp.headers.get("Cache-Control", ""))
        self.assert_offer_payload(detail_resp.json(), offer_one, is_selected=True)
        self.assert_no_user_secrets(detail_resp.content.decode("utf-8"))

        unselected_detail = owner_client.get(self.offer_detail_url(target.pk, offer_two.pk))
        self.assertEqual(unselected_detail.status_code, status.HTTP_200_OK)
        self.assert_offer_payload(unselected_detail.json(), offer_two, is_selected=False)

        # Verify pagination (20 per page) and constant query count on GET /offers/.
        User = get_user_model()
        with CaptureQueriesContext(connection) as small_queries:
            small_resp = owner_client.get(list_url)
        self.assertEqual(small_resp.status_code, status.HTTP_200_OK)
        self.assertIn("private", small_resp.headers.get("Cache-Control", ""))
        self.assertIn("no-store", small_resp.headers.get("Cache-Control", ""))
        self.assertEqual(small_resp.json()["count"], 2)

        extra_offers = []
        for idx in range(19):
            extra_user = User.objects.create_user(username=f"t04_extra_donor_{idx:02}")
            extra_offers.append(RequestOffer.objects.create(request=target, offered_by=extra_user))

        with CaptureQueriesContext(connection) as full_page_queries:
            page1 = owner_client.get(list_url)
        self.assertEqual(page1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(full_page_queries), len(small_queries))
        p1_data = page1.json()
        self.assertEqual(p1_data["count"], 21)
        self.assertEqual(p1_data["page"], 1)
        self.assertEqual(p1_data["page_size"], 20)
        self.assertEqual(p1_data["total_pages"], 2)
        self.assertEqual(len(p1_data["results"]), 20)
        self.assert_no_user_secrets(page1.content.decode("utf-8"))

        page2 = owner_client.get(list_url, {"page": 2})
        self.assertEqual(page2.status_code, status.HTTP_200_OK)
        p2_data = page2.json()
        self.assertEqual(len(p2_data["results"]), 1)
        self.assert_offer_payload(p2_data["results"][0], offer_one, is_selected=True)

        self.assertEqual(owner_client.get(list_url, {"page": "abc"}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(owner_client.get(f"{list_url}?page=1&page=2").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(owner_client.get(list_url, {"page": 3}).status_code, status.HTTP_404_NOT_FOUND)

    def test_selection_requires_owner_scopes_offer_and_allows_replace_until_fulfilled(self):
        target = self.make_request(status=Request.STATUS_OFFERED)
        other_request = self.make_request(owner=self.owner, title="Lain", status=Request.STATUS_OFFERED)
        offer_one = RequestOffer.objects.create(request=target, offered_by=self.donor)
        offer_two = RequestOffer.objects.create(request=target, offered_by=self.second_donor)
        foreign_offer = RequestOffer.objects.create(request=other_request, offered_by=self.donor)

        sel_url = self.selection_url(target.pk)
        before = self.database_snapshot()

        # Anonymous and non-owners (including donor & superuser) get 403 without DB writes.
        for actor in (None, self.donor, self.outsider, self.superuser):
            resp = self.auth_client(actor).patch(sel_url, data={"offer_id": offer_one.pk}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.database_snapshot(), before)

        owner_client = self.auth_client(self.owner)

        # Foreign offer belonging to another Request returns 404 and leaves DB untouched.
        foreign_resp = owner_client.patch(sel_url, data={"offer_id": foreign_offer.pk}, format="json")
        self.assertEqual(foreign_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(foreign_resp.json(), {"detail": "Penawaran tidak ditemukan."})
        self.assertEqual(self.database_snapshot(), before)

        missing_offer_resp = owner_client.patch(sel_url, data={"offer_id": 999999}, format="json")
        self.assertEqual(missing_offer_resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.database_snapshot(), before)

        for bad_payload in (
            {},
            {"offer_id": None},
            {"offer_id": 0},
            {"offer_id": -1},
            {"offer_id": True},
            {"offer_id": 1.5},
            {"offer_id": "abc"},
            {"offer_id": offer_one.pk, "extra": 1},
            [],
            None,
        ):
            with self.subTest(bad_payload=bad_payload):
                resp = owner_client.patch(sel_url, data=bad_payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self.database_snapshot(), before)

        # Owner selects offer_one -> 200 with refreshed Request annotations.
        first_sel = owner_client.patch(sel_url, data={"offer_id": offer_one.pk}, format="json")
        self.assertEqual(first_sel.status_code, status.HTTP_200_OK)
        self.assertEqual(first_sel.json()["selected_offer_id"], offer_one.pk)
        self.assertEqual(first_sel.json()["offer_count"], 2)
        self.assertIs(first_sel.json()["has_offered"], False)
        self.assertEqual(first_sel.json()["status"], Request.STATUS_OFFERED)
        self.assertNotIn(self.donor.username, first_sel.content.decode("utf-8"))

        # Owner can replace selection with offer_two while active and not fulfilled.
        second_sel = owner_client.patch(sel_url, data={"offer_id": offer_two.pk}, format="json")
        self.assertEqual(second_sel.status_code, status.HTTP_200_OK)
        self.assertEqual(second_sel.json()["selected_offer_id"], offer_two.pk)
        self.assertEqual(second_sel.json()["offer_count"], 2)
        target.refresh_from_db()
        self.assertEqual(target.selected_offer_id, offer_two.pk)

        # Fulfill then verify selection can no longer be changed (409 request_fulfilled).
        fulfill_resp = owner_client.patch(
            self.status_url(target.pk),
            data={"status": "fulfilled"},
            format="json",
        )
        self.assertEqual(fulfill_resp.status_code, status.HTTP_200_OK)
        before_locked = self.database_snapshot()
        after_fulfill_sel = owner_client.patch(sel_url, data={"offer_id": offer_one.pk}, format="json")
        self.assertEqual(after_fulfill_sel.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(after_fulfill_sel.json()["code"], "request_fulfilled")
        self.assertEqual(self.database_snapshot(), before_locked)

        # Expired request rejects selection with 409 request_expired.
        expired_req = self.make_request(
            status=Request.STATUS_OFFERED,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        expired_offer = RequestOffer.objects.create(request=expired_req, offered_by=self.donor)
        before_exp = self.database_snapshot()
        exp_sel = owner_client.patch(
            self.selection_url(expired_req.pk),
            data={"offer_id": expired_offer.pk},
            format="json",
        )
        self.assertEqual(exp_sel.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(exp_sel.json()["code"], "request_expired")
        self.assertEqual(self.database_snapshot(), before_exp)

    def test_status_fulfill_requires_owner_selection_and_active_and_supports_replay(self):
        target = self.make_request(status=Request.STATUS_OFFERED)
        offer = RequestOffer.objects.create(request=target, offered_by=self.donor)
        st_url = self.status_url(target.pk)
        before_no_sel = self.database_snapshot()

        # Anonymous and non-owners (including donor & superuser) get 403 without DB writes.
        for actor in (None, self.donor, self.outsider, self.superuser):
            resp = self.auth_client(actor).patch(st_url, data={"status": "fulfilled"}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.database_snapshot(), before_no_sel)

        owner_client = self.auth_client(self.owner)

        # Invalid status values (pending/offered/expired/etc.) and extra fields are rejected 400.
        for bad_payload in (
            {},
            {"status": "pending"},
            {"status": "offered"},
            {"status": "expired"},
            {"status": "FULFILLED"},
            {"status": ""},
            {"status": None},
            {"status": 1},
            {"status": "fulfilled", "extra": "x"},
            [],
            None,
        ):
            with self.subTest(bad_payload=bad_payload):
                resp = owner_client.patch(st_url, data=bad_payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self.database_snapshot(), before_no_sel)

        # Fulfill without selected_offer returns 409 offer_selection_required without DB changes.
        no_sel_resp = owner_client.patch(st_url, data={"status": "fulfilled"}, format="json")
        self.assertEqual(no_sel_resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(no_sel_resp.json()["code"], "offer_selection_required")
        self.assertEqual(self.database_snapshot(), before_no_sel)

        # Select offer, then fulfill -> 200 with refreshed annotations.
        sel_resp = owner_client.patch(
            self.selection_url(target.pk),
            data={"offer_id": offer.pk},
            format="json",
        )
        self.assertEqual(sel_resp.status_code, status.HTTP_200_OK)

        fulfill_resp = owner_client.patch(st_url, data={"status": "fulfilled"}, format="json")
        self.assertEqual(fulfill_resp.status_code, status.HTTP_200_OK)
        fulfill_data = fulfill_resp.json()
        self.assertEqual(fulfill_data["status"], Request.STATUS_FULFILLED)
        self.assertEqual(fulfill_data["effective_status"], Request.STATUS_FULFILLED)
        self.assertEqual(fulfill_data["selected_offer_id"], offer.pk)
        self.assertEqual(fulfill_data["offer_count"], 1)
        self.assertIs(fulfill_data["has_offered"], False)

        # Replay fulfill returns 200 with the exact same state (even if expires_at is now in the past).
        after_fulfill = self.database_snapshot()
        with patch(
            "request_board.models.timezone.now",
            return_value=timezone.now() + timedelta(days=5),
        ):
            replay_resp = owner_client.patch(st_url, data={"status": "fulfilled"}, format="json")
        self.assertEqual(replay_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(replay_resp.json(), fulfill_data)
        self.assertEqual(self.database_snapshot(), after_fulfill)

        # Unfulfilled expired request cannot be fulfilled (409 request_expired).
        expired_req = self.make_request(
            status=Request.STATUS_OFFERED,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        expired_offer = RequestOffer.objects.create(request=expired_req, offered_by=self.donor)
        expired_req.selected_offer = expired_offer
        expired_req.save(update_fields=["selected_offer"])
        before_exp = self.database_snapshot()
        exp_fulfill = owner_client.patch(
            self.status_url(expired_req.pk),
            data={"status": "fulfilled"},
            format="json",
        )
        self.assertEqual(exp_fulfill.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(exp_fulfill.json()["code"], "request_expired")
        self.assertEqual(self.database_snapshot(), before_exp)

    def test_t04_endpoints_enforce_real_csrf_and_reject_disallowed_methods(self):
        target = self.make_request()
        offer_seed = RequestOffer.objects.create(request=target, offered_by=self.second_donor)
        target.status = Request.STATUS_OFFERED
        target.save(update_fields=["status"])

        # Disallowed methods return 403 for anonymous and 405 for authenticated users without DB writes.
        method_cases = [
            (self.offers_url(target.pk), ("put", "patch", "delete")),
            (self.offer_detail_url(target.pk, offer_seed.pk), ("post", "put", "patch", "delete")),
            (self.selection_url(target.pk), ("get", "post", "put", "delete")),
            (self.status_url(target.pk), ("get", "post", "put", "delete")),
        ]
        for url, bad_methods in method_cases:
            for actor in (None, self.owner):
                client = self.auth_client(actor)
                for method in bad_methods:
                    with self.subTest(url=url, actor=getattr(actor, "username", "anon"), method=method):
                        before = self.database_snapshot()
                        resp = getattr(client, method)(url, data={}, format="json")
                        expected = (
                            status.HTTP_403_FORBIDDEN
                            if actor is None
                            else status.HTTP_405_METHOD_NOT_ALLOWED
                        )
                        self.assertEqual(resp.status_code, expected)
                        self.assertEqual(self.database_snapshot(), before)

        # Real SessionAuthentication + CSRF enforcement for POST offer, PATCH selection, PATCH status.
        donor_csrf = APIClient(enforce_csrf_checks=True)
        self.assertTrue(donor_csrf.login(username=self.donor.username, password=self.password))
        before = self.database_snapshot()
        no_csrf_offer = donor_csrf.post(self.offers_url(target.pk), data={}, format="json")
        self.assertEqual(no_csrf_offer.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.database_snapshot(), before)

        csrf_req_donor = RequestFactory().get("/")
        donor_token = get_token(csrf_req_donor)
        donor_csrf.cookies["csrftoken"] = csrf_req_donor.META["CSRF_COOKIE"]
        ok_offer = donor_csrf.post(
            self.offers_url(target.pk),
            data={},
            format="json",
            HTTP_X_CSRFTOKEN=donor_token,
        )
        self.assertEqual(ok_offer.status_code, status.HTTP_201_CREATED)
        donor_offer_id = ok_offer.json()["id"]

        owner_csrf = APIClient(enforce_csrf_checks=True)
        self.assertTrue(owner_csrf.login(username=self.owner.username, password=self.password))
        before_sel = self.database_snapshot()
        no_csrf_sel = owner_csrf.patch(
            self.selection_url(target.pk),
            data={"offer_id": donor_offer_id},
            format="json",
        )
        self.assertEqual(no_csrf_sel.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.database_snapshot(), before_sel)

        csrf_req_owner = RequestFactory().get("/")
        owner_token = get_token(csrf_req_owner)
        owner_csrf.cookies["csrftoken"] = csrf_req_owner.META["CSRF_COOKIE"]
        ok_sel = owner_csrf.patch(
            self.selection_url(target.pk),
            data={"offer_id": donor_offer_id},
            format="json",
            HTTP_X_CSRFTOKEN=owner_token,
        )
        self.assertEqual(ok_sel.status_code, status.HTTP_200_OK)
        self.assertEqual(ok_sel.json()["selected_offer_id"], donor_offer_id)

        before_st = self.database_snapshot()
        no_csrf_st = owner_csrf.patch(
            self.status_url(target.pk),
            data={"status": "fulfilled"},
            format="json",
        )
        self.assertEqual(no_csrf_st.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.database_snapshot(), before_st)

        ok_st = owner_csrf.patch(
            self.status_url(target.pk),
            data={"status": "fulfilled"},
            format="json",
            HTTP_X_CSRFTOKEN=owner_token,
        )
        self.assertEqual(ok_st.status_code, status.HTTP_200_OK)
        self.assertEqual(ok_st.json()["status"], Request.STATUS_FULFILLED)


class RequestBoardCrossEndpointAndCSRFIntegrationTests(APITestCase):
    """T06 cross-endpoint contract, real-page CSRF, Browsable API, and lifecycle tests."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.password = "t06-integration-pass-123"
        cls.owner = User.objects.create_user(
            username="t06_owner",
            password=cls.password,
            email="t06-owner@example.invalid",
            first_name="T06",
            last_name="Owner",
        )
        cls.donor = User.objects.create_user(
            username="t06_donor",
            password=cls.password,
            email="t06-donor@example.invalid",
            first_name="T06",
            last_name="Donor",
        )
        cls.other = User.objects.create_user(
            username="t06_other",
            password=cls.password,
            email="t06-other@example.invalid",
            first_name="T06",
            last_name="Other",
        )

    def database_snapshot(self):
        return {
            "requests": list(Request.objects.order_by("pk").values()),
            "offers": list(RequestOffer.objects.order_by("pk").values()),
            "keys": list(RequestCreationKey.objects.order_by("key").values()),
        }

    def _csrf_client(self, user, page_url):
        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=user.username, password=self.password))
        page_resp = client.get(page_url)
        self.assertEqual(page_resp.status_code, status.HTTP_200_OK)
        self.assertIn("csrftoken", client.cookies)
        return client, client.cookies["csrftoken"].value

    def _assert_csrf_rejected(self, fn):
        before = self.database_snapshot()
        no_tok = fn(None)
        self.assertEqual(no_tok.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("csrf", no_tok.json()["detail"].lower())
        self.assertEqual(self.database_snapshot(), before)

        bad_tok = fn("wrong-csrf-token-value")
        self.assertEqual(bad_tok.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("csrf", bad_tok.json()["detail"].lower())
        self.assertEqual(self.database_snapshot(), before)

    def test_real_page_csrf_across_all_mutating_endpoints(self):
        create_url = reverse("request_board_api:request_list")
        create_key = str(uuid4())
        payload = {
            "title": "Butuh sayur segar",
            "food_item": "Bayam",
            "quantity": "3.00",
            "unit": "ikat",
            "location": "Jakarta",
        }

        owner_create_client, owner_create_tok = self._csrf_client(
            self.owner,
            reverse("request_board:request_create"),
        )
        self._assert_csrf_rejected(
            lambda tok: owner_create_client.post(
                create_url,
                data=payload,
                format="json",
                HTTP_IDEMPOTENCY_KEY=create_key,
                **({"HTTP_X_CSRFTOKEN": tok} if tok else {}),
            )
        )
        created = owner_create_client.post(
            create_url,
            data=payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=create_key,
            HTTP_X_CSRFTOKEN=owner_create_tok,
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        req_id = created.json()["id"]

        donor_client, donor_tok = self._csrf_client(
            self.donor,
            reverse("request_board:request_board"),
        )
        offer_list_url = reverse("request_board_api:request_offer_list", args=[req_id])
        self._assert_csrf_rejected(
            lambda tok: donor_client.post(
                offer_list_url,
                data={},
                format="json",
                **({"HTTP_X_CSRFTOKEN": tok} if tok else {}),
            )
        )
        offered = donor_client.post(
            offer_list_url,
            data={},
            format="json",
            HTTP_X_CSRFTOKEN=donor_tok,
        )
        self.assertEqual(offered.status_code, status.HTTP_201_CREATED)
        offer_id = offered.json()["id"]

        owner_offers_client, owner_offers_tok = self._csrf_client(
            self.owner,
            reverse("request_board:request_offers", args=[req_id]),
        )
        selection_url = reverse("request_board_api:request_selection", args=[req_id])
        self._assert_csrf_rejected(
            lambda tok: owner_offers_client.patch(
                selection_url,
                data={"offer_id": offer_id},
                format="json",
                **({"HTTP_X_CSRFTOKEN": tok} if tok else {}),
            )
        )
        selected = owner_offers_client.patch(
            selection_url,
            data={"offer_id": offer_id},
            format="json",
            HTTP_X_CSRFTOKEN=owner_offers_tok,
        )
        self.assertEqual(selected.status_code, status.HTTP_200_OK)
        self.assertEqual(selected.json()["selected_offer_id"], offer_id)

        status_url = reverse("request_board_api:request_status", args=[req_id])
        self._assert_csrf_rejected(
            lambda tok: owner_offers_client.patch(
                status_url,
                data={"status": "fulfilled"},
                format="json",
                **({"HTTP_X_CSRFTOKEN": tok} if tok else {}),
            )
        )
        fulfilled = owner_offers_client.patch(
            status_url,
            data={"status": "fulfilled"},
            format="json",
            HTTP_X_CSRFTOKEN=owner_offers_tok,
        )
        self.assertEqual(fulfilled.status_code, status.HTTP_200_OK)
        self.assertEqual(fulfilled.json()["status"], Request.STATUS_FULFILLED)

        owner_board_client, owner_board_tok = self._csrf_client(
            self.owner,
            reverse("request_board:request_board"),
        )
        detail_url = reverse("request_board_api:request_detail", args=[req_id])
        self._assert_csrf_rejected(
            lambda tok: owner_board_client.delete(
                detail_url,
                **({"HTTP_X_CSRFTOKEN": tok} if tok else {}),
            )
        )
        deleted = owner_board_client.delete(detail_url, HTTP_X_CSRFTOKEN=owner_board_tok)
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(deleted.content, b"")
        self.assertFalse(Request.objects.filter(pk=req_id).exists())

    def test_anonymous_403_messages_include_auth_keyword_and_nonexistent_offer_returns_403(self):
        target = Request.objects.create(
            owner=self.owner,
            title="Bantuan beras",
            food_item="Beras",
            quantity=Decimal("2.00"),
            unit="kg",
        )
        offer = RequestOffer.objects.create(request=target, offered_by=self.donor)
        before = self.database_snapshot()
        anon = APIClient()

        calls = [
            ("post", reverse("request_board_api:request_list"), {"title": "x"}),
            ("delete", reverse("request_board_api:request_detail", args=[target.pk]), None),
            ("get", reverse("request_board_api:request_offer_list", args=[target.pk]), None),
            ("post", reverse("request_board_api:request_offer_list", args=[target.pk]), {}),
            ("get", reverse("request_board_api:request_offer_detail", args=[target.pk, offer.pk]), None),
            ("get", reverse("request_board_api:request_offer_detail", args=[target.pk, 999999]), None),
            ("patch", reverse("request_board_api:request_selection", args=[target.pk]), {"offer_id": offer.pk}),
            ("patch", reverse("request_board_api:request_status", args=[target.pk]), {"status": "fulfilled"}),
            ("get", reverse("request_board_api:request_creation_status", args=[uuid4()]), None),
        ]
        for method, url, payload in calls:
            with self.subTest(method=method, url=url):
                resp = getattr(anon, method)(url, data=payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                detail = resp.json()["detail"].lower()
                self.assertTrue(
                    "autentikasi" in detail or "authentication" in detail,
                    f"Unexpected auth detail for {method} {url}: {detail}",
                )
        self.assertEqual(self.database_snapshot(), before)

        other_client = APIClient()
        other_client.force_authenticate(user=self.other)
        non_owner_missing_offer = other_client.get(
            reverse("request_board_api:request_offer_detail", args=[target.pk, 999999])
        )
        self.assertEqual(non_owner_missing_offer.status_code, status.HTTP_403_FORBIDDEN)

    def test_selection_and_status_reject_415_and_malformed_json_without_db_writes(self):
        target = Request.objects.create(
            owner=self.owner,
            title="Bantuan susu",
            food_item="Susu",
            quantity=Decimal("1.00"),
            unit="liter",
            status=Request.STATUS_OFFERED,
        )
        offer = RequestOffer.objects.create(request=target, offered_by=self.donor)
        owner_client = APIClient()
        owner_client.force_authenticate(user=self.owner)
        before = self.database_snapshot()

        endpoints = [
            (reverse("request_board_api:request_selection", args=[target.pk]), {"offer_id": offer.pk}),
            (reverse("request_board_api:request_status", args=[target.pk]), {"status": "fulfilled"}),
        ]
        for url, valid_payload in endpoints:
            for bad_ct in ("text/plain", "application/x-www-form-urlencoded"):
                with self.subTest(url=url, bad_ct=bad_ct):
                    resp = owner_client.patch(url, data=json.dumps(valid_payload), content_type=bad_ct)
                    self.assertEqual(resp.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
                    self.assertEqual(self.database_snapshot(), before)

            for raw_body, content_type in (
                (b"", "application/json"),
                (b"null", "application/json"),
                (b"[]", "application/json"),
                (b"{bad", "application/json"),
                (b'{"offer_id": NaN}', "application/json"),
                (b'{"offer_id": 1, "offer_id": 2}', "application/json"),
                (b"\xff\xfe", "application/json"),
                (b"\xff\xfe", "application/json; charset=latin-1"),
            ):
                with self.subTest(url=url, raw_body=raw_body, content_type=content_type):
                    resp = owner_client.patch(url, data=raw_body, content_type=content_type)
                    self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertEqual(self.database_snapshot(), before)

    def test_browsable_api_html_renders_and_auth_login_available(self):
        target = Request.objects.create(
            owner=self.owner,
            title="Bantuan roti",
            food_item="Roti",
            quantity=Decimal("5.00"),
            unit="buah",
            status=Request.STATUS_OFFERED,
        )
        offer = RequestOffer.objects.create(request=target, offered_by=self.donor)

        anon = APIClient()
        owner_client = APIClient()
        owner_client.force_authenticate(user=self.owner)
        donor_client = APIClient()
        donor_client.force_authenticate(user=self.donor)

        expectations = [
            (anon, reverse("request_board_api:request_list"), status.HTTP_200_OK),
            (owner_client, reverse("request_board_api:request_detail", args=[target.pk]), status.HTTP_200_OK),
            (anon, reverse("request_board_api:request_offer_list", args=[target.pk]), status.HTTP_403_FORBIDDEN),
            (donor_client, reverse("request_board_api:request_offer_list", args=[target.pk]), status.HTTP_403_FORBIDDEN),
            (owner_client, reverse("request_board_api:request_offer_list", args=[target.pk]), status.HTTP_200_OK),
            (anon, reverse("request_board_api:request_offer_detail", args=[target.pk, offer.pk]), status.HTTP_403_FORBIDDEN),
            (owner_client, reverse("request_board_api:request_offer_detail", args=[target.pk, offer.pk]), status.HTTP_200_OK),
            (anon, reverse("request_board_api:request_selection", args=[target.pk]), status.HTTP_403_FORBIDDEN),
            (owner_client, reverse("request_board_api:request_selection", args=[target.pk]), status.HTTP_405_METHOD_NOT_ALLOWED),
            (anon, reverse("request_board_api:request_status", args=[target.pk]), status.HTTP_403_FORBIDDEN),
            (owner_client, reverse("request_board_api:request_status", args=[target.pk]), status.HTTP_405_METHOD_NOT_ALLOWED),
            (anon, reverse("request_board_api:request_creation_status", args=[uuid4()]), status.HTTP_403_FORBIDDEN),
            (owner_client, reverse("request_board_api:request_creation_status", args=[uuid4()]), status.HTTP_404_NOT_FOUND),
        ]
        for client, url, expected_status in expectations:
            with self.subTest(url=url, expected_status=expected_status):
                resp = client.get(url, HTTP_ACCEPT="text/html")
                self.assertEqual(resp.status_code, expected_status)
                self.assertIn("text/html", resp["Content-Type"])

        login_resp = anon.get("/api-auth/login/")
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
        self.assertIn("text/html", login_resp["Content-Type"])
        self.assertIn("csrftoken", login_resp.cookies)

    def test_end_to_end_multi_actor_lifecycle_and_legacy_adapter_coexistence(self):
        owner_client = APIClient()
        owner_client.force_authenticate(user=self.owner)
        donor_client = APIClient()
        donor_client.force_authenticate(user=self.donor)
        other_client = APIClient()
        other_client.force_authenticate(user=self.other)

        key = str(uuid4())
        create_resp = owner_client.post(
            reverse("request_board_api:request_list"),
            data={
                "title": "Butuh nasi kotak",
                "food_item": "Nasi Kotak",
                "quantity": "10.00",
                "unit": "porsi",
                "description": "Private description test",
                "location": "Bandung",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        req_id = create_resp.json()["id"]

        key_status_created = owner_client.get(
            reverse("request_board_api:request_creation_status", args=[key])
        )
        self.assertEqual(key_status_created.status_code, status.HTTP_200_OK)
        self.assertEqual(key_status_created.json(), {"state": "created", "request_id": req_id})

        offer_resp = donor_client.post(
            reverse("request_board_api:request_offer_list", args=[req_id]),
            data={},
            format="json",
        )
        self.assertEqual(offer_resp.status_code, status.HTTP_201_CREATED)
        offer_id = offer_resp.json()["id"]

        other_detail = other_client.get(reverse("request_board_api:request_detail", args=[req_id]))
        self.assertEqual(other_detail.status_code, status.HTTP_200_OK)
        other_json = other_detail.json()
        self.assertEqual(other_json["offer_count"], 1)
        self.assertIs(other_json["has_offered"], False)
        self.assertNotIn("offered_by", other_detail.content.decode("utf-8"))
        self.assertNotIn(self.donor.email, other_detail.content.decode("utf-8"))

        self.assertEqual(
            other_client.get(reverse("request_board_api:request_offer_list", args=[req_id])).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        owner_offers = owner_client.get(reverse("request_board_api:request_offer_list", args=[req_id]))
        self.assertEqual(owner_offers.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_offers.json()["count"], 1)

        owner_offer_detail = owner_client.get(
            reverse("request_board_api:request_offer_detail", args=[req_id, offer_id])
        )
        self.assertEqual(owner_offer_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(owner_offer_detail.json()["id"], offer_id)

        sel_resp = owner_client.patch(
            reverse("request_board_api:request_selection", args=[req_id]),
            data={"offer_id": offer_id},
            format="json",
        )
        self.assertEqual(sel_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(sel_resp.json()["selected_offer_id"], offer_id)

        closed_offer = other_client.post(
            reverse("request_board_api:request_offer_list", args=[req_id]),
            data={},
            format="json",
        )
        self.assertEqual(closed_offer.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(closed_offer.json()["code"], "offers_closed")

        self.assertEqual(
            donor_client.patch(
                reverse("request_board_api:request_status", args=[req_id]),
                data={"status": "fulfilled"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        fulfill_resp = owner_client.patch(
            reverse("request_board_api:request_status", args=[req_id]),
            data={"status": "fulfilled"},
            format="json",
        )
        self.assertEqual(fulfill_resp.status_code, status.HTTP_200_OK)
        replay_fulfill = owner_client.patch(
            reverse("request_board_api:request_status", args=[req_id]),
            data={"status": "fulfilled"},
            format="json",
        )
        self.assertEqual(replay_fulfill.status_code, status.HTTP_200_OK)
        self.assertEqual(replay_fulfill.json(), fulfill_resp.json())

        # Legacy adapter JSON and SSR board remain operational alongside /api/v1/.
        self.assertEqual(self.client.get(reverse("request_board:api_request_collection")).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(reverse("request_board:request_board")).status_code, status.HTTP_200_OK)

        del_resp = owner_client.delete(reverse("request_board_api:request_detail", args=[req_id]))
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(del_resp.content, b"")

        key_status_deleted = owner_client.get(
            reverse("request_board_api:request_creation_status", args=[key])
        )
        self.assertEqual(key_status_deleted.status_code, status.HTTP_200_OK)
        self.assertEqual(key_status_deleted.json(), {"state": "deleted", "request_id": None})

        replay_deleted = owner_client.post(
            reverse("request_board_api:request_list"),
            data={
                "title": "Butuh nasi kotak",
                "food_item": "Nasi Kotak",
                "quantity": "10.00",
                "unit": "porsi",
                "description": "Private description test",
                "location": "Bandung",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        self.assertEqual(replay_deleted.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(replay_deleted.json()["code"], "idempotency_deleted")
