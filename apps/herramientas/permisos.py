from apps.seguridad.permissions import permisos_efectivos


def tiene_permiso(request, codigo):
    """True si el usuario de la petición tiene el permiso `codigo`.

    No toca `request.alcance_efectivo` (a diferencia de TienePermiso.has_permission),
    y calcula el conjunto de permisos una sola vez por petición."""
    user = request.user
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    cache = getattr(request, '_herramientas_permisos', None)
    if cache is None:
        cache = permisos_efectivos(user)
        request._herramientas_permisos = cache
    return codigo in cache
