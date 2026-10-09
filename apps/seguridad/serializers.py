"""
serializers.py — Módulo de Seguridad.

Reglas aplicadas:
  - Todo input externo se valida aquí (en el borde), nunca en la vista.
  - Nunca se expone el password_hash en respuestas.
  - Validaciones explícitas con mensajes claros.
"""
import logging
from datetime import timedelta
from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from . import cache_utils

from .models import (
    Usuario, Rol, Permiso, Modulo, RolPermiso, UsuarioRol, UsuarioPermiso, UsuarioSucursal, Empresa,
    Departamento, Provincia, Distrito, TipoCuentaBancaria, CuentaBancaria
)

logger = logging.getLogger(__name__)


# ==============================================================================
# AUTENTICACIÓN
# ==============================================================================

MAX_INTENTOS_LOGIN = 5     # contraseñas incorrectas seguidas antes de bloquear la cuenta
MINUTOS_BLOQUEO_LOGIN = 15  # cuánto dura el bloqueo; se levanta solo al vencer


def _mensaje_cuenta_bloqueada(usuario):
    return (
        f"Cuenta bloqueada hasta {usuario.bloqueado_hasta:%d/%m/%Y %H:%M}. "
        "Contacte al administrador."
    )


def _registrar_intento_fallido(username):
    """Suma un intento fallido; al llegar al máximo bloquea la cuenta unos minutos."""
    with transaction.atomic():
        usuario = Usuario.objects.select_for_update().filter(username=username).first()
        if usuario is None or usuario.esta_bloqueado():
            return
        usuario.intentos_fallidos += 1
        campos = ["intentos_fallidos"]
        if usuario.intentos_fallidos >= MAX_INTENTOS_LOGIN:
            usuario.bloqueado_hasta = timezone.now() + timedelta(minutes=MINUTOS_BLOQUEO_LOGIN)
            usuario.intentos_fallidos = 0
            campos.append("bloqueado_hasta")
            logger.warning("Cuenta [%s] bloqueada %d min por intentos fallidos.", username, MINUTOS_BLOQUEO_LOGIN)
        usuario.save(update_fields=campos)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=50)
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        username = attrs.get("username")
        password = attrs.get("password")

        # Una cuenta bloqueada no prueba contraseñas: se avisa de inmediato (y no se cuentan más intentos).
        bloqueada = Usuario.objects.filter(username=username, bloqueado_hasta__gt=timezone.now()).first()
        if bloqueada:
            raise serializers.ValidationError(_mensaje_cuenta_bloqueada(bloqueada))

        usuario = authenticate(username=username, password=password)

        if not usuario:
            logger.warning("Intento de login fallido para username: %s", username)
            _registrar_intento_fallido(username)
            raise serializers.ValidationError(
                "Credenciales incorrectas. Verifique su usuario y contraseña."
            )

        if usuario.estado != "activo":
            logger.warning("Login denegado: usuario inactivo/bloqueado [%s].", username)
            raise serializers.ValidationError("Su cuenta está inactiva. Contacte al administrador.")

        if usuario.esta_bloqueado():
            raise serializers.ValidationError(
                f"Cuenta bloqueada hasta {usuario.bloqueado_hasta:%d/%m/%Y %H:%M}. "
                "Contacte al administrador."
            )

        attrs["usuario"] = usuario
        return attrs

    def create(self, validated_data):
        usuario = validated_data["usuario"]
        refresh = RefreshToken.for_user(usuario)

        # Actualizar último acceso
        usuario.ultimo_acceso = timezone.now()
        usuario.intentos_fallidos = 0
        usuario.save(update_fields=["ultimo_acceso", "intentos_fallidos"])

        logger.info("Login exitoso para usuario [%s].", usuario.username)

        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "usuario": {
                "id": str(usuario.id_usuario),
                "username": usuario.username,
                "nombre": usuario.nombres,
                "apellidos": usuario.apellidos,
                "nombre_completo": usuario.nombre_completo,
                "email": usuario.email,
                "avatar_url": usuario.avatar_url,
                "requiere_cambio_password": usuario.requiere_cambio_password,
            },
        }


# ==============================================================================
# EMPRESA Y UBIGEO
# ==============================================================================

class EmpresaPublicSerializer(serializers.ModelSerializer):
    """
    Para el GET público que usa el Kiosko (sin login): solo los datos que
    van impresas en un ticket. Nunca incluye credenciales SUNAT.
    """
    class Meta:
        model = Empresa
        fields = [
            'id', 'razon_social', 'ruc', 'direccion', 'departamento', 'provincia',
            'distrito', 'telefono', 'email', 'web', 'logo', 'dias_validez_cotizacion',
        ]


