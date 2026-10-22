from django.conf import settings
from django.db import models
from django.utils import timezone

class FoodLog(models.Model):
    STATUS_CHOICES = [
        ("habis", "Habis"), 
        ("terbuang", "Terbuang")
    ]
    
    UNIT_CHOICES = [
        ("kg", "Kilogram"), 
        ("gr", "Gram"), 
        ("pcs", "Pieces"), 
        ("porsi", "Porsi")
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="food_logs")
    food_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default="pcs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="habis")
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(default=timezone.now)
    rescue_plan_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-completed_at"]

    def __str__(self):
        return f"{self.food_name} - {self.status}"
    

