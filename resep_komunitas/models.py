from django.db import models

# Create your models here.

from django.conf import settings
from django.db import models


class CommunityRecipe(models.Model):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_recipes",
    )
    title = models.CharField(max_length=200)
    main_ingred = models.CharField(
        max_length=200,
        help_text="Bahan utama untuk pencarian resep.",
    )
    image_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="URL gambar resep.",
    )
    description = models.TextField()
    ingredients = models.TextField(
        help_text="Tulis bahan dan jumlahnya, satu bahan per baris.",
    )
    steps = models.TextField(
        help_text="Tulis langkah memasak secara berurutan.",
    )
    last_edited = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_edited"]

    def __str__(self): 
        return self.title