class EmpresaSerializer(serializers.ModelSerializer):
    """
    Para el panel de configuración (autenticado). Las credenciales SUNAT
    sensibles son write_only: se pueden guardar pero el GET nunca las
    devuelve. Se exponen flags *_configurada de solo lectura para que el
    frontend muestre "Configurado" sin necesitar el valor real.
    """
    sunat_clave_secundaria = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )
    sunat_gre_client_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )
    sunat_clave_secundaria_configurada = serializers.SerializerMethodField()
    sunat_gre_client_secret_configurada = serializers.SerializerMethodField()

    class Meta:
        model = Empresa
        fields = '__all__'

    def get_sunat_clave_secundaria_configurada(self, obj):
        return bool(obj.sunat_clave_secundaria)

    def get_sunat_gre_client_secret_configurada(self, obj):
        return bool(obj.sunat_gre_client_secret)

    def update(self, instance, validated_data):
        # Un secreto en blanco significa "no lo toques", no "bórralo": solo
        # se sobreescribe cuando el usuario ingresa un valor nuevo de verdad.
        for campo in ('sunat_clave_secundaria', 'sunat_gre_client_secret'):
            if campo in validated_data and not validated_data[campo]:
                validated_data.pop(campo)
        return super().update(instance, validated_data)


# ==============================================================================
# CUENTAS BANCARIAS
# ==============================================================================

class TipoCuentaBancariaSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoCuentaBancaria
        fields = '__all__'


class CuentaBancariaSerializer(serializers.ModelSerializer):
    tipo_cuenta_nombre = serializers.CharField(source='tipo_cuenta.nombre', read_only=True)

    class Meta:
        model = CuentaBancaria
        fields = '__all__'

# ==============================================================================
# UBIGEO
# ==============================================================================

class DepartamentoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Departamento
        fields = '__all__'


class ProvinciaSerializer(serializers.ModelSerializer):
    departamento_nombre = serializers.CharField(source='departamento.nombre', read_only=True)

    class Meta:
        model = Provincia
        fields = '__all__'


class DistritoSerializer(serializers.ModelSerializer):
    provincia_nombre = serializers.CharField(source='provincia.nombre', read_only=True)
    departamento = serializers.IntegerField(source='provincia.departamento_id', read_only=True)
    departamento_nombre = serializers.CharField(source='provincia.departamento.nombre', read_only=True)

    class Meta:
        model = Distrito
        fields = '__all__'


# ==============================================================================
# MÓDULOS
# ==============================================================================

class ModuloSerializer(serializers.ModelSerializer):
    submodulos = serializers.SerializerMethodField()

    class Meta:
        model = Modulo
        fields = ["id_modulo", "codigo", "nombre", "icono", "ruta", "orden", "visible_menu", "estado", "submodulos"]

    def get_submodulos(self, obj):
        # Si la vista ya cargó todos los módulos en una sola consulta, se arma
        # el árbol en memoria (evita una consulta por cada módulo del menú).
        hijos_por_padre = self.context.get("hijos_por_padre")
        if hijos_por_padre is not None:
            return ModuloSerializer(
                hijos_por_padre.get(obj.id_modulo, []), many=True, context=self.context
            ).data
        qs = obj.submodulos.filter(estado=True, visible_menu=True).order_by("orden", "nombre", "id_modulo")
        return ModuloSerializer(qs, many=True).data


# ==============================================================================
# PERMISOS
# ==============================================================================

class PermisoSerializer(serializers.ModelSerializer):
    modulo_nombre = serializers.CharField(source="id_modulo.nombre", read_only=True)

    class Meta:
        model = Permiso
        fields = [
            "id_permiso", "id_modulo", "modulo_nombre", "codigo", "nombre", "accion", "estado",
            "grupo_padre", "grupo_submodulo",
        ]


# ==============================================================================
# ROLES
# ==============================================================================

class RolPermisoSerializer(serializers.ModelSerializer):
    permiso = PermisoSerializer(source="id_permiso", read_only=True)

    class Meta:
        model = RolPermiso
        fields = ["id", "permiso", "alcance"]


class RolSerializer(serializers.ModelSerializer):
    permisos = RolPermisoSerializer(source="rol_permisos", many=True, read_only=True)

    class Meta:
        model = Rol
        fields = [
            "id_rol", "codigo", "nombre", "descripcion",
            "es_sistema", "estado", "fecha_creacion", "permisos",
        ]
        read_only_fields = ["id_rol", "fecha_creacion"]

    def validate_codigo(self, value):
        return value.upper().strip()


class AsignarPermisosRolSerializer(serializers.Serializer):
    """
    Serializer para asignar/actualizar permisos a un rol desde la interfaz.
    Recibe una lista de {id_permiso, alcance}.
    """
    permisos = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        allow_empty=True,
    )

    def validate_permisos(self, value):
        alcances_validos = {"GLOBAL", "TALLER", "ASIGNADO", "PROPIO"}
        for item in value:
            if "id_permiso" not in item:
                raise serializers.ValidationError("Cada permiso debe tener 'id_permiso'.")
            if "alcance" not in item or item["alcance"] not in alcances_validos:
                raise serializers.ValidationError(
                    f"Alcance inválido. Opciones: {', '.join(alcances_validos)}"
                )
        return value


