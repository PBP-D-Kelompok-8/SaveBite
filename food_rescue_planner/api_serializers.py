from rest_framework import serializers

from .models import FoodRescuePlan


class FoodRescuePlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodRescuePlan
        fields = [
            "id",
            "nama_bahan",
            "jumlah",
            "satuan",
            "target_date",
            "metode_aksi",
            "status",
            "notes",
            "created_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "created_at",
            "completed_at",
        ]

    def validate_nama_bahan(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(
                "Nama bahan tidak boleh kosong."
            )
        return value

    def validate_jumlah(self, value):
        if value < 1 or value > 2147483647:
            raise serializers.ValidationError(
                "Jumlah harus berupa bilangan bulat positif."
            )
        return value

    def create(self, validated_data):
        return FoodRescuePlan.objects.create(
            user=self.context["request"].user,
            **validated_data,
        )


class FoodRescuePlanUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodRescuePlan
        fields = [
            "nama_bahan",
            "jumlah",
            "satuan",
            "target_date",
            "metode_aksi",
            "status",
            "notes",
        ]

    def validate_nama_bahan(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(
                "Nama bahan tidak boleh kosong."
            )
        return value

    def validate_jumlah(self, value):
        if value < 1 or value > 2147483647:
            raise serializers.ValidationError(
                "Jumlah harus berupa bilangan bulat positif."
            )
        return value