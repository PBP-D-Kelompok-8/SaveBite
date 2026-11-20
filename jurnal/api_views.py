from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes, throttle_classes,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .api_auth import SignedTokenAuthentication
from .models import FoodLog
from .serializers import FoodLogFilterSerializer, FoodLogSerializer
# Reuse the existing helpers so the API and the HTML page always agree on
# filtering rules and summary numbers.
from .views import _food_log_summary, _food_logs_for_user


# Flutter sends `Authorization: Token <key>`. Session auth stays second so the
# Browsable API in a browser keeps working. Token requests need no CSRF token.
API_AUTHENTICATION = [SignedTokenAuthentication, SessionAuthentication]


class FoodLogPagination(PageNumberPagination):
    page_size = 6  # same as the HTML page


class FoodLogWriteThrottle(UserRateThrottle):
    """20 write requests per minute per user (reads are never throttled)."""

    scope = "foodlog_write"
    rate = "20/min"

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


@api_view(["GET", "HEAD", "POST"])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([IsAuthenticated])
@throttle_classes([FoodLogWriteThrottle])
def food_log_collection(request):
    if request.method == "POST":
        serializer = FoodLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)  # the owner always comes from the session
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    filters = FoodLogFilterSerializer(data=request.query_params)
    filters.is_valid(raise_exception=True)
    logs = _food_logs_for_user(request.user, filters.validated_data)
    paginator = FoodLogPagination()
    page = paginator.paginate_queryset(logs, request)
    serializer = FoodLogSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(["GET", "HEAD", "PUT", "PATCH", "DELETE"])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([IsAuthenticated])
@throttle_classes([FoodLogWriteThrottle])
def food_log_item(request, pk):
    # Filtering by user means someone else's entry looks like it doesn't exist (404).
    food_log = get_object_or_404(FoodLog, pk=pk, user=request.user)

    if request.method in ("GET", "HEAD"):
        return Response(FoodLogSerializer(food_log).data)

    if request.method == "DELETE":
        food_log.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = FoodLogSerializer(
        food_log, data=request.data, partial=request.method == "PATCH",
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["GET", "HEAD"])
@authentication_classes(API_AUTHENTICATION)
@permission_classes([IsAuthenticated])
def food_log_summary(request):
    summary = _food_log_summary(request.user)
    return Response({key: summary[key] for key in (
        "saved_count", "wasted_count", "total_count",
        "saved_percentage", "wasted_percentage",
    )})