# ==============================================================================
# USUARIOS
# ==============================================================================

class UsuarioListSerializer(serializers.ModelSerializer):
    """Serializer ligero para listas (solo campos necesarios — evitar SELECT *)."""
    nombre_completo = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    sucursales = serializers.SerializerMethodField()

    class Meta:
        model = Usuario
        fields = [
            "id_usuario", "username", "email", "nombres", "apellidos", "nombre_completo",
            "estado", "ultimo_acceso", "roles", "sucursales"
        ]

    def get_nombre_completo(self, obj):
        return obj.nombre_completo

    def get_roles(self, obj):
        # prefetch_related se aplica en la vista — aquí solo accedemos al resultado
        return [
            {"id_rol": ur.id_rol.id_rol, "nombre": ur.id_rol.nombre}
            for ur in obj.usuario_roles.all()
            if ur.esta_vigente() and ur.id_rol.estado
        ]

    def get_sucursales(self, obj):
        return [
            {"id_sucursal": us.sucursal.id, "nombre": us.sucursal.nombre}
            for us in obj.sucursales_asignadas.all()
            if us.sucursal.estado
        ]


class UsuarioDetalleSerializer(serializers.ModelSerializer):
    """Serializer completo para crear/editar usuario."""
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    roles_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
    )
    sucursales_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
    )

    class Meta:
        model = Usuario
        fields = [
            "id_usuario", "username", "email", "nombres", "apellidos",
            "documento", "telefono", "avatar_url", "estado",
            "requiere_cambio_password", "password", "roles_ids", "sucursales_ids"
        ]
        read_only_fields = ["id_usuario"]
        # Nunca exponemos: password_hash, intentos_fallidos, bloqueado_hasta

    def validate_username(self, value):
        return value.lower().strip()

    def validate_email(self, value):
        return value.lower().strip()

    def create(self, validated_data):
        roles_ids = validated_data.pop("roles_ids", [])
        sucursales_ids = validated_data.pop("sucursales_ids", [])
        password = validated_data.pop("password", None)

        usuario = Usuario(**validated_data)
        if password:
            usuario.set_password(password)  # Hashing seguro del framework
        else:
            usuario.set_unusable_password()
        usuario.save()

        # Asignar roles en bulk — nunca uno por uno con .create() en loop
        if roles_ids:
            UsuarioRol.objects.bulk_create([
                UsuarioRol(id_usuario=usuario, id_rol_id=rol_id)
                for rol_id in roles_ids
            ])
            cache_utils.invalidar()  # bulk_create no dispara señales

        if sucursales_ids:
            UsuarioSucursal.objects.bulk_create([
                UsuarioSucursal(id_usuario=usuario, sucursal_id=sucursal_id)
                for sucursal_id in sucursales_ids
            ])

        logger.info("Usuario creado: %s", usuario.username)
        return usuario

    def update(self, instance, validated_data):
        roles_ids = validated_data.pop("roles_ids", None)
        sucursales_ids = validated_data.pop("sucursales_ids", None)
        password = validated_data.pop("password", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()

        if roles_ids is not None:
            # Reemplazar roles: eliminar actuales y crear nuevos en bulk
            instance.usuario_roles.all().delete()
            UsuarioRol.objects.bulk_create([
                UsuarioRol(id_usuario=instance, id_rol_id=rol_id)
                for rol_id in roles_ids
            ])
            cache_utils.invalidar()  # bulk_create no dispara señales

        if sucursales_ids is not None:
            instance.sucursales_asignadas.all().delete()
            UsuarioSucursal.objects.bulk_create([
                UsuarioSucursal(id_usuario=instance, sucursal_id=sucursal_id)
                for sucursal_id in sucursales_ids
            ])

        logger.info("Usuario actualizado: %s", instance.username)
        return instance

class MiPerfilSerializer(serializers.ModelSerializer):
    """Serializer para que un usuario pueda actualizar su propio perfil."""
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    rol_nombre = serializers.SerializerMethodField()
    
    class Meta:
        model = Usuario
        fields = [
            "id_usuario", "username", "email", "nombres", "apellidos",
            "documento", "telefono", "avatar_url", "password", "rol_nombre"
        ]
        read_only_fields = ["id_usuario", "username"]

    def get_rol_nombre(self, obj):
        """Obtiene el nombre del primer rol activo y vigente del usuario."""
        ur = obj.usuario_roles.filter(estado=True).select_related('id_rol').first()
        if ur:
            return ur.id_rol.nombre
        return None

    def validate_email(self, value):
        return value.lower().strip()

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()
        logger.info("El usuario [%s] ha actualizado su propio perfil.", instance.username)
        return instance
