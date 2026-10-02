from django.db import migrations


PERMISOS_RECEPCIONISTA_CITAS = [
    "CITAS.VER",
    "CITAS.CREAR",
    "CITAS.EDITAR",
    "CITAS.CAMBIAR_ESTADO",
    "CITAS.RECEPCIONAR",
    "CITAS.VER_MENU",
]


def asignar_permisos_recepcionista(apps, schema_editor):
    Permiso = apps.get_model("seguridad", "Permiso")
    Rol = apps.get_model("seguridad", "Rol")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    recepcionista = Rol.objects.filter(codigo="RECEPCIONISTA").first()
    if not recepcionista:
        return

    for permiso in Permiso.objects.filter(codigo__in=PERMISOS_RECEPCIONISTA_CITAS):
        RolPermiso.objects.get_or_create(
            id_rol=recepcionista,
            id_permiso=permiso,
            defaults={"alcance": "TALLER"},
        )


def quitar_permisos_recepcionista(apps, schema_editor):
    Permiso = apps.get_model("seguridad", "Permiso")
    Rol = apps.get_model("seguridad", "Rol")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    recepcionista = Rol.objects.filter(codigo="RECEPCIONISTA").first()
    if not recepcionista:
        return

    permisos = Permiso.objects.filter(codigo__in=PERMISOS_RECEPCIONISTA_CITAS)
    RolPermiso.objects.filter(id_rol=recepcionista, id_permiso__in=permisos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0016_citas_menu_permissions"),
    ]

    operations = [
        migrations.RunPython(asignar_permisos_recepcionista, quitar_permisos_recepcionista),
    ]
