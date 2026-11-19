from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache

from rest_framework import exceptions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from jurnal.services import (
    delete_log_from_rescue_plan,
    sync_log_from_rescue_plan,
)
from request_board.api_exceptions import handle_domain_exceptions
from request_board.api_views import StrictJSONParser

from .models import FoodRescuePlan
from .api_serializers import (
    FoodRescuePlanSerializer,
    FoodRescuePlanUpdateSerializer,
)


@never_cache
@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@parser_classes([StrictJSONParser])
@handle_domain_exceptions
def rescue_plan_list(request):
    """List the current user's plans or create a new plan."""

    if request.method == "GET":
        plans = FoodRescuePlan.objects.filter(
            user=request.user
        ).order_by("-created_at")

        serializer = FoodRescuePlanSerializer(
            plans,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    serializer = FoodRescuePlanSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        plan = serializer.save()
        sync_log_from_rescue_plan(plan)

    response_serializer = FoodRescuePlanSerializer(plan)
    return Response(
        response_serializer.data,
        status=status.HTTP_201_CREATED,
    )


@never_cache
@api_view(["GET", "PATCH", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
@parser_classes([StrictJSONParser])
@handle_domain_exceptions
def rescue_plan_detail(request, plan_id):
    """Retrieve, update, or delete a plan owned by the current user."""

    plan = get_object_or_404(
        FoodRescuePlan,
        pk=plan_id,
        user=request.user,
    )

    if request.method == "GET":
        serializer = FoodRescuePlanSerializer(plan)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == "PATCH":
        serializer = FoodRescuePlanUpdateSerializer(
            plan,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            plan = serializer.save()

            if plan.status in ("success", "failed"):
                if plan.completed_at is None:
                    plan.completed_at = timezone.now()
            else:
                plan.completed_at = None

            plan.save(update_fields=["completed_at"])

            sync_log_from_rescue_plan(plan)

        response_serializer = FoodRescuePlanSerializer(plan)
        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )

    if request.body:
        raise exceptions.ValidationError(
            {"detail": "Body request untuk menghapus plan harus kosong."}
        )

    with transaction.atomic():
        delete_log_from_rescue_plan(plan)
        plan.delete()

    return Response(status=status.HTTP_204_NO_CONTENT)