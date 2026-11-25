
from django.utils.html import strip_tags
from rest_framework import serializers

from .models import CommunityRecipe


class CommunityRecipeSerializer(serializers.ModelSerializer):
    author = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = CommunityRecipe
        fields = [
            "id",
            "author",
            "title",
            "main_ingred",
            "image_url",
            "description",
            "ingredients",
            "steps",
            "last_edited",
        ]
        read_only_fields = [
            "id",
            "author",
            "last_edited",
        ]

    def validate_title(self, value):
        value = strip_tags(value).strip()

        if not value:
            raise serializers.ValidationError(
                "Judul resep tidak boleh kosong."
            )

        return value

    def validate_main_ingred(self, value):
        value = strip_tags(value).strip()

        if not value:
            raise serializers.ValidationError(
                "Bahan utama tidak boleh kosong."
            )

        return value

    def validate_description(self, value):
        return strip_tags(value).strip()

    def validate_ingredients(self, value):
        return strip_tags(value).strip()

    def validate_steps(self, value):
        return strip_tags(value).strip()
