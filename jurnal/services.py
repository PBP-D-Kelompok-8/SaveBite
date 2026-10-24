from django.utils import timezone

from jurnal.models import FoodLog

FINAL_STATUSES = ("success", "failed")
AUTO_NOTE_PREFIX = "Otomatis dari Rescue Plan"


def _status_from_plan(plan):
    return "terselamatkan" if plan.status == "success" else "terbuang"


def _auto_note(plan):
    return f"{AUTO_NOTE_PREFIX} ({plan.metode_aksi})"


def create_log_from_rescue_plan(plan):
    log, _ = FoodLog.objects.get_or_create(
        rescue_plan_id=plan.pk,
        defaults={
            "user": plan.user,
            "food_name": plan.nama_bahan,
            "quantity": plan.jumlah,
            "unit": plan.satuan,
            "status": _status_from_plan(plan),
            "notes": _auto_note(plan),
            "completed_at": timezone.now(),
        },
    )
    return log


def update_log_from_rescue_plan(log, plan):
    changed = []
    for field, value in (
        ("status", _status_from_plan(plan)),
        ("quantity", plan.jumlah),
        ("unit", plan.satuan),
        ("food_name", plan.nama_bahan),
    ):
        if getattr(log, field) != value:
            setattr(log, field, value)
            changed.append(field)

    # catatan hanya diperbarui kalau masih catatan otomatis (belum diedit user)
    new_note = _auto_note(plan)
    if log.notes.startswith(AUTO_NOTE_PREFIX) and log.notes != new_note:
        log.notes = new_note
        changed.append("notes")

    if changed:
        log.save(update_fields=changed)
    return log


def delete_log_from_rescue_plan(plan):
    FoodLog.objects.filter(rescue_plan_id=plan.pk).delete()


def sync_log_from_rescue_plan(plan):
    """Satu-satunya fungsi yang dipanggil dari modul Rescue Plan."""
    if plan.pk is None:
        return None
    if plan.status not in FINAL_STATUSES:
        delete_log_from_rescue_plan(plan)
        return None

    log = FoodLog.objects.filter(rescue_plan_id=plan.pk).first()
    if log is None:
        return create_log_from_rescue_plan(plan)
    return update_log_from_rescue_plan(log, plan)