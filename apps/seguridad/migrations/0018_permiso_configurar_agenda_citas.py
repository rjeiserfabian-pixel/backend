from django.db import migrations


CODIGO_PERMISO = "CITAS.CONFIGURAR_AGENDA"


def crear_permiso_configurar_agenda(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")
    Rol = apps.get_model("seguridad", "Rol")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    modulo_citas = Modulo.objects.filter(codigo="CITAS").first()
    modulo_taller = Modulo.objects.filter(codigo="TALLER").first()

    permiso, _ = Permiso.objects.update_or_create(
        codigo=CODIGO_PERMISO,
        defaults={
            "id_modulo": modulo_citas or modulo_taller,
            "nombre": "Configurar agenda de citas",
            "accion": "CONFIGURAR_AGENDA",
            "estado": True,
            "grupo_padre": "Taller",
            "grupo_submodulo": "Citas",
        },
    )

    admin = Rol.objects.filter(codigo="ADMINISTRADOR").first()
    if admin:
        RolPermiso.objects.get_or_create(
            id_rol=admin,
            id_permiso=permiso,
            defaults={"alcance": "GLOBAL"},
        )


def revertir_permiso_configurar_agenda(apps, schema_editor):
    Permiso = apps.get_model("seguridad", "Permiso")
    Permiso.objects.filter(codigo=CODIGO_PERMISO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0017_recepcionista_citas_permissions"),
    ]

    operations = [
        migrations.RunPython(crear_permiso_configurar_agenda, revertir_permiso_configurar_agenda),
    ]
