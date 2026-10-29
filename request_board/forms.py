from decimal import Decimal

from django import forms

from request_board.models import Request


BOARD_FIELD_ERRORS = {
    "required": "Kolom ini wajib diisi.",
    "invalid": "Masukkan nilai yang valid.",
    "invalid_choice": "Pilih salah satu pilihan yang tersedia.",
    "min_value": "Pastikan nilai minimal %(limit_value)s.",
    "max_length": "Gunakan paling banyak %(limit_value)s karakter.",
    "max_digits": "Gunakan paling banyak %(max)s digit.",
    "max_decimal_places": "Gunakan paling banyak %(max)s angka di belakang koma.",
    "max_whole_digits": "Gunakan paling banyak %(max)s digit sebelum koma.",
    "null_characters_not_allowed": "Karakter null tidak diperbolehkan.",
}


class TrimmedSelect(forms.Select):
    def format_value(self, value):
        if isinstance(value, str):
            value = value.strip()
        return super().format_value(value)


class BoardFilterForm(forms.Form):
    title = forms.CharField(
        label="Cari judul permintaan", max_length=120, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ketik judul permintaan..."}),
    )
    food_item = forms.CharField(label="Bahan", max_length=120, required=False, widget=TrimmedSelect)
    q = forms.CharField(
        label="Cari judul, bahan, atau lokasi", max_length=120, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Judul, bahan, atau lokasi"}),
    )
    status = forms.ChoiceField(
        label="Status", choices=[("", "Semua status"), *Request.STATUS_CHOICES], required=False,
    )
    location = forms.CharField(label="Lokasi", max_length=120, required=False, widget=forms.Select)
    sort = forms.ChoiceField(
        label="Urutkan", choices=[("newest", "Terbaru"), ("oldest", "Terlama")], required=False,
    )
    mine = forms.BooleanField(label="Permintaan saya", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.error_messages.update(BOARD_FIELD_ERRORS)

    def populate_locations(self):
        """Load public dropdown choices only when rendering the HTML board."""
        locations = list(Request.objects.exclude(location="").order_by("location")
                         .values_list("location", flat=True).distinct())
        selected = self.data.get("location", "").strip() if self.is_bound else ""
        if selected and len(selected) <= 120 and selected not in locations:
            locations.append(selected)
        self.fields["location"].widget.choices = [("", "Semua lokasi"), *[(value, value) for value in locations]]

    def populate_food_items(self) -> None:
        """Load public ingredient choices only when rendering the HTML board."""
        items = list(Request.objects.exclude(food_item="").order_by("food_item")
                     .values_list("food_item", flat=True).distinct())
        selected = self.data.get("food_item", "").strip() if self.is_bound else ""
        if selected and len(selected) <= 120 and selected not in items:
            items.append(selected)
        self.fields["food_item"].widget.choices = [("", "Semua bahan"), *[(value, value) for value in items]]


class RequestForm(forms.ModelForm):
    quantity = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    description = forms.CharField(
        max_length=1000,
        required=False,
        widget=forms.Textarea,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Instance fields also translate errors from native/model validators by code.
        for field in self.fields.values():
            field.error_messages.update(BOARD_FIELD_ERRORS)

    class Meta:
        model = Request
        fields = [
            "title",
            "food_item",
            "quantity",
            "unit",
            "description",
            "location",
        ]
