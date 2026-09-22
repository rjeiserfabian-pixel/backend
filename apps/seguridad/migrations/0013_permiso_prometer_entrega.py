"""
Separa "prometer/editar la fecha de entrega estimada" del permiso genérico
ORDENES_TRABAJO.EDITAR, igual que se hizo con el vencimiento de la
cotización (ver migración 0012).

A diferencia de esa, aquí el Mecánico SÍ conserva el permiso nuevo: suele
ser quien mejor conoce el avance real de la reparación, así que tiene
sentido que pueda prometer/ajustar la fecha de entrega. Por eso el backfill
no excluye ningún rol — todo rol que hoy tiene ORDENES_TRABAJO.EDITAR
conserva exactamente el mismo acceso que ya tenía.
"""
from django.db import migrations


def crear_permiso_prometer_entrega(apps, schema_editor):
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
        codigo="ORDENES_TRABAJO.PROMETER_ENTREGA",
        defaults={
            "id_modulo_id": modulo_taller.pk,
            "nombre": "Prometer/editar fecha de entrega",
            "accion": "PROMETER_ENTREGA",
            "estado": True,
            "grupo_padre": permiso_editar.grupo_padre,
            "grupo_submodulo": permiso_editar.grupo_submodulo,
        },
    )

    for rp in RolPermiso.objects.filter(id_permiso=permiso_editar):
        RolPermiso.objects.get_or_create(
            id_rol_id=rp.id_rol_id,
            id_permiso=permiso_nuevo,
            defaults={"alcance": rp.alcance},
        )


def revertir(apps, schema_editor):
    Permiso = apps.get_model("seguridad", "Permiso")
    Permiso.objects.filter(codigo="ORDENES_TRABAJO.PROMETER_ENTREGA").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0012_permiso_extender_vencimiento"),
    ]

    operations = [
        migrations.RunPython(crear_permiso_prometer_entrega, revertir),
    ]
