from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes

from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .models import CommunityRecipe
from .serializers import CommunityRecipeSerializer


# ==========================================
# API DAFTAR DAN TAMBAH RESEP
# ==========================================

@api_view(["GET", "POST"])
@authentication_classes([TokenAuthentication, SessionAuthentication,])
@permission_classes([IsAuthenticatedOrReadOnly])
def recipe_list_api(request):

    # GET: Mengambil daftar resep
    if request.method == "GET":
        recipes = CommunityRecipe.objects.select_related(
            "author"
        ).all()

        # Pencarian berdasarkan judul atau bahan utama
        query = request.query_params.get("q", "").strip()
        if query:
            recipes = recipes.filter(
                Q(title__icontains=query)
                | Q(main_ingred__icontains=query)
            )

        # Filter resep milik pengguna yang sedang login
        filter_type = request.query_params.get("filter", "all")
        if filter_type == "mine":
            if request.user.is_authenticated:
                recipes = recipes.filter(author=request.user)
            else:
                recipes = recipes.none()

        recipes = recipes.order_by("title")

        serializer = CommunityRecipeSerializer(
            recipes,
            many=True,
            context={"request": request},
        )

        return Response(serializer.data)

    # POST: Membuat resep baru
    serializer = CommunityRecipeSerializer(
        data=request.data,
        context={"request": request},
    )

    serializer.is_valid(raise_exception=True)

    # Author otomatis berasal dari user yang login
    serializer.save(author=request.user)

    return Response(
        serializer.data,
        status=status.HTTP_201_CREATED,
    )


# ==========================================
# API DETAIL, UPDATE, DAN DELETE RESEP
# ==========================================

@api_view(["GET", "PUT", "PATCH", "DELETE"])
@authentication_classes([
    TokenAuthentication,
    SessionAuthentication,
])
@permission_classes([IsAuthenticatedOrReadOnly])
def recipe_detail_api(request, pk):

    recipe = get_object_or_404(
        CommunityRecipe.objects.select_related("author"),
        pk=pk,
    )

    # GET: Mengambil detail satu resep
    if request.method == "GET":

        serializer = CommunityRecipeSerializer(
            recipe,
            context={"request": request},
        )

        return Response(serializer.data)

    # Operasi selain GET hanya boleh dilakukan pemilik resep
    if recipe.author_id != request.user.id:
        return Response(
            {
                "detail": "Kamu tidak memiliki izin untuk mengubah resep ini."
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    # DELETE: Menghapus resep
    if request.method == "DELETE":

        recipe.delete()

        return Response(
            status=status.HTTP_204_NO_CONTENT
        )

    # PUT/PATCH: Mengubah resep
    serializer = CommunityRecipeSerializer(
        recipe,
        data=request.data,
        partial=request.method == "PATCH",
        context={"request": request},
    )

    serializer.is_valid(raise_exception=True)

    serializer.save()

    return Response(serializer.data)
