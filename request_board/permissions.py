"""Permission classes and explicit object-permission helpers for Request Board API."""

from rest_framework import permissions


class IsRequestOwnerOrReadOnly(permissions.BasePermission):
    """Allow public reads; allow unsafe actions only for the object's owner."""

    message = "Hanya pemilik request yang dapat mengelola request ini."

    def has_permission(self, request, view) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(
            request.user
            and request.user.is_authenticated
            and obj.owner_id == request.user.pk
        )


def check_object_permissions(request, obj) -> None:
    """Invoke DRF object-level permission checks inside a function-based API view."""
    view = (request.parser_context or {}).get("view")
    if view is None:
        raise RuntimeError("Instance view DRF tidak ditemukan pada parser_context.")
    view.check_object_permissions(request, obj)
