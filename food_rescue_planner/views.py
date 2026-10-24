
from datetime import date

from django.contrib import messages
from django.shortcuts import render, redirect

from .models import FoodRescuePlan


def index(request):
    plans = FoodRescuePlan.objects.all().order_by('-created_at')

    context = {
        'plans': plans,
    }

    return render(
        request,
        'food_rescue_planner/index.html',
        context,
    )


def create_plan(request):
    if request.method != 'POST':
        return redirect('food_rescue_planner:index')

    nama_bahan = request.POST.get('nama_bahan', '').strip()
    jumlah_input = request.POST.get('jumlah', '').strip()
    satuan = request.POST.get('satuan', '').strip()
    target_date_input = request.POST.get('target_date', '').strip()
    metode_aksi = request.POST.get('metode_aksi', '').strip()
    notes = request.POST.get('notes', '').strip()

    # Validasi field wajib
    if not all([
        nama_bahan,
        jumlah_input,
        satuan,
        target_date_input,
        metode_aksi,
    ]):
        messages.error(request, 'Semua field wajib harus diisi.')
        return redirect('food_rescue_planner:index')

    # Validasi metode penyelamatan
    if metode_aksi not in dict(FoodRescuePlan.ACTION_CHOICES):
        messages.error(request, 'Metode penyelamatan tidak valid.')
        return redirect('food_rescue_planner:index')

    # Validasi satuan
    if satuan not in dict(FoodRescuePlan.UNIT_CHOICES):
        messages.error(request, 'Satuan makanan tidak valid.')
        return redirect('food_rescue_planner:index')

    # Validasi jumlah: bilangan bulat positif
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

    # Validasi tanggal
    try:
        target_date = date.fromisoformat(target_date_input)
    except ValueError:
        messages.error(request, 'Format tanggal tidak valid.')
        return redirect('food_rescue_planner:index')

    # Penyimpanan menunggu integrasi login
    messages.error(
        request,
        'Pembuatan rencana belum tersedia karena fitur login belum terintegrasi.',
    )
    return redirect('food_rescue_planner:index')
