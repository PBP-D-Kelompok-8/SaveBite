from django.utils.html import strip_tags
from rest_framework import serializers

from .models import FoodLog


class FoodLogSerializer(serializers.ModelSerializer):
    """JSON shape and input validation for one food journal entry."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    unit_display = serializers.CharField(source="get_unit_display", read_only=True)
    from_rescue_plan = serializers.SerializerMethodField()

    class Meta:
        model = FoodLog
        fields = [
            "id", "food_name", "quantity", "unit", "unit_display",
            "status", "status_display", "notes", "completed_at",
            "from_rescue_plan",
        ]
        # The client may never choose these. `user` and `rescue_plan_id` are not
        # listed at all, so they cannot be read or written through the API.
        read_only_fields = ["id", "completed_at"]

    def get_from_rescue_plan(self, obj):
        return obj.rescue_plan_id is not None

    def validate_food_name(self, value):
        value = strip_tags(value).strip()
        if not value:
            raise serializers.ValidationError("Nama makanan tidak boleh kosong.")
        return value

    def validate_notes(self, value):
        return strip_tags(value).strip()


class FoodLogFilterSerializer(serializers.Serializer):
    """Validates the query string of the list endpoint (same rules as FoodLogFilterForm)."""

    query = serializers.CharField(required=False, allow_blank=True, max_length=255)
    status = serializers.ChoiceField(
        choices=FoodLog.STATUS_CHOICES, required=False, allow_blank=True,
    )
    date_from = serializers.DateField(required=False, allow_null=True)
    date_to = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError({
                "date_to": "Tanggal akhir harus setelah atau sama dengan tanggal awal.",
            })
        return attrs