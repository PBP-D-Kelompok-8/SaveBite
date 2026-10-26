from django.conf import settings
from django.db import models


class FoodRescuePlan(models.Model):
    STATUS_CHOICES = [
        ('ongoing', 'On Going'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]

    ACTION_CHOICES = [
        ('cook', 'Cook'),
        ('preserve', 'Preserve'),
        ('share', 'Share'),
    ]

    UNIT_CHOICES = [
        ('kg', 'Kilogram'),
        ('gr', 'Gram'),
        ('pcs', 'Pieces'),
        ('porsi', 'Porsi'),
        ('liter', 'Liter'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='food_rescue_plans',
    )
    nama_bahan = models.CharField(max_length=100)
    jumlah = models.PositiveIntegerField(default=1)
    satuan = models.CharField(
        max_length=20,
        choices=UNIT_CHOICES,
        default='pcs',
    )
    target_date = models.DateField()
    metode_aksi = models.CharField(
        max_length=10,
        choices=ACTION_CHOICES,
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='ongoing',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.nama_bahan} - {self.get_status_display()}"
