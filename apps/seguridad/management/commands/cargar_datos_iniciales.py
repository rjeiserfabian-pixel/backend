"""
management/commands/cargar_datos_iniciales.py

Comando personalizado para poblar la base de datos con:
  - Todos los módulos del sistema (padre + submodulos para el menú)
  - Todos los permisos atomicos de cada módulo
  - 4 Roles iniciales: ADMINISTRADOR, RECEPCIONISTA, MECANICO, CLIENTE
  - Asignación de todos los permisos al rol ADMINISTRADOR (alcance GLOBAL)

Uso:
    python manage.py cargar_datos_iniciales

Diseñado para ser idempotente: si los datos ya existen, no los duplica.
Seguro para ejecutar en producción con datos existentes.
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.seguridad.models import Modulo, Rol, Permiso, RolPermiso

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Carga los datos iniciales de módulos, permisos y roles (idempotente)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Cargando datos iniciales del sistema ==="))

        # ── 1. ROLES ───────────────────────────────────────────────────────────
        roles_data = [
            {
                "codigo": "ADMINISTRADOR",
                "nombre": "Administrador",
                "descripcion": "Acceso total al sistema. Puede configurar roles, usuarios y permisos.",
                "es_sistema": True,
            },
            {
                "codigo": "RECEPCIONISTA",
                "nombre": "Recepcionista",
                "descripcion": "Gestión de citas, clientes y vehículos. Sin acceso a reportes financieros.",
                "es_sistema": False,
            },
            {
                "codigo": "MECANICO",
                "nombre": "Mecánico",
                "descripcion": "Acceso solo a las órdenes de trabajo asignadas.",
                "es_sistema": False,
            },
            {
                "codigo": "CLIENTE",
                "nombre": "Cliente",
                "descripcion": "Portal de cliente: visualiza sus propias órdenes y vehículos.",
                "es_sistema": False,
            },
        ]

        roles = {}
        for data in roles_data:
            rol, creado = Rol.objects.get_or_create(
                codigo=data["codigo"],
                defaults={**data, "estado": True},
            )
            roles[data["codigo"]] = rol
            self._log("Rol", data["codigo"], creado)

        # ── 2. MÓDULOS (con estructura padre → submodulos) ────────────────────
        #
        # Los códigos coinciden exactamente con los pre-existentes en DB.
        #
        modulos_data = [
            # ── Dashboard ─────────────────────────────────────────────────────
            {
                "codigo": "DASHBOARD", "nombre": "Dashboard",
                "icono": "dashboard", "ruta": "/dashboard",
                "orden": 1, "visible_menu": True, "padre": None,
            },
            # ── Contactos ─────────────────────────────────────────────────────
            {
                "codigo": "CONTACTOS", "nombre": "Contactos",
                "icono": "users", "ruta": None,
                "orden": 2, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "CLIENTES", "nombre": "Clientes",
                "icono": "users", "ruta": "/contactos/clientes",
                "orden": 1, "visible_menu": True, "padre": "CONTACTOS",
            },
            {
                "codigo": "PROVEEDORES", "nombre": "Proveedores",
                "icono": "store", "ruta": "/contactos/proveedores",
                "orden": 2, "visible_menu": True, "padre": "CONTACTOS",
            },
            {
                "codigo": "TRANSPORTISTAS", "nombre": "Transportistas",
                "icono": "truck", "ruta": "/contactos/transportistas",
                "orden": 3, "visible_menu": True, "padre": "CONTACTOS",
            },
            # ── Seguridad ─────────────────────────────────────────────────────
            {
                "codigo": "SEGURIDAD", "nombre": "Seguridad",
                "icono": "shield", "ruta": None,
                "orden": 3, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "USUARIOS", "nombre": "Usuarios",
                "icono": "users", "ruta": "/usuarios",
                "orden": 1, "visible_menu": True, "padre": "SEGURIDAD",
            },
            {
                "codigo": "ROLES", "nombre": "Roles y Permisos",
                "icono": "settings", "ruta": "/roles",
                "orden": 2, "visible_menu": True, "padre": "SEGURIDAD",
            },
            # ── Inventario ────────────────────────────────────────────────────
            {
                "codigo": "INVENTARIO", "nombre": "Inventario",
                "icono": "package", "ruta": None,
                "orden": 4, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "REPUESTOS", "nombre": "Repuestos",
                "icono": "package", "ruta": "/inventario/repuestos",
                "orden": 1, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "CATEGORIAS", "nombre": "Categorías",
                "icono": "tags", "ruta": "/inventario/categorias",
                "orden": 2, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "MARCAS", "nombre": "Marcas",
                "icono": "tag", "ruta": "/inventario/marcas",
                "orden": 3, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "KARDEX", "nombre": "Kardex",
                "icono": "list", "ruta": "/inventario/kardex",
                "orden": 4, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "ALMACENES", "nombre": "Almacenes",
                "icono": "store", "ruta": "/inventario/almacenes",
                "orden": 5, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "UNIDADES", "nombre": "Unidades",
                "icono": "ruler", "ruta": "/inventario/unidades",
                "orden": 6, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "UBICACIONES", "nombre": "Ubicaciones",
                "icono": "map-pin", "ruta": "/inventario/ubicaciones",
                "orden": 7, "visible_menu": True, "padre": "INVENTARIO",
            },
            {
                "codigo": "SUCURSALES", "nombre": "Sucursales",
                "icono": "store", "ruta": "/inventario/sucursales",
                "orden": 8, "visible_menu": True, "padre": "INVENTARIO",
            },
            # ── Taller ────────────────────────────────────────────────────────
            {
                "codigo": "TALLER", "nombre": "Taller",
                "icono": "wrench", "ruta": None,
                "orden": 6, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "VEHICULOS", "nombre": "Vehículos",
                "icono": "car", "ruta": "/vehiculos",
                "orden": 1, "visible_menu": True, "padre": "TALLER",
            },
            {
                "codigo": "ORDENES_TRABAJO", "nombre": "Órdenes de Trabajo",
                "icono": "clipboard-list", "ruta": "/taller/ordenes",
                "orden": 2, "visible_menu": True, "padre": "TALLER",
            },
            {
                "codigo": "PLANTILLAS_TALLER", "nombre": "Plantillas de Servicio",
                "icono": "filetext", "ruta": "/taller/plantillas",
                "orden": 3, "visible_menu": True, "padre": "TALLER",
            },
            {
                "codigo": "TIPOS_SERVICIO", "nombre": "Tipos de Servicio",
                "icono": "list-checks", "ruta": "/taller/tipos-servicio",
                "orden": 4, "visible_menu": True, "padre": "TALLER",
            },
            # ── Ventas ────────────────────────────────────────────────────────
            {
                "codigo": "VENTAS", "nombre": "Ventas",
                "icono": "shoppingcart", "ruta": None,
                "orden": 7, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "POS", "nombre": "Punto de Venta (POS)",
                "icono": "credit-card", "ruta": "/ventas/pos",
                "orden": 1, "visible_menu": True, "padre": "VENTAS",
            },
            {
                "codigo": "REGISTRO_MANUAL_VENTAS", "nombre": "Registro Manual",
                "icono": "filetext", "ruta": "/ventas/registro-manual",
                "orden": 2, "visible_menu": True, "padre": "VENTAS",
            },
            {
                "codigo": "CAJA_CONFIG", "nombre": "Configuración de Ventas",
                "icono": "settings", "ruta": "/ventas/configuracion",
                "orden": 3, "visible_menu": True, "padre": "VENTAS",
            },
            # ── Cuentas ───────────────────────────────────────────────────────
            {
                "codigo": "CUENTAS", "nombre": "Cuentas",
                "icono": "banknote", "ruta": None,
                "orden": 8, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "POR_COBRAR", "nombre": "Por Cobrar",
                "icono": "credit-card", "ruta": "/cuentas/por-cobrar",
                "orden": 1, "visible_menu": True, "padre": "CUENTAS",
            },
            {
                "codigo": "COMPRAS_CXP", "nombre": "Por Pagar",
                "icono": "credit-card", "ruta": "/compras/cuentas-por-pagar",
                "orden": 2, "visible_menu": True, "padre": "CUENTAS",
            },
            # ── Compras ───────────────────────────────────────────────────────
            {
                "codigo": "COMPRAS", "nombre": "Compras",
                "icono": "shopping-bag", "ruta": None,
                "orden": 9, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "COMPRAS_HISTORIAL", "nombre": "Registro de Compras",
                "icono": "shopping-cart", "ruta": "/compras",
                "orden": 1, "visible_menu": True, "padre": "COMPRAS",
            },
            # ── Reportes ──────────────────────────────────────────────────────
            {
                "codigo": "REPORTES", "nombre": "Reportes",
                "icono": "filetext", "ruta": None,
                "orden": 10, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "REPORTES_VENTAS", "nombre": "Reporte de Ventas",
                "icono": "filetext", "ruta": "/reportes/ventas",
                "orden": 1, "visible_menu": True, "padre": "REPORTES",
            },
            {
                "codigo": "REPORTES_PRODUCTOS", "nombre": "Reporte de Productos",
                "icono": "package", "ruta": "/reportes/productos",
                "orden": 2, "visible_menu": True, "padre": "REPORTES",
            },
            {
                "codigo": "REPORTES_CLIENTES", "nombre": "Reporte de Clientes",
                "icono": "users", "ruta": "/reportes/clientes",
                "orden": 3, "visible_menu": True, "padre": "REPORTES",
            },
            {
                "codigo": "REPORTES_COMPRAS", "nombre": "Reporte Compra",
                "icono": "shopping-cart", "ruta": "/reportes/compras",
                "orden": 4, "visible_menu": True, "padre": "REPORTES",
            },
            {
                "codigo": "REPORTES_AVANZADO", "nombre": "Reporte Avanzado",
                "icono": "banknote", "ruta": "/reportes/avanzado",
                "orden": 5, "visible_menu": True, "padre": "REPORTES",
            },
            {
                "codigo": "REPORTES_VEHICULOS", "nombre": "Reporte Vehículo",
                "icono": "car", "ruta": "/reportes/vehiculos",
                "orden": 6, "visible_menu": True, "padre": "REPORTES",
            },
            {
                "codigo": "REPORTES_CAJA", "nombre": "Reporte de Caja",
                "icono": "wallet", "ruta": "/reportes/caja",
                "orden": 7, "visible_menu": True, "padre": "REPORTES",
            },
            # ── Cajas ─────────────────────────────────────────────────────────
            {
                "codigo": "CAJAS", "nombre": "Cajas",
                "icono": "wallet", "ruta": None,
                "orden": 11, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "CAJAS_DASHBOARD", "nombre": "Dashboard Cajas",
                "icono": "wallet", "ruta": "/cajas",
                "orden": 1, "visible_menu": True, "padre": "CAJAS",
            },
            {
                "codigo": "CAJAS_TRANSFERENCIAS", "nombre": "Transferencias",
                "icono": "arrowrightleft", "ruta": "/cajas/transferencias",
                "orden": 2, "visible_menu": True, "padre": "CAJAS",
            },
            {
                "codigo": "CAJAS_HISTORIAL", "nombre": "Historial de Cajas",
                "icono": "history", "ruta": "/cajas/historial",
                "orden": 3, "visible_menu": True, "padre": "CAJAS",
            },
            # ── Configuración ─────────────────────────────────────────────────
            {
                "codigo": "CONFIG", "nombre": "Configuracion",
                "icono": "settings", "ruta": None,
                "orden": 12, "visible_menu": True, "padre": None,
            },
            {
                "codigo": "SERIES_INTERNAS", "nombre": "Series Internas",
                "icono": "list", "ruta": "/seguridad/series-internas",
                "orden": 1, "visible_menu": True, "padre": "CONFIG",
            },
            {
                "codigo": "VEHICULOS_TRANSPORTE", "nombre": "Vehículos de Transporte",
                "icono": "truck", "ruta": "/configuracion/vehiculos-transporte",
                "orden": 2, "visible_menu": True, "padre": "CONFIG",
            },
            {
                "codigo": "CUENTAS_BANCARIAS", "nombre": "Cuentas Bancarias",
                "icono": "banknote", "ruta": "/seguridad/cuentas-bancarias",
                "orden": 3, "visible_menu": True, "padre": "CONFIG",
            },
            {
                "codigo": "UBIGEO", "nombre": "Ubicaciones",
                "icono": "map-pin", "ruta": "/seguridad/ubigeo",
                "orden": 4, "visible_menu": True, "padre": "CONFIG",
            },
            {
                "codigo": "EMPRESA", "nombre": "Empresa",
                "icono": "building", "ruta": "/seguridad/empresa",
                "orden": 5, "visible_menu": True, "padre": "CONFIG",
            },
        ]

        # Primero crear todos los módulos padre (sin padre)
        modulos = {}
        padres = [m for m in modulos_data if m["padre"] is None]
        hijos = [m for m in modulos_data if m["padre"] is not None]

        for data in padres:
            modulo, creado = Modulo.objects.get_or_create(
                codigo=data["codigo"],
                defaults={
                    "nombre": data["nombre"],
                    "icono": data.get("icono"),
                    "ruta": data.get("ruta"),
                    "orden": data.get("orden", 0),
                    "visible_menu": data.get("visible_menu", True),
                    "estado": True,
                    "id_modulo_padre": None,
                },
            )
            # Update si ya existía pero con otros datos (opcional)
            if not creado:
                modulo.nombre = data["nombre"]
                modulo.icono = data.get("icono")
                modulo.ruta = data.get("ruta")
                modulo.orden = data.get("orden", 0)
                modulo.save()
            modulos[data["codigo"]] = modulo
            self._log("Módulo (padre)", data["codigo"], creado)

        # Luego crear los submodulos enlazando al padre
        for data in hijos:
            padre_codigo = data["padre"]
            padre_obj = modulos.get(padre_codigo)
            if not padre_obj:
                # El padre puede ya existir en BD sin haber pasado por el loop (reejecutado)
                try:
                    padre_obj = Modulo.objects.get(codigo=padre_codigo)
                    modulos[padre_codigo] = padre_obj
                except Modulo.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(f"  [!!] Padre '{padre_codigo}' no encontrado para '{data['codigo']}'. Saltando.")
                    )
                    continue

            modulo, creado = Modulo.objects.get_or_create(
                codigo=data["codigo"],
                defaults={
                    "nombre": data["nombre"],
                    "icono": data.get("icono"),
                    "ruta": data.get("ruta"),
                    "orden": data.get("orden", 0),
                    "visible_menu": data.get("visible_menu", True),
                    "estado": True,
                    "id_modulo_padre": padre_obj,
                },
            )
            if not creado:
                modulo.nombre = data["nombre"]
                modulo.icono = data.get("icono")
                modulo.ruta = data.get("ruta")
                modulo.orden = data.get("orden", 0)
                modulo.id_modulo_padre = padre_obj
                modulo.save()
            modulos[data["codigo"]] = modulo
            self._log("Módulo (hijo)", data["codigo"], creado)

        # ── 3. PERMISOS ───────────────────────────────────────────────────────
        #
        permisos_data = [
            # ── DASHBOARD ─────────────────────────────────────────────────────
            {"modulo": "DASHBOARD", "codigo": "DASHBOARD.VER",    "nombre": "Ver Dashboard",    "accion": "VER"},

            # ── SEGURIDAD ─────────────────────────────────────────────────────
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.USUARIOS.VER",      "nombre": "Ver usuarios",             "accion": "VER"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.USUARIOS.CREAR",    "nombre": "Crear usuarios",           "accion": "CREAR"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.USUARIOS.EDITAR",   "nombre": "Editar usuarios",          "accion": "EDITAR"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.USUARIOS.ELIMINAR", "nombre": "Eliminar usuarios",        "accion": "ELIMINAR"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.ROLES.VER",         "nombre": "Ver roles",                "accion": "VER"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.ROLES.CREAR",       "nombre": "Crear roles",              "accion": "CREAR"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.ROLES.EDITAR",      "nombre": "Editar roles y permisos",  "accion": "EDITAR"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.ROLES.ELIMINAR",    "nombre": "Eliminar roles",           "accion": "ELIMINAR"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.PERMISOS.VER",      "nombre": "Ver catálogo de permisos", "accion": "VER"},
            {"modulo": "SEGURIDAD", "codigo": "SEGURIDAD.MODULOS.VER",       "nombre": "Ver módulos del sistema",  "accion": "VER"},

            # ── CONTACTOS ─────────────────────────────────────────────────────
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.CLIENTES.VER",           "nombre": "Ver clientes",           "accion": "VER"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.CLIENTES.CREAR",         "nombre": "Crear clientes",         "accion": "CREAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.CLIENTES.EDITAR",        "nombre": "Editar clientes",        "accion": "EDITAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.CLIENTES.ELIMINAR",      "nombre": "Eliminar clientes",      "accion": "ELIMINAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.PROVEEDORES.VER",        "nombre": "Ver proveedores",        "accion": "VER"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.PROVEEDORES.CREAR",      "nombre": "Crear proveedores",      "accion": "CREAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.PROVEEDORES.EDITAR",     "nombre": "Editar proveedores",     "accion": "EDITAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.PROVEEDORES.ELIMINAR",   "nombre": "Eliminar proveedores",   "accion": "ELIMINAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.TRANSPORTISTAS.VER",     "nombre": "Ver transportistas",     "accion": "VER"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.TRANSPORTISTAS.CREAR",   "nombre": "Crear transportistas",   "accion": "CREAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.TRANSPORTISTAS.EDITAR",  "nombre": "Editar transportistas",  "accion": "EDITAR"},
            {"modulo": "CONTACTOS", "codigo": "CONTACTOS.TRANSPORTISTAS.ELIMINAR","nombre": "Eliminar transportistas","accion": "ELIMINAR"},

            # ── INVENTARIO ────────────────────────────────────────────────────
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.REPUESTOS.VER",        "nombre": "Ver repuestos",          "accion": "VER"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.REPUESTOS.CREAR",      "nombre": "Crear repuestos",        "accion": "CREAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.REPUESTOS.EDITAR",     "nombre": "Editar repuestos",       "accion": "EDITAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.REPUESTOS.ELIMINAR",   "nombre": "Eliminar repuestos",     "accion": "ELIMINAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.CATEGORIAS.VER",       "nombre": "Ver categorías",         "accion": "VER"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.CATEGORIAS.CREAR",     "nombre": "Crear categorías",       "accion": "CREAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.CATEGORIAS.EDITAR",    "nombre": "Editar categorías",      "accion": "EDITAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.CATEGORIAS.ELIMINAR",  "nombre": "Eliminar categorías",    "accion": "ELIMINAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.MARCAS.VER",           "nombre": "Ver marcas",             "accion": "VER"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.MARCAS.CREAR",         "nombre": "Crear marcas",           "accion": "CREAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.MARCAS.EDITAR",        "nombre": "Editar marcas",          "accion": "EDITAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.MARCAS.ELIMINAR",      "nombre": "Eliminar marcas",        "accion": "ELIMINAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.KARDEX.VER",           "nombre": "Ver kardex",             "accion": "VER"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.ALMACENES.VER",        "nombre": "Ver almacenes",          "accion": "VER"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.ALMACENES.CREAR",      "nombre": "Crear almacenes",        "accion": "CREAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.ALMACENES.EDITAR",     "nombre": "Editar almacenes",       "accion": "EDITAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.TRASLADOS.VER",        "nombre": "Ver traslados",          "accion": "VER"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.TRASLADOS.CREAR",      "nombre": "Crear traslados",        "accion": "CREAR"},
            {"modulo": "INVENTARIO", "codigo": "INVENTARIO.TRASLADOS.APROBAR",    "nombre": "Aprobar traslados",      "accion": "APROBAR"},

            # ── VEHÍCULOS ─────────────────────────────────────────────────────
            {"modulo": "VEHICULOS", "codigo": "VEHICULOS.VER",      "nombre": "Ver vehículos",      "accion": "VER"},
            {"modulo": "VEHICULOS", "codigo": "VEHICULOS.CREAR",    "nombre": "Registrar vehículos","accion": "CREAR"},
            {"modulo": "VEHICULOS", "codigo": "VEHICULOS.EDITAR",   "nombre": "Editar vehículos",   "accion": "EDITAR"},
            {"modulo": "VEHICULOS", "codigo": "VEHICULOS.ELIMINAR", "nombre": "Eliminar vehículos", "accion": "ELIMINAR"},

            # ── TALLER ────────────────────────────────────────────────────────
            {"modulo": "TALLER", "codigo": "ORDENES_TRABAJO.VER",           "nombre": "VER Órdenes de Trabajo",           "accion": "VER"},
            {"modulo": "TALLER", "codigo": "ORDENES_TRABAJO.CREAR",         "nombre": "CREAR Órdenes de Trabajo",         "accion": "CREAR"},
            {"modulo": "TALLER", "codigo": "ORDENES_TRABAJO.EDITAR",        "nombre": "EDITAR Órdenes de Trabajo",        "accion": "EDITAR"},
            {"modulo": "TALLER", "codigo": "ORDENES_TRABAJO.ELIMINAR",      "nombre": "ELIMINAR Órdenes de Trabajo",      "accion": "ELIMINAR"},
            {"modulo": "TALLER", "codigo": "ORDENES_TRABAJO.CAMBIAR_ESTADO","nombre": "Cambiar Estado Orden de Trabajo",  "accion": "CAMBIAR_ESTADO"},
            {"modulo": "TALLER", "codigo": "ORDENES_TRABAJO.APROBAR",       "nombre": "Aprobar/Finalizar Orden de Trabajo","accion": "APROBAR"},
            {"modulo": "TALLER", "codigo": "PLANTILLAS_TALLER.VER",         "nombre": "Ver Plantillas de Servicio",       "accion": "VER"},
            {"modulo": "TALLER", "codigo": "PLANTILLAS_TALLER.CREAR",       "nombre": "Crear Plantillas",                 "accion": "CREAR"},
            {"modulo": "TALLER", "codigo": "PLANTILLAS_TALLER.EDITAR",      "nombre": "Editar Plantillas",                "accion": "EDITAR"},
            {"modulo": "TALLER", "codigo": "PLANTILLAS_TALLER.ELIMINAR",    "nombre": "Eliminar Plantillas",              "accion": "ELIMINAR"},
            {"modulo": "TALLER", "codigo": "TIPOS_SERVICIO.VER",            "nombre": "Ver Tipos de Servicio",            "accion": "VER"},
            {"modulo": "TALLER", "codigo": "TIPOS_SERVICIO.CREAR",          "nombre": "Crear Tipos de Servicio",          "accion": "CREAR"},
            {"modulo": "TALLER", "codigo": "TIPOS_SERVICIO.EDITAR",         "nombre": "Editar Tipos de Servicio",         "accion": "EDITAR"},

            # ── VENTAS ────────────────────────────────────────────────────────
            {"modulo": "VENTAS", "codigo": "VENTAS.POS.VER",              "nombre": "Acceder al POS",            "accion": "VER"},
            {"modulo": "VENTAS", "codigo": "VENTAS.POS.CREAR",            "nombre": "Registrar venta en el POS", "accion": "CREAR"},
            {"modulo": "VENTAS", "codigo": "VENTAS.REGISTRO_MANUAL.VER",  "nombre": "Ver registro manual",       "accion": "VER"},
            {"modulo": "VENTAS", "codigo": "VENTAS.REGISTRO_MANUAL.CREAR","nombre": "Crear venta manual",        "accion": "CREAR"},
            {"modulo": "VENTAS", "codigo": "VENTAS.CONFIGURACION.VER",    "nombre": "Ver config. de ventas",     "accion": "VER"},
            {"modulo": "VENTAS", "codigo": "VENTAS.CONFIGURACION.EDITAR", "nombre": "Editar config. de ventas",  "accion": "EDITAR"},

            # ── CUENTAS ───────────────────────────────────────────────────────
            {"modulo": "CUENTAS", "codigo": "CUENTAS.POR_COBRAR.VER",            "nombre": "Ver cuentas por cobrar",       "accion": "VER"},
            {"modulo": "CUENTAS", "codigo": "CUENTAS.POR_COBRAR.REGISTRAR_PAGO", "nombre": "Registrar pago a cuenta",      "accion": "REGISTRAR_PAGO"},
            {"modulo": "CUENTAS", "codigo": "CUENTAS.POR_PAGAR.VER",             "nombre": "Ver cuentas por pagar",        "accion": "VER"},
            {"modulo": "CUENTAS", "codigo": "CUENTAS.POR_PAGAR.REGISTRAR_PAGO",  "nombre": "Registrar pago a proveedor",   "accion": "REGISTRAR_PAGO"},

            # ── COMPRAS ───────────────────────────────────────────────────────
            {"modulo": "COMPRAS", "codigo": "COMPRAS.VER",      "nombre": "Ver compras",           "accion": "VER"},
            {"modulo": "COMPRAS", "codigo": "COMPRAS.CREAR",    "nombre": "Registrar compras",     "accion": "CREAR"},
            {"modulo": "COMPRAS", "codigo": "COMPRAS.EDITAR",   "nombre": "Editar compras",        "accion": "EDITAR"},
            {"modulo": "COMPRAS", "codigo": "COMPRAS.ELIMINAR", "nombre": "Eliminar compras",      "accion": "ELIMINAR"},
            {"modulo": "COMPRAS", "codigo": "COMPRAS.APROBAR",  "nombre": "Aprobar compras",       "accion": "APROBAR"},

            # ── REPORTES ──────────────────────────────────────────────────────
            {"modulo": "REPORTES", "codigo": "REPORTES.VENTAS.VER",     "nombre": "Ver reporte de ventas",     "accion": "VER"},
            {"modulo": "REPORTES", "codigo": "REPORTES.VENTAS.EXPORTAR","nombre": "Exportar reporte de ventas","accion": "EXPORTAR"},
            {"modulo": "REPORTES", "codigo": "REPORTES.PRODUCTOS.VER",  "nombre": "Ver reporte de productos",  "accion": "VER"},
            {"modulo": "REPORTES", "codigo": "REPORTES.CLIENTES.VER",   "nombre": "Ver reporte de clientes",   "accion": "VER"},
            {"modulo": "REPORTES", "codigo": "REPORTES.COMPRAS.VER",    "nombre": "Ver reporte de compras",    "accion": "VER"},
            {"modulo": "REPORTES", "codigo": "REPORTES.AVANZADO.VER",   "nombre": "Ver reporte avanzado",      "accion": "VER"},
            {"modulo": "REPORTES", "codigo": "REPORTES.VEHICULO.VER",   "nombre": "Ver reporte de vehículos",  "accion": "VER"},

            # ── CAJAS ─────────────────────────────────────────────────────────
            {"modulo": "CAJAS", "codigo": "CAJAS.VER",                "nombre": "Ver dashboard de cajas",        "accion": "VER"},
            {"modulo": "CAJAS", "codigo": "CAJAS.SESION.ABRIR",       "nombre": "Abrir sesión de caja",          "accion": "ABRIR"},
            {"modulo": "CAJAS", "codigo": "CAJAS.SESION.CERRAR",      "nombre": "Cerrar sesión de caja",         "accion": "CERRAR"},
            {"modulo": "CAJAS", "codigo": "CAJAS.MOVIMIENTOS.VER",    "nombre": "Ver movimientos de caja",       "accion": "VER"},
            {"modulo": "CAJAS", "codigo": "CAJAS.MOVIMIENTOS.CREAR",  "nombre": "Registrar movimiento manual",   "accion": "CREAR"},
            {"modulo": "CAJAS", "codigo": "CAJAS.MOVIMIENTOS.APROBAR","nombre": "Aprobar movimientos de caja",   "accion": "APROBAR"},
            {"modulo": "CAJAS", "codigo": "CAJAS.MOVIMIENTOS.RECHAZAR","nombre": "Rechazar movimientos de caja", "accion": "RECHAZAR"},
            {"modulo": "CAJAS", "codigo": "CAJAS.TRANSFERENCIAS.VER", "nombre": "Ver transferencias entre cajas","accion": "VER"},
            {"modulo": "CAJAS", "codigo": "CAJAS.TRANSFERENCIAS.CREAR","nombre": "Realizar transferencia",       "accion": "TRANSFERIR"},
            {"modulo": "CAJAS", "codigo": "CAJAS.HISTORIAL.VER",      "nombre": "Ver historial de sesiones",     "accion": "VER"},

            # ── CONFIGURACIÓN ─────────────────────────────────────────────────
            {"modulo": "SERIES_INTERNAS", "codigo": "SERIES_INTERNAS.VER",         "nombre": "Ver series internas",           "accion": "VER"},
            {"modulo": "SERIES_INTERNAS", "codigo": "SERIES_INTERNAS.CREAR",       "nombre": "Crear series internas",         "accion": "CREAR"},
            {"modulo": "SERIES_INTERNAS", "codigo": "SERIES_INTERNAS.EDITAR",      "nombre": "Editar series internas",        "accion": "EDITAR"},
            {"modulo": "VEHICULOS_TRANSPORTE", "codigo": "VEHICULOS_TRANSPORTE.VER",    "nombre": "Ver vehículos de transporte",   "accion": "VER"},
            {"modulo": "VEHICULOS_TRANSPORTE", "codigo": "VEHICULOS_TRANSPORTE.CREAR",  "nombre": "Crear vehículos de transporte", "accion": "CREAR"},
            {"modulo": "VEHICULOS_TRANSPORTE", "codigo": "VEHICULOS_TRANSPORTE.EDITAR", "nombre": "Editar vehículos de transporte","accion": "EDITAR"},
            {"modulo": "CUENTAS_BANCARIAS", "codigo": "CUENTAS_BANCARIAS.VER",     "nombre": "Ver cuentas bancarias",         "accion": "VER"},
            {"modulo": "CUENTAS_BANCARIAS", "codigo": "CUENTAS_BANCARIAS.CREAR",   "nombre": "Crear cuentas bancarias",       "accion": "CREAR"},
            {"modulo": "CUENTAS_BANCARIAS", "codigo": "CUENTAS_BANCARIAS.EDITAR",  "nombre": "Editar cuentas bancarias",      "accion": "EDITAR"},
            {"modulo": "UBIGEO", "codigo": "UBIGEO.VER",                          "nombre": "Ver ubicaciones",               "accion": "VER"},
            {"modulo": "EMPRESA", "codigo": "EMPRESA.VER",                        "nombre": "Ver datos de la empresa",       "accion": "VER"},
            {"modulo": "EMPRESA", "codigo": "EMPRESA.EDITAR",                     "nombre": "Editar datos de la empresa",    "accion": "EDITAR"},
        ]

        permisos = {}
        for data in permisos_data:
            modulo_codigo = data["modulo"]
            modulo_obj = modulos.get(modulo_codigo)
            if not modulo_obj:
                # Puede existir en BD si el script ya corrió antes
                try:
                    modulo_obj = Modulo.objects.get(codigo=modulo_codigo)
                    modulos[modulo_codigo] = modulo_obj
                except Modulo.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(f"  [!!] Módulo '{modulo_codigo}' no encontrado para permiso '{data['codigo']}'. Saltando.")
                    )
                    continue

            permiso, creado = Permiso.objects.get_or_create(
                codigo=data["codigo"],
                defaults={
                    "id_modulo": modulo_obj,
                    "nombre": data["nombre"],
                    "accion": data["accion"],
                    "estado": True,
                },
            )
            if not creado:
                permiso.nombre = data["nombre"]
                permiso.accion = data["accion"]
                permiso.save()
            permisos[data["codigo"]] = permiso
            self._log("Permiso", data["codigo"], creado)

        # ── 4. ASIGNAR TODOS LOS PERMISOS AL ROL ADMINISTRADOR (GLOBAL) ───────
        admin_rol = roles["ADMINISTRADOR"]
        nuevos_rp = []
        for permiso in permisos.values():
            exists = RolPermiso.objects.filter(
                id_rol=admin_rol, id_permiso=permiso
            ).exists()
            if not exists:
                nuevos_rp.append(
                    RolPermiso(id_rol=admin_rol, id_permiso=permiso, alcance="GLOBAL")
                )

        if nuevos_rp:
            RolPermiso.objects.bulk_create(nuevos_rp)
            self.stdout.write(
                self.style.SUCCESS(f"  [OK] {len(nuevos_rp)} permisos nuevos asignados a ADMINISTRADOR")
            )
        else:
            self.stdout.write("  [--] ADMINISTRADOR ya tenía todos los permisos.")

        self.stdout.write(self.style.SUCCESS(
            f"\n[DONE] Datos iniciales cargados/actualizados:"
            f"\n  - {len(modulos)} módulos"
            f"\n  - {len(permisos)} permisos"
            f"\n  - {len(roles)} roles"
            f"\n"
        ))

    def _log(self, tipo, codigo, creado):
        if creado:
            self.stdout.write(self.style.SUCCESS(f"  [OK] {tipo} creado: {codigo}"))
        else:
            self.stdout.write(f"  [--] {tipo} ya existe (actualizado): {codigo}")
