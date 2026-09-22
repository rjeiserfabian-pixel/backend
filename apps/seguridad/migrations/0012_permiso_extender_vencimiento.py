"""
Separa "extender la fecha de vencimiento de la cotización de una Orden de
Trabajo" del permiso genérico ORDENES_TRABAJO.EDITAR.

Hasta ahora el botón "Editar Fecha" (vencimiento de la cotización) se
mostraba a cualquiera con permiso de editar la orden en general (motivo,
kilometraje, etc.), y el endpoint PATCH que la guarda tampoco distinguía
ese campo de los demás. Se crea un permiso dedicado
(ORDENES_TRABAJO.EXTENDER_VENCIMIENTO) que el ViewSet exige puntualmente
solo cuando el campo "fecha_vencimiento_cotizacion" viene en la petición.

Backfill: se otorga automáticamente a todo rol que hoy tiene
ORDENES_TRABAJO.EDITAR, EXCEPTO MECANICO — ese es justo el caso de uso que
motivó este cambio (el mecánico no debe poder extender el vencimiento de una
cotización, pero sí debe seguir pudiendo editar el resto de la orden).
"""
from django.db import migrations

ROLES_EXCLUIDOS_DEL_BACKFILL = {"MECANICO"}


def crear_permiso_extender_vencimiento(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    try:
        permiso_editar = Permiso.objects.get(codigo="ORDENES_TRABAJO.EDITAR")
    except Permiso.DoesNotExist:
        return

    try:
        modulo_taller = Modulo.objects.get(codigo="TALLER")
    except Modulo.DoesNotExist:
        modulo_taller = permiso_editar.id_modulo

    permiso_nuevo, _ = Permiso.objects.get_or_create(
        codigo="ORDENES_TRABAJO.EXTENDER_VENCIMIENTO",
        defaults={
            "id_modulo_id": modulo_taller.pk,
            "nombre": "Extender vencimiento de cotización",
            "accion": "EXTENDER_VENCIMIENTO",
            "estado": True,
            "grupo_padre": permiso_editar.grupo_padre,
            "grupo_submodulo": permiso_editar.grupo_submodulo,
        },
    )

    for rp in RolPermiso.objects.filter(id_permiso=permiso_editar).select_related("id_rol"):
        if rp.id_rol.codigo in ROLES_EXCLUIDOS_DEL_BACKFILL:
            continue
        RolPermiso.objects.get_or_create(
            id_rol_id=rp.id_rol_id,
            id_permiso=permiso_nuevo,
            defaults={"alcance": rp.alcance},
        )


def revertir(apps, schema_editor):
    Permiso = apps.get_model("seguridad", "Permiso")
    # CASCADE en Permiso borra tambien los RolPermiso creados.
    Permiso.objects.filter(codigo="ORDENES_TRABAJO.EXTENDER_VENCIMIENTO").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0011_permisos_ver_menu"),
    ]

    operations = [
        migrations.RunPython(crear_permiso_extender_vencimiento, revertir),
    ]
