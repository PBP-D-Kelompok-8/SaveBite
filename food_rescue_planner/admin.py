from django.contrib import admin
from .models import FoodRescuePlan


@admin.register(FoodRescuePlan)
class FoodRescuePlanAdmin(admin.ModelAdmin):
    list_display = (
        'food_name',
        'user',
        'quantity',
        'unit',
        'target_date',
        'action_method',
        'status',
    )
    list_filter = ('status', 'action_method', 'target_date')
    search_fields = ('food_name', 'user__username')