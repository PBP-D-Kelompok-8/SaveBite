
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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='food_rescue_plans',
    )
    food_name = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20)
    target_date = models.DateField()
    action_method = models.CharField(
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
        return f"{self.food_name} - {self.get_status_display()}"
