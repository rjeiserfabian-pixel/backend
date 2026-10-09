"""Invalida el caché de seguridad cuando cambia algo que afecta permisos, menú o empresa."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .cache_utils import invalidar
from .models import Empresa, Modulo, Permiso, Rol, RolPermiso, Usuario, UsuarioPermiso, UsuarioRol

MODELOS_QUE_INVALIDAN = (Rol, Permiso, RolPermiso, UsuarioRol, UsuarioPermiso, Modulo, Empresa)
# Estos campos cambian en cada inicio de sesión y no afectan los permisos.
CAMPOS_SIN_EFECTO = {'ultimo_acceso', 'intentos_fallidos', 'last_login'}


def _invalida_modelo(sender, **kwargs):
    invalidar()


for _modelo in MODELOS_QUE_INVALIDAN:
    post_save.connect(_invalida_modelo, sender=_modelo, dispatch_uid=f'seg_cache_save_{_modelo.__name__}')
    post_delete.connect(_invalida_modelo, sender=_modelo, dispatch_uid=f'seg_cache_delete_{_modelo.__name__}')


@receiver(post_save, sender=Usuario, dispatch_uid='seg_cache_save_Usuario')
def _usuario_guardado(sender, update_fields=None, **kwargs):
    # Un cambio de superusuario, estado o datos del usuario sí puede alterar sus permisos.
    if update_fields is not None and set(update_fields) <= CAMPOS_SIN_EFECTO:
        return
    invalidar()
