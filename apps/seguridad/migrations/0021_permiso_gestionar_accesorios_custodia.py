"""
Crea el permiso dedicado ORDENES_TRABAJO.GESTIONAR_ACCESORIOS
para registrar y modificar pertenencias y accesorios en custodia
en una Orden de Trabajo de forma independiente a ORDENES_TRABAJO.CREAR.
"""
from django.db import migrations


ROLES_EXCLUIDOS_DEL_BACKFILL = {"MECANICO", "TÉCNICO_AUTOMOTRIZ"}


def crear_permiso_gestionar_accesorios(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")
    Rol = apps.get_model("seguridad", "Rol")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    try:
        permiso_crear = Permiso.objects.get(codigo="ORDENES_TRABAJO.CREAR")
        grupo_padre = permiso_crear.grupo_padre
        grupo_submodulo = permiso_crear.grupo_submodulo
    except Permiso.DoesNotExist:
        grupo_padre = "Taller"
        grupo_submodulo = "Órdenes de Trabajo"

    modulo_taller = Modulo.objects.filter(codigo="TALLER").first()
    if not modulo_taller:
        return

    permiso_nuevo, _ = Permiso.objects.get_or_create(
        codigo="ORDENES_TRABAJO.GESTIONAR_ACCESORIOS",
        defaults={
            "id_modulo": modulo_taller,
            "nombre": "Registrar / Modificar pertenencias en custodia",
            "accion": "GESTIONAR_ACCESORIOS",
            "estado": True,
            "grupo_padre": grupo_padre,
            "grupo_submodulo": grupo_submodulo,
        },
    )

    # Otorgar por defecto a administradores y roles con permiso de crear (asesor, gerente, soporte),
    # pero NO al mecánico por defecto, para que el administrador pueda otorgarlo o revocarlo libremente.
    roles_base = Rol.objects.filter(codigo__in=["ADMINISTRADOR", "SOPORTE", "ASESOR_DE_SERVICIO", "GERENTE"])
    for rol in roles_base:
        RolPermiso.objects.get_or_create(
            id_rol=rol,
            id_permiso=permiso_nuevo,
            defaults={"alcance": "GLOBAL"},
        )

    # Replicar a otros roles que tengan ORDENES_TRABAJO.CREAR excepto los excluidos
    try:
        for rp in RolPermiso.objects.filter(id_permiso__codigo="ORDENES_TRABAJO.CREAR").select_related("id_rol"):
            if rp.id_rol.codigo in ROLES_EXCLUIDOS_DEL_BACKFILL:
                continue
            RolPermiso.objects.get_or_create(
                id_rol_id=rp.id_rol_id,
                id_permiso=permiso_nuevo,
                defaults={"alcance": rp.alcance},
            )
    except Exception:
        pass


def revertir(apps, schema_editor):
    Permiso = apps.get_model("seguridad", "Permiso")
    Permiso.objects.filter(codigo="ORDENES_TRABAJO.GESTIONAR_ACCESORIOS").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0020_permisos_creditos_anteriores"),
    ]

    operations = [
        migrations.RunPython(crear_permiso_gestionar_accesorios, revertir),
    ]
