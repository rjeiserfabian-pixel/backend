import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.seguridad.permissions import PermisoPorMetodoMixin, TienePermiso

from . import services_mantenimientos as svc
from .models import (
    AsignacionHerramienta,
    Herramienta,
    IncidenciaHerramienta,
    PlanMantenimientoHerramienta,
    RegistroMantenimiento,
)
from .pagination import HerramientasPagination
from .permisos import tiene_permiso
from .serializers_mantenimientos import (
    CancelarMantenimientoSerializer,
    CrearIncidenciaSerializer,
    FinalizarMantenimientoSerializer,
    IncidenciaSerializer,
    IniciarMantenimientoSerializer,
    PlanMantenimientoSerializer,
    RegistroMantenimientoSerializer,
    ResolverIncidenciaSerializer,
)

logger = logging.getLogger(__name__)

PERM_COSTOS = 'HERRAMIENTAS.COSTOS.VER'
MAX_ALERTAS = 30
DIAS_AVISO_GARANTIA = 30


class AccionPermisoMixin(PermisoPorMetodoMixin):
    """Añade dos cosas al mixin compartido (sin modificarlo):
    - métodos HTTP no soportados responden 405 en vez de 500;
    - acciones extra se gatean con el permiso indicado en `_permiso_por_accion`."""
    _permiso_por_accion = {}

    def get_permissions(self):
        if self.action is None:
            return [TienePermiso(self.permiso_ver)]
        atributo = self._permiso_por_accion.get(self.action)
        if atributo:
            return [TienePermiso(getattr(self, atributo))]
        return super().get_permissions()


# ───────────── Planes de mantenimiento ─────────────

class PlanMantenimientoViewSet(AccionPermisoMixin, viewsets.ModelViewSet):
    permiso_ver = 'HERRAMIENTAS.MANTENIMIENTOS.VER'
    permiso_crear = 'HERRAMIENTAS.MANTENIMIENTOS.CREAR'
    permiso_editar = 'HERRAMIENTAS.MANTENIMIENTOS.EDITAR'
    permiso_eliminar = 'HERRAMIENTAS.MANTENIMIENTOS.ELIMINAR'
    serializer_class = PlanMantenimientoSerializer
    pagination_class = HerramientasPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['nombre', 'herramienta__codigo', 'herramienta__nombre']

    def get_queryset(self):
        qs = PlanMantenimientoHerramienta.objects.filter(herramienta__estado=True).select_related(
            'herramienta'
        ).order_by(F('proxima_fecha').asc(nulls_last=True), 'id')
        params = self.request.query_params
        if params.get('herramienta'):
            qs = qs.filter(herramienta_id=params['herramienta'])
        if params.get('sucursal'):
            qs = qs.filter(herramienta__sucursal_id=params['sucursal'])
        # Por defecto solo planes activos; ?activo=todos los incluye todos.
        activo = params.get('activo', '1')
        if activo in ('1', 'true', 'True'):
            qs = qs.filter(activo=True)
        elif activo in ('0', 'false', 'False'):
            qs = qs.filter(activo=False)
        vencimiento = params.get('vencimiento')
        if vencimiento == 'VENCIDO':
            qs = qs.filter(activo=True).filter(svc.planes_vencidos_q())
        elif vencimiento == 'POR_VENCER':
            qs = qs.filter(activo=True).filter(svc.planes_por_vencer_q()).exclude(svc.planes_vencidos_q())
        return qs

    def perform_create(self, serializer):
        herramienta = serializer.validated_data['herramienta']
        # La línea base de horas es la lectura actual de la herramienta.
        serializer.save(creado_por=self.request.user, ultimas_horas=herramienta.horas_uso_acumuladas)

    def perform_destroy(self, instance):
        # Los planes no se borran: se desactivan para conservar el historial.
        instance.activo = False
        instance.save(update_fields=['activo'])


# ───────────── Registros de mantenimiento ─────────────

