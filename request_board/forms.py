from decimal import Decimal

from django import forms

from request_board.models import Request


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
