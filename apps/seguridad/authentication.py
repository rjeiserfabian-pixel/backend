from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTAuthenticationAuditable(JWTAuthentication):
    """
    Igual que JWTAuthentication, pero deja el usuario autenticado en la petición de Django.
    El middleware de auditoría corre FUERA de DRF y, sin esto, no sabría quién hizo la acción.
    """

    def authenticate(self, request):
        resultado = super().authenticate(request)
        if resultado is not None:
            request._request.usuario_auditoria = resultado[0]
        return resultado
