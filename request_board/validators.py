"""Shared input sanitization and validation helpers for Request Board."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import ProhibitNullCharactersValidator
from django.utils import timezone
from django.utils.html import strip_tags

TITLE_MAX_LENGTH = 120
FOOD_ITEM_MAX_LENGTH = 120
LOCATION_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 1000

QUANTITY_MIN = Decimal("0.01")
QUANTITY_MAX_DIGITS = 8
QUANTITY_DECIMAL_PLACES = 2

DEFAULT_EXPIRATION_HOURS = 24

ALLOWED_CREATE_FIELDS = (
    "title",
    "food_item",
    "quantity",
    "unit",
    "description",
    "location",
    "expires_at",
)

_null_char_validator = ProhibitNullCharactersValidator(
    message="Karakter null tidak diperbolehkan.",
    code="null_characters_not_allowed",
)


def clean_request_text(
    value: str,
    *,
    max_length: int,
    required: bool = False,
) -> str:
    """Strip HTML tags and outer whitespace, then enforce emptiness and length rules."""
    _null_char_validator(value)
    cleaned = strip_tags(value).strip()
    if required and not cleaned:
        raise ValidationError("Kolom ini wajib diisi.", code="required")
    if len(cleaned) > max_length:
        raise ValidationError(
            f"Gunakan paling banyak {max_length} karakter.",
            code="max_length",
        )
    return cleaned


def default_expires_at():
    """Return the default expiration deadline (+24 hours from now)."""
    return timezone.now() + timedelta(hours=DEFAULT_EXPIRATION_HOURS)


def validate_future_expires_at(value):
    """Ensure an explicit expiration timestamp lies strictly in the future."""
    if value <= timezone.now():
        raise ValidationError(
            "Batas waktu harus di masa mendatang.",
            code="invalid",
        )
    return value