class RegistroMantenimientoViewSet(AccionPermisoMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                                   viewsets.GenericViewSet):
    permiso_ver = 'HERRAMIENTAS.MANTENIMIENTOS.VER'
    permiso_crear = 'HERRAMIENTAS.MANTENIMIENTOS.CREAR'
    permiso_editar = 'HERRAMIENTAS.MANTENIMIENTOS.EDITAR'
    permiso_eliminar = 'HERRAMIENTAS.MANTENIMIENTOS.ELIMINAR'
    serializer_class = RegistroMantenimientoSerializer
    pagination_class = HerramientasPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['herramienta__codigo', 'herramienta__nombre', 'descripcion']
    _permiso_por_accion = {'finalizar': 'permiso_editar', 'cancelar': 'permiso_editar'}

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['ver_costos'] = tiene_permiso(self.request, PERM_COSTOS)
        return context

    def _base_queryset(self):
        return RegistroMantenimiento.objects.select_related(
            'herramienta', 'plan', 'creado_por'
        )

    def get_queryset(self):
        qs = self._base_queryset()
        params = self.request.query_params
        for campo, filtro in (
            ('estado', 'estado'), ('tipo', 'tipo'),
            ('herramienta', 'herramienta_id'), ('sucursal', 'herramienta__sucursal_id'),
        ):
            if params.get(campo):
                qs = qs.filter(**{filtro: params[campo]})
        return qs

    def _responder(self, registro, codigo=status.HTTP_200_OK):
        registro = self._base_queryset().get(pk=registro.pk)
        return Response(self.get_serializer(registro).data, status=codigo)

    def create(self, request, *args, **kwargs):
        serializer = IniciarMantenimientoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        registro = svc.iniciar_mantenimiento(
            herramienta_id=d['herramienta'], tipo=d['tipo'], descripcion=d['descripcion'],
            usuario=request.user, plan_id=d.get('plan'), realizado_por=d.get('realizado_por', ''),
        )
        return self._responder(registro, status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='finalizar')
    def finalizar(self, request, pk=None):
        registro = self.get_object()
        serializer = FinalizarMantenimientoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        # Sin el permiso de costos, el valor enviado se ignora.
        costo = d['costo'] if tiene_permiso(request, PERM_COSTOS) else None
        svc.finalizar_mantenimiento(
            registro_id=registro.pk, usuario=request.user, resultado=d['resultado'], costo=costo,
            repuestos_usados=d['repuestos_usados'], realizado_por=d['realizado_por'],
            estado_fisico_final=d['estado_fisico_final'],
            dejar_fuera_de_servicio=d['dejar_fuera_de_servicio'],
        )
        return self._responder(registro)

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, pk=None):
        registro = self.get_object()
        serializer = CancelarMantenimientoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        svc.cancelar_mantenimiento(
            registro_id=registro.pk, usuario=request.user, motivo=serializer.validated_data['motivo']
        )
        return self._responder(registro)


# ───────────── Incidencias ─────────────

class IncidenciaViewSet(AccionPermisoMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        viewsets.GenericViewSet):
    permiso_ver = 'HERRAMIENTAS.INCIDENCIAS.VER'
    permiso_crear = 'HERRAMIENTAS.INCIDENCIAS.CREAR'
    permiso_editar = 'HERRAMIENTAS.INCIDENCIAS.EDITAR'
    permiso_eliminar = 'HERRAMIENTAS.INCIDENCIAS.EDITAR'
    serializer_class = IncidenciaSerializer
    pagination_class = HerramientasPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['herramienta__codigo', 'herramienta__nombre', 'descripcion']
    _permiso_por_accion = {'resolver': 'permiso_editar'}

    def _base_queryset(self):
        return IncidenciaHerramienta.objects.select_related(
            'herramienta', 'responsable', 'reportada_por', 'resuelta_por'
        )

    def _solo_propias(self):
        alcance = getattr(self.request, 'alcance_efectivo', 'PROPIO')
        return (not self.request.user.is_superuser) and alcance in ('PROPIO', 'ASIGNADO')

    def get_queryset(self):
        qs = self._base_queryset()
        if self._solo_propias():
            qs = qs.filter(Q(reportada_por=self.request.user) | Q(responsable=self.request.user))
        params = self.request.query_params
        for campo, filtro in (
            ('estado', 'estado'), ('tipo', 'tipo'), ('herramienta', 'herramienta_id'),
        ):
            if params.get(campo):
                qs = qs.filter(**{filtro: params[campo]})
        return qs

    def create(self, request, *args, **kwargs):
        serializer = CrearIncidenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        incidencia = svc.crear_incidencia(
            herramienta_id=d['herramienta'], tipo=d['tipo'], descripcion=d['descripcion'],
            usuario=request.user, solo_propias=self._solo_propias(),
        )
        data = IncidenciaSerializer(self._base_queryset().get(pk=incidencia.pk)).data
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='resolver')
    def resolver(self, request, pk=None):
        incidencia = self.get_object()
        serializer = ResolverIncidenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        svc.resolver_incidencia(
            incidencia_id=incidencia.pk, usuario=request.user, decision=d['decision'],
            notas=d['notas'], puede_dar_baja=tiene_permiso(request, 'HERRAMIENTAS.BAJA.APROBAR'),
        )
        return Response(IncidenciaSerializer(self._base_queryset().get(pk=incidencia.pk)).data)


# ───────────── Alertas (campana del header) ─────────────

