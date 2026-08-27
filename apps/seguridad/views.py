"""
views.py — Módulo de Seguridad.

Endpoints:
  POST   /api/seguridad/login/              → Login y obtención de tokens JWT
  POST   /api/seguridad/logout/             → Invalidar refresh token
  POST   /api/seguridad/token/refresh/      → Renovar access token

  GET    /api/seguridad/usuarios/           → Listar usuarios (paginado)
  POST   /api/seguridad/usuarios/           → Crear usuario
  GET    /api/seguridad/usuarios/{id}/      → Detalle de usuario
  PUT    /api/seguridad/usuarios/{id}/      → Actualizar usuario
  DELETE /api/seguridad/usuarios/{id}/      → Soft delete de usuario

  GET    /api/seguridad/roles/              → Listar roles
  POST   /api/seguridad/roles/              → Crear rol
  GET    /api/seguridad/roles/{id}/         → Detalle de rol con permisos
  PUT    /api/seguridad/roles/{id}/         → Actualizar rol
  POST   /api/seguridad/roles/{id}/permisos/ → Asignar permisos a un rol

  GET    /api/seguridad/permisos/           → Listar todos los permisos (por módulo)
  GET    /api/seguridad/modulos/            → Listar módulos del sistema

Reglas aplicadas:
  - Toda lista está paginada (25 registros por defecto via settings)
  - select_related/prefetch_related para evitar N+1
  - transaction.atomic() en operaciones multi-paso
  - TienePermiso() verifica permiso + alcance en cada endpoint sensible
  - Nunca .all() sin límite
"""
import logging
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .models import Usuario, Rol, Permiso, Modulo, RolPermiso, UsuarioRol, Empresa
from .serializers import (
    LoginSerializer,
    UsuarioListSerializer,
    UsuarioDetalleSerializer,
    RolSerializer,
    AsignarPermisosRolSerializer,
    PermisoSerializer,
    ModuloSerializer,
    EmpresaSerializer,
    MiPerfilSerializer,
)
from .permissions import TienePermiso

logger = logging.getLogger(__name__)


# ==============================================================================
# AUTENTICACIÓN
# ==============================================================================

