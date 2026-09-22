"""
Separa "ver el módulo en el menú lateral" de "poder leer esos datos desde
cualquier pantalla" (ej. los desplegables de Nueva Orden de Trabajo).

Hasta ahora Modulo.permiso_ver apuntaba al MISMO código que ya exigía el
ViewSet/endpoint (ej. VEHICULOS.VER), así que dar ese permiso para que un
desplegable funcionara también hacía aparecer el módulo completo como opción
independiente en el menú. Esta migración crea, para cada módulo hoja con
permiso_ver, un permiso nuevo dedicado solo a la visibilidad en el menú
(<CODIGO_MODULO>.VER_MENU) y reapunta Modulo.permiso_ver hacia él.

El permiso de datos original (ej. VEHICULOS.VER) NO se toca: sigue siendo
exactamente el mismo que ya exige el backend en cada endpoint, así que nada
dejará de funcionar para nadie.

Para que ningún rol/usuario pierda hoy la visibilidad de un módulo que ya
podía ver, todo RolPermiso/UsuarioPermiso que tuviera el permiso de datos
viejo recibe automáticamente el nuevo permiso de menú equivalente.
"""
from django.db import migrations

# (codigo_del_modulo_hoja, codigo_de_permiso_de_datos_que_usa_hoy_como_permiso_ver)
MODULOS_CON_PERMISO_VER = [
    ("CLIENTES", "CONTACTOS.CLIENTES.VER"),
    ("PROVEEDORES", "CONTACTOS.PROVEEDORES.VER"),
    ("TRANSPORTISTAS", "CONTACTOS.TRANSPORTISTAS.VER"),
    ("USUARIOS", "SEGURIDAD.USUARIOS.VER"),
    ("ROLES", "SEGURIDAD.ROLES.VER"),
    ("REPUESTOS", "INVENTARIO.REPUESTOS.VER"),
    ("CATEGORIAS", "INVENTARIO.CATEGORIAS.VER"),
    ("MARCAS", "INVENTARIO.MARCAS.VER"),
    ("KARDEX", "INVENTARIO.KARDEX.VER"),
    ("ALMACENES", "INVENTARIO.ALMACENES.VER"),
    ("UNIDADES", "INVENTARIO.REPUESTOS.VER"),
    ("UBICACIONES", "INVENTARIO.ALMACENES.VER"),
    ("SUCURSALES", "INVENTARIO.ALMACENES.VER"),
    ("STOCK_UBICACIONES", "INVENTARIO.STOCK_UBICACIONES.VER"),
    ("GUIAS_REMISION", "INVENTARIO.TRASLADOS.VER"),
    ("TRASLADOS_ALMACEN", "INVENTARIO.TRASLADOS.VER"),
    ("IMPUESTOS", "INVENTARIO.IMPUESTOS.VER"),
    ("VEHICULOS", "VEHICULOS.VER"),
    ("ORDENES_TRABAJO", "ORDENES_TRABAJO.VER"),
    ("PLANTILLAS_TALLER", "PLANTILLAS_TALLER.VER"),
    ("TIPOS_SERVICIO", "TIPOS_SERVICIO.VER"),
    ("POS", "VENTAS.POS.VER"),
    ("REGISTRO_MANUAL_VENTAS", "VENTAS.REGISTRO_MANUAL.VER"),
    ("CAJA_CONFIG", "VENTAS.CONFIGURACION.VER"),
    ("POR_COBRAR", "CUENTAS.POR_COBRAR.VER"),
    ("COMPRAS_CXP", "CUENTAS.POR_PAGAR.VER"),
    ("COMPRAS_HISTORIAL", "COMPRAS.VER"),
    ("COMPRAS_TIPO_COMPROBANTE", "COMPRAS.VER"),
    ("REPORTES_VENTAS", "REPORTES.VENTAS.VER"),
    ("REPORTES_PRODUCTOS", "REPORTES.PRODUCTOS.VER"),
    ("REPORTES_CLIENTES", "REPORTES.CLIENTES.VER"),
    ("REPORTES_COMPRAS", "REPORTES.COMPRAS.VER"),
    ("REPORTES_AVANZADO", "REPORTES.AVANZADO.VER"),
    ("REPORTES_VEHICULOS", "REPORTES.VEHICULO.VER"),
    ("REPORTES_KIOSKOS", "REPORTES.KIOSKOS.VER"),
    ("CAJAS_DASHBOARD", "CAJAS.VER"),
    ("CAJAS_TRANSFERENCIAS", "CAJAS.TRANSFERENCIAS.VER"),
    ("CAJAS_HISTORIAL", "CAJAS.HISTORIAL.VER"),
    ("SERIES_INTERNAS", "SERIES_INTERNAS.VER"),
    ("VEHICULOS_TRANSPORTE", "VEHICULOS_TRANSPORTE.VER"),
    ("CUENTAS_BANCARIAS", "CUENTAS_BANCARIAS.VER"),
    ("UBIGEO", "UBIGEO.VER"),
    ("EMPRESA", "EMPRESA.VER"),
    ("KIOSKOS", "CONFIGURACION.KIOSKOS.VER"),
]


