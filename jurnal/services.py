from django.utils import timezone

from jurnal.models import FoodLog


def create_log_from_rescue_plan(plan):
    if plan.status not in ("success", "failed"):
        return None

    status = "terselamatkan" if plan.status == "success" else "terbuang"
    log, created = FoodLog.objects.get_or_create(
        rescue_plan_id=plan.pk,
        defaults={
            "user": plan.user,
            "food_name": plan.nama_bahan,
            "quantity": plan.jumlah,
            "unit": plan.satuan,
            "status": status,
            "notes": f"Otomatis dari Rescue Plan ({plan.metode_aksi})",
            "completed_at": timezone.now(),
        },
    )
    if not created and log.status != status:
        log.status = status
        log.save(update_fields=["status"])
    return log