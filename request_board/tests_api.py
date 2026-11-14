from types import ModuleType

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, InvalidPage, PageNotAnInteger
from django.http import Http404
from django.middleware.csrf import get_token
from django.test import RequestFactory, override_settings
from django.urls import path, resolve, reverse
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
from request_board.models import Request, RequestOffer


class DRFFoundationTests(APITestCase):
    """Verify DRF installation, local settings isolation, and routing foundation."""

    def test_rest_framework_installed_without_global_policy_overrides(self):
        self.assertIn("rest_framework", settings.INSTALLED_APPS)
        global_drf_settings = getattr(settings, "REST_FRAMEWORK", {})
        self.assertNotIn("DEFAULT_AUTHENTICATION_CLASSES", global_drf_settings)
        self.assertNotIn("DEFAULT_PERMISSION_CLASSES", global_drf_settings)
        self.assertNotIn("EXCEPTION_HANDLER", global_drf_settings)

    def test_api_urls_namespace_mounted_at_api_v1_without_dummy_endpoints(self):
        self.assertEqual(api_urls.app_name, "request_board_api")
        self.assertEqual(api_urls.urlpatterns, [])

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
