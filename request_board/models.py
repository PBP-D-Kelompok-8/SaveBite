from django.conf import settings
from django.db import models

STATUS_PENDING = 'pending'
STATUS_OFFERED = 'offered'
STATUS_FULFILLED = 'fulfilled'

STATUS_CHOICES = [
    (STATUS_PENDING, 'Pending'),
    (STATUS_OFFERED, 'Ditawarkan'),
    (STATUS_FULFILLED, 'Terpenuhi'),
]

UNIT_CHOICES = [
    ('gram', 'Gram'),
    ('kg', 'Kg'),
    ('ml', 'Ml'),
    ('liter', 'Liter'),
    ('buah', 'Buah'),
    ('butir', 'Butir'),
    ('ikat', 'Ikat'),
    ('porsi', 'Porsi'),
]


class Request(models.Model):
    STATUS_PENDING = STATUS_PENDING
    STATUS_OFFERED = STATUS_OFFERED
    STATUS_FULFILLED = STATUS_FULFILLED
    STATUS_CHOICES = STATUS_CHOICES
    UNIT_CHOICES = UNIT_CHOICES

    id = models.BigAutoField(primary_key=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='requests',
    )
    title = models.CharField(max_length=120)
    food_item = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=8, decimal_places=2)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES)
    description = models.TextField(blank=True, default='')
    location = models.CharField(max_length=120, blank=True, default='')
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    selected_offer = models.ForeignKey(
        'RequestOffer', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='selected_for_requests',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'requests'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='request_quantity_gt_zero',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[choice[0] for choice in STATUS_CHOICES]),
                name='request_status_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(unit__in=[choice[0] for choice in UNIT_CHOICES]),
                name='request_unit_valid',
            ),
        ]

    def __str__(self):
        return self.title


class RequestOffer(models.Model):
    id = models.BigAutoField(primary_key=True)
    request = models.ForeignKey(
        Request,
        on_delete=models.CASCADE,
        related_name='offers',
    )
    offered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='request_offers',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'request_offers'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['request', 'offered_by'],
                name='unique_request_offer',
            ),
        ]

    def __str__(self):
        return f"Offer on {self.request.title} by {self.offered_by}"
