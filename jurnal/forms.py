from django import forms

from .models import FoodLog


class FoodLogFilterForm(forms.Form):
    """Validated, combinable filters for a user's food journal."""

    query = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.SearchInput(attrs={
            "class": "food-journal__search-input",
            "placeholder": "Cari nama atau catatan...",
            "aria-label": "Cari nama atau catatan",
        }),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Semua status"), *FoodLog.STATUS_CHOICES],
        widget=forms.Select(attrs={
            "class": "food-journal__filter-control",
            "aria-label": "Filter berdasarkan status",
        }),
    )
    date_from = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"], 
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "food-journal__filter-control",
            "aria-label": "Tanggal awal",
        }),
    )
    date_to = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "food-journal__filter-control",
            "aria-label": "Tanggal akhir",
        }),
    )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error(
                "date_to",
                "Tanggal akhir harus setelah atau sama dengan tanggal awal.",
            )
        return cleaned_data


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
