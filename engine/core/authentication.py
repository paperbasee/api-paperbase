from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTAuthenticationAllowAPIKey(JWTAuthentication):
    """
    Skip JWT parsing when Authorization bearer carries an API key token.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None
        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None
        token = raw_token.decode("utf-8")
        if token.startswith("ak_live_"):
            return None
        return super().authenticate(request)
