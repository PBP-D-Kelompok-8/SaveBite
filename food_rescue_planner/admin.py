from django.contrib import admin
from .models import FoodRescuePlan


@admin.register(FoodRescuePlan)
class FoodRescuePlanAdmin(admin.ModelAdmin):
    list_display = (
        'nama_bahan',
        'user',
        'jumlah',
        'satuan',
        'target_date',
        'metode_aksi',
        'status',
    )
    list_filter = ('status', 'metode_aksi', 'target_date')
    search_fields = ('nama_bahan', 'user__username')