class AlertasHerramientasView(APIView):
    """Mantenimientos vencidos o por vencer, préstamos vencidos y garantías por vencer.

    Devuelve { total, results } con el mismo formato que usa la campana del header."""

    def get_permissions(self):
        return [TienePermiso('HERRAMIENTAS.MANTENIMIENTOS.VER')]

    def get(self, request):
        hoy = timezone.localdate()
        sucursal = request.query_params.get('sucursal_id')
        urgentes, otros = [], []
        total = 0

        planes = PlanMantenimientoHerramienta.objects.filter(
            activo=True, herramienta__estado=True
        ).exclude(
            herramienta__estado_operativo__in=[
                Herramienta.EstadoOperativo.PERDIDA, Herramienta.EstadoOperativo.DADA_DE_BAJA,
            ]
        ).select_related('herramienta')
        if sucursal:
            planes = planes.filter(herramienta__sucursal_id=sucursal)

        vencidos = planes.filter(svc.planes_vencidos_q(hoy))
        por_vencer = planes.filter(svc.planes_por_vencer_q(hoy)).exclude(svc.planes_vencidos_q(hoy))
        total += vencidos.count() + por_vencer.count()
        for plan in vencidos.order_by('proxima_fecha', 'id')[:MAX_ALERTAS]:
            dias = max((hoy - plan.proxima_fecha).days, 0) if plan.proxima_fecha else 0
            exceso = (
                plan.herramienta.horas_uso_acumuladas - plan.proximas_horas
                if plan.proximas_horas is not None else Decimal('0')
            )
            etiqueta = f'{dias} d' if plan.proxima_fecha and plan.proxima_fecha < hoy else f'+{exceso:g} h'
            urgentes.append({
                'id': f'plan-{plan.pk}', 'tipo': 'MANTENIMIENTO', 'urgencia': 'VENCIDO',
                'titulo': f'{plan.herramienta.codigo} - {plan.herramienta.nombre}',
                'subtitulo': f'Mantenimiento vencido: {plan.nombre}',
                'dias_vencido': dias, 'etiqueta': etiqueta, 'to': '/herramientas/mantenimientos',
            })
        for plan in por_vencer.order_by('proxima_fecha', 'id')[:MAX_ALERTAS]:
            restante = (plan.proxima_fecha - hoy).days if plan.proxima_fecha else None
            otros.append({
                'id': f'plan-{plan.pk}', 'tipo': 'MANTENIMIENTO', 'urgencia': 'POR_VENCER',
                'titulo': f'{plan.herramienta.codigo} - {plan.herramienta.nombre}',
                'subtitulo': f'Mantenimiento por vencer: {plan.nombre}',
                'dias_vencido': 0, 'etiqueta': f'en {restante} d' if restante is not None else 'pronto',
                'to': '/herramientas/mantenimientos',
            })

        if TienePermiso('HERRAMIENTAS.ASIGNACIONES.VER').has_permission(request, self):
            prestamos = AsignacionHerramienta.objects.filter(
                estado=AsignacionHerramienta.Estado.ACTIVA, fecha_devolucion_esperada__lt=hoy
            ).select_related('herramienta', 'tecnico')
            if sucursal:
                prestamos = prestamos.filter(herramienta__sucursal_id=sucursal)
            alcance = getattr(request, 'alcance_efectivo', 'GLOBAL')
            if not request.user.is_superuser and alcance in ('PROPIO', 'ASIGNADO'):
                prestamos = prestamos.filter(tecnico=request.user)
            total += prestamos.count()
            for a in prestamos.order_by('fecha_devolucion_esperada')[:MAX_ALERTAS]:
                dias = (hoy - a.fecha_devolucion_esperada).days
                urgentes.append({
                    'id': f'asignacion-{a.pk}', 'tipo': 'PRESTAMO', 'urgencia': 'VENCIDO',
                    'titulo': f'{a.herramienta.codigo} - {a.herramienta.nombre}',
                    'subtitulo': f'Sin devolver: {a.tecnico.nombres} {a.tecnico.apellidos}'.strip(),
                    'dias_vencido': dias, 'etiqueta': f'{dias} d', 'to': '/herramientas/asignaciones',
                })

        if tiene_permiso(request, 'HERRAMIENTAS.INVENTARIO.VER'):
            garantias = Herramienta.objects.filter(
                estado=True, garantia_hasta__gte=hoy,
                garantia_hasta__lte=hoy + timedelta(days=DIAS_AVISO_GARANTIA),
            ).exclude(estado_operativo__in=[
                Herramienta.EstadoOperativo.PERDIDA, Herramienta.EstadoOperativo.DADA_DE_BAJA,
            ])
            if sucursal:
                garantias = garantias.filter(sucursal_id=sucursal)
            total += garantias.count()
            for h in garantias.order_by('garantia_hasta')[:MAX_ALERTAS]:
                restante = (h.garantia_hasta - hoy).days
                otros.append({
                    'id': f'garantia-{h.pk}', 'tipo': 'GARANTIA', 'urgencia': 'POR_VENCER',
                    'titulo': f'{h.codigo} - {h.nombre}', 'subtitulo': 'Garantía por vencer',
                    'dias_vencido': 0, 'etiqueta': f'en {restante} d', 'to': '/herramientas',
                })

        urgentes.sort(key=lambda i: -i['dias_vencido'])
        return Response({'total': total, 'results': (urgentes + otros)[:MAX_ALERTAS]})
