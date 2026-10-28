
from django import forms

from .models import CommunityRecipe


class CommunityRecipeForm(forms.ModelForm):
    class Meta:
        model = CommunityRecipe
        fields = [
            "title",
            "main_ingred",
            "image_url",
            "description",
            "ingredients",
            "steps",
        ]
        labels = {
            "title": "Judul Resep",
            "main_ingred": "Bahan Utama",
            "image_url": "URL Foto Resep",
            "description": "Deskripsi",
            "ingredients": "Alat & Bahan",
            "steps": "Langkah-langkah",
        }
        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "Contoh: Nasi Goreng Sayur",
            }),
            "main_ingred": forms.TextInput(attrs={
                "placeholder": "Contoh: Telur",
            }),
            "image_url": forms.URLInput(attrs={
                "placeholder": "https://example.com/foto-resep.jpg",
            }),
            "description": forms.Textarea(attrs={
                "placeholder": "Ceritakan resep ini secara singkat.",
                "rows": 3,
            }),
            "ingredients": forms.Textarea(attrs={
                "placeholder": "Contoh:\n2 butir telur\n1 mangkuk nasi",
                "rows": 5,
            }),
            "steps": forms.Textarea(attrs={
                "placeholder": "Tuliskan langkah memasak secara berurutan.",
                "rows": 6,
            }),
        }
