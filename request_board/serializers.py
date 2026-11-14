"""DRF serializers for Request Board read and input validation contracts."""

from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from request_board.forms import BOARD_FIELD_ERRORS
from request_board.models import Request
from request_board.validators import (
    ALLOWED_CREATE_FIELDS,
    DESCRIPTION_MAX_LENGTH,
    FOOD_ITEM_MAX_LENGTH,
    LOCATION_MAX_LENGTH,
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    QUANTITY_MIN,
    TITLE_MAX_LENGTH,
    clean_request_text,
    default_expires_at,
    validate_future_expires_at,
)

_SERIALIZER_TEXT_ERRORS = {
    **BOARD_FIELD_ERRORS,
    "blank": BOARD_FIELD_ERRORS["required"],
    "null": BOARD_FIELD_ERRORS["required"],
}


class _StrictTextField(serializers.CharField):
    """CharField that rejects non-string primitives such as numbers and booleans."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class RequestOwnerSerializer(serializers.ModelSerializer):
    """Safe public representation of a request owner without sensitive user fields."""

    display_name = serializers.CharField(source="get_username", read_only=True)

    class Meta:
        model = get_user_model()
        fields = ("id", "display_name")
        read_only_fields = ("id", "display_name")


class RequestSerializer(serializers.ModelSerializer):
    """Read-only representation of Request for list, detail, and action responses."""

    owner = RequestOwnerSerializer(read_only=True)
    quantity = serializers.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        coerce_to_string=True,
        read_only=True,
    )
    effective_status = serializers.CharField(read_only=True)
    offer_count = serializers.SerializerMethodField()
    has_offered = serializers.SerializerMethodField()
    selected_offer_id = serializers.SerializerMethodField()

    class Meta:
        model = Request
        fields = (
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
        )
        read_only_fields = fields

    def get_offer_count(self, obj: Request) -> int:
        annotated = getattr(obj, "offer_count", None)
        if annotated is not None:
            return int(annotated)
        return obj.offers.count()

    def get_has_offered(self, obj: Request) -> bool:
        annotated = getattr(obj, "has_offered", None)
        if annotated is not None:
            return bool(annotated)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return obj.offers.filter(offered_by_id=user.pk).exists()

    def get_selected_offer_id(self, obj: Request) -> int | None:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and obj.owner_id == user.pk
        ):
            return obj.selected_offer_id
        return None


class _CreateExpiresAtField(serializers.DateTimeField):
    """DateTimeField that leaves omitted deadlines unset when temporal checks are deferred."""

    def get_default(self):
        parent = self.parent
        if parent is not None and parent.context.get("defer_future_check"):
            raise serializers.SkipField()
        return super().get_default()


class RequestCreateSerializer(serializers.ModelSerializer):
    """Strict input serializer for creating a Request (consumed by T03)."""

    title = _StrictTextField(
        max_length=TITLE_MAX_LENGTH,
        required=True,
        allow_blank=False,
        allow_null=False,
        trim_whitespace=True,
        error_messages=_SERIALIZER_TEXT_ERRORS,
    )
    food_item = _StrictTextField(
        max_length=FOOD_ITEM_MAX_LENGTH,
        required=True,
        allow_blank=False,
        allow_null=False,
        trim_whitespace=True,
        error_messages=_SERIALIZER_TEXT_ERRORS,
    )
    quantity = serializers.DecimalField(
        max_digits=QUANTITY_MAX_DIGITS,
        decimal_places=QUANTITY_DECIMAL_PLACES,
        min_value=QUANTITY_MIN,
        required=True,
        allow_null=False,
        error_messages={**BOARD_FIELD_ERRORS, "null": BOARD_FIELD_ERRORS["required"]},
    )
    unit = serializers.ChoiceField(
        choices=Request.UNIT_CHOICES,
        required=True,
        allow_blank=False,
        allow_null=False,
        error_messages={**BOARD_FIELD_ERRORS, "null": BOARD_FIELD_ERRORS["required"]},
    )
    description = _StrictTextField(
        max_length=DESCRIPTION_MAX_LENGTH,
        required=False,
        allow_blank=True,
        allow_null=False,
        default="",
        trim_whitespace=True,
        error_messages=_SERIALIZER_TEXT_ERRORS,
    )
    location = _StrictTextField(
        max_length=LOCATION_MAX_LENGTH,
        required=False,
        allow_blank=True,
        allow_null=False,
        default="",
        trim_whitespace=True,
        error_messages=_SERIALIZER_TEXT_ERRORS,
    )
    expires_at = _CreateExpiresAtField(
        required=False,
        allow_null=False,
        default=default_expires_at,
        error_messages={**BOARD_FIELD_ERRORS, "null": "Isi batas waktu permintaan."},
    )

    class Meta:
        model = Request
        fields = ALLOWED_CREATE_FIELDS

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                {"detail": "Payload harus berupa object."},
                code="invalid_payload",
            )
        unknown_fields = sorted(set(data.keys()) - set(ALLOWED_CREATE_FIELDS))
        if unknown_fields:
            raise serializers.ValidationError(
                {field: ["Field ini tidak diizinkan."] for field in unknown_fields},
                code="invalid_fields",
            )
        return super().to_internal_value(data)

    def _run_shared_validator(self, validator, *args, **kwargs):
        try:
            return validator(*args, **kwargs)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                [str(message) for message in exc.messages],
                code=exc.code or "invalid",
            ) from exc

    def validate_title(self, value: str) -> str:
        return self._run_shared_validator(
            clean_request_text,
            value,
            max_length=TITLE_MAX_LENGTH,
            required=True,
        )

    def validate_food_item(self, value: str) -> str:
        return self._run_shared_validator(
            clean_request_text,
            value,
            max_length=FOOD_ITEM_MAX_LENGTH,
            required=True,
        )

    def validate_description(self, value: str) -> str:
        return self._run_shared_validator(
            clean_request_text,
            value,
            max_length=DESCRIPTION_MAX_LENGTH,
            required=False,
        )

    def validate_location(self, value: str) -> str:
        return self._run_shared_validator(
            clean_request_text,
            value,
            max_length=LOCATION_MAX_LENGTH,
            required=False,
        )

    def validate_expires_at(self, value):
        if self.context.get("defer_future_check"):
            return value
        return self._run_shared_validator(validate_future_expires_at, value)
