"""Local domain exception mapping for Request Board DRF views."""

from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, InvalidPage, PageNotAnInteger
from rest_framework import exceptions, status
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler

from request_board.models import Request, RequestOffer

CONFLICT_ERROR_CODES = frozenset({
    "request_expired",
    "request_fulfilled",
    "offers_closed",
    "offer_selection_required",
    "idempotency_conflict",
    "idempotency_deleted",
})


class ConflictError(exceptions.APIException):
    """DRF 409 Conflict carrying both human-readable detail and machine code."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Terjadi konflik state atau idempotensi."
    default_code = "conflict"

    def __init__(self, detail=None, code=None):
        resolved_detail = self.default_detail if detail is None else detail
        resolved_code = self.default_code if code is None else code
        if isinstance(resolved_detail, dict):
            payload = resolved_detail
        else:
            payload = {"detail": resolved_detail, "code": resolved_code}
        super().__init__(detail=payload, code=resolved_code)


def _format_validation_error(exc: ValidationError) -> exceptions.APIException:
    if hasattr(exc, "error_dict"):
        field_errors = {}
        for field_name, messages in exc.message_dict.items():
            text_messages = [str(message) for message in messages]
            if field_name == "__all__":
                if len(exc.message_dict) == 1 and len(text_messages) == 1:
                    return exceptions.ValidationError(detail={"detail": text_messages[0]})
                key = api_settings.NON_FIELD_ERRORS_KEY
            else:
                key = field_name
            field_errors[key] = text_messages
        return exceptions.ValidationError(detail=field_errors)

    messages = [str(message) for message in exc.messages] if hasattr(exc, "messages") else [str(exc)]
    detail_text = messages[0] if len(messages) == 1 else " ".join(messages)
    return exceptions.ValidationError(
        detail={"detail": detail_text or "Input tidak valid."},
        code=getattr(exc, "code", None) or "invalid",
    )


def map_domain_exception(
    exc: Exception,
    *,
    conflict_codes=CONFLICT_ERROR_CODES,
) -> exceptions.APIException | None:
    """Convert known Django/domain exceptions into DRF APIExceptions (400/403/404/409)."""
    if isinstance(exc, exceptions.APIException):
        return exc

    if isinstance(exc, PermissionDenied):
        return exceptions.PermissionDenied(detail=str(exc) or "Izin ditolak.")

    if isinstance(exc, Request.DoesNotExist):
        return exceptions.NotFound(detail="Request tidak ditemukan.", code="request_not_found")

    if isinstance(exc, RequestOffer.DoesNotExist):
        return exceptions.NotFound(detail="Penawaran tidak ditemukan.", code="offer_not_found")

    if isinstance(exc, ObjectDoesNotExist):
        return exceptions.NotFound(detail="Data tidak ditemukan.", code="not_found")

    if isinstance(exc, EmptyPage):
        return exceptions.NotFound(
            detail=str(exc) or "Halaman yang diminta tidak ditemukan.",
            code="page_not_found",
        )

    if isinstance(exc, PageNotAnInteger):
        return exceptions.ValidationError(
            detail={"detail": str(exc) or "Nomor halaman harus berupa bilangan bulat positif."},
            code="invalid_page",
        )

    if isinstance(exc, InvalidPage):
        return exceptions.ValidationError(
            detail={"detail": str(exc) or "Nomor halaman tidak valid."},
            code="invalid_page",
        )

    if isinstance(exc, ValidationError):
        error_code = getattr(exc, "code", None)
        if error_code is not None and error_code in conflict_codes:
            message = str(exc.message) if hasattr(exc, "message") else " ".join(str(m) for m in exc.messages)
            return ConflictError(detail=message, code=error_code)
        return _format_validation_error(exc)

    return None


def request_board_exception_handler(exc, context):
    """Local DRF exception handler preserving 400/403/404/409 domain error contracts."""
    mapped = map_domain_exception(exc)
    if mapped is not None:
        exc = mapped
    return drf_exception_handler(exc, context)


def handle_domain_exceptions(view_func):
    """Wrap an @api_view handler to map local domain exceptions into DRF responses."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except Exception as exc:
            mapped = map_domain_exception(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    return wrapped
