from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .models import CommunityRecipe
from .serializers import CommunityRecipeSerializer
from .permissions import IsAuthorOrReadOnly


@api_view(["GET", "HEAD", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthorOrReadOnly])
def recipe_list_api(request):

    # GET: Mengambil seluruh resep
    if request.method in ["GET", "HEAD"]:
        recipes = CommunityRecipe.objects.select_related("author").all()

        query = request.query_params.get("q", "").strip()

        if query:
            recipes = recipes.filter(
                Q(title__icontains=query) |
                Q(main_ingred__icontains=query)
            )

        serializer = CommunityRecipeSerializer(
            recipes,
            many=True,
            context={"request": request},
        )

        return Response(serializer.data)

    # POST: Membuat resep baru
    serializer = CommunityRecipeSerializer(data=request.data)

    serializer.is_valid(raise_exception=True)

    serializer.save(author=request.user)

    return Response(
        serializer.data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "HEAD", "PUT", "PATCH", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthorOrReadOnly])
def recipe_detail_api(request, pk):

    recipe = get_object_or_404(
        CommunityRecipe.objects.select_related("author"),
        pk=pk,
    )

    # Periksa apakah pengguna boleh mengubah resep ini.
    # Pemeriksaan ini diperlukan karena kita menggunakan function-based view.
    if request.method not in ["GET", "HEAD"]:
        permission = IsAuthorOrReadOnly()

        if not permission.has_object_permission(request, None, recipe):
            return Response(
                {"detail": permission.message},
                status=status.HTTP_403_FORBIDDEN,
            )

    # GET: Mengambil detail resep
    if request.method in ["GET", "HEAD"]:
        serializer = CommunityRecipeSerializer(
            recipe,
            context={"request": request},
        )

        return Response(serializer.data)

    # DELETE: Menghapus resep
    if request.method == "DELETE":
        recipe.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    # PUT/PATCH: Mengubah resep
    serializer = CommunityRecipeSerializer(
        recipe,
        data=request.data,
        partial=request.method == "PATCH",
    )

    serializer.is_valid(raise_exception=True)

    serializer.save()

    return Response(serializer.data)