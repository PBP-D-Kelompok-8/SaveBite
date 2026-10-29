from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import (
    BooleanField,
    Count,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    Value,
    prefetch_related_objects,
)

from request_board.forms import RequestForm
from request_board.models import Request, RequestOffer

PAGE_SIZE = 20
OFFER_PREVIEW_LIMIT = 20


def _require_saved_user(actor) -> None:
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or getattr(actor, "pk", None) is None
        or getattr(getattr(actor, "_state", None), "adding", False)
    ):
        raise PermissionDenied("Aktor tidak terautentikasi atau belum tersimpan.")


def create_request(*, actor, data) -> Request:
    _require_saved_user(actor)

    form = RequestForm(data=data)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())

    food_request = form.save(commit=False)
    food_request.owner = actor
    food_request.save()
    return food_request


def offer_request(*, actor, request_id: int) -> tuple[RequestOffer, bool]:
    _require_saved_user(actor)

    with transaction.atomic():
        locked_request = Request.objects.select_for_update().get(pk=request_id)

        if locked_request.owner_id == actor.pk:
            raise PermissionDenied("Pemilik request tidak dapat membuat tawaran.")

        if locked_request.status == Request.STATUS_FULFILLED:
            raise ValidationError(
                "Request sudah terpenuhi.",
                code="request_fulfilled",
            )

        existing = RequestOffer.objects.filter(request=locked_request, offered_by=actor).first()
        if locked_request.selected_offer_id and existing is None:
            raise ValidationError("Penawaran sudah ditutup.", code="offers_closed")

        offer, created = RequestOffer.objects.get_or_create(
            request=locked_request,
            offered_by=actor,
        )

        if locked_request.status == Request.STATUS_PENDING:
            locked_request.status = Request.STATUS_OFFERED
            locked_request.save(update_fields=["status", "updated_at"])

        return offer, created


def select_request_offer(*, actor, request_id: int, offer_id: int) -> Request:
    _require_saved_user(actor)
    with transaction.atomic():
        locked_request = Request.objects.select_for_update().get(pk=request_id)
        if locked_request.owner_id != actor.pk:
            raise PermissionDenied("Hanya pemilik request yang dapat memilih penawar.")
        if locked_request.status == Request.STATUS_FULFILLED:
            raise ValidationError("Request sudah terpenuhi.", code="request_fulfilled")
        offer = RequestOffer.objects.get(pk=offer_id, request=locked_request)
        if locked_request.selected_offer_id != offer.pk:
            locked_request.selected_offer = offer
            locked_request.save(update_fields=["selected_offer", "updated_at"])
        return locked_request


def fulfill_request(*, actor, request_id: int) -> Request:
    _require_saved_user(actor)

    with transaction.atomic():
        locked_request = Request.objects.select_for_update().get(pk=request_id)

        if locked_request.owner_id != actor.pk:
            raise PermissionDenied("Hanya pemilik request yang dapat memenuhi request.")

        if locked_request.status != Request.STATUS_FULFILLED and not locked_request.selected_offer_id:
            raise ValidationError(
                "Pilih penawar sebelum menandai request terpenuhi.",
                code="offer_selection_required",
            )

        if locked_request.status == Request.STATUS_OFFERED:
            locked_request.status = Request.STATUS_FULFILLED
            locked_request.save(update_fields=["status", "updated_at"])

        return locked_request


def delete_request(*, actor, request_id: int) -> None:
    _require_saved_user(actor)

    with transaction.atomic():
        locked_request = Request.objects.select_for_update().get(pk=request_id)

        if locked_request.owner_id != actor.pk:
            raise PermissionDenied("Hanya pemilik request yang dapat menghapus request.")

        locked_request.delete()


