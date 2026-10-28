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


class BoardFilterForm(forms.Form):
    q = forms.CharField(label="Cari judul, bahan, atau lokasi", max_length=120, required=False)
    status = forms.ChoiceField(
        label="Status", choices=[("", "Semua status"), *Request.STATUS_CHOICES], required=False,
    )
    mine = forms.BooleanField(label="Permintaan saya", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.error_messages.update(BOARD_FIELD_ERRORS)


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
