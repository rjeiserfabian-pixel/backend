from django.db import migrations


PERMISOS_CITAS = [
    ("CITAS.VER", "Ver citas", "VER"),
    ("CITAS.CREAR", "Crear citas", "CREAR"),
    ("CITAS.EDITAR", "Editar citas", "EDITAR"),
    ("CITAS.ELIMINAR", "Eliminar citas", "ELIMINAR"),
    ("CITAS.CAMBIAR_ESTADO", "Cambiar estado de citas", "CAMBIAR_ESTADO"),
    ("CITAS.RECEPCIONAR", "Recepcionar citas", "RECEPCIONAR"),
    ("CITAS.VER_MENU", 'Mostrar "Citas" en el menu', "VER_MENU"),
]


def crear_citas_menu_y_permisos(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")
    Rol = apps.get_model("seguridad", "Rol")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")

    taller = Modulo.objects.filter(codigo="TALLER").first()
    citas, _ = Modulo.objects.update_or_create(
        codigo="CITAS",
        defaults={
            "nombre": "Citas",
            "icono": "calendar",
            "ruta": "/taller/citas",
            "orden": 2,
            "visible_menu": True,
            "estado": True,
            "id_modulo_padre": taller,
            "permiso_ver": "CITAS.VER_MENU",
        },
    )

    Modulo.objects.filter(codigo="ORDENES_TRABAJO").update(orden=3)
    Modulo.objects.filter(codigo="PLANTILLAS_TALLER").update(orden=4)
    Modulo.objects.filter(codigo="TIPOS_SERVICIO").update(orden=5)

    permisos = []
    for codigo, nombre, accion in PERMISOS_CITAS:
        permiso, _ = Permiso.objects.update_or_create(
            codigo=codigo,
            defaults={
                "id_modulo": taller or citas,
                "nombre": nombre,
                "accion": accion,
                "estado": True,
                "grupo_padre": "Taller",
                "grupo_submodulo": "Citas",
            },
        )
        permisos.append(permiso)

    admin = Rol.objects.filter(codigo="ADMINISTRADOR").first()
    if admin:
        for permiso in permisos:
            RolPermiso.objects.get_or_create(
                id_rol=admin,
                id_permiso=permiso,
                defaults={"alcance": "GLOBAL"},
            )


def revertir_citas_menu_y_permisos(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")

    Permiso.objects.filter(codigo__in=[codigo for codigo, _, _ in PERMISOS_CITAS]).delete()
    Modulo.objects.filter(codigo="CITAS").delete()
    Modulo.objects.filter(codigo="ORDENES_TRABAJO").update(orden=2)
    Modulo.objects.filter(codigo="PLANTILLAS_TALLER").update(orden=3)
    Modulo.objects.filter(codigo="TIPOS_SERVICIO").update(orden=4)


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0015_comparador_placa_menu"),
    ]

    operations = [
        migrations.RunPython(crear_citas_menu_y_permisos, revertir_citas_menu_y_permisos),
    ]
