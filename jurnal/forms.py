from django import forms

from .models import FoodLog


class FoodLogForm(forms.ModelForm):
    """Form for creating and editing a user's food journal entry."""

    quantity = forms.IntegerField(
        label="Jumlah",
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": "input__field",
                "min": 1,
                "step": 1,
                "placeholder": "Masukkan jumlah",
            }
        ),
    )

    class Meta:
        model = FoodLog
        fields = ["food_name", "quantity", "unit", "status", "notes"]
        labels = {
            "food_name": "Nama makanan",
            "quantity": "Jumlah",
            "unit": "Satuan",
            "status": "Status",
            "notes": "Catatan",
        }
        widgets = {
            "food_name": forms.TextInput(
                attrs={
                    "class": "input__field",
                    "placeholder": "Masukkan nama makanan",
                    "autocomplete": "off",
                }
            ),
            "unit": forms.Select(
                attrs={"class": "input__field input__field--select"}
            ),
            "status": forms.Select(
                attrs={"class": "input__field input__field--select"}
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "input__field",
                    "rows": 3,
                    "placeholder": "Tambahkan catatan (opsional)",
                }
            ),
        }
