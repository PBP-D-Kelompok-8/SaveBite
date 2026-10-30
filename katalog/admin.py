from django.contrib import admin

from .models import SavedRecipe


@admin.register(SavedRecipe)
class SavedRecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "meal_id", "category", "area", "created_at")
    list_filter = ("category", "area", "created_at")
    search_fields = ("name", "meal_id", "user__username")
