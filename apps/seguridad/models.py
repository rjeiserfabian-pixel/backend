"""
models.py — Módulo de Seguridad.

Modelos:
  - Usuario (AbstractBaseUser custom)
  - Modulo
  - Rol
  - Permiso
  - RolPermiso (Rol <-> Permiso con alcance)
  - UsuarioRol (Usuario <-> Rol)
  - UsuarioPermiso (Excepción directa ALLOW/DENY por usuario)
  - Sesion
  - Auditoria

Reglas aplicadas:
  - UUID como PK en Usuario (más seguro que int secuencial expuesto en URLs)
  - db_index=True en campos usados frecuentemente en filter() y order_by()
  - soft delete en Usuario (fecha_eliminacion)
  - Nunca texto plano para contraseñas: usa el hashing del framework (set_password)
"""
import uuid
import logging

from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone

from .managers import UsuarioManager

logger = logging.getLogger(__name__)


class Usuario(AbstractBaseUser, PermissionsMixin):
    """
    Usuario custom que reemplaza al User de Django.
    Usa AbstractBaseUser para controlar completamente los campos
    y el hashing de contraseña (PBKDF2 por defecto del framework).
    """
    ESTADO_CHOICES = [
        ("activo", "Activo"),
        ("inactivo", "Inactivo"),
        ("bloqueado", "Bloqueado"),
    ]

    id_usuario = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    username = models.CharField(max_length=50, unique=True)
    email = models.EmailField(max_length=150, unique=True, db_index=True)
    nombres = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    documento = models.CharField(max_length=20, blank=True, null=True)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    avatar_url = models.TextField(blank=True, null=True)

    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default="activo",
        db_index=True,  # Se filtra frecuentemente por estado
    )
    ultimo_acceso = models.DateTimeField(null=True, blank=True)
    intentos_fallidos = models.IntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    requiere_cambio_password = models.BooleanField(default=False)
    mfa_habilitado = models.BooleanField(default=False)

    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    fecha_eliminacion = models.DateTimeField(null=True, blank=True)  # soft delete

    # Campos requeridos por PermissionsMixin y admin de Django
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UsuarioManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email", "nombres", "apellidos"]

    class Meta:
        db_table = "usuarios"
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ["apellidos", "nombres"]

    def __str__(self):
        return f"{self.nombres} {self.apellidos} ({self.username})"

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}"

    def esta_bloqueado(self):
        """Verifica si el usuario está bloqueado por intentos fallidos."""
        if self.bloqueado_hasta and self.bloqueado_hasta > timezone.now():
            return True
        return False


class Modulo(models.Model):
    """
    Módulo del sistema (ej: ORDENES, CLIENTES, SEGURIDAD).
    Sirve para agrupar permisos y construir el menú dinámico en el frontend.
    """
    id_modulo = models.AutoField(primary_key=True)
    id_modulo_padre = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submodulos",
    )
    codigo = models.CharField(max_length=50, unique=True)  # ej: 'ORDENES'
    nombre = models.CharField(max_length=100)
    icono = models.CharField(max_length=50, blank=True, null=True)
    ruta = models.CharField(max_length=100, blank=True, null=True)
    orden = models.IntegerField(default=0)
    visible_menu = models.BooleanField(default=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = "modulos"
        verbose_name = "Módulo"
        verbose_name_plural = "Módulos"
        ordering = ["orden", "nombre"]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"


class Rol(models.Model):
    """
    Rol del sistema (ej: ADMINISTRADOR, MECANICO).
    Los roles son datos, no enums hardcodeados en código.
    Se pueden crear desde la interfaz sin tocar código.
    """
    id_rol = models.AutoField(primary_key=True)
    codigo = models.CharField(max_length=50, unique=True, db_index=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    es_sistema = models.BooleanField(default=False)  # True = no se puede borrar
    estado = models.BooleanField(default=True, db_index=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "roles"
        verbose_name = "Rol"
        verbose_name_plural = "Roles"
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.codigo} — {self.nombre}"


class Permiso(models.Model):
    """
    Permiso atómico del sistema (ej: ORDENES.APROBAR).
    El catálogo de permisos posibles está atado a lo que el sistema sabe hacer.
    Lo configurable es quién tiene cuáles, no cuáles existen.
    """
    id_permiso = models.AutoField(primary_key=True)
    id_modulo = models.ForeignKey(
        Modulo,
        on_delete=models.CASCADE,
        related_name="permisos",
        db_index=True,
    )
    codigo = models.CharField(max_length=100, unique=True, db_index=True)
    nombre = models.CharField(max_length=150)
    accion = models.CharField(
        max_length=30,
        db_index=True,  # Se filtra frecuentemente por acción
    )  # VER/CREAR/EDITAR/ELIMINAR/APROBAR/CAMBIAR_ESTADO
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = "permisos"
        verbose_name = "Permiso"
        verbose_name_plural = "Permisos"
        ordering = ["id_modulo", "accion"]

    def __str__(self):
        return f"{self.codigo}"


class RolPermiso(models.Model):
    """
    Tabla intermedia Rol <-> Permiso.
    Incluye el ALCANCE de datos que tiene ese rol sobre ese permiso.
    Esta tabla es el corazón del motor de permisos configurable.
    """
    ALCANCE_CHOICES = [
        ("GLOBAL", "Global — acceso total"),
        ("TALLER", "Taller — solo su taller/sucursal"),
        ("ASIGNADO", "Asignado — solo registros asignados a él"),
        ("PROPIO", "Propio — solo los que él mismo creó"),
    ]

    id_rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name="rol_permisos",
        db_index=True,
    )
    id_permiso = models.ForeignKey(
        Permiso,
        on_delete=models.CASCADE,
        related_name="rol_permisos",
        db_index=True,
    )
    alcance = models.CharField(
        max_length=30,
        choices=ALCANCE_CHOICES,
        default="PROPIO",
    )

    class Meta:
        db_table = "rol_permisos"
        verbose_name = "Permiso de Rol"
        verbose_name_plural = "Permisos de Roles"
        unique_together = [("id_rol", "id_permiso")]

    def __str__(self):
        return f"{self.id_rol.codigo} → {self.id_permiso.codigo} [{self.alcance}]"


class UsuarioRol(models.Model):
    """
    Asignación de roles a usuarios.
    Un usuario puede tener múltiples roles.
    Soporta fecha de expiración para roles temporales.
    """
    id_usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name="usuario_roles",
        db_index=True,
    )
    id_rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name="usuario_roles",
        db_index=True,
    )
    fecha_asignacion = models.DateTimeField(default=timezone.now)
    fecha_expiracion = models.DateTimeField(null=True, blank=True)  # null = indefinido
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = "usuario_roles"
        verbose_name = "Rol de Usuario"
        verbose_name_plural = "Roles de Usuarios"
        unique_together = [("id_usuario", "id_rol")]

    def __str__(self):
        return f"{self.id_usuario.username} → {self.id_rol.codigo}"

    def esta_vigente(self):
        """Verifica que el rol no haya expirado."""
        if self.fecha_expiracion and self.fecha_expiracion < timezone.now():
            return False
        return self.estado


