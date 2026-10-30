from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator

class FoodLog(models.Model):
    STATUS_CHOICES = [
        ("terselamatkan", "Terselamatkan"), 
        ("terbuang", "Terbuang")
    ]
    
    UNIT_CHOICES = [
        ("kg", "Kilogram"), 
        ("gr", "Gram"), 
        ("pcs", "Pieces"), 
        ("porsi", "Porsi"),
        ("liter", "Liter"),
        
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="food_logs")
    food_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)],)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default="pcs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="terselamatkan")
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(default=timezone.now)
    rescue_plan_id = models.PositiveIntegerField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ["-completed_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="foodlog_quantity_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["terselamatkan", "terbuang"]),
                name="foodlog_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(unit__in=["kg", "gr", "pcs", "porsi", "liter"]),
                name="foodlog_unit_valid",
            ),
        ]

    def __str__(self):
        return f"{self.food_name} - {self.status}"
    

