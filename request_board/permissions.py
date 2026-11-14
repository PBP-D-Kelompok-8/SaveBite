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


class IsRequestOwner(permissions.BasePermission):
    """Allow authenticated requests only when the caller owns the target Request."""

    message = "Hanya pemilik request yang dapat mengelola request ini."

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and obj.owner_id == request.user.pk
        )


class CanAccessRequestOffers(permissions.BasePermission):
    """Allow Request owners to read offers and authenticated non-owners to submit offers."""

    message = "Izin akses penawaran ditolak."

    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj) -> bool:
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            self.message = "Hanya pemilik request yang dapat melihat daftar penawar."
            return obj.owner_id == request.user.pk
        if request.method == "POST":
            self.message = "Pemilik request tidak dapat membuat tawaran."
            return obj.owner_id != request.user.pk
        return False


def check_object_permissions(request, obj) -> None:
    """Invoke DRF object-level permission checks inside a function-based API view."""
    view = (request.parser_context or {}).get("view")
    if view is None:
        raise RuntimeError("Instance view DRF tidak ditemukan pada parser_context.")
    view.check_object_permissions(request, obj)
