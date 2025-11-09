from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from users.models import User


class BearerAuthentication(BaseAuthentication):
    """Bearer Authentication."""

    keyword = "Bearer"

    def authenticate(self, request):
        auth = request.headers.get("Authorization")
        if not auth:
            return None
        parts = auth.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            return None
        token = parts[1]
        try:
            user = User.objects.get(token=token)
        except User.DoesNotExist:
            raise AuthenticationFailed("Invalid token")
        return (user, None)


class APIKeyAuthentication(BaseAuthentication):
    """API Key Authentication."""

    def authenticate(self, request):
        auth = request.headers.get("X-API-Key")
        if not auth:
            return None
        try:
            user = User.objects.get(token=auth)
        except User.DoesNotExist:
            raise AuthenticationFailed("Invalid token")
        return (user, None)
