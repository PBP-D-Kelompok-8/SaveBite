from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from jurnal.services import sync_log_from_rescue_plan

from .models import FoodRescuePlan


def index(request):
    if request.user.is_authenticated:
        plans = FoodRescuePlan.objects.filter(
            user=request.user
        ).order_by('-created_at')
    else:
        plans = FoodRescuePlan.objects.none()

    return render(
        request,
        'food_rescue_planner/index.html',
        {'plans': plans},
    )


def create_plan(request):
    if request.method != 'POST':
        return redirect('food_rescue_planner:index')

    if not request.user.is_authenticated:
        messages.error(
            request,
            'Silakan login terlebih dahulu untuk membuat rencana.',
        )
        return redirect('food_rescue_planner:index')

    nama_bahan = request.POST.get('nama_bahan', '').strip()
    jumlah_input = request.POST.get('jumlah', '').strip()
    satuan = request.POST.get('satuan', '').strip()
    target_date_input = request.POST.get('target_date', '').strip()
    metode_aksi = request.POST.get('metode_aksi', '').strip()
    notes = request.POST.get('notes', '').strip()

    if not all([
        nama_bahan,
        jumlah_input,
        satuan,
        target_date_input,
        metode_aksi,
    ]):
        messages.error(request, 'Semua field wajib harus diisi.')
        return redirect('food_rescue_planner:index')

    if metode_aksi not in dict(FoodRescuePlan.ACTION_CHOICES):
        messages.error(request, 'Metode penyelamatan tidak valid.')
        return redirect('food_rescue_planner:index')

    if satuan not in dict(FoodRescuePlan.UNIT_CHOICES):
        messages.error(request, 'Satuan makanan tidak valid.')
        return redirect('food_rescue_planner:index')

    try:
        if not jumlah_input.isascii() or not jumlah_input.isdigit():
            raise ValueError

        jumlah = int(jumlah_input)
        if jumlah < 1 or jumlah > 2147483647:
            raise ValueError
    except ValueError:
        messages.error(
            request,
            'Jumlah makanan harus berupa bilangan bulat positif.',
        )
        return redirect('food_rescue_planner:index')

    try:
        target_date = date.fromisoformat(target_date_input)
    except ValueError:
        messages.error(request, 'Format tanggal tidak valid.')
        return redirect('food_rescue_planner:index')

    plan = FoodRescuePlan.objects.create(
        user=request.user,
        nama_bahan=nama_bahan,
        jumlah=jumlah,
        satuan=satuan,
        target_date=target_date,
        metode_aksi=metode_aksi,
        notes=notes,
    )
    sync_log_from_rescue_plan(plan)

    messages.success(request, 'Rencana penyelamatan berhasil dibuat!')
    return redirect('food_rescue_planner:index')


def edit_plan(request, plan_id):
    if not request.user.is_authenticated:
        messages.error(request, 'Silakan login terlebih dahulu.')
        return redirect('food_rescue_planner:index')

    plan = get_object_or_404(
        FoodRescuePlan,
        pk=plan_id,
        user=request.user,
    )

    if request.method == 'POST':
        nama_bahan = request.POST.get('nama_bahan', '').strip()
        jumlah_input = request.POST.get('jumlah', '').strip()
        satuan = request.POST.get('satuan', '').strip()
        target_date_input = request.POST.get('target_date', '').strip()
        metode_aksi = request.POST.get('metode_aksi', '').strip()
        status = request.POST.get('status', '').strip()
        notes = request.POST.get('notes', '').strip()

        if not all([
            nama_bahan,
            jumlah_input,
            satuan,
            target_date_input,
            metode_aksi,
            status,
        ]):
            messages.error(request, 'Semua field wajib harus diisi.')
            return redirect(
                'food_rescue_planner:edit',
                plan_id=plan.pk,
            )

        if metode_aksi not in dict(FoodRescuePlan.ACTION_CHOICES):
            messages.error(request, 'Metode penyelamatan tidak valid.')
            return redirect(
                'food_rescue_planner:edit',
                plan_id=plan.pk,
            )

        if satuan not in dict(FoodRescuePlan.UNIT_CHOICES):
            messages.error(request, 'Satuan makanan tidak valid.')
            return redirect(
                'food_rescue_planner:edit',
                plan_id=plan.pk,
            )

        if status not in dict(FoodRescuePlan.STATUS_CHOICES):
            messages.error(request, 'Status rencana tidak valid.')
            return redirect(
                'food_rescue_planner:edit',
                plan_id=plan.pk,
            )

        try:
            if not jumlah_input.isascii() or not jumlah_input.isdigit():
                raise ValueError

            jumlah = int(jumlah_input)
            if jumlah < 1 or jumlah > 2147483647:
                raise ValueError

            target_date = date.fromisoformat(target_date_input)
        except ValueError:
            messages.error(
                request,
                'Jumlah atau format tanggal tidak valid.',
            )
            return redirect(
                'food_rescue_planner:edit',
                plan_id=plan.pk,
            )

        plan.nama_bahan = nama_bahan
        plan.jumlah = jumlah
        plan.satuan = satuan
        plan.target_date = target_date
        plan.metode_aksi = metode_aksi
        plan.status = status
        plan.notes = notes

        if status in ('success', 'failed'):
            if plan.completed_at is None:
                plan.completed_at = timezone.now()
        else:
            plan.completed_at = None

        plan.save()

        sync_log_from_rescue_plan(plan)

        messages.success(request, 'Rencana berhasil diperbarui!')
        return redirect('food_rescue_planner:index')

    return render(
        request,
        'food_rescue_planner/edit.html',
        {
            'plan': plan,
            'unit_choices': FoodRescuePlan.UNIT_CHOICES,
            'action_choices': FoodRescuePlan.ACTION_CHOICES,
            'status_choices': FoodRescuePlan.STATUS_CHOICES,
        },
    )

def delete_plan(request, plan_id):
    if not request.user.is_authenticated:
        messages.error(request, 'Silakan login terlebih dahulu.')
        return redirect('food_rescue_planner:index')

    if request.method != 'POST':
        return redirect('food_rescue_planner:index')

    plan = get_object_or_404(
        FoodRescuePlan,
        pk=plan_id,
        user=request.user,
    )

    plan.delete()

    messages.success(request, 'Rencana berhasil dihapus!')
    return redirect('food_rescue_planner:index')