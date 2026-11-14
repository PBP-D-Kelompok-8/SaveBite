"""Function-based DRF API views for Request Board v1 endpoints."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import json
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import PageNotAnInteger
from django.views.decorators.cache import never_cache
from rest_framework import exceptions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import JSONParser, get_encoding
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from request_board.api_exceptions import handle_domain_exceptions
from request_board.forms import BoardFilterForm
from request_board.models import Request, RequestCreationKey, RequestOffer
from request_board.permissions import (
    CanAccessRequestOffers,
    IsRequestOwner,
    IsRequestOwnerOrReadOnly,
    check_object_permissions,
)
from request_board.serializers import (
    RequestCreateSerializer,
    RequestOfferCreateSerializer,
    RequestOfferSerializer,
    RequestSelectionSerializer,
    RequestSerializer,
    RequestStatusSerializer,
)
from request_board.services import (
    create_request_from_api_idempotent,
    delete_request,
    fulfill_request,
    get_board_page,
    get_request_for_display,
    get_request_offers_page,
    offer_request,
    select_request_offer,
)


def _reject_nonfinite_constant(constant: str):
    """Reject NaN and Infinity literals accepted by Python's default JSON parser."""
    raise ValueError(f"Literal non-finite '{constant}' tidak diizinkan.")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """Reject duplicate JSON object keys to prevent ambiguous canonical digests."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Kunci duplikat terdeteksi: '{key}'.")
        result[key] = value
    return result


class StrictJSONParser(JSONParser):
    """JSON parser preserving Decimal precision and rejecting duplicate/non-finite inputs."""

    def parse(self, stream, media_type=None, parser_context=None):
        parser_context = parser_context or {}
        encoding = get_encoding(parser_context)
        if encoding.lower().replace("-", "") != "utf8":
            raise exceptions.ParseError("Charset tidak didukung. Gunakan UTF-8.")

        try:
            raw_text = stream.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise exceptions.ParseError("Body request harus berformat UTF-8 valid.") from exc

        if not raw_text:
            raise exceptions.ParseError("Body request tidak boleh kosong.")

        try:
            return json.loads(
                raw_text,
                parse_float=Decimal,
                parse_constant=_reject_nonfinite_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (json.JSONDecodeError, ValueError, InvalidOperation) as exc:
            raise exceptions.ParseError(f"Format JSON tidak valid: {exc}") from exc


def _parse_idempotency_key(request) -> UUID:
    raw_key = request.headers.get("Idempotency-Key")
    if raw_key is None or not isinstance(raw_key, str) or not raw_key.strip():
        raise exceptions.ValidationError(
            {"detail": "Header Idempotency-Key wajib diisi dengan UUID yang valid."}
        )
    try:
        return UUID(raw_key.strip())
    except ValueError as exc:
        raise exceptions.ValidationError(
            {"detail": "Header Idempotency-Key tidak valid; gunakan format UUID."}
        ) from exc


def _extract_page_value(request) -> str | None:
    page_param_list = request.query_params.getlist("page")
    if len(page_param_list) > 1:
        raise PageNotAnInteger("Parameter halaman tidak boleh berulang.")
    return page_param_list[0] if page_param_list else None


def _paginated_response(page, results_data) -> Response:
    return Response(
        {
            "count": page.paginator.count,
            "page": page.number,
            "page_size": page.paginator.per_page,
            "total_pages": page.paginator.num_pages,
            "results": results_data,
        },
        status=status.HTTP_200_OK,
    )


def _refresh_request_response(request, *, request_id: int, status_code: int = status.HTTP_200_OK) -> Response:
    persisted_request = get_request_for_display(
        request_id=request_id,
        actor=request.user,
    )
    serializer = RequestSerializer(persisted_request, context={"request": request})
    return Response(serializer.data, status=status_code)


def _handle_request_list_get(request):
    """Return a paginated, filtered public list of food rescue requests."""
    page_value = _extract_page_value(request)

    filter_form = BoardFilterForm(request.query_params)
    if not filter_form.is_valid():
        raise ValidationError(filter_form.errors.as_data())

    if filter_form.cleaned_data["mine"] and not request.user.is_authenticated:
        raise PermissionDenied("Silakan masuk untuk melihat permintaan Anda.")

    page = get_board_page(
        actor=request.user,
        page_value=page_value,
        filters=filter_form.cleaned_data,
    )
    serializer = RequestSerializer(
        page.object_list,
        many=True,
        context={"request": request},
    )
    return _paginated_response(page, serializer.data)


def _handle_request_list_post(request):
    """Create a Request idempotently from JSON input under an owner row lock."""
    payload = request.data
    if not isinstance(payload, Mapping):
        raise exceptions.ValidationError(
            {"detail": "Payload harus berupa object."},
            code="invalid_payload",
        )

    key = _parse_idempotency_key(request)
    create_serializer = RequestCreateSerializer(
        data=payload,
        context={"defer_future_check": True},
    )
    create_serializer.is_valid(raise_exception=True)

    food_request, created = create_request_from_api_idempotent(
        actor=request.user,
        validated_data=create_serializer.validated_data,
        key=key,
    )
    return _refresh_request_response(
        request,
        request_id=food_request.pk,
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsRequestOwnerOrReadOnly])
@parser_classes([StrictJSONParser])
@handle_domain_exceptions
def request_list(request):
    """List public requests or create a new request idempotently."""
    if request.method == "GET":
        return _handle_request_list_get(request)
    return _handle_request_list_post(request)


@never_cache
@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@handle_domain_exceptions
def request_creation_status(request, key):
    """Return private idempotency status for a creation key owned by request.user."""
    record = RequestCreationKey.objects.filter(owner=request.user, key=key).first()
    if record is None:
        raise exceptions.NotFound(detail="Kunci pembuatan request tidak ditemukan.")

    if record.request_id is None:
        return Response(
            {"state": "deleted", "request_id": None},
            status=status.HTTP_200_OK,
        )

    return Response(
        {"state": "created", "request_id": record.request_id},
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsRequestOwnerOrReadOnly])
@handle_domain_exceptions
def request_detail(request, request_id: int):
    """Retrieve a single request publicly or delete it as its owner."""
    food_request = get_request_for_display(request_id=request_id, actor=request.user)
    check_object_permissions(request, food_request)

    if request.method == "GET":
        serializer = RequestSerializer(food_request, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.body:
        raise exceptions.ValidationError(
            {"detail": "Body request untuk menghapus request harus kosong."}
        )

    delete_request(actor=request.user, request_id=food_request.pk)
    return Response(status=status.HTTP_204_NO_CONTENT)


def _handle_request_offer_list_get(request, food_request: Request):
    page_value = _extract_page_value(request)
    page = get_request_offers_page(food_request=food_request, page_value=page_value)
    serializer = RequestOfferSerializer(
        page.object_list,
        many=True,
        context={"request": request, "food_request": food_request},
    )
    return _paginated_response(page, serializer.data)


def _handle_request_offer_list_post(request, food_request: Request):
    if not request.body:
        raise exceptions.ParseError("Body request tidak boleh kosong.")
    offer_serializer = RequestOfferCreateSerializer(data=request.data)
    offer_serializer.is_valid(raise_exception=True)

    offer, created = offer_request(actor=request.user, request_id=food_request.pk)
    persisted_request = get_request_for_display(
        request_id=food_request.pk,
        actor=request.user,
    )
    persisted_offer = RequestOffer.objects.select_related("offered_by").get(pk=offer.pk)
    response_serializer = RequestOfferSerializer(
        persisted_offer,
        context={"request": request, "food_request": persisted_request},
    )
    return Response(
        response_serializer.data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@never_cache
@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([CanAccessRequestOffers])
@parser_classes([StrictJSONParser])
@handle_domain_exceptions
def request_offer_list(request, request_id: int):
    """List offers privately for the request owner or submit an offer as a non-owner."""
    food_request = Request.objects.only("id", "owner_id", "selected_offer_id").get(pk=request_id)
    check_object_permissions(request, food_request)

    if request.method == "GET":
        return _handle_request_offer_list_get(request, food_request)
    return _handle_request_offer_list_post(request, food_request)


@never_cache
@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsRequestOwner])
@handle_domain_exceptions
def request_offer_detail(request, request_id: int, offer_id: int):
    """Retrieve a single offer scoped to the parent Request for its owner."""
    food_request = Request.objects.only("id", "owner_id", "selected_offer_id").get(pk=request_id)
    check_object_permissions(request, food_request)

    offer = RequestOffer.objects.select_related("offered_by").get(
        pk=offer_id,
        request=food_request,
    )
    serializer = RequestOfferSerializer(
        offer,
        context={"request": request, "food_request": food_request},
    )
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["PATCH"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsRequestOwner])
@parser_classes([StrictJSONParser])
@handle_domain_exceptions
def request_selection(request, request_id: int):
    """Select or replace the chosen offer on an active Request as its owner."""
    food_request = Request.objects.only("id", "owner_id").get(pk=request_id)
    check_object_permissions(request, food_request)

    selection_serializer = RequestSelectionSerializer(data=request.data)
    selection_serializer.is_valid(raise_exception=True)

    select_request_offer(
        actor=request.user,
        request_id=food_request.pk,
        offer_id=selection_serializer.validated_data["offer_id"],
    )
    return _refresh_request_response(request, request_id=food_request.pk)


@api_view(["PATCH"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsRequestOwner])
@parser_classes([StrictJSONParser])
@handle_domain_exceptions
def request_status(request, request_id: int):
    """Mark an active Request with a selected offer as fulfilled as its owner."""
    food_request = Request.objects.only("id", "owner_id").get(pk=request_id)
    check_object_permissions(request, food_request)

    status_serializer = RequestStatusSerializer(data=request.data)
    status_serializer.is_valid(raise_exception=True)

    fulfill_request(actor=request.user, request_id=food_request.pk)
    return _refresh_request_response(request, request_id=food_request.pk)
