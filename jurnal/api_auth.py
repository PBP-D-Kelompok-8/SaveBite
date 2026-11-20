"""Token login for the Flutter app that needs no settings change and no migration.

The token is a signed value (HMAC with the project's SECRET_KEY) holding the user id.
Nothing is stored in the database.
"""
from django.contrib.auth import authenticate, get_user_model
from django.core import signing
from django.utils.crypto import salted_hmac
from rest_framework import exceptions, serializers
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes, throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

TOKEN_SALT = "jurnal.api-token"
TOKEN_MAX_AGE = 60 * 60 * 24 * 14  # tokens expire after 14 days


def _password_fingerprint(user):
    # Changing the password changes this value, which invalidates old tokens.
    # It is a keyed hash, so the token does not reveal anything about the password hash.
    return salted_hmac(TOKEN_SALT, user.password).hexdigest()[:20]


def make_token(user):
    return signing.dumps(
        {"uid": user.pk, "pw": _password_fingerprint(user)}, salt=TOKEN_SALT,
    )


class SignedTokenAuthentication(BaseAuthentication):
    """Reads `Authorization: Token <token>` and resolves it to a user."""

    keyword = "Token"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None  # no token sent: let the next authenticator (session) try
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed("Invalid token header.")
        try:
            token = parts[1].decode()
            data = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        except (UnicodeError, signing.BadSignature):  # includes SignatureExpired
            raise exceptions.AuthenticationFailed("Invalid or expired token.")

        user = None
        if isinstance(data, dict):
            user = get_user_model().objects.filter(pk=data.get("uid"), is_active=True).first()
        if user is None or data.get("pw") != _password_fingerprint(user):
            raise exceptions.AuthenticationFailed("Invalid or expired token.")
        return (user, token)

    def authenticate_header(self, request):
        return self.keyword  # makes unauthenticated requests return 401


class LoginThrottle(AnonRateThrottle):
    scope = "jurnal_login"
    rate = "10/min"


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def login(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(request=request, **serializer.validated_data)
    if user is None:
        # 400 (not 401) so a wrong password is not confused with an expired token.
        return Response({"detail": "Username atau password salah."}, status=400)
    return Response({"token": make_token(user)})