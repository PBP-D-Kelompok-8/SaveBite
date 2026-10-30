from django import forms

from .models import SavedRecipe


class SearchForm(forms.Form):
    """Memeriksa isian pencarian resep."""

    q = forms.CharField(required=False, max_length=100, strip=True)
    ingredients = forms.CharField(required=False, max_length=200, strip=True)

    def clean_ingredients(self):
        value = self.cleaned_data.get("ingredients", "")
        parts = [part.strip() for part in value.split(",") if part.strip()]
        ingredients = []

        for part in parts:
            ingredient = " ".join(part.lower().split())
            if ingredient and ingredient not in ingredients:
                ingredients.append(ingredient)

        if len(ingredients) > 3:
            raise forms.ValidationError("Masukkan maksimal 3 bahan sekaligus.")

        return ", ".join(ingredients)


class SaveRecipeForm(forms.Form):
    meal_id = forms.CharField(max_length=10)


class SavedRecipeNoteForm(forms.ModelForm):
    note = forms.CharField(
        required=False,
        max_length=500,
        label="Catatan atau tips pribadi",
        error_messages={"max_length": "Maksimal 500 karakter."},
        widget=forms.Textarea(
            attrs={
                "class": "input__field kat-textarea",
                "rows": 4,
                "maxlength": 500,
                "placeholder": "Contoh: kurangi garam atau ganti ayam dengan tahu.",
            }
        ),
    )

    class Meta:
        model = SavedRecipe
        fields = ["note"]
