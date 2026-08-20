"""
permissions.py — Motor de Permisos (Permission Engine).

Este es el corazón del módulo de seguridad. Se ejecuta en CADA request
protegido del backend, nunca en el frontend.

Flujo de verificación:
  1. ¿Está autenticado? (token válido, usuario activo)
  2. ¿Existe una excepción DENY directa en UsuarioPermiso? → Denegar inmediatamente
  3. ¿Existe una excepción ALLOW directa en UsuarioPermiso? → Permitir (con alcance)
  4. ¿Tiene el permiso vía algún rol activo? → Determinar alcance efectivo
  5. Denegar si no cumple nada

Regla de conflicto: DENY explícito siempre gana sobre ALLOW, sin excepciones.

Uso en vistas DRF:
    permission_classes = [TienePermiso("ORDENES.APROBAR")]
"""
import logging
from django.utils import timezone
from rest_framework.permissions import BasePermission

from .models import UsuarioPermiso, UsuarioRol, RolPermiso

logger = logging.getLogger(__name__)

# Alcances en orden de amplitud (de más restrictivo a más amplio)
JERARQUIA_ALCANCE = ["PROPIO", "ASIGNADO", "TALLER", "GLOBAL"]


class TienePermiso(BasePermission):
    """
    Permission class reutilizable para DRF.

    Ejemplo de uso:
        permission_classes = [TienePermiso("ORDENES.APROBAR")]

    Devuelve el alcance efectivo en request.alcance_efectivo
    para que la vista pueda filtrar el queryset correctamente.
    """

    def __init__(self, codigo_permiso: str):
        self.codigo_permiso = codigo_permiso

    def has_permission(self, request, view):
        usuario = request.user

        # Paso 1: Verificar autenticación básica
        if not usuario or not usuario.is_authenticated:
            logger.warning("Acceso denegado: usuario no autenticado. Permiso: %s", self.codigo_permiso)
            return False

        if usuario.estado != "activo":
            logger.warning("Acceso denegado: usuario inactivo/bloqueado [%s]. Permiso: %s", usuario.username, self.codigo_permiso)
            return False

        if usuario.esta_bloqueado():
            logger.warning("Acceso denegado: usuario bloqueado por intentos fallidos [%s].", usuario.username)
            return False

        # Los superusuarios de Django tienen acceso total por defecto
        if usuario.is_superuser:
            request.alcance_efectivo = "GLOBAL"
            return True

        # Paso 2: Verificar excepción DENY directa en usuario_permisos
        # DENY explícito siempre gana — se verifica primero
        deny_directo = UsuarioPermiso.objects.filter(
            id_usuario=usuario,
            id_permiso__codigo=self.codigo_permiso,
            tipo="DENY",
            estado=True,
        ).filter(
            # Solo vigentes (fecha_fin null = indefinido)
            models_Q(fecha_fin__isnull=True) | models_Q(fecha_fin__gte=timezone.now())
        ).exists()

        if deny_directo:
            logger.info("Permiso denegado explícitamente para [%s] en [%s].", usuario.username, self.codigo_permiso)
            return False

        # Paso 3: Verificar excepción ALLOW directa en usuario_permisos
        allow_directo = UsuarioPermiso.objects.filter(
            id_usuario=usuario,
            id_permiso__codigo=self.codigo_permiso,
            tipo="ALLOW",
            estado=True,
        ).filter(
            models_Q(fecha_fin__isnull=True) | models_Q(fecha_fin__gte=timezone.now())
        ).first()

        if allow_directo:
            request.alcance_efectivo = allow_directo.alcance or "PROPIO"
            logger.debug("Permiso ALLOW directo para [%s] en [%s]. Alcance: %s", usuario.username, self.codigo_permiso, request.alcance_efectivo)
            return True

        # Paso 4: Verificar permiso vía roles activos
        # select_related para evitar N+1 al acceder a id_rol e id_permiso
        roles_activos_ids = UsuarioRol.objects.filter(
            id_usuario=usuario,
            estado=True,
        ).filter(
            models_Q(fecha_expiracion__isnull=True) | models_Q(fecha_expiracion__gte=timezone.now())
        ).values_list("id_rol_id", flat=True)

        rol_permiso = RolPermiso.objects.filter(
            id_rol_id__in=roles_activos_ids,
            id_permiso__codigo=self.codigo_permiso,
        ).select_related("id_permiso").first()

        if not rol_permiso:
            logger.info("Sin permiso [%s] para usuario [%s].", self.codigo_permiso, usuario.username)
            return False

        request.alcance_efectivo = rol_permiso.alcance
        logger.debug("Permiso vía rol para [%s] en [%s]. Alcance: %s", usuario.username, self.codigo_permiso, request.alcance_efectivo)
        return True

    def has_object_permission(self, request, view, obj):
        """
        Verifica el alcance a nivel de objeto específico.
        Úsalo en vistas de detalle (retrieve, update, destroy).
        """
        alcance = getattr(request, "alcance_efectivo", "PROPIO")
        usuario = request.user

        if usuario.is_superuser:
            return True

        if alcance == "GLOBAL":
            return True

        if alcance == "TALLER":
            # Para escalabilidad futura con multi-taller.
            # Por ahora, un taller único = acceso global.
            return True

        if alcance == "ASIGNADO":
            # El objeto debe tener un campo que lo relacione con el usuario
            mecanico_id = getattr(obj, "mecanico_id", None)
            tecnico_id = getattr(obj, "tecnico_id", None)
            asignado_a_id = getattr(obj, "asignado_a_id", None)
            for campo in [mecanico_id, tecnico_id, asignado_a_id]:
                if campo and str(campo) == str(usuario.id_usuario):
                    return True
            return False

        if alcance == "PROPIO":
            # El objeto debe haber sido creado por el usuario
            creado_por_id = getattr(obj, "creado_por_id", None)
            usuario_id = getattr(obj, "id_usuario_id", None)
            for campo in [creado_por_id, usuario_id]:
                if campo and str(campo) == str(usuario.id_usuario):
                    return True
            return False

        return False


# Importación local para evitar circular imports
from django.db.models import Q as models_Q  # noqa: E402
