from django.contrib import admin

# Register your models here.

from .models import CommunityRecipe
@admin.register(CommunityRecipe)
class CommunityRecipeAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "main_ingred", "last_edited")
    search_fields = ("title", "main_ingred", "author__username")
    list_filter = ("last_edited",)
