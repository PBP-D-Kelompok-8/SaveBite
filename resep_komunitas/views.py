
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import CommunityRecipe


def recipe_list(request):
    """
    Menampilkan feed Resep Komunitas.

    Fitur:
    - Menampilkan semua resep secara default.
    - Mencari resep berdasarkan judul atau bahan utama.
    - Memfilter resep milik user yang sedang login.
    - Memfilter resep yang sudah di-star oleh user.

    Parameter GET:
    - q: kata kunci pencarian.
    - filter: all, mine, atau saved.

    Data yang dikirim ke template:
    - recipes: daftar resep yang cocok.
    - query: kata kunci yang sedang digunakan.
    - filter_type: filter yang sedang aktif.
    """

    # Ambil resep beserta author untuk mengurangi query database
    # saat template mengakses recipe.author.
    recipes = CommunityRecipe.objects.select_related(
        "author"
    ).prefetch_related("starred_by")

    # GET digunakan untuk pencarian karena pencarian tidak mengubah data.
    query = request.GET.get("q", "").strip()
    filter_type = request.GET.get("filter", "all")

    # Q memungkinkan pencarian pada dua field sekaligus menggunakan OR.
    # Contoh: keyword "telur" cocok dengan judul ATAU bahan utama.
    if query:
        recipes = recipes.filter(
            Q(title__icontains=query)
            | Q(main_ingred__icontains=query)
        )

    # Filter resep yang dibuat oleh user yang sedang login.
    if filter_type == "mine":
        if request.user.is_authenticated:
            recipes = recipes.filter(author=request.user)
        else:
            # Pengunjung yang belum login tidak melihat resep pribadi
            # melalui filter ini.
            recipes = recipes.none()

    # Filter resep yang telah disimpan melalui fitur star.
    elif filter_type == "saved":
        if request.user.is_authenticated:
            recipes = recipes.filter(starred_by=request.user)
        else:
            recipes = recipes.none()

    # Render menggabungkan template HTML dengan data dari database.
    return render(
        request,
        "recipe_list.html",
        {
            "recipes": recipes,
            "query": query,
            "filter_type": filter_type,
        },
    )

@login_required
def recipe_delete(request, pk):
    """
    Menghapus resep milik user yang sedang login.

    GET:
        Menampilkan halaman konfirmasi.

    POST:
        Menghapus resep setelah user mengonfirmasi tindakan.
    """

    recipe = get_object_or_404(
        CommunityRecipe,
        pk=pk,
        author=request.user,
    )

    # Jangan menghapus data hanya karena URL dibuka.
    # Penghapusan dilakukan setelah form konfirmasi dikirim dengan POST.
    if request.method == "POST":
        recipe.delete()
        return redirect("community_recipes:recipe_list")

    return render(
        request,
        "recipe_confirm_delete.html",
        {"recipe": recipe},
    )


@login_required
@require_POST
def recipe_toggle_star(request, pk):
    """
    Menambah atau membatalkan star pada sebuah resep.

    Hanya menerima POST dan membutuhkan user yang login.

    Jika request berasal dari AJAX:
        Mengembalikan JSON berisi status star dan jumlah star terbaru.

    Jika request biasa:
        Mengarahkan user kembali ke halaman detail resep.

    Pemilik resep tidak boleh memberi star pada resepnya sendiri.
    """

    recipe = get_object_or_404(CommunityRecipe, pk=pk)

    # Cegah author memberi star pada resepnya sendiri.
    if recipe.author_id == request.user.pk:
        message = "Kamu tidak bisa memberi star pada resep sendiri."

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {"error": message},
                status=400,
            )

        return redirect(
            "community_recipes:recipe_detail",
            pk=pk,
        )

    # Cari apakah user sudah memberi star sebelumnya.
    relation = recipe.starred_by.filter(pk=request.user.pk)

    if relation.exists():
        # Jika sudah ada, hapus relasinya: star dibatalkan.
        relation.delete()
        is_starred = False
    else:
        # Jika belum ada, tambahkan relasi star.
        recipe.starred_by.add(request.user)
        is_starred = True

    # Hitung ulang jumlah star setelah perubahan.
    star_count = recipe.starred_by.count()

    # XMLHttpRequest menandakan request AJAX dari JavaScript.
    # JSON dipakai agar halaman dapat memperbarui tombol dan jumlah
    # star tanpa reload seluruh halaman.
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "is_starred": is_starred,
            "star_count": star_count,
        })

    # Fallback untuk request POST biasa tanpa AJAX.
    return redirect(
        "community_recipes:recipe_detail",
        pk=pk,
    )