def crear_permisos_ver_menu(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")
    RolPermiso = apps.get_model("seguridad", "RolPermiso")
    UsuarioPermiso = apps.get_model("seguridad", "UsuarioPermiso")

    for modulo_codigo, codigo_ver_actual in MODULOS_CON_PERMISO_VER:
        try:
            modulo = Modulo.objects.get(codigo=modulo_codigo)
        except Modulo.DoesNotExist:
            # Instalación parcial/desincronizada: no rompemos la migración por esto.
            continue

        try:
            permiso_ver_actual = Permiso.objects.get(codigo=codigo_ver_actual)
        except Permiso.DoesNotExist:
            continue

        codigo_menu = f"{modulo_codigo}.VER_MENU"
        permiso_menu, _ = Permiso.objects.get_or_create(
            codigo=codigo_menu,
            defaults={
                "id_modulo_id": permiso_ver_actual.id_modulo_id,
                "nombre": f'Mostrar "{modulo.nombre}" en el menú',
                "accion": "VER_MENU",
                "estado": True,
                "grupo_padre": permiso_ver_actual.grupo_padre,
                "grupo_submodulo": permiso_ver_actual.grupo_submodulo,
            },
        )

        # Desde ahora este módulo se muestra en el menú según el permiso
        # nuevo, desacoplado del permiso de datos que sigue exigiendo el
        # ViewSet/endpoint (ese no se toca).
        if modulo.permiso_ver != codigo_menu:
            modulo.permiso_ver = codigo_menu
            modulo.save(update_fields=["permiso_ver"])

        # Backfill: quien hoy ve el módulo (por tener el permiso de datos
        # viejo) debe seguir viéndolo exactamente igual después de este
        # cambio, sin que un admin tenga que reconfigurar nada a mano.
        for rp in RolPermiso.objects.filter(id_permiso=permiso_ver_actual):
            RolPermiso.objects.get_or_create(
                id_rol_id=rp.id_rol_id,
                id_permiso=permiso_menu,
                defaults={"alcance": rp.alcance},
            )

        for up in UsuarioPermiso.objects.filter(id_permiso=permiso_ver_actual):
            UsuarioPermiso.objects.get_or_create(
                id_usuario_id=up.id_usuario_id,
                id_permiso=permiso_menu,
                tipo=up.tipo,
                defaults={
                    "alcance": up.alcance,
                    "fecha_inicio": up.fecha_inicio,
                    "fecha_fin": up.fecha_fin,
                    "estado": up.estado,
                },
            )


def revertir(apps, schema_editor):
    Modulo = apps.get_model("seguridad", "Modulo")
    Permiso = apps.get_model("seguridad", "Permiso")

    for modulo_codigo, codigo_ver_actual in MODULOS_CON_PERMISO_VER:
        Modulo.objects.filter(codigo=modulo_codigo).update(permiso_ver=codigo_ver_actual)
        # CASCADE en Permiso borra también los RolPermiso/UsuarioPermiso creados.
        Permiso.objects.filter(codigo=f"{modulo_codigo}.VER_MENU").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("seguridad", "0010_empresa_sunat_clave_secundaria_and_more"),
    ]

    operations = [
        migrations.RunPython(crear_permisos_ver_menu, revertir),
    ]
