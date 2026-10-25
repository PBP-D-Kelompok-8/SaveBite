from django import forms

from .models import SavedRecipe


class SearchForm(forms.Form):
    """Validasi parameter pencarian yang dikirim dari JavaScript Fetch API."""

    q = forms.CharField(required=False, max_length=100, strip=True)
    ingredients = forms.CharField(required=False, max_length=200, strip=True)

    def clean_ingredients(self):
        value = self.cleaned_data.get("ingredients", "")
        terms = [part.strip() for part in value.split(",") if part.strip()]
        normalized_terms = [" ".join(term.lower().split()) for term in terms]
        unique_terms = list(dict.fromkeys(normalized_terms))
        if len(unique_terms) > 3:
            raise forms.ValidationError("Masukkan maksimal 3 bahan sekaligus.")
        return ", ".join(terms)


class SaveRecipeForm(forms.Form):
    meal_id = forms.RegexField(regex=r"^[0-9]{1,10}$", max_length=10)


class SavedRecipeNoteForm(forms.ModelForm):
    class Meta:
        model = SavedRecipe
        fields = ["note"]
        labels = {"note": "Catatan atau tips pribadi"}
        widgets = {
            "note": forms.Textarea(
                attrs={
                    "rows": 4,
                    "maxlength": 500,
                    "placeholder": "Contoh: kurangi garam atau ganti ayam dengan tahu.",
                }
            )
        }