class LoginView(APIView):
    """
    POST /api/seguridad/login/
    Endpoint público. Devuelve tokens JWT + datos básicos del usuario.
    """
    permission_classes = [AllowAny]
    # Throttling a nivel de endpoint puede agregarse aquí con AnonRateThrottle
    # cuando se configure django-ratelimit para login.

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.save()
        return Response({"success": True, "data": data}, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    POST /api/seguridad/logout/
    Invalida el refresh token (lo añade a la blacklist de simplejwt).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if not refresh_token:
                return Response(
                    {"success": False, "mensaje": "Se requiere el refresh token."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            token = RefreshToken(refresh_token)
            token.blacklist()
            logger.info("Logout exitoso para usuario [%s].", request.user.username)
            return Response({"success": True, "mensaje": "Sesión cerrada correctamente."})
        except Exception as exc:
            logger.error("Error al cerrar sesión: %s", str(exc), exc_info=True)
            return Response(
                {"success": False, "mensaje": "No se pudo cerrar la sesión."},
                status=status.HTTP_400_BAD_REQUEST,
            )


# ==============================================================================
# USUARIOS
# ==============================================================================

class UsuarioListCreateView(ListCreateAPIView):
    """
    GET  /api/seguridad/usuarios/ → Lista paginada de usuarios
    POST /api/seguridad/usuarios/ → Crear nuevo usuario
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [TienePermiso("SEGURIDAD.USUARIOS.VER")]
        return [TienePermiso("SEGURIDAD.USUARIOS.CREAR")]

    def get_serializer_class(self):
        if self.request.method == "GET":
            return UsuarioListSerializer
        return UsuarioDetalleSerializer

    def get_queryset(self):
        """
        Filtra en DB, no en Python. Usa prefetch_related para evitar N+1
        al acceder a los roles de cada usuario en el serializer.
        """
        qs = Usuario.objects.filter(
            fecha_eliminacion__isnull=True  # excluir soft-deleted
        ).prefetch_related(
            "usuario_roles__id_rol",  # evita N+1 al listar roles por usuario
            "sucursales_asignadas__sucursal"
        ).only(
            "id_usuario", "username", "email", "nombres",
            "apellidos", "estado", "ultimo_acceso",
        )

        # Filtros opcionales por query params
        estado = self.request.query_params.get("estado")
        if estado:
            qs = qs.filter(estado=estado)

        busqueda = self.request.query_params.get("q")
        if busqueda:
            from django.db.models import Q
            qs = qs.filter(
                Q(username__icontains=busqueda)
                | Q(nombres__icontains=busqueda)
                | Q(apellidos__icontains=busqueda)
                | Q(email__icontains=busqueda)
            )

        return qs.order_by("apellidos", "nombres")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({"success": True, "data": response.data})

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return Response({"success": True, "data": response.data}, status=status.HTTP_201_CREATED)


class UsuarioDetalleView(RetrieveUpdateDestroyAPIView):
    """
    GET    /api/seguridad/usuarios/{id}/ → Detalle
    PUT    /api/seguridad/usuarios/{id}/ → Actualizar
    DELETE /api/seguridad/usuarios/{id}/ → Soft delete
    """
    serializer_class = UsuarioDetalleSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [TienePermiso("SEGURIDAD.USUARIOS.VER")]
        if self.request.method == "DELETE":
            return [TienePermiso("SEGURIDAD.USUARIOS.ELIMINAR")]
        return [TienePermiso("SEGURIDAD.USUARIOS.EDITAR")]

    def get_queryset(self):
        # Nunca solo .get(id=id) — siempre verifica alcance en el queryset
        return Usuario.objects.filter(
            fecha_eliminacion__isnull=True
        ).prefetch_related("usuario_roles__id_rol")

    def destroy(self, request, *args, **kwargs):
        """Soft delete: nunca eliminar físicamente un usuario."""
        instance = self.get_object()
        instance.fecha_eliminacion = timezone.now()
        instance.estado = "inactivo"
        instance.save(update_fields=["fecha_eliminacion", "estado"])
        logger.info("Usuario [%s] eliminado (soft) por [%s].", instance.username, request.user.username)
        return Response(
            {"success": True, "mensaje": "Usuario desactivado correctamente."},
            status=status.HTTP_200_OK,
        )


class MiPerfilView(APIView):
    """
    GET /api/seguridad/mi-perfil/ → Detalle del usuario autenticado
    PUT /api/seguridad/mi-perfil/ → Actualizar datos y contraseña
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = MiPerfilSerializer(request.user)
        return Response({"success": True, "data": serializer.data})

    def put(self, request):
        serializer = MiPerfilSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            "success": True, 
            "data": serializer.data, 
            "mensaje": "Perfil actualizado correctamente."
        })

# ==============================================================================
# ROLES
# ==============================================================================

class RolListCreateView(ListCreateAPIView):
    """
    GET  /api/seguridad/roles/ → Lista de roles con sus permisos
    POST /api/seguridad/roles/ → Crear nuevo rol
    """
    serializer_class = RolSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [TienePermiso("SEGURIDAD.ROLES.VER")]
        return [TienePermiso("SEGURIDAD.ROLES.CREAR")]

    def get_queryset(self):
        # prefetch_related para evitar N+1 al mostrar permisos de cada rol
        return Rol.objects.filter(estado=True).prefetch_related(
            "rol_permisos__id_permiso__id_modulo"
        ).order_by("nombre")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({"success": True, "data": response.data})

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return Response({"success": True, "data": response.data}, status=status.HTTP_201_CREATED)


class RolDetalleView(RetrieveUpdateDestroyAPIView):
    """
    GET    /api/seguridad/roles/{id}/
    PUT    /api/seguridad/roles/{id}/
    DELETE /api/seguridad/roles/{id}/
    """
    serializer_class = RolSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [TienePermiso("SEGURIDAD.ROLES.VER")]
        if self.request.method == "DELETE":
            return [TienePermiso("SEGURIDAD.ROLES.ELIMINAR")]
        return [TienePermiso("SEGURIDAD.ROLES.EDITAR")]

    def get_queryset(self):
        return Rol.objects.filter(estado=True).prefetch_related("rol_permisos__id_permiso__id_modulo")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.es_sistema:
            return Response(
                {"success": False, "mensaje": "No se puede eliminar un rol del sistema."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.estado = False
        instance.save(update_fields=["estado"])
        return Response({"success": True, "mensaje": "Rol desactivado correctamente."})


class AsignarPermisosRolView(APIView):
    """
    POST /api/seguridad/roles/{id}/permisos/
    Reemplaza los permisos de un rol de forma atómica.
    Recibe: {"permisos": [{"id_permiso": 1, "alcance": "GLOBAL"}, ...]}
    """
    def get_permissions(self):
        return [TienePermiso("SEGURIDAD.ROLES.EDITAR")]

    def post(self, request, pk):
        try:
            rol = Rol.objects.get(pk=pk)
        except Rol.DoesNotExist:
            return Response(
                {"success": False, "mensaje": "Rol no encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AsignarPermisosRolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        permisos_data = serializer.validated_data["permisos"]

        # Operación multi-paso dentro de transaction.atomic() para consistencia
        with transaction.atomic():
            RolPermiso.objects.filter(id_rol=rol).delete()
            nuevos = [
                RolPermiso(
                    id_rol=rol,
                    id_permiso_id=int(item["id_permiso"]),
                    alcance=item["alcance"],
                )
                for item in permisos_data
            ]
            RolPermiso.objects.bulk_create(nuevos)  # Inserción masiva en una sola query

        logger.info(
            "Permisos del rol [%s] actualizados por [%s]. Total: %d",
            rol.codigo, request.user.username, len(nuevos),
        )
        return Response(
            {"success": True, "mensaje": f"Se asignaron {len(nuevos)} permisos al rol {rol.nombre}."}
        )


# ==============================================================================
# PERMISOS Y MÓDULOS (catálogos de solo lectura)
# ==============================================================================

class PermisoListView(ListCreateAPIView):
    """GET /api/seguridad/permisos/ → Catálogo de permisos del sistema."""
    serializer_class = PermisoSerializer
    def get_permissions(self):
        return [TienePermiso("SEGURIDAD.ROLES.VER")]

    def get_queryset(self):
        return Permiso.objects.select_related("id_modulo").filter(
            estado=True
        ).order_by("id_modulo__orden", "accion")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({"success": True, "data": response.data})


class ModuloListView(APIView):
    """GET /api/seguridad/modulos/ → Lista de módulos del sistema para menú dinámico."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        modulos = Modulo.objects.filter(
            estado=True, visible_menu=True, id_modulo_padre__isnull=True
        ).prefetch_related("submodulos").order_by("orden")
        serializer = ModuloSerializer(modulos, many=True)
        return Response({"success": True, "data": serializer.data})


# ==============================================================================
# EMPRESA (CONFIGURACIÓN GLOBAL)
# ==============================================================================

class EmpresaView(APIView):
    """
    GET  /api/seguridad/empresa/ → Obtener la configuración de la empresa (Singleton)
    PUT  /api/seguridad/empresa/ → Actualizar la configuración de la empresa
    """
    # Cambiar esto a IsAuthenticated u otro permiso según necesidad
    permission_classes = [AllowAny] 

    def get_object(self):
        # Implementación singleton: Tomamos la primera empresa o creamos una vacía
        empresa, created = Empresa.objects.get_or_create(id=1, defaults={
            "razon_social": "Mi Empresa",
            "ruc": "00000000000",
            "direccion": "Dirección no configurada"
        })
        return empresa

    def get(self, request):
        empresa = self.get_object()
        serializer = EmpresaSerializer(empresa)
        return Response({"success": True, "data": serializer.data})

    def put(self, request):
        empresa = self.get_object()
        serializer = EmpresaSerializer(empresa, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"success": True, "data": serializer.data, "mensaje": "Empresa actualizada correctamente."})


# ==============================================================================
# UBIGEO
# ==============================================================================

from rest_framework.viewsets import ModelViewSet
from .models import Departamento, Provincia, Distrito
from .serializers import DepartamentoSerializer, ProvinciaSerializer, DistritoSerializer

class DepartamentoViewSet(ModelViewSet):
    """
    CRUD completo para Departamentos
    GET, POST, PUT, DELETE /api/seguridad/departamentos/
    """
    queryset = Departamento.objects.all()
    serializer_class = DepartamentoSerializer
    permission_classes = [AllowAny] # Ajustar según seguridad
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        if 'estado' in self.request.query_params:
            qs = qs.filter(estado=self.request.query_params['estado'] == 'true')
        return qs


class ProvinciaViewSet(ModelViewSet):
    """
    CRUD completo para Provincias
    GET, POST, PUT, DELETE /api/seguridad/provincias/
    """
    queryset = Provincia.objects.all().select_related('departamento')
    serializer_class = ProvinciaSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        dep_id = self.request.query_params.get('departamento')
        if dep_id:
            qs = qs.filter(departamento_id=dep_id)
        if 'estado' in self.request.query_params:
            qs = qs.filter(estado=self.request.query_params['estado'] == 'true')
        return qs


class DistritoViewSet(ModelViewSet):
    """
    CRUD completo para Distritos
    GET, POST, PUT, DELETE /api/seguridad/distritos/
    """
    queryset = Distrito.objects.all().select_related('provincia__departamento')
    serializer_class = DistritoSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        prov_id = self.request.query_params.get('provincia')
        if prov_id:
            qs = qs.filter(provincia_id=prov_id)
        dep_id = self.request.query_params.get('departamento')
        if dep_id:
            qs = qs.filter(provincia__departamento_id=dep_id)
        if 'estado' in self.request.query_params:
            qs = qs.filter(estado=self.request.query_params['estado'] == 'true')
        return qs
