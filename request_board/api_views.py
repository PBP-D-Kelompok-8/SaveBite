"""Function-based DRF API views for Request Board v1 endpoints."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import PageNotAnInteger
from rest_framework import exceptions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from request_board.api_exceptions import handle_domain_exceptions
from request_board.forms import BoardFilterForm
from request_board.permissions import IsRequestOwnerOrReadOnly, check_object_permissions
from request_board.serializers import RequestSerializer
from request_board.services import delete_request, get_board_page, get_request_for_display


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
@handle_domain_exceptions
def request_list(request):
    """Return a paginated, filtered public list of food rescue requests."""
    page_param_list = request.query_params.getlist("page")
    if len(page_param_list) > 1:
        raise PageNotAnInteger("Parameter halaman tidak boleh berulang.")

    page_value = page_param_list[0] if page_param_list else None

    filter_form = BoardFilterForm(request.query_params)
    if not filter_form.is_valid():
        raise ValidationError(filter_form.errors.as_data())

    if filter_form.cleaned_data["mine"] and not request.user.is_authenticated:
        raise PermissionDenied("Silakan masuk untuk melihat permintaan Anda.")

    page = get_board_page(
        actor=request.user,
        page_value=page_value,
        filters=filter_form.cleaned_data,
    )
    serializer = RequestSerializer(
        page.object_list,
        many=True,
        context={"request": request},
    )
    return Response(
        {
            "count": page.paginator.count,
            "page": page.number,
            "page_size": page.paginator.per_page,
            "total_pages": page.paginator.num_pages,
            "results": serializer.data,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsRequestOwnerOrReadOnly])
@handle_domain_exceptions
def request_detail(request, request_id: int):
    """Retrieve a single request publicly or delete it as its owner."""
    food_request = get_request_for_display(request_id=request_id, actor=request.user)
    check_object_permissions(request, food_request)

    if request.method == "GET":
        serializer = RequestSerializer(food_request, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.body:
        raise exceptions.ValidationError(
            {"detail": "Body request untuk menghapus request harus kosong."}
        )

    delete_request(actor=request.user, request_id=food_request.pk)
    return Response(status=status.HTTP_204_NO_CONTENT)
