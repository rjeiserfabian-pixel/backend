from django.db import migrations


def crear_comparador_placa(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")
    Rol = apps.get_model("seguridad", "Rol")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    inventario = Modulo.objects.filter(codigo="INVENTARIO").first()
    modulo, _ = Modulo.objects.update_or_create(
        codigo="COMPARADOR_PLACA",
        defaults={
            "nombre": "Comparador por Placa",
            "icono": "search",
            "ruta": "/inventario/comparador-placa",
            "orden": 2,
            "visible_menu": True,
            "estado": True,
            "permiso_ver": "COMPARADOR_PLACA.VER_MENU",
            "id_modulo_padre": inventario,
        },
    )

    permisos_data = [
        {
            "codigo": "INVENTARIO.COMPARADOR_PLACA.VER",
            "nombre": "Ver comparador por placa",
            "accion": "VER",
        },
        {
            "codigo": "COMPARADOR_PLACA.VER_MENU",
            "nombre": 'Mostrar "Comparador por Placa" en el menu',
            "accion": "VER_MENU",
        },
    ]

    permisos = []
    for item in permisos_data:
        permiso, _ = Permiso.objects.update_or_create(
            codigo=item["codigo"],
            defaults={
                "id_modulo": inventario or modulo,
                "nombre": item["nombre"],
                "accion": item["accion"],
                "estado": True,
                "grupo_padre": "Inventario",
                "grupo_submodulo": "Comparador por Placa",
            },
        )
        permisos.append(permiso)

    for rol in Rol.objects.filter(codigo__in=["ADMINISTRADOR", "MECANICO", "VENDEDOR"]):
        for permiso in permisos:
            RolPermiso.objects.get_or_create(
                id_rol=rol,
                id_permiso=permiso,
                defaults={"alcance": "GLOBAL"},
            )


def revertir_comparador_placa(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")

    Modulo.objects.filter(codigo="COMPARADOR_PLACA").delete()
    Permiso.objects.filter(
        codigo__in=[
            "INVENTARIO.COMPARADOR_PLACA.VER",
            "COMPARADOR_PLACA.VER_MENU",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0014_empresa_nombre_comercial_empresa_ubigeo_and_more"),
    ]

    operations = [
        migrations.RunPython(crear_comparador_placa, revertir_comparador_placa),
    ]
