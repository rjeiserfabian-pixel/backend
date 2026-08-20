"""
admin.py — Registro de modelos del módulo Seguridad en el panel de Django Admin.
Sirve como respaldo técnico para desarrolladores durante el desarrollo.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario, Modulo, Rol, Permiso, RolPermiso, UsuarioRol, UsuarioPermiso, Sesion, Auditoria


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ["username", "email", "nombres", "apellidos", "estado", "ultimo_acceso"]
    list_filter = ["estado", "is_staff"]
    search_fields = ["username", "email", "nombres", "apellidos"]
    ordering = ["apellidos", "nombres"]
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Información Personal", {"fields": ("nombres", "apellidos", "email", "documento", "telefono")}),
        ("Estado", {"fields": ("estado", "requiere_cambio_password", "mfa_habilitado", "intentos_fallidos", "bloqueado_hasta")}),
        ("Permisos Django", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "email", "nombres", "apellidos", "password1", "password2"),
        }),
    )


@admin.register(Modulo)
class ModuloAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "id_modulo_padre", "orden", "visible_menu", "estado"]
    list_filter = ["estado", "visible_menu"]
    search_fields = ["codigo", "nombre"]


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "es_sistema", "estado", "fecha_creacion"]
    list_filter = ["es_sistema", "estado"]
    search_fields = ["codigo", "nombre"]


@admin.register(Permiso)
class PermisoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "accion", "id_modulo", "estado"]
    list_filter = ["accion", "estado", "id_modulo"]
    search_fields = ["codigo", "nombre"]


@admin.register(RolPermiso)
class RolPermisoAdmin(admin.ModelAdmin):
    list_display = ["id_rol", "id_permiso", "alcance"]
    list_filter = ["alcance", "id_rol"]


@admin.register(UsuarioRol)
class UsuarioRolAdmin(admin.ModelAdmin):
    list_display = ["id_usuario", "id_rol", "estado", "fecha_asignacion", "fecha_expiracion"]
    list_filter = ["estado", "id_rol"]


@admin.register(UsuarioPermiso)
class UsuarioPermisoAdmin(admin.ModelAdmin):
    list_display = ["id_usuario", "id_permiso", "tipo", "alcance", "estado"]
    list_filter = ["tipo", "estado"]


@admin.register(Sesion)
class SesionAdmin(admin.ModelAdmin):
    list_display = ["id_usuario", "ip", "cerrada", "fecha_inicio", "ultimo_acceso"]
    list_filter = ["cerrada"]
    readonly_fields = ["id_sesion", "token_hash"]


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    list_display = ["fecha", "id_usuario", "modulo", "accion", "tabla_afectada", "registro_id", "ip"]
    list_filter = ["modulo", "accion"]
    search_fields = ["id_usuario__username", "tabla_afectada", "registro_id"]
    readonly_fields = ["id_auditoria", "datos_anteriores", "datos_nuevos"]
