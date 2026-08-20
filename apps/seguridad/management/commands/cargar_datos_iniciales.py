"""
management/commands/cargar_datos_iniciales.py

Comando personalizado para poblar la base de datos con:
  - 1 Módulo: SEGURIDAD
  - 4 Roles iniciales: ADMINISTRADOR, RECEPCIONISTA, MECANICO, CLIENTE
  - 10 Permisos base para el módulo SEGURIDAD
  - Asignación de todos los permisos al rol ADMINISTRADOR (alcance GLOBAL)

Uso:
    python manage.py cargar_datos_iniciales

Diseñado para ser idempotente: si los datos ya existen, no los duplica.
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.seguridad.models import Modulo, Rol, Permiso, RolPermiso

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Carga los datos iniciales del módulo de seguridad (idempotente)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Cargando datos iniciales ==="))

        # 1. MÓDULOS
        modulo_seg, creado = Modulo.objects.get_or_create(
            codigo="SEGURIDAD",
            defaults={
                "nombre": "Seguridad",
                "icono": "shield",
                "ruta": "/seguridad",
                "orden": 1,
                "visible_menu": True,
                "estado": True,
            },
        )
        self._log("Módulo", "SEGURIDAD", creado)

        # 2. ROLES
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

        # 3. PERMISOS BASE DEL MÓDULO SEGURIDAD
        permisos_data = [
            {"codigo": "SEGURIDAD.USUARIOS.VER",      "nombre": "Ver usuarios",             "accion": "VER"},
            {"codigo": "SEGURIDAD.USUARIOS.CREAR",    "nombre": "Crear usuarios",           "accion": "CREAR"},
            {"codigo": "SEGURIDAD.USUARIOS.EDITAR",   "nombre": "Editar usuarios",          "accion": "EDITAR"},
            {"codigo": "SEGURIDAD.USUARIOS.ELIMINAR", "nombre": "Eliminar usuarios",        "accion": "ELIMINAR"},
            {"codigo": "SEGURIDAD.ROLES.VER",         "nombre": "Ver roles",                "accion": "VER"},
            {"codigo": "SEGURIDAD.ROLES.CREAR",       "nombre": "Crear roles",              "accion": "CREAR"},
            {"codigo": "SEGURIDAD.ROLES.EDITAR",      "nombre": "Editar roles y permisos",  "accion": "EDITAR"},
            {"codigo": "SEGURIDAD.ROLES.ELIMINAR",    "nombre": "Eliminar roles",           "accion": "ELIMINAR"},
            {"codigo": "SEGURIDAD.PERMISOS.VER",      "nombre": "Ver catálogo de permisos", "accion": "VER"},
            {"codigo": "SEGURIDAD.MODULOS.VER",       "nombre": "Ver módulos del sistema",  "accion": "VER"},
        ]

        permisos = {}
        for data in permisos_data:
            permiso, creado = Permiso.objects.get_or_create(
                codigo=data["codigo"],
                defaults={
                    "id_modulo": modulo_seg,
                    "nombre": data["nombre"],
                    "accion": data["accion"],
                    "estado": True,
                },
            )
            permisos[data["codigo"]] = permiso
            self._log("Permiso", data["codigo"], creado)

        # 4. ASIGNAR TODOS LOS PERMISOS AL ROL ADMINISTRADOR (alcance GLOBAL)
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
                self.style.SUCCESS(f"  [OK] {len(nuevos_rp)} permisos asignados a ADMINISTRADOR")
            )
        else:
            self.stdout.write("  [--] ADMINISTRADOR ya tenia todos los permisos.")

        self.stdout.write(self.style.SUCCESS("\n[DONE] Datos iniciales cargados correctamente.\n"))

    def _log(self, tipo, codigo, creado):
        if creado:
            self.stdout.write(self.style.SUCCESS(f"  [OK] {tipo} creado: {codigo}"))
        else:
            self.stdout.write(f"  [--] {tipo} ya existe: {codigo}")
