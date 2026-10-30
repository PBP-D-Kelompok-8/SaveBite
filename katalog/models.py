from django.conf import settings
from django.db import models


class SavedRecipe(models.Model):
    """Bookmark resep TheMealDB milik seorang pengguna."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_recipes",
    )
    meal_id = models.CharField(max_length=20)
    name = models.CharField(max_length=200)
    thumbnail = models.URLField(max_length=500, blank=True)
    category = models.CharField(max_length=100, blank=True)
    area = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True, max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "meal_id"],
                name="unique_saved_recipe_per_user",
            ),
        ]
        verbose_name = "resep tersimpan"
        verbose_name_plural = "resep tersimpan"

    def __str__(self):
        return f"{self.name} ({self.user})"