def _requests_for_display(actor):
    """Muat pemilik, jumlah penawaran, dan status penawaran pengguna sekaligus."""
    if actor is not None and getattr(actor, "is_authenticated", False):
        has_offered = Exists(
            RequestOffer.objects.filter(request=OuterRef("pk"), offered_by=actor)
        )
    else:
        has_offered = Value(False, output_field=BooleanField())

    return Request.objects.select_related("owner", "selected_offer__offered_by").annotate(
        offer_count=Count("offers", distinct=True),
        has_offered=has_offered,
    )


def get_board_page(*, actor=None, page_value: str | int | None = None, filters=None):
    """Ambil satu halaman board dengan urutan terbaru yang stabil."""
    filters = filters or {}
    queryset = _requests_for_display(actor)
    if filters.get("q"):
        term = filters["q"]
        queryset = queryset.filter(
            Q(title__icontains=term) | Q(food_item__icontains=term) | Q(location__icontains=term)
        )
    if filters.get("title"):
        queryset = queryset.filter(title__icontains=filters["title"])
    if filters.get("food_item"):
        queryset = queryset.filter(food_item__iexact=filters["food_item"])
    if filters.get("status"):
        queryset = queryset.filter(status=filters["status"])
    if filters.get("location"):
        queryset = queryset.filter(location__iexact=filters["location"])
    if filters.get("mine"):
        _require_saved_user(actor)
        queryset = queryset.filter(owner=actor)
    ordering = ("created_at", "id") if filters.get("sort") == "oldest" else ("-created_at", "-id")
    queryset = queryset.order_by(*ordering)
    paginator = Paginator(queryset, PAGE_SIZE)

    if page_value is None:
        page_number = 1
    elif isinstance(page_value, str):
        if not (
            page_value.isascii()
            and page_value.isdigit()
            and any(char in "123456789" for char in page_value)
        ):
            raise PageNotAnInteger("Nomor halaman harus berupa bilangan bulat positif.")

        page_digits = page_value.lstrip("0")
        max_page_digits = str(paginator.num_pages)
        # Bandingkan sebagai teks sebelum int() agar input ribuan digit tetap aman.
        if len(page_digits) > len(max_page_digits) or (
            len(page_digits) == len(max_page_digits) and page_digits > max_page_digits
        ):
            raise EmptyPage("Nomor halaman melebihi rentang yang tersedia.")
        page_number = int(page_digits)
    elif isinstance(page_value, int):
        if page_value < 1:
            raise EmptyPage("Nomor halaman harus berupa bilangan bulat positif.")
        page_number = page_value
    else:
        raise PageNotAnInteger("Tipe nomor halaman tidak didukung.")

    return paginator.page(page_number)


def get_request_for_display(*, request_id: int, actor=None) -> Request:
    return _requests_for_display(actor).get(pk=request_id)


def get_owner_offer_previews(*, actor=None, requests=None) -> dict[int, dict]:
    """Ambil maksimal 20 penawar terbaru per permintaan, hanya untuk pemilik."""
    if actor is None or not getattr(actor, "is_authenticated", False) or not requests:
        return {}

    owner_requests = [
        food_request for food_request in requests
        if getattr(food_request, "owner_id", None) == actor.pk
    ]
    if not owner_requests:
        return {}

    # Batas berlaku per permintaan di SQL; to_attr menjaga cache offers tetap utuh.
    prefetch_related_objects(
        owner_requests,
        Prefetch(
            "offers",
            queryset=RequestOffer.objects.select_related("offered_by").order_by(
                "-created_at", "-id"
            )[:OFFER_PREVIEW_LIMIT],
            to_attr="preview_offers",
        ),
    )

    previews = {}
    for food_request in owner_requests:
        offers = getattr(food_request, "preview_offers", [])
        total = getattr(food_request, "offer_count", None)
        if total is None:
            total = food_request.offers.count()
        else:
            total = int(total)

        previews[food_request.pk] = {
            "items": [
                {
                    "display_name": offer.offered_by.get_username(),
                    "created_at": offer.created_at,
                }
                for offer in offers
            ],
            "total": total,
            "has_more": total > OFFER_PREVIEW_LIMIT,
        }
    return previews
