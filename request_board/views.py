import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, InvalidPage, PageNotAnInteger
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
    JsonResponse,
)
from django.template.response import TemplateResponse
from django.urls import reverse

from request_board.forms import RequestForm
from request_board.models import Request
from request_board.services import (
    create_request,
    delete_request,
    fulfill_request,
    get_board_page,
    get_owner_offer_previews,
    get_request_for_display,
    offer_request,
)

ALLOWED_CREATE_FIELDS = frozenset(RequestForm.Meta.fields)
TEXT_FIELDS = tuple(field for field in RequestForm.Meta.fields if field != "quantity")


class JsonParseError(Exception):
    """Kesalahan format atau tipe payload JSON dari klien."""

    def __init__(self, *, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _reject_nonfinite_constant(constant: str):
    """Tolak NaN dan Infinity yang diterima parser bawaan Python."""
    raise ValueError(f"Literal non-finite '{constant}' tidak diizinkan.")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """Tolak kunci duplikat, termasuk pada object bersarang."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Kunci duplikat terdeteksi: '{key}'.")
        result[key] = value
    return result


def json_error(
    *,
    code: str,
    message: str,
    status: int,
    fields: dict | None = None,
) -> JsonResponse:
    payload = {
        "error": {
            "code": code,
            "message": message,
            "fields": fields if fields is not None else {},
        }
    }
    return JsonResponse(payload, status=status)


def _api_method_not_allowed(request, allowed_methods: str) -> JsonResponse:
    response = json_error(
        code="method_not_allowed",
        message=f"Metode {request.method} tidak diizinkan.",
        status=405,
    )
    response["Allow"] = allowed_methods
    return response


def _api_service_error(
    error, *, conflict_code=None, not_found_message="Request tidak ditemukan."
):
    if isinstance(error, Request.DoesNotExist):
        return json_error(code="request_not_found", message=not_found_message, status=404)

    if isinstance(error, PermissionDenied):
        return json_error(
            code="permission_denied", message=str(error) or "Izin ditolak.", status=403,
        )

    if (
        isinstance(error, ValidationError)
        and conflict_code is not None
        and getattr(error, "code", None) == conflict_code
    ):
        return json_error(code=conflict_code, message=str(error.message), status=409)

    raise error


def serialize_request(food_request: Request) -> dict:
    """Kirim hanya field publik; identitas penawar tetap privat."""
    owner = food_request.owner
    owner_username = owner.get_username() if owner else ""

    offer_count = getattr(food_request, "offer_count", None)
    if offer_count is None:
        offer_count = food_request.offers.count()

    return {
        "id": food_request.id,
        "title": food_request.title,
        "food_item": food_request.food_item,
        "quantity": format(food_request.quantity, ".2f"),
        "unit": food_request.unit,
        "description": food_request.description or "",
        "location": food_request.location or "",
        "owner": {"display_name": owner_username},
        "status": food_request.status,
        "created_at": food_request.created_at.isoformat(),
        "updated_at": food_request.updated_at.isoformat(),
        "offer_count": int(offer_count),
    }


def parse_json_object(request, *, allow_empty: bool = False) -> dict:
    """Baca object JSON UTF-8; gunakan Decimal agar jumlah tetap presisi."""
    if allow_empty and not request.body:
        return {}

    if request.content_type != "application/json":
        raise JsonParseError(
            code="invalid_content_type",
            message="Media type tidak didukung. Gunakan application/json.",
        )

    charset = request.content_params.get("charset")
    if charset is not None and charset.strip().lower() != "utf-8":
        raise JsonParseError(
            code="invalid_content_type",
            message="Charset tidak didukung. Gunakan UTF-8.",
        )

    if not request.body:
        raise JsonParseError(
            code="invalid_json",
            message="Body request tidak boleh kosong.",
        )

    try:
        raw_text = request.body.decode("utf-8")
    except UnicodeDecodeError:
        raise JsonParseError(
            code="invalid_json",
            message="Body request harus berformat UTF-8 valid.",
        )

    try:
        payload = json.loads(
            raw_text,
            parse_float=Decimal,
            parse_constant=_reject_nonfinite_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError, InvalidOperation):
        raise JsonParseError(
            code="invalid_json",
            message="Format JSON tidak valid.",
        )

    if not isinstance(payload, dict):
        raise JsonParseError(
            code="invalid_payload",
            message="Payload JSON harus berupa object.",
        )

    return payload


def _validate_create_keys(payload: dict) -> None:
    """Tolak field tambahan sebelum memeriksa tipe dan isi."""
    unknown_keys = set(payload) - ALLOWED_CREATE_FIELDS
    if unknown_keys:
        raise JsonParseError(
            code="invalid_fields",
            message="Payload memuat field yang tidak diizinkan.",
        )


def _validate_create_types(payload: dict) -> None:
    """Periksa tipe JSON sebelum validasi isi melalui RequestForm."""
    for field_name in TEXT_FIELDS:
        if field_name in payload and not isinstance(payload[field_name], str):
            raise JsonParseError(
                code="invalid_payload",
                message=f"Field '{field_name}' harus berupa string.",
            )

    if "quantity" in payload:
        quantity_value = payload["quantity"]
        is_boolean = isinstance(quantity_value, bool)
        is_decimal_input = isinstance(quantity_value, (str, int, Decimal))
        if is_boolean or not is_decimal_input:
            raise JsonParseError(
                code="invalid_payload",
                message="Field 'quantity' harus berupa string desimal atau number finite.",
            )


def _handle_create_request(request) -> JsonResponse:
    if not request.user.is_authenticated:
        return json_error(
            code="authentication_required",
            message="Autentikasi diperlukan untuk membuat request.",
            status=401,
        )

    try:
        payload = parse_json_object(request, allow_empty=False)
        _validate_create_keys(payload)
        _validate_create_types(payload)
    except JsonParseError as exc:
        return json_error(code=exc.code, message=exc.message, status=exc.status)

    try:
        created_request = create_request(actor=request.user, data=payload)
    except ValidationError as exc:
        field_errors = {}
        if hasattr(exc, "message_dict"):
            for field_name, messages in exc.message_dict.items():
                field_errors[field_name] = [str(message) for message in messages]
        elif hasattr(exc, "messages"):
            field_errors["__all__"] = [str(message) for message in exc.messages]
        else:
            field_errors["__all__"] = [str(exc)]
        return json_error(
            code="validation_error",
            message="Validasi data request gagal.",
            status=400,
            fields=field_errors,
        )
    except PermissionDenied as exc:
        return _api_service_error(exc)

    try:
        persisted_request = get_request_for_display(
            request_id=created_request.pk,
            actor=request.user,
        )
    except Request.DoesNotExist as exc:
        return _api_service_error(
            exc, not_found_message="Request yang baru dibuat tidak ditemukan.",
        )

    response_payload = {
        "data": serialize_request(persisted_request),
    }
    return JsonResponse(response_payload, status=201)


def _handle_list_requests(request) -> JsonResponse:
    """Kirim halaman permintaan publik beserta informasi pagination."""
    page_param_list = request.GET.getlist("page")
    if len(page_param_list) > 1:
        return json_error(
            code="invalid_page",
            message="Parameter halaman tidak boleh berulang.",
            status=400,
        )

    page_value = page_param_list[0] if page_param_list else None

    try:
        page = get_board_page(actor=request.user, page_value=page_value)
    except PageNotAnInteger:
        return json_error(
            code="invalid_page",
            message="Nomor halaman harus berupa bilangan bulat positif.",
            status=400,
        )
    except EmptyPage:
        return json_error(
            code="page_not_found",
            message="Halaman yang diminta tidak ditemukan.",
            status=404,
        )
    except InvalidPage:
        return json_error(
            code="invalid_page",
            message="Nomor halaman tidak valid.",
            status=400,
        )

    pagination_metadata = {
        "page": page.number,
        "page_size": page.paginator.per_page,
        "total_items": page.paginator.count,
        "total_pages": page.paginator.num_pages,
        "has_next": page.has_next(),
        "has_previous": page.has_previous(),
    }
    response_payload = {
        "data": [serialize_request(food_request) for food_request in page.object_list],
        "pagination": pagination_metadata,
    }
    return JsonResponse(response_payload, status=200)


def api_request_collection(request):
    if request.method == "GET":
        return _handle_list_requests(request)

    if request.method == "POST":
        return _handle_create_request(request)

    return _api_method_not_allowed(request, "GET, POST")


def api_request_offer(request, request_id: int) -> JsonResponse:
    """Buat penawaran; pengiriman ulang tidak menambah penawaran."""
    if request.method != "POST":
        return _api_method_not_allowed(request, "POST")

    if not request.user.is_authenticated:
        return json_error(
            code="authentication_required",
            message="Autentikasi diperlukan untuk membuat penawaran.",
            status=401,
        )

    try:
        payload = parse_json_object(request, allow_empty=True)
        if payload:
            raise JsonParseError(
                code="invalid_fields",
                message="Payload penawaran tidak boleh memuat field apa pun.",
            )
    except JsonParseError as exc:
        return json_error(code=exc.code, message=exc.message, status=exc.status)

    try:
        _offer, created = offer_request(actor=request.user, request_id=request_id)
    except (Request.DoesNotExist, PermissionDenied, ValidationError) as exc:
        return _api_service_error(exc, conflict_code="request_fulfilled")

    try:
        persisted_request = get_request_for_display(
            request_id=request_id,
            actor=request.user,
        )
    except Request.DoesNotExist as exc:
        return _api_service_error(exc)

    response_payload = {
        "data": {
            "request": serialize_request(persisted_request),
            "created": created,
        }
    }
    return JsonResponse(response_payload, status=201 if created else 200)


def _validate_status_payload(payload: dict) -> None:
    """Hanya terima status fulfilled; field tambahan ditolak lebih dulu."""
    extra_keys = set(payload) - {"status"}
    if extra_keys:
        raise JsonParseError(
            code="invalid_fields",
            message="Payload memuat field yang tidak diizinkan.",
        )

    if payload.get("status") != "fulfilled":
        raise JsonParseError(
            code="invalid_status",
            message="Status harus bernilai 'fulfilled'.",
        )


def api_request_status(request, request_id: int) -> JsonResponse:
    if request.method != "PUT":
        return _api_method_not_allowed(request, "PUT")

    if not request.user.is_authenticated:
        return json_error(
            code="authentication_required",
            message="Autentikasi diperlukan untuk mengubah status request.",
            status=401,
        )

    try:
        payload = parse_json_object(request, allow_empty=False)
        _validate_status_payload(payload)
    except JsonParseError as exc:
        return json_error(code=exc.code, message=exc.message, status=exc.status)

    try:
        fulfill_request(actor=request.user, request_id=request_id)
    except (Request.DoesNotExist, PermissionDenied, ValidationError) as exc:
        return _api_service_error(exc, conflict_code="request_not_offered")

    try:
        persisted_request = get_request_for_display(
            request_id=request_id,
            actor=request.user,
        )
    except Request.DoesNotExist as exc:
        return _api_service_error(exc)

    response_payload = {
        "data": serialize_request(persisted_request),
    }
    return JsonResponse(response_payload, status=200)


def api_request_delete(request, request_id: int) -> HttpResponse:
    """Hapus permintaan milik pengguna; body wajib kosong."""
    if request.method != "DELETE":
        return _api_method_not_allowed(request, "DELETE")

    if not request.user.is_authenticated:
        return json_error(
            code="authentication_required",
            message="Autentikasi diperlukan untuk menghapus request.",
            status=401,
        )

    if request.body:
        return json_error(
            code="invalid_payload",
            message="Body request untuk menghapus request harus kosong.",
            status=400,
        )

    try:
        delete_request(actor=request.user, request_id=request_id)
    except (Request.DoesNotExist, PermissionDenied) as exc:
        return _api_service_error(exc)

    return HttpResponse(status=204)


def request_board(request):
    """Tampilkan board publik dan ringkasan penawar khusus pemilik."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    page_param_list = request.GET.getlist("page")
    if len(page_param_list) > 1:
        return HttpResponse("Parameter halaman tidak boleh berulang.", status=400)

    page_value = page_param_list[0] if page_param_list else None

    try:
        page_obj = get_board_page(actor=request.user, page_value=page_value)
    except EmptyPage:
        return HttpResponse("Halaman yang diminta tidak ditemukan.", status=404)
    except InvalidPage:
        return HttpResponse("Nomor halaman tidak valid.", status=400)

    page_items = list(page_obj)
    owner_offer_previews = get_owner_offer_previews(
        actor=request.user,
        requests=page_items,
    )

    context = {
        "page_obj": page_obj,
        "owner_offer_previews": owner_offer_previews,
    }
    return TemplateResponse(request, "request_board/index.html", context)


@login_required(login_url="request_board:login")
def request_create(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    form_data = request.POST if request.method == "POST" else None
    form = RequestForm(data=form_data)
    if request.method == "POST" and form.is_valid():
        create_request(actor=request.user, data=request.POST)
        messages.success(request, "Permintaan bantuan makanan berhasil dibuat.")
        return HttpResponseRedirect(reverse("request_board:request_board"))

    return TemplateResponse(request, "request_board/request_form.html", {"form": form})


@login_required(login_url="request_board:login")
def request_action(request, request_id: int, action: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    board_url = reverse("request_board:request_board")
    try:
        if action == "offer":
            _offer, created = offer_request(actor=request.user, request_id=request_id)
        elif action == "fulfill":
            fulfill_request(actor=request.user, request_id=request_id)
        elif action == "delete":
            delete_request(actor=request.user, request_id=request_id)
        else:
            return HttpResponseNotFound("Aksi tidak valid.")
    except Request.DoesNotExist:
        return HttpResponseNotFound("Permintaan makanan tidak ditemukan.")
    except PermissionDenied as exc:
        return HttpResponseForbidden(str(exc) or "Izin ditolak.")
    except ValidationError as exc:
        expected_conflicts = {
            "offer": "request_fulfilled",
            "fulfill": "request_not_offered",
        }
        expected_code = expected_conflicts.get(action)
        if expected_code is None or getattr(exc, "code", None) != expected_code:
            raise
        messages.error(request, str(exc.message))
        return HttpResponseRedirect(board_url)

    if action == "offer":
        if created:
            messages.success(request, "Penawaran bantuan makanan berhasil dikirim.")
        else:
            messages.info(
                request,
                "Anda sudah mengirimkan penawaran untuk permintaan ini sebelumnya.",
            )
    elif action == "fulfill":
        messages.success(request, "Permintaan makanan berhasil diselesaikan.")
    else:
        messages.success(request, "Permintaan makanan berhasil dihapus.")
    return HttpResponseRedirect(board_url)
