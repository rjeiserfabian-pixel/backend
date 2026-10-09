import logging

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.seguridad.permissions import PermisoPorMetodoMixin, TienePermiso

from . import services
from .models import AsignacionHerramienta, CategoriaHerramienta, Herramienta, HistorialHerramienta
from .pagination import HerramientasPagination
from .permisos import tiene_permiso
from .services_mantenimientos import tiene_mantenimiento_en_curso
from .serializers import (
    AnularSerializer,
    AsignacionSerializer,
    CambiarEstadoSerializer,
    CategoriaHerramientaSerializer,
    DevolverSerializer,
    EntregarSerializer,
    HerramientaSerializer,
    HistorialHerramientaSerializer,
)

logger = logging.getLogger(__name__)

# Estados que solo puede fijar quien tiene el permiso de baja.
ESTADOS_DE_BAJA = {
    Herramienta.EstadoOperativo.PERDIDA,
    Herramienta.EstadoOperativo.DADA_DE_BAJA,
}


def _registrar_historial(herramienta, accion, detalle, usuario):
    HistorialHerramienta.objects.create(
        herramienta=herramienta, accion=accion, detalle=detalle, usuario=usuario
    )


class CategoriaHerramientaViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = 'HERRAMIENTAS.CATEGORIAS.VER'
    permiso_crear = 'HERRAMIENTAS.CATEGORIAS.CREAR'
    permiso_editar = 'HERRAMIENTAS.CATEGORIAS.EDITAR'
    permiso_eliminar = 'HERRAMIENTAS.CATEGORIAS.ELIMINAR'
    serializer_class = CategoriaHerramientaSerializer
    # Catálogo pequeño que el frontend consume completo (selectores): sin paginar a propósito.
    pagination_class = None
    filter_backends = [filters.SearchFilter]
    search_fields = ['nombre']

    def get_queryset(self):
        return (
            CategoriaHerramienta.objects.filter(estado=True)
            .annotate(
                total_herramientas_anotado=Count('herramientas', filter=Q(herramientas__estado=True))
            )
            .order_by('nombre')
        )

    def perform_destroy(self, instance):
        # Borrado lógico; se rechaza si aún tiene herramientas activas.
        if instance.herramientas.filter(estado=True).exists():
            raise ValidationError(
                'No se puede eliminar: la categoría tiene herramientas registradas.'
            )
        instance.estado = False
        instance.save(update_fields=['estado'])


class HerramientaViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = 'HERRAMIENTAS.INVENTARIO.VER'
    permiso_crear = 'HERRAMIENTAS.INVENTARIO.CREAR'
    permiso_editar = 'HERRAMIENTAS.INVENTARIO.EDITAR'
    permiso_eliminar = 'HERRAMIENTAS.INVENTARIO.ELIMINAR'
    serializer_class = HerramientaSerializer
    pagination_class = HerramientasPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['codigo', 'nombre', 'marca', 'modelo', 'numero_serie']
    ordering_fields = ['codigo', 'nombre', 'fecha_compra', 'estado_operativo']
    ordering = ['codigo']

    def get_queryset(self):
        qs = Herramienta.objects.filter(estado=True).select_related(
            'categoria', 'sucursal', 'almacen', 'proveedor'
        )
        params = self.request.query_params
        for campo, filtro in (
            ('categoria', 'categoria_id'),
            ('sucursal', 'sucursal_id'),
            ('estado_operativo', 'estado_operativo'),
            ('estado_fisico', 'estado_fisico'),
        ):
            valor = params.get(campo)
            if valor:
                qs = qs.filter(**{filtro: valor})
        return qs

    def perform_create(self, serializer):
        with transaction.atomic():
            herramienta = serializer.save(creado_por=self.request.user)
            _registrar_historial(
                herramienta, HistorialHerramienta.Accion.CREACION,
                'Herramienta registrada.', self.request.user,
            )

    def perform_update(self, serializer):
        with transaction.atomic():
            herramienta = serializer.save()
            campos = ', '.join(sorted(serializer.validated_data.keys()))
            _registrar_historial(
                herramienta, HistorialHerramienta.Accion.EDICION,
                f'Campos modificados: {campos}.' if campos else 'Edición sin cambios.',
                self.request.user,
            )

    def perform_destroy(self, instance):
        if instance.estado_operativo == Herramienta.EstadoOperativo.ASIGNADA:
            raise ValidationError('No se puede eliminar una herramienta que está asignada.')
        if tiene_mantenimiento_en_curso(instance):
            raise ValidationError('No se puede eliminar: tiene un mantenimiento en curso.')
        with transaction.atomic():
            instance.estado = False
            instance.save(update_fields=['estado', 'fecha_actualizacion'])
            _registrar_historial(
                instance, HistorialHerramienta.Accion.BAJA,
                'Registro eliminado del inventario.', self.request.user,
            )

    def get_permissions(self):
        # POST /cambiar-estado/ es una edición, no una creación.
        if self.action == 'cambiar_estado':
            return [TienePermiso(self.permiso_editar)]
        return super().get_permissions()

    @action(detail=True, methods=['post'], url_path='cambiar-estado')
    def cambiar_estado(self, request, pk=None):
        serializer = CambiarEstadoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nuevo = serializer.validated_data['estado_operativo']
        motivo = serializer.validated_data['motivo'].strip()
        if not motivo:
            return Response({'motivo': ['El motivo es obligatorio.']}, status=status.HTTP_400_BAD_REQUEST)

        if nuevo in ESTADOS_DE_BAJA and not TienePermiso('HERRAMIENTAS.BAJA.APROBAR').has_permission(
            request, self
        ):
            return Response(
                {'detail': 'No tiene permiso para dar de baja o marcar como perdida una herramienta.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if nuevo == Herramienta.EstadoOperativo.ASIGNADA:
            return Response(
                {'estado_operativo': ['El estado "Asignada" solo se establece al entregar la herramienta.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            herramienta = get_object_or_404(
                self.get_queryset().select_for_update(of=('self',)), pk=pk
            )
            actual = herramienta.estado_operativo
            if actual == Herramienta.EstadoOperativo.ASIGNADA:
                return Response(
                    {'detail': 'La herramienta está asignada; debe registrarse su devolución primero.'},
                    status=status.HTTP_409_CONFLICT,
                )
            if tiene_mantenimiento_en_curso(herramienta):
                return Response(
                    {'detail': 'La herramienta tiene un mantenimiento en curso; finalícelo o cancélelo primero.'},
                    status=status.HTTP_409_CONFLICT,
                )
            if actual == Herramienta.EstadoOperativo.DADA_DE_BAJA:
                return Response(
                    {'detail': 'Una herramienta dada de baja no puede cambiar de estado.'},
                    status=status.HTTP_409_CONFLICT,
                )
            if actual == nuevo:
                return Response(
                    {'estado_operativo': ['La herramienta ya tiene ese estado.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            herramienta.estado_operativo = nuevo
            herramienta.save(update_fields=['estado_operativo', 'fecha_actualizacion'])
            _registrar_historial(
                herramienta, HistorialHerramienta.Accion.CAMBIO_ESTADO,
                f'{actual} → {nuevo}. Motivo: {motivo}', request.user,
            )
        logger.info(
            'Herramienta %s: estado %s -> %s por usuario %s',
            herramienta.codigo, actual, nuevo, request.user.pk,
        )
        return Response(self.get_serializer(herramienta).data)

    @action(detail=True, methods=['get'], url_path='historial')
    def historial(self, request, pk=None):
        herramienta = self.get_object()
        qs = herramienta.historial.select_related('usuario')[:100]
        return Response(HistorialHerramientaSerializer(qs, many=True).data)


class AsignacionViewSet(PermisoPorMetodoMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        viewsets.GenericViewSet):
    """Entregas y devoluciones. No hay PUT/PATCH/DELETE: se entrega, se devuelve o se anula."""
    permiso_ver = 'HERRAMIENTAS.ASIGNACIONES.VER'
    permiso_crear = 'HERRAMIENTAS.ASIGNACIONES.CREAR'
    permiso_editar = 'HERRAMIENTAS.ASIGNACIONES.EDITAR'
    permiso_eliminar = 'HERRAMIENTAS.ASIGNACIONES.ELIMINAR'
    serializer_class = AsignacionSerializer
    pagination_class = HerramientasPagination
    filter_backends = [filters.SearchFilter]
    search_fields = [
        'herramienta__codigo', 'herramienta__nombre',
        'tecnico__nombres', 'tecnico__apellidos',
    ]

    # Acciones que no son "crear" se gatean con su permiso propio.
    _permiso_por_accion = {
        'devolver': 'permiso_editar',
        'anular': 'permiso_eliminar',
        'tecnicos': 'permiso_crear',
        'herramientas_disponibles': 'permiso_crear',
    }

    def get_permissions(self):
        if self.action is None:
            # Método HTTP no soportado (PUT/PATCH/DELETE): exige VER y DRF responde 405.
            return [TienePermiso(self.permiso_ver)]
        atributo = self._permiso_por_accion.get(self.action)
        if atributo:
            return [TienePermiso(getattr(self, atributo))]
        return super().get_permissions()

    def _base_queryset(self):
        return AsignacionHerramienta.objects.select_related(
            'herramienta', 'herramienta__sucursal', 'tecnico', 'entregado_por', 'recibido_por'
        )

    def get_queryset(self):
        qs = self._base_queryset()
        user = self.request.user
        alcance = getattr(self.request, 'alcance_efectivo', 'PROPIO')
        if not user.is_superuser:
            if alcance in ('PROPIO', 'ASIGNADO'):
                qs = qs.filter(tecnico=user)  # un técnico solo ve sus herramientas
            elif alcance == 'TALLER':
                sucursales = user.sucursales_asignadas.values_list('sucursal_id', flat=True)
                qs = qs.filter(herramienta__sucursal_id__in=sucursales)

        params = self.request.query_params
        for campo, filtro in (
            ('estado', 'estado'), ('tecnico', 'tecnico_id'),
            ('herramienta', 'herramienta_id'), ('sucursal', 'herramienta__sucursal_id'),
        ):
            valor = params.get(campo)
            if valor:
                qs = qs.filter(**{filtro: valor})
        if params.get('vencidas') in ('1', 'true', 'True'):
            qs = qs.filter(
                estado=AsignacionHerramienta.Estado.ACTIVA,
                fecha_devolucion_esperada__lt=timezone.localdate(),
            )
        return qs

    def create(self, request, *args, **kwargs):
        serializer = EntregarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        forzar = datos.get('forzar', False)
        if forzar and not tiene_permiso(request, 'HERRAMIENTAS.ASIGNACIONES.EXCEPCION'):
            raise PermissionDenied('No tiene permiso para autorizar entregas con mantenimiento vencido.')
        asignacion, advertencias = services.entregar_herramienta(
            herramienta_id=datos['herramienta'],
            tecnico_id=datos['tecnico'],
            entregado_por=request.user,
            fecha_devolucion_esperada=datos.get('fecha_devolucion_esperada'),
            observaciones=datos.get('observaciones', ''),
            autorizar_excepcion=forzar,
            motivo_excepcion=datos.get('motivo_excepcion', '').strip(),
        )
        data = AsignacionSerializer(self._base_queryset().get(pk=asignacion.pk)).data
        data['advertencias'] = advertencias
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='devolver')
    def devolver(self, request, pk=None):
        asignacion = self.get_object()  # respeta el alcance del usuario
        serializer = DevolverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data
        services.devolver_herramienta(
            asignacion_id=asignacion.pk,
            recibido_por=request.user,
            estado_fisico_devolucion=datos['estado_fisico_devolucion'],
            horas_uso_periodo=datos['horas_uso_periodo'],
            requiere_mantenimiento=datos['requiere_mantenimiento'],
            observaciones=datos.get('observaciones', ''),
        )
        return Response(AsignacionSerializer(self._base_queryset().get(pk=asignacion.pk)).data)

    @action(detail=True, methods=['post'], url_path='anular')
    def anular(self, request, pk=None):
        asignacion = self.get_object()
        serializer = AnularSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.anular_asignacion(
            asignacion_id=asignacion.pk, usuario=request.user,
            motivo=serializer.validated_data['motivo'],
        )
        return Response(AsignacionSerializer(self._base_queryset().get(pk=asignacion.pk)).data)

    @action(detail=False, methods=['get'], url_path='tecnicos', pagination_class=None)
    def tecnicos(self, request):
        """Técnicos a los que se puede entregar (para el selector del formulario)."""
        resultado = [
            {
                'id': str(u.pk),
                'nombre': f'{u.nombres} {u.apellidos}'.strip(),
                'sucursales': [a.sucursal_id for a in u.sucursales_asignadas.all()],
            }
            for u in services.tecnicos_asignables().prefetch_related('sucursales_asignadas')
        ]
        return Response(resultado)

    @action(detail=False, methods=['get'], url_path='herramientas-disponibles')
    def herramientas_disponibles(self, request):
        """Herramientas listas para entregar (máx. 50, con búsqueda)."""
        qs = Herramienta.objects.filter(
            estado=True, estado_operativo=Herramienta.EstadoOperativo.DISPONIBLE
        ).select_related('sucursal')
        buscar = request.query_params.get('search', '').strip()
        if buscar:
            qs = qs.filter(
                Q(codigo__icontains=buscar) | Q(nombre__icontains=buscar)
                | Q(marca__icontains=buscar) | Q(numero_serie__icontains=buscar)
            )
        sucursal = request.query_params.get('sucursal')
        if sucursal:
            qs = qs.filter(sucursal_id=sucursal)
        return Response([
            {'id': h.id, 'codigo': h.codigo, 'nombre': h.nombre, 'marca': h.marca,
             'sucursal': h.sucursal_id, 'sucursal_nombre': h.sucursal.nombre}
            for h in qs.order_by('codigo')[:50]
        ])