class UsuarioPermiso(models.Model):
    """
    Excepción directa por usuario (ALLOW/DENY puntual).
    Permite, por ejemplo, que un mecánico específico pueda eliminar,
    aunque su rol normalmente no lo permita — sin crear un rol nuevo.
    Regla: DENY siempre gana sobre ALLOW.
    """
    TIPO_CHOICES = [
        ("ALLOW", "Permitir"),
        ("DENY", "Denegar"),
    ]
    ALCANCE_CHOICES = RolPermiso.ALCANCE_CHOICES

    id_usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name="usuario_permisos",
        db_index=True,
    )
    id_permiso = models.ForeignKey(
        Permiso,
        on_delete=models.CASCADE,
        related_name="usuario_permisos",
        db_index=True,
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    alcance = models.CharField(max_length=30, choices=ALCANCE_CHOICES, null=True, blank=True)
    fecha_inicio = models.DateTimeField(default=timezone.now)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = "usuario_permisos"
        verbose_name = "Permiso Directo de Usuario"
        verbose_name_plural = "Permisos Directos de Usuarios"

    def __str__(self):
        return f"{self.tipo}: {self.id_usuario.username} → {self.id_permiso.codigo}"


class Sesion(models.Model):
    """
    Registro de sesiones activas por usuario.
    Permite ver y cerrar sesiones remotamente.
    """
    id_sesion = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    id_usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name="sesiones",
        db_index=True,
    )
    token_hash = models.TextField()
    ip = models.CharField(max_length=45, blank=True, null=True)
    dispositivo = models.CharField(max_length=100, blank=True, null=True)
    navegador = models.CharField(max_length=100, blank=True, null=True)
    fecha_inicio = models.DateTimeField(default=timezone.now)
    ultimo_acceso = models.DateTimeField(default=timezone.now)
    fecha_expiracion = models.DateTimeField(null=True, blank=True)
    cerrada = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "sesiones"
        verbose_name = "Sesión"
        verbose_name_plural = "Sesiones"
        ordering = ["-ultimo_acceso"]

    def __str__(self):
        return f"Sesión {self.id_usuario.username} — {self.ip}"


class Auditoria(models.Model):
    """
    Registro inmutable de acciones realizadas en el sistema.
    Guarda quién hizo qué, cuándo, desde dónde y sobre qué dato.
    """
    id_auditoria = models.BigAutoField(primary_key=True)
    id_usuario = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        related_name="auditorias",
        db_index=True,
    )
    modulo = models.CharField(max_length=50, db_index=True)
    accion = models.CharField(max_length=50, db_index=True)
    tabla_afectada = models.CharField(max_length=50)
    registro_id = models.CharField(max_length=50, blank=True, null=True)
    datos_anteriores = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    ip = models.CharField(max_length=45, blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "auditoria"
        verbose_name = "Registro de Auditoría"
        verbose_name_plural = "Registros de Auditoría"
        ordering = ["-fecha"]

    def __str__(self):
        return f"[{self.fecha:%Y-%m-%d %H:%M}] {self.modulo}.{self.accion} por {self.id_usuario}"


class UsuarioSucursal(models.Model):
    """
    Relación que indica a qué sucursales tiene acceso un usuario.
    Si un usuario es Administrador (rol), puede ignorar esto y ver todas.
    """
    id_usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name="sucursales_asignadas",
        db_index=True,
    )
    sucursal = models.ForeignKey(
        'inventario.Sucursal',
        on_delete=models.CASCADE,
        related_name="usuarios_asignados",
        db_index=True,
    )
    estado = models.BooleanField(default=True)
    fecha_asignacion = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "usuario_sucursales"
        verbose_name = "Sucursal de Usuario"
        verbose_name_plural = "Sucursales de Usuarios"
        unique_together = [("id_usuario", "sucursal")]

    def __str__(self):
        return f"{self.id_usuario.username} → {self.sucursal}"
